# gcms-blank-subtract (build repo)

GC-MS BLANK 全質譜背景扣除工具 `blank_subtract.py` 的 Windows 打包 repo。
GitHub Actions (windows-latest) 以 PyInstaller --onefile 建置
`GCMS_BlankSubtract.exe`（Win11 x64、免安裝 Python），建置後以
`GCMS_BlankSubtract.exe --help` 做煙霧測試，通過才上傳 artifact。

用法與參數見主專案 README（workspace/gcms-blank-sub）。
本 repo 不含任何 GC-MS 數據檔。
