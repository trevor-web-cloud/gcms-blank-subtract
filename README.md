# GCMS BlankSubtract

Full-spectrum background subtraction for legacy Agilent / HP GC-MS data:
subtracts a complete BLANK run, scan by scan and ion by ion, from a sample
run — something MSD ChemStation itself cannot do (its built-in BSB only
subtracts a single background spectrum from every scan of the *same* file).

Ships as a single-file Windows executable (`GCMS_BlankSubtract.exe`,
Windows 11 x64, no Python installation required) and as a Python script
(`blank_subtract.py`).

## Applicable data format

**DATA.MS** — the core raw-data file used by legacy Agilent (and earlier
HP) GC/MS workstations running **MSD ChemStation** to store mass spectra
and chromatograms. Each acquisition is a `.D` folder; the full-scan data
live in `DATA.MS` inside it (SIM data, when present, live in `DATASIM.MS`).

This tool reads the whole `.D` folder of both the sample and the blank run
(`DATA.MS` plus `acqmeth.txt`, `PRE_POST.INI`, `runstart.txt` and, when
present, the ChemStation integration report `RESULTS.CSV`). It is designed
for scan-mode EI data such as those produced by 6890 / 5973-era systems
under ChemStation E.02, and requires both runs to share:

- the same number of scans and the same m/z grid,
- retention-time axes aligned to within 0.01 min (no re-alignment is
  performed — misaligned pairs are rejected, see *Preflight checks*),
- the same acquisition method and EM Gain Factor (override with `--force`).

## What it does

For every scan and every m/z, with scaling factor α (default 1.0):

```
corrected_signed = sample − α × blank
corrected_clip   = max(corrected_signed, 0)
```

- **Preflight checks** — scan count, m/z grid, RT-axis deviation, Gain
  Factor, method file and instrument serial are compared before any math;
  hard mismatches abort with a clear message instead of silently producing
  wrong numbers.
- **Quantitation cross-check** — if the sample folder contains a
  ChemStation `RESULTS.CSV`, every reported peak is re-integrated over the
  identical scan window before and after subtraction, so the effect on each
  target peak is a single auditable percentage.
- **QC summary** — total-ion balance (exact mass-balance check on the
  signed matrix), fraction of cells clipped at zero, blank share per
  retention-time region, and the blank's dominant background ions
  (typically solvent-front air ions and siloxane column-bleed ions).

## Outputs (three formats)

| Format | Files | Intended use |
|---|---|---|
| CSV / Excel | `corrected_TIC_EIC.csv`, `peak_area_comparison.csv`, `apex_spectra_all.csv`, `qc_summary.csv`, `summary.xlsx`, two PNG figures | Direct review and quantitation audit (the *signed* matrix is available only here) |
| AIA / CDF | `<sample>_corr.CDF` — ANDI-MS netCDF, clip version | Back into **MSD ChemStation**: *File → Import File → Load AIA File*, then integrate, extract spectra and run library searches as usual. Note: AIA carries no peak names or processing method — re-assign the method after loading. |
| mzML | `<sample>_corr.mzML` — mzML 1.1.0, incl. TIC chromatogram | OpenChrom, MZmine and the ProteoWizard ecosystem |

The tool never writes a new `.D` / `DATA.MS`: the format is proprietary
and has no reliable open writer, so corrected data are delivered in the
open formats above.

## Usage

### Windows executable (GUI)

Double-click `GCMS_BlankSubtract.exe` (or run it with `--gui`): a window
opens with folder pickers for the sample `.D`, the BLANK `.D` and the
output folder, plus the α factor and format check-boxes, a live run log,
and an *Open output folder* button on completion.

The GUI follows the system language (Chinese on Chinese Windows, English
on English Windows) and can be switched in the window at any time. The
console code page is set to UTF-8 at startup and all CSVs are written as
UTF-8 with BOM, so nothing is garbled on English-language Windows.

> The executable is unsigned; on first run SmartScreen will warn —
> choose *More info → Run anyway*. A one-file build also starts a few
> seconds slower on first launch while it unpacks itself.

### Command line (exe or Python script, identical parameters)

```
GCMS_BlankSubtract.exe --sample "D:\data\SAMPLE.D" --blank "D:\data\BLANK.D"
```

| Parameter | Required | Default | Description |
|---|---|---|---|
| `--sample` | yes* | — | Sample `.D` folder |
| `--blank` | yes* | — | BLANK `.D` folder |
| `--alpha` | no | `1.0` | Blank scaling factor (e.g. `1.2` subtracts 20 % more) |
| `--out` | no | `out_<sample name>` | Output folder |
| `--ions` | no | auto | EIC target ions, comma-separated; auto = ions from `RESULTS.CSV`, else `57,91,104,105,118,135` |
| `--formats` | no | `csv,cdf,mzml` | Output formats, comma-separated |
| `--force` | no | off | Continue even if Gain Factor / method differ (flagged in QC) |
| `--gui` | no | off | Open the folder-picker window (*not required when running with no arguments at all) |

Python: `python blank_subtract.py --sample ... --blank ...` with
dependencies `rainbow-api, numpy, pandas, scipy, matplotlib, openpyxl`
(see `requirements.txt`).

## Build

`GCMS_BlankSubtract.exe` is built on GitHub Actions (`windows-latest`,
Python 3.12, PyInstaller `--onefile`, see
`.github/workflows/build-exe.yml`). Every build must pass two smoke tests
on the Windows runner before the artifact is published: `--help` exits 0,
and `--gui-smoke` creates and destroys a real Tk window.

Current release: **v1.2** — see the Releases page. Verified behaviour
highlights: exact signed-matrix mass balance; exported CDF and mzML
re-read point-for-point identical to the corrected matrix (3,621 scans,
825,546 points on the reference data set).

## Version history

- **v1.2** — English-Windows safety: console code page forced to UTF-8,
  GUI auto Chinese/English by system locale with in-window switch,
  CSV outputs as UTF-8 with BOM.
- **v1.1** — GUI folder-picker window (double-click / `--gui`); command
  line unchanged.
- **v1.0** — Initial release: full-spectrum blank subtraction with
  preflight checks, RESULTS.CSV peak audit, QC report, and CSV/Excel +
  AIA/CDF + mzML exports.

## Notes

- ChemStation peak areas come from its internal integrator (with its own
  baseline handling and scale); compare *before vs. after* within this
  tool's identical windows and method rather than absolute values across
  the two systems.
- One blank run can serve a whole sequence acquired under the same method
  and Gain; re-check the preflight report for each sample.
