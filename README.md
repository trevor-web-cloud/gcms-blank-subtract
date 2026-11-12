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

**Definition of α (alpha).** α is the ratio between the background
contribution actually present in the *sample* injection and the
background measured in the *blank* injection:

```
α = (background present in the sample run) ÷ (background in the blank run)
```

- α = 1.0 — the sample contains exactly as much background as the blank
  run measured (the normal case for back-to-back runs in one sequence
  with the same injection volume and method).
- α > 1 — the sample run carries more background than the blank showed
  (e.g. larger injection volume); subtract proportionally more.
- α < 1 — the sample run carries less (e.g. smaller injection volume or
  dilution); subtract proportionally less.
- α = 0 — no subtraction at all.

α scales an *amount of background*, nothing else: it must be calibrated
on background-only evidence (see *Automatic α estimation* below), never
on total signal, which is dominated by the analytes. Different
background sources also scale differently — column bleed follows the
temperature program (α ≈ 1 regardless of sample amount), solvent
impurities follow the injected volume, and carryover from a previous
sample is not present in the blank at all, so no α can correct it.

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

### Opening the exported files

| Format | Software that opens it | Cost / download |
|---|---|---|
| CSV / Excel (`.csv`, `.xlsx`) | Microsoft Excel; **LibreOffice Calc**; any text editor (CSV only) | Excel: commercial (Microsoft 365). LibreOffice: free — [download](https://www.libreoffice.org/download/download-libreoffice/) |
| AIA / CDF (`.CDF`) | **Agilent MSD ChemStation** (Data Analysis → *File → Import File → Load AIA File*); **OpenChrom**; **AMDIS** (NIST) | ChemStation: commercial, licensed from Agilent (no public download). OpenChrom: free — [openchrom.net](https://www.openchrom.net). AMDIS: free — [NIST CHEMDATA](https://chemdata.nist.gov/dokuwiki/doku.php?id=chemdata:amdis) |
| mzML (`.mzML`) | **OpenChrom**; **MZmine**; **SeeMS** viewer (part of ProteoWizard) | All free — OpenChrom: [openchrom.net](https://www.openchrom.net); MZmine: [mzmine.github.io](https://mzmine.github.io/); ProteoWizard / SeeMS: [download page](https://proteowizard.sourceforge.io/download.html) |

Notes: OpenChrom (free, Windows / macOS / Linux) opens **both** the CDF
and the mzML — and even the original Agilent `.D` folders — so it is the
single most convenient viewer for the corrected data. AMDIS additionally
performs spectral deconvolution and NIST library searching on the CDF.
For returning to the original ChemStation workflow (integration, method
quantitation), the CDF in ChemStation remains the primary route.

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
| `--blank` | no | — | BLANK `.D` folder. **May be omitted**: with no blank the tool skips preflight and subtraction and exports the sample's raw full spectra instead (files named `<sample>_raw.CDF` / `_raw.mzML`; QC records `alpha_mode = no_blank`) |
| `--alpha` | no | `1.0` | Blank scaling factor (e.g. `1.2` subtracts 20 % more), or `auto` — estimate α from the data (see below) |
| `--out` | no | `out_<sample name>` | Output folder |
| `--ions` | no | auto | EIC target ions, comma-separated; auto = ions from `RESULTS.CSV`, else `57,91,104,105,118,135` |
| `--formats` | no | `csv,cdf,mzml` | Output formats, comma-separated |
| `--force` | no | off | Continue even if Gain Factor / method differ (flagged in QC) |
| `--gui` | no | off | Open the folder-picker window (*not required when running with no arguments at all) |

Python: `python blank_subtract.py --sample ... --blank ...` with
dependencies `rainbow-api, numpy, pandas, scipy, matplotlib, openpyxl`
(see `requirements.txt`).

### Automatic α estimation (`--alpha auto`)

When the blank and the sample differ in overall intensity (different
injection volume, dilution or matrix load), a hand-picked α can distort
peaks. With `--alpha auto` (or typing `auto` in the GUI's Alpha field)
the tool estimates α from background-only evidence, by two independent
routes, and discloses both in the console log, `qc_summary.csv` and the
run README. **In the GUI the "Auto estimate" checkbox is ticked by
default** (the α entry is greyed out); untick it for a manual α
(default 1.0):

1. **Marker-ion least squares** — for each siloxane bleed ion
   (m/z 281, 355, 207, 147, 73) fit αᵢ = Σ Sᵢ·Bᵢ / Σ Bᵢ² over time and
   take the median (robust against a marker that also appears in an
   analyte).
2. **Background-region median** — per scan, α_t = (S_t·B_t)/(B_t·B_t),
   restricted to blank-dominated scans, again taking the median.

If both routes agree within 30 % their mean is used; the marker route
wins on disagreement; if neither route has data the tool falls back to
α = 1.0 and says so. The estimate is clamped to [0.05, 5.0]. On the
reference data set (a same-sequence matched pair) the two routes gave
0.990 and 0.957, so α = 0.973 was adopted — close to the ideal 1.0, as
expected. α compensates *amount* differences only; it cannot repair
retention-time shifts or peak-shape differences — re-run a matched
blank for those.

## Build

`GCMS_BlankSubtract.exe` is built on GitHub Actions (`windows-latest`,
Python 3.12, PyInstaller `--onefile`, see
`.github/workflows/build-exe.yml`). Every build must pass two smoke tests
on the Windows runner before the artifact is published: `--help` exits 0,
and `--gui-smoke` creates and destroys a real Tk window.

Current release: **v1.5** — see the Releases page. Verified behaviour
highlights: exact signed-matrix mass balance; exported CDF and mzML
re-read point-for-point identical to the corrected matrix (3,621 scans,
825,546 points on the reference data set).

## Version history

- **v1.5** — GUI: "Auto estimate" checkbox for α, **ticked by default**
  (α entry greyed out while ticked); untick for manual α (default 1.0).
  Typing `auto` in the α field still works. CLI unchanged.
- **v1.4** — No-blank mode: `--blank` is now optional (GUI asks for
  confirmation); without a blank the tool exports the raw sample in all
  formats (`*_raw.CDF` / `*_raw.mzML`) with QC disclosure. Added the
  formal definition of α to this README.
- **v1.3** — Automatic α estimation (`--alpha auto` / GUI Alpha field):
  marker-ion least squares + background-region median, both disclosed
  in QC; manual α unchanged (regression-verified identical output).
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
