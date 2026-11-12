# GCMS BlankSubtract（中文說明）

> English version: [README.md](README.md) — 本檔與英文版同步維護，內容一致。

舊版 Agilent／HP GC-MS 數據的全質譜背景扣除工具：把一支完整的 BLANK
run 逐 scan、逐離子從樣品 run 中扣除——這是 MSD ChemStation 本身做不到
的（它內建的 BSB 只能拿單一背景質譜扣同一支檔案的全程）。

發布形式：單一 Windows 執行檔 `GCMS_BlankSubtract.exe`（Windows 11
x64、免安裝 Python），以及 Python 腳本 `blank_subtract.py`。

## 適用資料格式

**DATA.MS**——舊版 Agilent（與早期的 HP）GC/MS 工作站以 **MSD
ChemStation** 儲存質譜與層析圖譜原始數據的核心檔案。每次擷取是一個
`.D` 資料夾，全掃描數據存放在其中的 `DATA.MS`（SIM 數據若有，存於
`DATASIM.MS`）。

本工具讀取樣品與 BLANK 兩支 `.D` 資料夾的完整內容（`DATA.MS` 以及
`acqmeth.txt`、`PRE_POST.INI`、`runstart.txt`，若有 ChemStation 積分
報告 `RESULTS.CSV` 一併讀取）。適用於掃描模式 EI 數據，例如
6890／5973 世代系統在 ChemStation E.02 下產生的數據，且兩支 run
必須符合：

- scan 數相同、m/z grid 相同；
- 兩支的保留時間軸對齊在 0.01 min 內（本工具不做 RT 重新對齊，
  不對齊的配對會被拒絕，見「前置檢查」）；
- 擷取方法與 EM Gain Factor 相同（可用 `--force` 放行）。

## 運作方式

對每個 scan、每個 m/z，以係數 α（預設 1.0）計算：

```
corrected_signed = sample − α × blank
corrected_clip   = max(corrected_signed, 0)
```

**α（alpha）的定義。** α 是「樣品那一針實際含有的背景量」與「BLANK
那一針量到的背景量」之比：

```
α = （樣品 run 中實際存在的背景）÷（BLANK run 量到的背景）
```

- α = 1.0——樣品含有的背景與 BLANK 量到的等量（同序列、同進樣量
  連續擷取的常態）。
- α > 1——樣品那一針背景比 BLANK 顯示的更多（例如進樣量較大），
  按比例多扣。
- α < 1——樣品那一針背景較少（例如進樣量較小或經稀釋），按比例
  少扣。
- α = 0——完全不扣除。

α 縮放的是「背景的量」，僅此而已：定標只能用背景專屬證據（見下方
「自動估算 α」），絕不能用總訊號（總訊號由分析物主導）。不同背景
來源的縮放律也不同——管柱流失隨溫度程式（與樣品量無關，α ≈ 1）、
溶劑不純物隨進樣體積、前一針殘留（carryover）根本不在 BLANK 裡，
任何 α 都救不了。

- **前置檢查**——scan 數、m/z grid、RT 軸偏差、Gain Factor、方法檔
  與儀器序號在計算前逐項比對；硬性不符直接停機並說明原因，不會
  默默產出錯誤數字。
- **定量對照**——樣品資料夾若有 ChemStation 的 `RESULTS.CSV`，
  每個報告峰都以相同 scan 窗重新積分，比對扣除前後，背景對每個
  目標峰的影響就是一個可稽核的百分比。
- **QC 摘要**——總離子量平衡（signed 矩陣的質量守恆檢查為零）、
  截零格子比例、各 RT 區段的 BLANK 占比、BLANK 的主要背景離子
  （通常是溶劑前緣的空氣離子與矽氧烷管柱流失離子）。

## 輸出（三種格式）

| 格式 | 產出檔案 | 用途 |
|---|---|---|
| CSV／Excel | `corrected_TIC_EIC.csv`、`peak_area_comparison.csv`、`apex_spectra_all.csv`、`qc_summary.csv`、`summary.xlsx`、兩張圖 | 直接判讀與定量稽核（signed 版僅此格式提供） |
| AIA／CDF | `<樣品名>_corr.CDF`——ANDI-MS netCDF，clip 版 | 回 **MSD ChemStation**：*File → Import File → Load AIA File* 載入後照常積分、取質譜、圖庫搜尋。注意：AIA 不帶峰名與處理方法，載入後需重新指定方法（見下方專節）。 |
| mzML | `<樣品名>_corr.mzML`——mzML 1.1.0，含 TIC chromatogram | OpenChrom、MZmine 與 ProteoWizard 生態 |

本工具絕不寫回新的 `.D`／`DATA.MS`：該格式為專有格式且無可靠的
開源 writer，扣除後數據以上述開放格式交付。

### 三種格式用什麼程式開啟

| 格式 | 可開啟軟體 | 費用／下載 |
|---|---|---|
| CSV／Excel（`.csv`、`.xlsx`） | Microsoft Excel；**LibreOffice Calc**；任何文字編輯器（僅 CSV） | Excel：商業軟體（Microsoft 365）。LibreOffice：免費——[下載](https://www.libreoffice.org/download/download-libreoffice/) |
| AIA／CDF（`.CDF`） | **Agilent MSD ChemStation**（Data Analysis → *File → Import File → Load AIA File*）；**OpenChrom**；**AMDIS**（NIST） | ChemStation：Agilent 授權商業軟體、無公開下載。OpenChrom：免費——[openchrom.net](https://www.openchrom.net)。AMDIS：免費——[NIST CHEMDATA](https://chemdata.nist.gov/dokuwiki/doku.php?id=chemdata:amdis) |
| mzML（`.mzML`） | **OpenChrom**；**MZmine**；**SeeMS** 檢視器（ProteoWizard 附屬） | 皆免費——OpenChrom：[openchrom.net](https://www.openchrom.net)；MZmine：[mzmine.github.io](https://mzmine.github.io/)；ProteoWizard／SeeMS：[下載頁](https://proteowizard.sourceforge.io/download.html) |

註：OpenChrom（免費，Windows／macOS／Linux）同時能開 CDF 與
mzML——甚至原始 Agilent `.D` 資料夾——是檢視扣除後數據最方便的
單一工具。AMDIS 還能對 CDF 做質譜去卷積與 NIST 圖庫搜尋。若要回
ChemStation 原流程（積分、方法定量），CDF 仍是主要路線。

### CDF 回 ChemStation 重新指定處理方法（MSD ChemStation E.02）

AIA/CDF 不帶處理方法，載入後須把擷取時用的 `.M` 掛回去，積分參數
與定量資料庫（化合物 RT、目標／確認離子、校正曲線）即恢復：

1. 開 **Data Analysis**（離線模式即可）。
2. **File → Import File → Load AIA File…**，選 `<樣品名>_corr.CDF`。
3. **Method → Load Method…**，選**擷取時同一支方法**（儀器電腦
   通常在 `C:\msdchem\1\methods\`）。
4. 定量前先驗證：已知峰的 RT 應與原始 `.D` 一致，取峰頂質譜確認
   背景離子已降低。CDF 保留原始 RT 軸，方法的 RT 窗口應直接命中。
5. **Quant → Calculate…**，選報告格式與輸出即完成定量，使用方法
   內既有的校正曲線，**不需重新校正**。
6. 需要修積分時用 **Quant → QEdit Results…**；報告請存檔並與
   CDF 併存。

注意：只載入、勿修改方法——改積分事件即失去與原始結果之可比性。
Calculate 若為灰色，先 Load Method 再 Load AIA File，或載入 CDF
後再重掛一次方法。驗收比對：同一方法對原始 `.D` 也跑一次
Calculate，乾淨峰結果應非常接近。報告表頭的樣品資訊欄位來自
AIA、可能為預設值，屬外觀問題，不影響數字。

## 使用方式

### Windows 執行檔（圖形介面）

雙擊 `GCMS_BlankSubtract.exe`（或加 `--gui`）：視窗內有樣品 `.D`、
BLANK `.D` 與輸出資料夾的選擇器、α 係數與格式勾選框、即時執行
紀錄，完成後有「開啟輸出資料夾」按鈕。

介面依系統語系自動顯示（中文 Windows 顯示中文、英文 Windows 顯示
英文），視窗右上角可隨時切換。啟動時主控台字碼頁自動切為 UTF-8，
CSV 以帶 BOM 的 UTF-8 寫出，英文版 Windows 不會出現亂碼。

BLANK 可省略：GUI 未選 BLANK 時會跳確認框；省略時不執行扣除、
直接匯出原始數據（見參數表）。

> 執行檔未簽章，首次執行 SmartScreen 會警告——選「其他資訊 →
> 仍要執行」。單一檔案版首次啟動需自我解壓，會慢幾秒。

### 命令列（exe 與 Python 腳本參數相同）

```
GCMS_BlankSubtract.exe --sample "D:\data\SAMPLE.D" --blank "D:\data\BLANK.D"
```

| 參數 | 必填 | 預設 | 說明 |
|---|---|---|---|
| `--sample` | 是* | — | 樣品 `.D` 資料夾 |
| `--blank` | 否 | — | BLANK `.D` 資料夾。**可省略**：省略時跳過前置檢查與扣除，直接匯出樣品原始全質譜（檔名 `<樣品名>_raw.CDF`／`_raw.mzML`；QC 記 `alpha_mode = no_blank`） |
| `--alpha` | 否 | `1.0` | BLANK 扣除係數（如 `1.2` 多扣 20%），或填 `auto` 由數據自動估算（見下節） |
| `--out` | 否 | `out_<樣品名>` | 輸出資料夾 |
| `--ions` | 否 | 自動 | EIC 目標離子，逗號分隔；自動 = 取 `RESULTS.CSV` 的離子、無則 `57,91,104,105,118,135` |
| `--formats` | 否 | `csv,cdf,mzml` | 輸出格式，逗號分隔 |
| `--force` | 否 | 關 | Gain Factor／方法不符時仍繼續（QC 會註記） |
| `--gui` | 否 | 關 | 開啟資料夾選擇視窗（*完全不帶參數時也會自動開啟） |

Python：`python blank_subtract.py --sample ... --blank ...`，依賴
`rainbow-api, numpy, pandas, scipy, matplotlib, openpyxl`（見
`requirements.txt`）。

### 自動估算 α（`--alpha auto`）

當 BLANK 與樣品整體強度差異大（進樣量、稀釋倍數、基質負荷不同），
手選的 α 可能造成失真。填 `--alpha auto`（或在 GUI 勾選 Auto——
**預設勾選**；取消勾選改手動，預設 1.0）時，工具以兩路獨立方法
從背景專屬證據估算 α，並把兩路結果揭露在主控台紀錄、
`qc_summary.csv` 與執行紀錄 README：

1. **標記離子最小平方**——對每個矽氧烷流失離子（m/z 281、355、
   207、147、73）逐時擬合 αᵢ = Σ Sᵢ·Bᵢ / Σ Bᵢ²，取中位數（對
   「某標記同時出現在分析物」的情形穩健）。
2. **背景區中位數**——逐 scan 計算 α_t = (S_t·B_t)/(B_t·B_t)，
   僅取 BLANK 主導的 scans，再取中位數。

兩路相對差 ≤30% 取平均；衝突時以標記路為準；兩路皆無資料時退回
α = 1.0 並註明。估值夾限 [0.05, 5.0]。參考數據（同序列配對）兩路
為 0.990 與 0.957，採用 α = 0.973——接近理想 1.0，符合預期。α 只
補償「量」的差異，修不了 RT 漂移或峰形差異——那種情形請重跑一支
配對的 BLANK。

## 建置

`GCMS_BlankSubtract.exe` 以 GitHub Actions 建置（`windows-latest`、
Python 3.12、PyInstaller `--onefile`，見
`.github/workflows/build-exe.yml`）。每次建置須先通過 Windows
runner 上的兩項煙霧測試才會發布成品：`--help` 退出碼 0、
`--gui-smoke` 實際建立並銷毀一個 Tk 視窗。

現行版本：**v1.6**——見 Releases 頁。已驗證行為：signed 矩陣質量
守恆為零；匯出的 CDF 與 mzML 重讀後與扣除矩陣逐點一致（參考
數據：3,621 scans、825,546 點）。

## 版本沿革

- **v1.6**——文件：ChemStation 重新指定處理方法流程（Load AIA
  File → Load Method → Calculate）寫入本說明與每次執行的
  `README_run.md`。
- **v1.5**——GUI：α 的「Auto 自動估算」勾選框，**預設勾選**
  （勾選時 α 輸入框停用）；取消勾選改手動（預設 1.0）；欄位
  直接打 `auto` 仍可用。CLI 不變。
- **v1.4**——無 BLANK 模式：`--blank` 改選填（GUI 會跳確認）；
  無 BLANK 時三格式匯出原始樣品（`*_raw.CDF`／`*_raw.mzML`），
  QC 揭露。本說明新增 α 的正式定義。
- **v1.3**——自動估算 α（`--alpha auto`／GUI Alpha 欄位）：標記
  離子最小平方＋背景區中位數，皆揭露於 QC；手動 α 輸出經迴歸
  驗證與前版逐位一致。
- **v1.2**——英文版 Windows 防亂碼：主控台字碼頁強制 UTF-8、
  GUI 依系統語系自動中／英並可即時切換、CSV 改帶 BOM 的 UTF-8。
- **v1.1**——GUI 資料夾選擇視窗（雙擊／`--gui`）；命令列不變。
- **v1.0**——初版：全質譜 BLANK 扣除、前置檢查、RESULTS.CSV
  峰稽核、QC 報告、CSV/Excel＋AIA/CDF＋mzML 三格式匯出。

## 注意事項

- ChemStation 峰面積來自其內部積分器（自有基線處理與尺度）；
  跨系統比較請在本工具相同窗、同法之下比「扣除前 vs 扣除後」，
  不要直接比絕對值。
- 同一支 BLANK 可服務同方法、同 Gain 的整個序列；每個樣品仍請
  複核前置檢查報告。
