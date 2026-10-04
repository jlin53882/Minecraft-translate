@echo off
chcp 65001 >nul
REM ============================================================
REM  Minecraft Translator 打包腳本（Nuitka standalone）— 草稿，尚未實測
REM
REM  輸出結構（資料放 exe 旁邊）：
REM    dist\MinecraftTranslator\MinecraftTranslator.exe
REM    dist\MinecraftTranslator\config.json  ← 由 config.example.json 複製（僅首次）
REM    dist\MinecraftTranslator\快取資料\、logs\ ...  ← 執行時產生
REM
REM  重新打包是「更新」不是「重建」：
REM    1. Nuitka 先輸出到 dist\_staging（只清這個暫存資料夾）。
REM    2. 驗證 staging 內有 exe 才發佈；build 失敗時 dist\MinecraftTranslator 完全不動。
REM    3. 以 tools\publish_dist.py 發佈：新版 packaged files（exe、DLL/PYD、assets）完全取代
REM       舊版——上次發佈過、這次已不存在的檔案會依 .packaged_manifest.json 刪除，避免舊
REM       DLL/PYD/套件殘留成混合版本；使用者資料（config.json、replace_rules.json、logs\、
REM       快取資料\、學名資料庫\、.icon_cache\、輸出資料夾）不在 manifest 內，永遠不會被刪。
REM    限制：從沒有 manifest 的舊安裝首次升級時，舊遺留檔無法判斷，不會被刪（需手動清一次）。
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
  --include-package=translation_tool ^
  --include-package=app ^
  --include-data-dir=assets=assets ^
  --include-data-files=translation_tool/core/resource_pack_version.json=translation_tool/core/resource_pack_version.json ^
  --include-data-files=pyproject.toml=pyproject.toml ^
  --output-dir=%STAGING_DIR% ^
  --output-filename=%APP_NAME%.exe ^
  %ICON_OPT% ^
  main.py
if %errorlevel% neq 0 ( pause & exit /b 1 )

echo [3/3] 發佈到 %OUTPUT_DIR%\%APP_NAME%（不刪除既有資料）...
uv run --frozen python tools\publish_dist.py ^
  --staging "%STAGING_DIR%\main.dist" ^
  --target "%OUTPUT_DIR%\%APP_NAME%" ^
  --exe %APP_NAME%.exe ^
  --update-file config.example.json ^
  --seed-file config.example.json:config.json ^
  --seed-file replace_rules.json
if %errorlevel% neq 0 ( pause & exit /b 1 )

echo 完成：%OUTPUT_DIR%\%APP_NAME%
pause
