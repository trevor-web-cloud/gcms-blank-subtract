# -*- coding: utf-8 -*-
"""
GC-MS BLANK 背景扣除工具（Agilent MSD ChemStation .D 資料夾）
================================================================
用途 : 以一份 BLANK run 的 DATA.MS 為背景，逐 scan、逐 m/z 對樣品 run 做
       全質譜背景扣除，產出扣除後 TIC/EIC、目標峰面積前後對照、峰頂淨化
       質譜、QC 摘要與前後對照圖。

扣除定義:
    corrected_signed = sample - alpha * blank          (可為負，供定量核對)
    corrected_clip   = max(corrected_signed, 0)        (負值截零，近似
                       ChemStation / 圖庫搜尋慣例；截零比例會在 QC 揭露)

前置檢查 (preflight，不符預設停機、可用 --force 放行 Gain/方法不符):
    - 兩份 .D 的 scan 數相同、m/z grid 完全相同
    - RT 軸最大偏差 <= 0.01 min (預設門檻；本工具不做 RT 對齊/扭曲校正)
    - Gain Factor 與方法檔名一致 (不一致代表偵測器響應不同，直接扣除不成立)

輸入 : --sample <.D 資料夾> --blank <.D 資料夾>
輸出 (--out 資料夾，--formats 控制，預設 csv,cdf,mzml 三種全開):
    [csv] corrected_TIC_EIC.csv     RT / TIC(raw,blank,corr_clip,corr_signed) /
                              目標離子 EIC(raw,corr_clip)
    [csv] peak_area_comparison.csv  RESULTS.CSV 各峰以相同 scan 窗積分之
                              raw / blank / corr_clip / corr_signed 面積與
                              扣除變化 %，並列 ChemStation 報告面積供對照
    [csv] apex_spectra_all.csv      各 RESULTS 峰頂點 scan 的質譜 (raw/blank/corr)
    [csv] qc_summary.csv            總量、截零比例、各 RT 區段背景占比、BLANK
                              主要背景離子、前置檢查結果
    [csv] fig_TIC_overlay.png       TIC 前後對照 (全程 + 目標峰區放大)
    [csv] fig_apex_spectrum.png     主峰峰頂質譜 前後對照
    [csv] summary.xlsx              上述表格的 Excel 彙總
    [cdf] <sample>_corr.CDF         扣除後 (clip 版) 全質譜，ANDI-MS (AIA)
                              netCDF 格式，可由 MSD ChemStation 以
                              File > Import File > Load AIA File 載入分析；
                              變數布局比照開源 chromConverter write_andi_ms
                              與 ANDI-MS 慣例 (scan 單位秒、僅存 >0 點)
    [mzml] <sample>_corr.mzML       扣除後 (clip 版) 全質譜 mzML 1.1.0，
                              供 OpenChrom / MZmine / ProteoWizard 生態分析
    README_run.md             本次執行參數與判讀說明

注意 : rainbow-api 僅讀取 DATA.MS，本工具不產生新的 .D / DATA.MS；
       CDF/mzML 匯出的是 clip 版 (負值已截零)，因兩格式之強度值以
       非負為前提；signed 版僅存於 CSV。
       ChemStation 面積為其內部積分器結果 (含基線處理)，與本工具以相同
       scan 窗做梯形/逐 scan 總和之面積在絕對尺度上可能不同，比較時以
       「同窗、同法、扣除前後變化 %」為準。
       CDF 能否被特定 ChemStation 版本開啟，須於該實機驗收；本工具
       交付前會以獨立重讀驗證 CDF/mzML 內容與扣除矩陣一致。
依賴 : rainbow-api, numpy, pandas, matplotlib, openpyxl, scipy (CDF 寫出)
"""
import argparse
import base64
import csv
import os
import re
import sys
import zlib
from datetime import datetime

import numpy as np
import pandas as pd
import rainbow as rb

# Windows 主控台預設字碼頁 (cp950/cp1252 等) 無法輸出部分中文字元，
# argparse --help 與中文訊息會丟 UnicodeEncodeError；強制 UTF-8 輸出，
# 並把主控台字碼頁切為 65001 (UTF-8)，英文版 Windows 亦可正確顯示中文。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.platform.startswith("win"):
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass

RT_DEV_LIMIT_MIN = 0.01          # RT 軸允許最大偏差 (min)，超過視為需對齊、本工具停機
DEFAULT_REGIONS = [(2.287, 3.0), (3.0, 5.0), (5.0, 6.0), (6.0, 7.0),
                   (7.0, 10.0), (10.0, 14.0), (14.0, 18.0)]


# ----------------------------------------------------------------------------
# 讀取與中繼資料
# ----------------------------------------------------------------------------
def _read_text(path):
    with open(path, "rb") as f:
        return f.read().decode("latin-1", errors="replace")


def parse_metadata(dpath):
    """自 acqmeth.txt / runstart.txt / PRE_POST.INI 取方法、Gain、儀器序號。"""
    meta = {"dpath": dpath, "folder": os.path.basename(dpath.rstrip("/"))}
    acq_path = os.path.join(dpath, "acqmeth.txt")
    acq = _read_text(acq_path) if os.path.exists(acq_path) else ""

    def grab(pattern, cast=float, default=None):
        m = re.search(pattern, acq)
        if not m:
            return default
        try:
            return cast(m.group(1))
        except ValueError:
            return default

    meta["gain_factor"] = grab(r"Gain Factor\s*:\s*([\d.]+)")
    meta["em_voltage"] = grab(r"Resulting EM Voltage\s*:\s*([\d.]+)")
    meta["low_mass"] = grab(r"Low Mass\s*:\s*([\d.]+)")
    meta["high_mass"] = grab(r"High Mass\s*:\s*([\d.]+)")
    meta["threshold"] = grab(r"Threshold\s*:\s*([\d.]+)")
    meta["solvent_delay_min"] = grab(r"Solvent Delay\s*:\s*([\d.]+)")
    m = re.search(r"C:\\MSDCHEM\\1\\METHODS\\(\S+\.M)", acq)
    meta["method_file"] = m.group(1) if m else ""

    pre_path = os.path.join(dpath, "PRE_POST.INI")
    pre = _read_text(pre_path) if os.path.exists(pre_path) else ""
    m = re.search(r"serial_n=(\S+)", pre)
    meta["instrument_serial"] = m.group(1) if m else ""
    m = re.search(r"RunType=(\S+)", pre)
    meta["run_type"] = m.group(1) if m else ""

    run_path = os.path.join(dpath, "runstart.txt")
    run = _read_text(run_path) if os.path.exists(run_path) else ""
    m = re.search(r"Sample Name \(_dataname\$\) = (\S+)", run)
    meta["sample_name"] = m.group(1) if m else meta["folder"]
    return meta


def load_ms(dpath):
    datadir = rb.read(dpath)
    ms = datadir.get_file("DATA.MS")
    if ms is None:
        raise FileNotFoundError(f"{dpath} 內找不到 DATA.MS")
    rt = np.asarray(ms.xlabels, dtype=float)
    mz = np.asarray(ms.ylabels, dtype=float)
    mat = np.asarray(ms.data, dtype=float)
    return rt, mz, mat


# ----------------------------------------------------------------------------
# RESULTS.CSV (ChemStation EIC 積分報告) 解析
# ----------------------------------------------------------------------------
def parse_results_csv(path):
    """回傳 peaks: [{ion, peak_no, rt_min, first, max, last, height_cs, area_cs}]。
    first/max/last 為 ChemStation scan 編號 (1-based)。"""
    peaks = []
    if not path or not os.path.exists(path):
        return peaks
    text = _read_text(path)
    cur_ion = None
    for line in text.splitlines():
        m = re.match(r"\[INT EIC Ion\s+([\d.]+)", line)
        if m:
            cur_ion = int(round(float(m.group(1))))
            continue
        m = re.match(r"\d+=\s*,(.*)$", line)
        if m and cur_ion is not None:
            body = m.group(1)
            if '"Peak"' in body:      # Header 行跳過
                continue
            parts = next(csv.reader([body]))
            try:
                vals = [p.strip().strip('"') for p in parts]
                peaks.append({
                    "ion": cur_ion,
                    "peak_no": int(vals[0]),
                    "rt_min": float(vals[1]),
                    "first": int(vals[2]),
                    "max": int(vals[3]),
                    "last": int(vals[4]),
                    "pk_ty": vals[5],
                    "height_cs": float(vals[6]),
                    "area_cs": float(vals[7]),
                })
            except (ValueError, IndexError):
                continue
    return peaks


# ----------------------------------------------------------------------------
# 前置檢查
# ----------------------------------------------------------------------------
def preflight(s_meta, b_meta, rt_s, mz_s, rt_b, mz_b, force=False):
    checks = []

    def add(name, ok, detail, hard=True):
        checks.append({"check": name, "pass": bool(ok), "detail": detail,
                       "hard_fail": hard})
        return ok

    add("scan_count_equal", len(rt_s) == len(rt_b),
        f"sample={len(rt_s)}, blank={len(rt_b)}")
    add("mz_grid_equal", np.array_equal(mz_s, mz_b),
        f"sample n={len(mz_s)}, blank n={len(mz_b)}")
    if len(rt_s) == len(rt_b):
        dev = float(np.max(np.abs(rt_s - rt_b)))
        add("rt_axis_deviation", dev <= RT_DEV_LIMIT_MIN,
            f"max|dRT|={dev:.6f} min (limit {RT_DEV_LIMIT_MIN})")
    else:
        add("rt_axis_deviation", False, "scan 數不同，無法比對 RT 軸")
    add("gain_factor_equal",
        s_meta["gain_factor"] == b_meta["gain_factor"],
        f"sample={s_meta['gain_factor']}, blank={b_meta['gain_factor']}",
        hard=not force)
    add("method_file_equal",
        s_meta["method_file"] == b_meta["method_file"],
        f"sample={s_meta['method_file']}, blank={b_meta['method_file']}",
        hard=not force)
    add("instrument_serial_equal",
        s_meta["instrument_serial"] == b_meta["instrument_serial"],
        f"sample={s_meta['instrument_serial']}, blank={b_meta['instrument_serial']}",
        hard=False)
    return checks


# ----------------------------------------------------------------------------
# ANDI-MS (AIA/CDF) 匯出 — netCDF classic (CDF-1)，僅存強度 > 0 之點
# ----------------------------------------------------------------------------
def write_andi_cdf(path, rt_min, mz, mat, sample_name):
    """mat 為扣除後 (clip) 矩陣。scan 時間依 ANDI-MS 慣例存秒。"""
    from scipy.io import netcdf_file

    rt_sec = np.asarray(rt_min, dtype=float) * 60.0
    n_scans = len(rt_sec)
    counts = np.array([int(np.count_nonzero(row > 0)) for row in mat],
                      dtype=np.int64)
    scan_index = np.zeros(n_scans, dtype=np.int64)
    if n_scans > 1:
        scan_index[1:] = np.cumsum(counts)[:-1]
    mass_all, int_all, time_all = [], [], []
    for i in range(n_scans):
        sel = mat[i] > 0
        mass_all.append(mz[sel].astype(np.float32))
        int_all.append(mat[i][sel].astype(np.float32))
        time_all.append(np.full(int(sel.sum()), rt_sec[i], dtype=np.float32))
    mass_cat = np.concatenate(mass_all) if mass_all else np.array([], np.float32)
    int_cat = np.concatenate(int_all) if int_all else np.array([], np.float32)
    time_cat = np.concatenate(time_all) if time_all else np.array([], np.float32)
    n_points = len(mass_cat)

    durs = np.diff(rt_sec)
    med_dur = float(np.median(durs)) if len(durs) else 0.0
    scan_duration = np.append(durs, med_dur).astype(float)
    tic = mat.sum(axis=1)

    f = netcdf_file(path, "w", version=1)
    try:
        f.createDimension("point_number", n_points)
        f.createDimension("scan_number", n_scans)
        f.createDimension("range_number", 1)
        f.createDimension("instrument_number", 1)
        f.createDimension("error_number", 1)
        f.createDimension("_32_byte_string", 32)
        f.createDimension("_64_byte_string", 64)

        def var(name, typ, dims, data, units=None):
            v = f.createVariable(name, typ, dims)
            v[:] = data
            if units is not None:
                v.units = units
            return v

        def char_var(name, text, strlen, dims):
            v = f.createVariable(name, "c", dims)
            arr = np.full((strlen, 1), b" ", dtype="S1")
            b = text.encode("ascii", "replace")[:strlen]
            for j, ch in enumerate(b):
                arr[j, 0] = bytes([ch])
            v[:] = arr
            return v

        var("error_log", "c", ("error_number", "_64_byte_string"),
            np.full((1, 64), b" ", dtype="S1"))
        var("a_d_sampling_rate", "d", ("range_number",),
            np.array([1.0 / med_dur if med_dur else 0.0]))
        var("a_d_co_detector_delay", "d", ("range_number",), np.array([0.0]))
        var("scan_acquisition_time", "d", ("scan_number",), rt_sec,
            units="Seconds")
        var("scan_duration", "d", ("scan_number",), scan_duration)
        var("inter_scan_time", "d", ("scan_number",), scan_duration)
        var("resolution", "d", ("scan_number",), np.zeros(n_scans))
        var("actual_scan_number", "i", ("scan_number",),
            np.arange(1, n_scans + 1, dtype=np.int32))
        var("total_intensity", "d", ("scan_number",), tic,
            units="Arbitrary Intensity Units")
        var("mass_range_min", "d", ("scan_number",),
            np.full(n_scans, float(mz.min())))
        var("mass_range_max", "d", ("scan_number",),
            np.full(n_scans, float(mz.max())))
        var("time_range_min", "d", ("scan_number",),
            np.full(n_scans, float(rt_sec.min())))
        var("time_range_max", "d", ("scan_number",),
            np.full(n_scans, float(rt_sec.max())))
        var("scan_index", "i", ("scan_number",), scan_index.astype(np.int32))
        var("point_count", "i", ("scan_number",), counts.astype(np.int32))
        var("time_values", "f", ("point_number",), time_cat, units="Seconds")
        var("mass_values", "f", ("point_number",), mass_cat, units="M/Z")
        var("intensity_values", "f", ("point_number",), int_cat,
            units="Arbitrary Intensity Units")
        char_var("instrument_name", "New Power", 32,
                 ("_32_byte_string", "instrument_number"))
        char_var("instrument_mfr", "Agilent Technologies", 32,
                 ("_32_byte_string", "instrument_number"))
        char_var("instrument_model", "5973N MSD", 32,
                 ("_32_byte_string", "instrument_number"))
        char_var("instrument_sw_version", "MSD ChemStation E.02.02.1431", 32,
                 ("_32_byte_string", "instrument_number"))
        char_var("instrument_os_version", "", 32,
                 ("_32_byte_string", "instrument_number"))

        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        f.dataset_completeness = "C1"
        f.ms_template_revision = "1.0.1"
        f.netcdf_revision = "netCDF classic (CDF-1) via scipy"
        f.netcdf_file_date_time_stamp = stamp
        f.experiment_date_time_stamp = stamp
        f.experiment_title = f"{sample_name} (BLANK subtracted)"
        f.experiment_type = "Mass Spectrometry"
        f.source_file_reference = sample_name
        f.test_ionization_mode = "Electron Impact"
        f.test_ionization_polarity = "Positive Polarity"
        f.test_detector_type = "Electron Multiplier"
        f.raw_data_nscans = n_scans
        f.raw_data_starting_scan_no = 1
        f.raw_data_mass_factor = 1
        f.raw_data_time_factor = 1
        f.raw_data_intensity_factor = 1
        f.raw_data_intensity_offset = 0
        f.raw_data_mass_units = "m/z"
        f.raw_data_time_units = "Seconds"
        f.raw_data_intensity_units = "Arbitrary Intensity Units"
        f.raw_data_mass_format = "Float"
        f.raw_data_time_format = "Float"
        f.raw_data_intensity_format = "Float"
        f.comments = ("BLANK-subtracted (clip) data generated by "
                      "blank_subtract.py from Agilent .D/DATA.MS")
    finally:
        f.close()
    return n_points


# ----------------------------------------------------------------------------
# mzML 匯出 — mzML 1.1.0，32-bit float + zlib，僅存強度 > 0 之點
# ----------------------------------------------------------------------------
def _b64_array(arr, dtype):
    raw = np.asarray(arr, dtype=dtype).tobytes()
    return base64.b64encode(zlib.compress(raw)).decode("ascii")


def write_mzml(path, rt_min, mz, mat, sample_name, tic):
    n_scans = len(rt_min)
    lines = []
    w = lines.append
    w('<?xml version="1.0" encoding="utf-8"?>')
    w('<mzML xmlns="http://psi.hupo.org/ms/mzml" '
      'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
      'xsi:schemaLocation="http://psi.hupo.org/ms/mzml '
      'http://psidev.info/files/ms/mzML/xsd/mzML1.1.0.xsd" version="1.1.0">')
    w("<cvList count=\"2\">")
    w('<cv id="MS" fullName="Proteomics Standards Initiative Mass Spectrometry Ontology" '
      'URI="https://raw.githubusercontent.com/HUPO-PSI/psi-ms-CV/master/psi-ms.obo"/>')
    w('<cv id="UO" fullName="Unit Ontology" '
      'URI="https://raw.githubusercontent.com/bio-ontology-research-group/unit-ontology/master/unit.obo"/>')
    w("</cvList>")
    w("<fileDescription><fileContent>")
    w('<cvParam accession="MS:1000579" name="MS1 spectrum" cvRef="MS"/>')
    w('<cvParam accession="MS:1000580" name="ms level" cvRef="MS" value="1"/>')
    w("</fileContent></fileDescription>")
    w('<softwareList count="1"><software id="blank_subtract" version="1.1">')
    w('<cvParam accession="MS:1000531" name="software" cvRef="MS" value="blank_subtract.py"/>')
    w("</software></softwareList>")
    w('<instrumentConfigurationList count="1">'
      '<instrumentConfiguration id="IC1"><cvParam accession="MS:1000031" '
      'name="instrument model" cvRef="MS" value="Agilent 5973N MSD"/>'
      "</instrumentConfiguration></instrumentConfigurationList>")
    w('<dataProcessingList count="1"><dataProcessing id="DP1">'
      '<processingMethod order="0" softwareRef="blank_subtract">'
      '<cvParam accession="MS:1000544" name="Conversion to mzML" cvRef="MS"/>'
      "</processingMethod></dataProcessing></dataProcessingList>")
    w(f'<run id="{sample_name}_corr" defaultInstrumentConfigurationRef="IC1">')
    w(f'<spectrumList count="{n_scans}" defaultDataProcessingRef="DP1">')
    for i in range(n_scans):
        sel = mat[i] > 0
        mz_arr = mz[sel].astype(np.float32)
        in_arr = mat[i][sel].astype(np.float32)
        w(f'<spectrum index="{i}" id="scan={i + 1}" '
          f'defaultArrayLength="{len(mz_arr)}">')
        w('<cvParam accession="MS:1000511" name="ms level" cvRef="MS" value="1"/>')
        w('<cvParam accession="MS:1000127" name="centroid spectrum" cvRef="MS"/>')
        w('<cvParam accession="MS:1000130" name="positive scan" cvRef="MS"/>')
        w(f'<cvParam accession="MS:1000285" name="total ion current" cvRef="MS" '
          f'value="{tic[i]:.6g}"/>')
        w(f'<scanList count="1"><scan><cvParam accession="MS:1000016" '
          f'name="scan start time" cvRef="MS" value="{rt_min[i]:.6f}" '
          f'unitAccession="UO:0000031" unitName="minute" unitCvRef="UO"/>'
          f"</scan></scanList>")
        if len(mz_arr):
            base_peak = float(in_arr.max())
            base_mz = float(mz_arr[int(np.argmax(in_arr))])
            w(f'<cvParam accession="MS:1000505" name="base peak intensity" '
              f'cvRef="MS" value="{base_peak:.6g}"/>')
            w(f'<cvParam accession="MS:1000504" name="base peak m/z" '
              f'cvRef="MS" value="{base_mz:.4f}"/>')
        w('<binaryDataArrayList count="2">')
        for acc, name, arr in (("MS:1000514", "m/z array", mz_arr),
                               ("MS:1000515", "intensity array", in_arr)):
            w("<binaryDataArray>")
            w('<cvParam accession="MS:1000521" name="32-bit float" cvRef="MS"/>')
            w('<cvParam accession="MS:1000574" name="zlib compression" cvRef="MS"/>')
            w(f'<cvParam accession="{acc}" name="{name}" cvRef="MS"/>')
            w(f"<binary>{_b64_array(arr, '<f4')}</binary>")
            w("</binaryDataArray>")
        w("</binaryDataArrayList></spectrum>")
    w("</spectrumList>")
    w('<chromatogramList count="1" defaultDataProcessingRef="DP1">')
    w('<chromatogram index="0" id="TIC" defaultArrayLength="%d">' % n_scans)
    w('<cvParam accession="MS:1000235" name="total ion current chromatogram" cvRef="MS"/>')
    w('<binaryDataArrayList count="2">')
    for acc, name, arr in (("MS:1000595", "time array",
                            np.asarray(rt_min, dtype=np.float64)),
                           ("MS:1000515", "intensity array",
                            np.asarray(tic, dtype=np.float64))):
        w("<binaryDataArray>")
        w('<cvParam accession="MS:1000523" name="64-bit float" cvRef="MS"/>')
        w('<cvParam accession="MS:1000576" name="no compression" cvRef="MS"/>')
        w(f'<cvParam accession="{acc}" name="{name}" cvRef="MS"/>')
        w(f"<binary>{base64.b64encode(arr.tobytes()).decode('ascii')}</binary>")
        w("</binaryDataArray>")
    w("</binaryDataArrayList></chromatogram></chromatogramList>")
    w("</run></mzML>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return n_scans


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def trapz_window(rt, y, i0, i1):
    return float(np.trapezoid(y[i0:i1 + 1], rt[i0:i1 + 1]))


def _gui_smoke():
    """CI 煙霧測試: 建立並立即銷毀 Tk 根視窗，驗證打包後 tkinter 可用。"""
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    root.update()
    root.destroy()
    print("GUI smoke OK (tkinter 可用)")


GUI_STR = {
    "zh": {
        "title": "GCMS BLANK 背景扣除",
        "sample": "樣品 .D：", "blank": "BLANK .D：", "out": "輸出資料夾：",
        "browse": "選擇資料夾…", "pick_title": "選擇 .D 資料夾",
        "out_hint": "(輸出留空 = out_<樣品名>，建於目前目錄)",
        "alpha": "Alpha 扣除係數：", "formats": "匯出格式：",
        "run": "開始扣除", "open_out": "開啟輸出資料夾",
        "ready": "請選擇樣品與 BLANK 的 .D 資料夾",
        "running": "執行中…", "done_status": "完成，輸出於 ",
        "fail_status": "執行未完成，請看紀錄",
        "err": "錯誤", "warn": "提醒", "done": "完成",
        "path_missing": " 資料夾不存在：",
        "no_datams": " 資料夾內找不到 DATA.MS，仍要繼續嗎？",
        "alpha_nan": "Alpha 必須是數字，或填 auto 自動估算", "no_format": "至少選一種匯出格式",
        "done_msg": "背景扣除完成。\n輸出資料夾：",
    },
    "en": {
        "title": "GCMS BLANK Background Subtraction",
        "sample": "Sample .D:", "blank": "BLANK .D:", "out": "Output folder:",
        "browse": "Browse...",
        "pick_title": "Select .D folder",
        "out_hint": "(leave empty = out_<sample name>, in current folder)",
        "alpha": "Alpha factor:", "formats": "Formats:",
        "run": "Run subtraction", "open_out": "Open output folder",
        "ready": "Select the sample and BLANK .D folders",
        "running": "Running...",
        "done_status": "Done. Output: ",
        "fail_status": "Failed - see log",
        "err": "Error", "warn": "Warning", "done": "Done",
        "path_missing": " folder not found: ",
        "no_datams": " has no DATA.MS inside. Continue anyway?",
        "alpha_nan": "Alpha must be a number, or 'auto'",
        "no_format": "Select at least one output format",
        "done_msg": "Subtraction complete.\nOutput folder: ",
    },
}


def _detect_lang():
    """依系統語系選 GUI 語言: zh 開頭 -> 中文，其餘 (含英文版 Windows) -> 英文。"""
    try:
        import locale
        loc = (locale.getlocale()[0] or "").lower()
    except Exception:
        loc = ""
    return "zh" if loc.startswith(("zh", "chinese")) else "en"


def launch_gui():
    """圖形介面: 資料夾選擇視窗 (雙擊 exe 不帶參數、或 --gui 啟動)。
    依系統語系自動中/英，可用視窗右上角按鈕即時切換，避免英文版
    Windows 上出現亂碼或缺字。"""
    import queue
    import threading
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext

    lang = {"cur": _detect_lang()}
    T = lambda: GUI_STR[lang["cur"]]  # noqa: E731

    root = tk.Tk()
    root.geometry("680x560")
    root.minsize(580, 480)

    sample_var = tk.StringVar()
    blank_var = tk.StringVar()
    out_var = tk.StringVar()
    alpha_var = tk.StringVar(value="1.0")
    csv_var = tk.BooleanVar(value=True)
    cdf_var = tk.BooleanVar(value=True)
    mzml_var = tk.BooleanVar(value=True)
    status_var = tk.StringVar()

    container = tk.Frame(root)
    container.pack(fill="both", expand=True)
    state = {"log_text": ""}

    def build():
        for w in container.winfo_children():
            w.destroy()
        t = T()
        root.title(t["title"])
        top = tk.Frame(container, padx=12, pady=6)
        top.pack(fill="x")
        tk.Label(top, text="Language:").pack(side="right")
        tk.Button(top, text="English", width=8,
                  command=lambda: switch("en")).pack(side="right", padx=2)
        tk.Button(top, text="中文", width=8,
                  command=lambda: switch("zh")).pack(side="right", padx=2)

        frm = tk.Frame(container, padx=12, pady=4)
        frm.pack(fill="x")

        def browse(var):
            d = filedialog.askdirectory(title=t["pick_title"], mustexist=True)
            if d:
                var.set(d)

        def path_row(row, label, var):
            tk.Label(frm, text=label, width=16, anchor="e").grid(
                row=row, column=0, sticky="e", pady=3)
            tk.Entry(frm, textvariable=var).grid(
                row=row, column=1, sticky="ew", pady=3)
            tk.Button(frm, text=t["browse"], width=13,
                      command=lambda: browse(var)).grid(
                row=row, column=2, padx=6, pady=3)

        path_row(0, t["sample"], sample_var)
        path_row(1, t["blank"], blank_var)
        path_row(2, t["out"], out_var)
        frm.columnconfigure(1, weight=1)
        tk.Label(frm, text=t["out_hint"], fg="gray").grid(
            row=3, column=1, sticky="w")

        opt = tk.Frame(container, padx=12)
        opt.pack(fill="x")
        tk.Label(opt, text=t["alpha"]).pack(side="left")
        tk.Entry(opt, textvariable=alpha_var, width=7).pack(
            side="left", padx=(0, 16))
        tk.Label(opt, text=t["formats"]).pack(side="left")
        tk.Checkbutton(opt, text="CSV/Excel", variable=csv_var).pack(side="left")
        tk.Checkbutton(opt, text="CDF (ChemStation)", variable=cdf_var).pack(side="left")
        tk.Checkbutton(opt, text="mzML", variable=mzml_var).pack(side="left")

        log = scrolledtext.ScrolledText(container, height=16,
                                        font=("Consolas", 9))
        log.pack(fill="both", expand=True, padx=12, pady=8)
        if state["log_text"]:
            log.insert("end", state["log_text"])
            log.see("end")
        state["log_widget"] = log

        btns = tk.Frame(container, padx=12)
        btns.pack(fill="x", pady=(0, 4))
        run_btn = tk.Button(btns, text=t["run"], width=16,
                            command=lambda: on_run(run_btn, open_btn))
        run_btn.pack(side="left")
        open_btn = tk.Button(btns, text=t["open_out"], width=18,
                             command=on_open, state="disabled")
        open_btn.pack(side="left", padx=8)
        state["run_btn"], state["open_btn"] = run_btn, open_btn
        if not status_var.get():
            status_var.set(t["ready"])
        tk.Label(container, textvariable=status_var, anchor="w",
                 fg="gray").pack(fill="x", padx=12, pady=(0, 8))

    def switch(new_lang):
        if new_lang != lang["cur"]:
            lang["cur"] = new_lang
            w = state.get("log_widget")
            if w is not None:
                state["log_text"] = w.get("1.0", "end")
            build()

    log_q = queue.Queue()
    result = {"out": None}

    class _LogWriter:
        def write(self, s):
            if s:
                log_q.put(("log", s))
            return len(s)

        def flush(self):
            pass

    def poll():
        t = T()
        try:
            while True:
                kind, payload = log_q.get_nowait()
                w = state.get("log_widget")
                if kind == "log" and w is not None:
                    w.insert("end", payload)
                    w.see("end")
                elif kind == "done":
                    result["out"] = payload
                    status_var.set(t["done_status"] + str(payload))
                    state["run_btn"].config(state="normal")
                    state["open_btn"].config(state="normal")
                    messagebox.showinfo(t["done"], t["done_msg"] + str(payload))
                elif kind in ("exit", "error"):
                    status_var.set(t["fail_status"])
                    state["run_btn"].config(state="normal")
                    messagebox.showerror(t["err"], str(payload))
        except queue.Empty:
            pass
        root.after(120, poll)

    def on_run(run_btn, open_btn):
        t = T()
        sample, blank = sample_var.get().strip(), blank_var.get().strip()
        for label, p in ((t["sample"], sample), (t["blank"], blank)):
            if not p or not os.path.isdir(p):
                messagebox.showerror(t["err"], label + t["path_missing"] + p)
                return
            if not os.path.exists(os.path.join(p, "DATA.MS")):
                if not messagebox.askokcancel(
                        t["warn"], label + t["no_datams"] + "\n" + p):
                    return
        a_txt = alpha_var.get().strip()
        if a_txt.lower() == "auto":
            alpha = "auto"
        else:
            try:
                alpha = float(a_txt)
            except ValueError:
                messagebox.showerror(t["err"], t["alpha_nan"])
                return
        fmts = [n for n, v in (("csv", csv_var), ("cdf", cdf_var),
                               ("mzml", mzml_var)) if v.get()]
        if not fmts:
            messagebox.showerror(t["err"], t["no_format"])
            return
        ns = argparse.Namespace(
            sample=sample, blank=blank, alpha=alpha,
            out=out_var.get().strip() or None, ions=None, force=False,
            formats=",".join(fmts), gui=False, gui_smoke=False)
        run_btn.config(state="disabled")
        open_btn.config(state="disabled")
        status_var.set(t["running"])

        def worker():
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = _LogWriter()
            try:
                out_path = run_pipeline(ns)
                log_q.put(("done", out_path))
            except SystemExit as e:
                log_q.put(("exit", str(e)))
            except Exception as e:  # noqa: BLE001 - GUI 需攔截並顯示
                log_q.put(("error", f"{type(e).__name__}: {e}"))
            finally:
                sys.stdout, sys.stderr = old_out, old_err

        threading.Thread(target=worker, daemon=True).start()

    def on_open():
        if result["out"] and os.path.isdir(result["out"]):
            try:
                os.startfile(result["out"])  # Windows
            except AttributeError:
                import subprocess
                subprocess.Popen(["xdg-open", result["out"]])

    build()
    poll()
    root.mainloop()


def main():
    ap = argparse.ArgumentParser(description="GC-MS BLANK 全質譜背景扣除")
    ap.add_argument("--sample", help="樣品 .D 資料夾路徑")
    ap.add_argument("--blank", help="BLANK .D 資料夾路徑")
    ap.add_argument("--gui", action="store_true",
                    help="開啟圖形介面 (資料夾選擇視窗)；不帶任何參數時亦同")
    ap.add_argument("--gui-smoke", action="store_true",
                    help=argparse.SUPPRESS)
    ap.add_argument("--alpha", default="1.0",
                    help="BLANK 扣除係數 (預設 1.0; 填 auto 由標記離子+"
                         "背景區自動估算)")
    ap.add_argument("--out", default=None, help="輸出資料夾 (預設 out_<sample>)")
    ap.add_argument("--ions", default=None,
                    help="EIC 目標離子，逗號分隔 (預設取 RESULTS.CSV 離子，"
                         "無則 57,91,104,105,118,135)")
    ap.add_argument("--force", action="store_true",
                    help="Gain/方法不符時仍繼續 (結果會註記)")
    ap.add_argument("--formats", default="csv,cdf,mzml",
                    help="匯出格式，逗號分隔 (預設 csv,cdf,mzml 全開；"
                         "csv 含圖與 Excel)")
    args = ap.parse_args()
    if isinstance(args.alpha, str) and args.alpha.strip().lower() == "auto":
        args.alpha = "auto"
    else:
        try:
            args.alpha = float(args.alpha)
        except (TypeError, ValueError):
            ap.error("--alpha 必須是數字或 auto")

    if args.gui_smoke:
        _gui_smoke()
        return
    if args.gui or (not args.sample and not args.blank):
        launch_gui()
        return
    if not args.sample or not args.blank:
        ap.error("--sample 與 --blank 皆為必填 (或不帶參數開啟圖形介面)")
    run_pipeline(args)



# 背景標記離子: 矽氧烷管柱流失/系統背景 (分析物通常不用)
MARKER_IONS = (281, 355, 207, 147, 73)


def estimate_alpha(S, B, mz):
    """自動估算 alpha: 兩路獨立估計, 揭露依據, 不黑箱。

    (1) 標記離子路: 每個標記離子逐時最小平方
        alpha_i = sum(S_i * B_i) / sum(B_i^2);
        閘門: BLANK 該離子總量 >= BLANK TIC 的 0.05%; 取通過者中位數。
    (2) 背景區路: 逐 scan alpha_t = (S_t . B_t) / (B_t . B_t),
        僅取 BLANK 主導 (該 scan BLANK TIC >= 樣品 TIC 50%) 且
        BLANK TIC >= 全程 BLANK TIC 中位數的 scan, 取中位數
        (逐 scan 中位數對分析物共流出穩健)。
    建議值: 兩路皆有且相對差 <= 30% 取平均; 僅一路取該路; 皆無 None。
    夾限 [0.05, 5.0]。回傳 dict(marker, region, suggested, marker_detail,
    region_scans)。"""
    tic_s, tic_b = S.sum(axis=1), B.sum(axis=1)
    mz_index = {int(round(m)): i for i, m in enumerate(mz)}
    per_ion = {}
    for ion in MARKER_IONS:
        j = mz_index.get(ion)
        if j is None:
            continue
        b_col, s_col = B[:, j], S[:, j]
        if float(b_col.sum()) < 0.0005 * float(tic_b.sum()):
            continue
        den = float(b_col @ b_col)
        if den <= 0:
            continue
        per_ion[ion] = float(s_col @ b_col) / den
    marker = (float(np.median(list(per_ion.values()))) if per_ion else None)
    detail = ", ".join(f"m/z {k}={v:.3f}" for k, v in per_ion.items())

    num = np.einsum("ij,ij->i", S, B)
    den = np.einsum("ij,ij->i", B, B)
    pos = tic_b[tic_b > 0]
    med_b = float(np.median(pos)) if len(pos) else 0.0
    gate = (den > 0) & (tic_b >= 0.5 * tic_s) & (tic_b >= med_b)
    region = (float(np.median(num[gate] / den[gate]))
              if int(gate.sum()) >= 10 else None)
    n_scans = int(gate.sum())

    if marker is not None and region is not None:
        rel = abs(marker - region) / max(marker, region)
        suggested = (marker + region) / 2 if rel <= 0.30 else marker
    elif marker is not None:
        suggested = marker
    else:
        suggested = region
    if suggested is not None:
        suggested = float(min(max(suggested, 0.05), 5.0))
    return {"marker": marker, "region": region, "suggested": suggested,
            "marker_detail": detail, "region_scans": n_scans}


def run_pipeline(args):
    for p in (args.sample, args.blank):
        if not os.path.isdir(p):
            sys.exit(f"找不到資料夾: {p}")

    s_meta = parse_metadata(args.sample)
    b_meta = parse_metadata(args.blank)
    rt, mz, S = load_ms(args.sample)
    rt_b, mz_b, B = load_ms(args.blank)

    checks = preflight(s_meta, b_meta, rt, mz, rt_b, mz_b, force=args.force)
    for c in checks:
        print(f"[{'PASS' if c['pass'] else 'FAIL'}] {c['check']}: {c['detail']}")
    hard_fails = [c for c in checks if not c["pass"] and c["hard_fail"]]
    if hard_fails:
        sys.exit("前置檢查未通過 (硬性): " +
                 ", ".join(c["check"] for c in hard_fails) +
                 "。 RT/scan/m/z 不一致需先對齊，本工具不自動處理。")

    # ---- alpha 決定 (手動值或自動估算) ----
    args.alpha_mode = "manual"
    args.alpha_est = None
    if isinstance(args.alpha, str) and args.alpha.strip().lower() == "auto":
        est = estimate_alpha(S, B, mz)
        args.alpha_est, args.alpha_mode = est, "auto"
        mk = f"{est['marker']:.3f} ({est['marker_detail']})" \
            if est["marker"] is not None else "無可用標記"
        rg = f"{est['region']:.3f} ({est['region_scans']} scans)" \
            if est["region"] is not None else "無可用區段"
        print(f"[alpha auto] 標記離子路: {mk}")
        print(f"[alpha auto] 背景區路: {rg}")
        if est["suggested"] is None:
            print("[alpha auto] 兩路皆無估計, 退回 alpha = 1.0")
            args.alpha = 1.0
        else:
            args.alpha = est["suggested"]
            print(f"[alpha auto] 採用 alpha = {args.alpha:.3f}")

    sample_name = s_meta["sample_name"]
    out = args.out or f"out_{sample_name}"
    os.makedirs(out, exist_ok=True)

    # ---- 背景扣除 (全質譜矩陣) ----
    corr_signed = S - args.alpha * B
    corr_clip = np.clip(corr_signed, 0, None)
    clipped_frac = float(np.mean(corr_signed < 0))
    tic_raw, tic_blk = S.sum(axis=1), B.sum(axis=1)
    tic_clip, tic_sign = corr_clip.sum(axis=1), corr_signed.sum(axis=1)

    mz_index = {int(round(m)): i for i, m in enumerate(mz)}

    def eic(mat, ion):
        return mat[:, mz_index[ion]] if ion in mz_index else np.zeros(len(rt))

    # ---- RESULTS.CSV 峰窗積分對照 ----
    peaks = parse_results_csv(os.path.join(args.sample, "RESULTS.CSV"))
    ions = ([int(x) for x in args.ions.split(",")] if args.ions else
            sorted({p["ion"] for p in peaks}) or [57, 91, 104, 105, 118, 135])

    peak_rows = []
    for p in peaks:
        i0 = max(p["first"] - 1, 0)          # ChemStation scan 為 1-based
        i1 = min(p["last"] - 1, len(rt) - 1)
        ion = p["ion"]
        e_raw, e_blk = eic(S, ion), eic(B, ion)
        e_clip, e_sign = eic(corr_clip, ion), eic(corr_signed, ion)
        a_raw = trapz_window(rt, e_raw, i0, i1)
        a_blk = trapz_window(rt, e_blk, i0, i1)
        a_clip = trapz_window(rt, e_clip, i0, i1)
        a_sign = trapz_window(rt, e_sign, i0, i1)
        peak_rows.append({
            **p,
            "area_raw_trapz": a_raw,
            "area_blank_trapz": a_blk,
            "area_corr_clip_trapz": a_clip,
            "area_corr_signed_trapz": a_sign,
            "change_clip_pct": (100 * (a_clip - a_raw) / a_raw) if a_raw else np.nan,
            "change_signed_pct": (100 * (a_sign - a_raw) / a_raw) if a_raw else np.nan,
            "area_raw_scansum": float(e_raw[i0:i1 + 1].sum()),
            "area_cs_note": "ChemStation 內部積分 (含基線)，絕對尺度僅供參考",
        })
    peak_df = pd.DataFrame(peak_rows)
    if not peak_df.empty:
        peak_df.to_csv(os.path.join(out, "peak_area_comparison.csv"), index=False, encoding="utf-8-sig")

    # ---- TIC/EIC 長表 ----
    df = pd.DataFrame({
        "RT_min": rt,
        "TIC_raw": tic_raw,
        "TIC_blank_x_alpha": args.alpha * tic_blk,
        "TIC_corr_clip": tic_clip,
        "TIC_corr_signed": tic_sign,
    })
    for ion in ions:
        df[f"EIC_{ion}_raw"] = eic(S, ion)
        df[f"EIC_{ion}_corr_clip"] = eic(corr_clip, ion)
    df.to_csv(os.path.join(out, "corrected_TIC_EIC.csv"),
              index=False, float_format="%.5f", encoding="utf-8-sig")

    # ---- CDF / mzML 匯出 (clip 版全質譜) ----
    formats = {x.strip().lower() for x in args.formats.split(",") if x.strip()}
    export_info = {}
    if "cdf" in formats:
        cdf_path = os.path.join(out, f"{sample_name}_corr.CDF")
        n_pts = write_andi_cdf(cdf_path, rt, mz, corr_clip, sample_name)
        export_info["cdf"] = (cdf_path, n_pts)
        print(f"  CDF (ANDI-MS) 已寫出: {cdf_path} ({n_pts} points)")
    if "mzml" in formats:
        mzml_path = os.path.join(out, f"{sample_name}_corr.mzML")
        write_mzml(mzml_path, rt, mz, corr_clip, sample_name, tic_clip)
        export_info["mzml"] = (mzml_path, len(rt))
        print(f"  mzML 已寫出: {mzml_path} ({len(rt)} spectra)")

    # ---- 峰頂質譜 (各 RESULTS 峰 max scan) ----
    spec_frames = []
    for p in peaks:
        idx = min(max(p["max"] - 1, 0), len(rt) - 1)
        raw_v, blk_v, cor_v = S[idx], args.alpha * B[idx], corr_clip[idx]
        f = pd.DataFrame({
            "ion_section": p["ion"], "peak_no": p["peak_no"],
            "apex_rt_min": float(rt[idx]), "apex_scan": idx + 1,
            "mz": mz, "abundance_raw": raw_v,
            "abundance_blank_x_alpha": blk_v,
            "abundance_corr_clip": cor_v,
            "rel_raw_pct": 100 * raw_v / raw_v.max() if raw_v.max() else 0.0,
            "rel_corr_pct": 100 * cor_v / cor_v.max() if cor_v.max() else 0.0,
        })
        spec_frames.append(f[(f["abundance_raw"] > 0) | (f["abundance_corr_clip"] > 0)])
    if spec_frames:
        pd.concat(spec_frames, ignore_index=True).to_csv(
            os.path.join(out, "apex_spectra_all.csv"), index=False,
            encoding="utf-8-sig")

    # ---- QC 摘要 ----
    qc = [
        {"item": "sample", "value": sample_name},
        {"item": "blank", "value": b_meta["sample_name"]},
        {"item": "alpha", "value": args.alpha},
        {"item": "n_scans", "value": len(rt)},
        {"item": "rt_range_min", "value": f"{rt.min():.3f}-{rt.max():.3f}"},
        {"item": "n_mz", "value": len(mz)},
        {"item": "tic_sum_raw", "value": float(tic_raw.sum())},
        {"item": "tic_sum_blank_x_alpha", "value": float(args.alpha * tic_blk.sum())},
        {"item": "tic_sum_corr_clip", "value": float(tic_clip.sum())},
        {"item": "tic_sum_corr_signed", "value": float(tic_sign.sum())},
        {"item": "blank_share_of_raw_pct",
         "value": 100 * float(args.alpha * tic_blk.sum()) / float(tic_raw.sum())},
        {"item": "corr_clip_share_of_raw_pct",
         "value": 100 * float(tic_clip.sum()) / float(tic_raw.sum())},
        {"item": "clipped_cell_fraction", "value": clipped_frac},
        {"item": "export_cdf",
         "value": (f"{os.path.basename(export_info['cdf'][0])} "
                   f"({export_info['cdf'][1]} points, clip)")
                  if "cdf" in export_info else "not exported"},
        {"item": "export_mzml",
         "value": (f"{os.path.basename(export_info['mzml'][0])} "
                   f"({export_info['mzml'][1]} spectra, clip)")
                  if "mzml" in export_info else "not exported"},
        {"item": "mass_balance_check_signed",
         "value": float(tic_sign.sum()) -
                  (float(tic_raw.sum()) - args.alpha * float(tic_blk.sum()))},
    ]
    if getattr(args, "alpha_mode", "manual") == "auto" and args.alpha_est:
        e = args.alpha_est
        qc[3:3] = [
            {"item": "alpha_mode", "value": "auto"},
            {"item": "alpha_est_marker_ls",
             "value": e["marker"] if e["marker"] is not None else "n/a"},
            {"item": "alpha_est_region_median",
             "value": e["region"] if e["region"] is not None else "n/a"},
            {"item": "alpha_est_marker_detail",
             "value": e["marker_detail"] or "n/a"},
        ]
    else:
        qc[3:3] = [{"item": "alpha_mode", "value": "manual"}]
    for lo, hi in DEFAULT_REGIONS:
        m = (rt >= lo) & (rt < hi)
        if m.any() and tic_raw[m].sum():
            qc.append({"item": f"blank_share_RT_{lo:.2f}_{hi:.2f}_pct",
                       "value": 100 * float(args.alpha * tic_blk[m].sum())
                                / float(tic_raw[m].sum())})
    tot_blk = B.sum(axis=0)
    for i in np.argsort(tot_blk)[::-1][:10]:
        qc.append({"item": f"blank_top_ion_mz_{int(round(mz[i]))}_share_pct",
                   "value": 100 * float(tot_blk[i]) / float(tot_blk.sum())})
    for c in checks:
        qc.append({"item": f"preflight_{c['check']}",
                   "value": f"{'PASS' if c['pass'] else 'FAIL'} ({c['detail']})"})
    qc_df = pd.DataFrame(qc)
    qc_df.to_csv(os.path.join(out, "qc_summary.csv"), index=False, encoding="utf-8-sig")

    # ---- 圖 ----
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 1, figsize=(11, 6.5))
    axes[0].plot(rt, tic_raw, lw=0.7, label="Sample TIC (raw)")
    axes[0].plot(rt, args.alpha * tic_blk, lw=0.7, label="BLANK TIC x alpha")
    axes[0].plot(rt, tic_clip, lw=0.7, label="Corrected TIC (clip)")
    axes[0].set_ylabel("TIC abundance")
    axes[0].set_title(f"TIC before/after BLANK subtraction - {sample_name}")
    axes[0].legend(fontsize=8)
    if not peak_df.empty:
        main_peak = peak_df.loc[peak_df["area_cs"].idxmax()]
        c = float(main_peak["rt_min"])
    else:
        c = float(rt[np.argmax(tic_raw)])
    m = (rt >= c - 1.0) & (rt <= c + 1.0)
    axes[1].plot(rt[m], tic_raw[m], lw=0.8, label="raw")
    axes[1].plot(rt[m], tic_clip[m], lw=0.8, label="corrected (clip)")
    axes[1].set_xlabel("RT (min)")
    axes[1].set_ylabel("TIC abundance")
    axes[1].set_title(f"Zoom around RT {c:.2f} min")
    axes[1].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(out, "fig_TIC_overlay.png"), dpi=150)
    plt.close(fig)

    if not peak_df.empty:
        sub104 = peak_df[peak_df["ion"] == 104]
        pick = (sub104.loc[sub104["area_cs"].idxmax()] if not sub104.empty
                else peak_df.loc[peak_df["area_cs"].idxmax()])
        idx = min(max(int(pick["max"]) - 1, 0), len(rt) - 1)
        raw_v, cor_v = S[idx], corr_clip[idx]
        fig, axes = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
        for ax, v, t in [(axes[0], raw_v, "raw"), (axes[1], cor_v, "corrected (clip)")]:
            ax.vlines(mz, 0, v, lw=0.8)
            ax.set_ylabel("abundance")
            ax.set_title(f"Apex spectrum RT {rt[idx]:.3f} min - {t}")
        axes[1].set_xlabel("m/z")
        fig.tight_layout()
        fig.savefig(os.path.join(out, "fig_apex_spectrum.png"), dpi=150)
        plt.close(fig)

    # ---- Excel 彙總 ----
    with pd.ExcelWriter(os.path.join(out, "summary.xlsx")) as xw:
        qc_df.to_excel(xw, sheet_name="qc_summary", index=False)
        if not peak_df.empty:
            peak_df.to_excel(xw, sheet_name="peak_area_comparison", index=False)
        df.head(5000).to_excel(xw, sheet_name="TIC_EIC_head5000", index=False)

    # ---- README ----
    alpha_line = f"- alpha = {args.alpha}"
    if getattr(args, "alpha_mode", "manual") == "auto" and args.alpha_est:
        e = args.alpha_est
        f3 = lambda v: f"{v:.3f}" if v is not None else "n/a"  # noqa: E731
        alpha_line += (f" (自動估算: 標記離子路 {f3(e['marker'])} / "
                       f"背景區路 {f3(e['region'])} / "
                       f"標記明細 {e['marker_detail'] or 'n/a'})")
    readme = f"""# BLANK 背景扣除執行紀錄

- 樣品: {sample_name} ({s_meta['folder']})
- BLANK: {b_meta['sample_name']} ({b_meta['folder']})
- 方法: {s_meta['method_file']} / Gain Factor {s_meta['gain_factor']} (兩份一致性見 qc_summary)
{alpha_line}
- 扣除定義: corrected_signed = sample - alpha*blank; corrected_clip = max(signed, 0)
- scans = {len(rt)}, RT {rt.min():.3f}-{rt.max():.3f} min, m/z grid {len(mz)} 點
- BLANK 總 TIC 占樣品 {100*float(args.alpha*tic_blk.sum())/float(tic_raw.sum()):.1f}%；
  扣除後 (clip) 總 TIC 為原始之 {100*float(tic_clip.sum())/float(tic_raw.sum()):.1f}%；
  負值截零格子比例 {clipped_frac:.3f} (clip 版偏倚來源，定量請對照 signed 版)

## 判讀注意
1. 面積比較以 peak_area_comparison.csv 為準: 同一峰窗、同一積分法下比
   raw vs corr 的變化 %；area_cs 欄為 ChemStation 內部積分 (含其基線
   處理)，絕對尺度與本工具梯形面積不同，僅供對照存在性。
2. 目標峰區間若 blank 貢獻 <1%，扣除主要作用在質譜淨化與未知峰判讀，
   不應期待定量數字大幅變動。
3. 本工具不產生新的 .D/DATA.MS。回原軟體分析請用同目錄的
   {sample_name}_corr.CDF (ChemStation: File > Import File > Load AIA
   File；載入後需重新指定處理方法) 或 {sample_name}_corr.mzML
   (OpenChrom/MZmine)；兩者皆為 clip 版全質譜。
"""
    with open(os.path.join(out, "README_run.md"), "w", encoding="utf-8") as f:
        f.write(readme)

    print(f"\n完成: {out}")
    print(f"  TIC raw sum={tic_raw.sum():.4e}  blank={args.alpha*tic_blk.sum():.4e} "
          f"({100*args.alpha*tic_blk.sum()/tic_raw.sum():.1f}%)  "
          f"corr_clip={tic_clip.sum():.4e} ({100*tic_clip.sum()/tic_raw.sum():.1f}%)")
    print(f"  clipped cell fraction={clipped_frac:.3f}")
    if not peak_df.empty:
        print("  峰面積變化 (clip, %):")
        for _, r in peak_df.iterrows():
            print(f"    ion {int(r['ion'])} peak {int(r['peak_no'])} "
                  f"RT {r['rt_min']:.3f}: {r['change_clip_pct']:+.2f}% "
                  f"(signed {r['change_signed_pct']:+.2f}%)")
    return out


if __name__ == "__main__":
    main()
