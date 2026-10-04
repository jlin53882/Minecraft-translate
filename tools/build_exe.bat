@echo off
chcp 65001 >nul
REM ============================================================
REM  Minecraft Translator 打包腳本（Nuitka standalone）— 草稿，尚未在 Windows 實測
REM
REM  輸出結構（app\ 與 data\ 分離）：
REM    dist\MinecraftTranslator\MinecraftTranslator.bat       ← 啟動器（每次覆蓋）
REM    dist\MinecraftTranslator\app\MinecraftTranslator.exe   ← 程式：每次整個換掉
REM    dist\MinecraftTranslator\data\config.json、logs\、快取資料\ ...  ← 使用者資料：永遠保留
REM
REM  重新打包是「換掉 app\、保留 data\」：
REM    1. Nuitka 先輸出到 dist\_staging（只清這個暫存資料夾）。
REM    2. 驗證 staging 內有 exe 才發佈；build 失敗時 dist\MinecraftTranslator 完全不動。
REM    3. tools\publish_dist.py 先在旁邊準備 app.new，再換成 app\（失敗會還原；程式正在
REM       執行時會失敗並提示先關閉），舊版 DLL/PYD/套件不會殘留。data\ 從不被刪除或覆蓋，
REM       config.json、replace_rules.json 只在不存在時才建立。
REM    4. 舊版平面式安裝（exe 與資料混在同一資料夾）：不刪任何東西；使用者資料會在新版
REM       第一次啟動時由程式搬進 data\，舊程式檔確認新版正常後可手動刪除。
REM
REM  資料位置由 translation_tool/utils/app_paths.py 決定：
REM    exe 位於名為 app 的資料夾 → 資料在上一層的 data\；否則（舊版平面式）在 exe 旁邊。
REM    可用環境變數 MCT_DATA_DIR 覆蓋。
REM  首次測試請保持 console 模式（force），確認能啟動後再改 disable。
REM ============================================================
setlocal
cd /d "%~dp0.."

uv --version >nul 2>nul
if %errorlevel% neq 0 (
    echo 找不到 uv，請先安裝：https://docs.astral.sh/uv/
    pause
    exit /b 1
)

set APP_NAME=MinecraftTranslator
set OUTPUT_DIR=dist
set STAGING_DIR=%OUTPUT_DIR%\_staging
set CONSOLE_MODE=force

echo [1/3] 同步環境...
uv sync --frozen
if %errorlevel% neq 0 ( pause & exit /b 1 )

echo [2/3] Nuitka 打包（輸出到 staging，不動正式資料夾）...
if exist "%STAGING_DIR%" rmdir /s /q "%STAGING_DIR%"
if exist icon.ico ( set ICON_OPT=--windows-icon-from-ico=icon.ico ) else ( set ICON_OPT= )

uv run --frozen --with nuitka python -m nuitka ^
  --standalone ^
  --jobs=%NUMBER_OF_PROCESSORS% ^
  --windows-console-mode=%CONSOLE_MODE% ^
  --include-package=flet ^
  --include-package-data=flet ^
  --include-package=flet_desktop ^
  --include-package-data=flet_desktop ^
  --include-package=opencc ^
  --include-package-data=opencc ^
  --include-package=translation_tool ^
  --include-package=app ^
  --include-data-dir=assets=assets ^
  --include-data-files=translation_tool/core/resource_pack_version.json=translation_tool/core/resource_pack_version.json ^
  --include-data-files=pyproject.toml=pyproject.toml ^
  --include-data-files=config.example.json=config.example.json ^
  --output-dir=%STAGING_DIR% ^
  --output-filename=%APP_NAME%.exe ^
  %ICON_OPT% ^
  main.py
if %errorlevel% neq 0 ( pause & exit /b 1 )

echo [3/3] 發佈到 %OUTPUT_DIR%\%APP_NAME%（換掉 app\、保留 data\）...
uv run --frozen python tools\publish_dist.py ^
  --staging "%STAGING_DIR%\main.dist" ^
  --target "%OUTPUT_DIR%\%APP_NAME%" ^
  --exe %APP_NAME%.exe ^
  --launcher %APP_NAME%.bat ^
  --update-file config.example.json ^
  --seed-file config.example.json:config.json ^
  --seed-file replace_rules.json
if %errorlevel% neq 0 ( pause & exit /b 1 )

echo 完成：%OUTPUT_DIR%\%APP_NAME%
pause
