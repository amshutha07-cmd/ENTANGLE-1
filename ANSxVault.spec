# -*- mode: python ; coding: utf-8 -*-
# Cross-platform PyInstaller spec. Build the native engine first:  python build_engine.py
# Then:  pyinstaller ANSxVault.spec      (produces dist/ANSxVault/ for the current OS)
import os
import sys

_LIB = ("shatter.dll" if sys.platform.startswith("win")
        else "libshatter.dylib" if sys.platform == "darwin" else "libshatter.so")

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[(_LIB, '.')],
    # Ship your relay address inside the app: copy default_config.example.json to default_config.json and edit it
    datas=[('default_config.json', '.')] if os.path.exists('default_config.json') else [],
    hiddenimports=['keyring.backends'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ANSxVault',
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='ANSxVault',
)
