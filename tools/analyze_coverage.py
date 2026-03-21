#!/usr/bin/env python3
"""分析專案模組與測試覆蓋情況"""
# ruff: noqa: E402

import os
import pkgutil

from translation_tool.utils.log_unit import log_error

# 收集所有模組
modules = []

# translation_tool 子模組
import translation_tool
for importer, modname, ispkg in pkgutil.iter_modules(translation_tool.__path__):
    modules.append(f"translation_tool.{modname}")
    if ispkg:
        try:
            submod = __import__(f"translation_tool.{modname}", fromlist=[""])
            if hasattr(submod, "__path__"):
                for _, subname, is_sub_pkg in pkgutil.iter_modules(submod.__path__):
                    fullname = f"translation_tool.{modname}.{subname}"
                    modules.append(fullname)
        except (ImportError, AttributeError) as e:
            log_error(f"載入模組失敗: {e}")

# app 子模組
import app
for importer, modname, ispkg in pkgutil.iter_modules(app.__path__):
    if modname != "views":  # 跳过 views
        modules.append(f"app.{modname}")

print("=== 專案模組清單 ===")
for m in sorted(modules):
    print(m)

print("\n=== 現有測試檔案 ===")
test_dir = "tests"
if os.path.exists(test_dir):
    tests = [f.replace("test_", "").replace(".py", "") for f in os.listdir(test_dir) if f.startswith("test_")]
    for t in sorted(tests):
        print(t)

print("\n=== 比對：缺少測試的模組 ===")
covered = set()
for t in tests:
    # 簡單比對
    for m in modules:
        if t.replace("_", "") in m.replace("_", "").replace(".", ""):
            covered.add(m)
            
missing = set(modules) - covered
for m in sorted(missing):
    print(f"  ❌ {m}")
