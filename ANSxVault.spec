# -*- mode: python ; coding: utf-8 -*-
# Cross-platform PyInstaller spec. Build the native engine first:  python build_engine.py
# Then:  pyinstaller ANSxVault.spec      (produces dist/ANSxVault/ for the current OS)
import os
import sys

_LIB = ("shatter.dll" if sys.platform.startswith("win")
        else "libshatter.dylib" if sys.platform == "darwin" else "libshatter.so")
_BINARIES = [(_LIB, '.')]
if sys.platform.startswith("win"):          # OpenSSL / zlib DLLs that build_engine.py copied next to shatter.dll
    import pe_imports
    _deps, _missing = pe_imports.non_system_dependencies(_LIB, ['.'])
    if _missing:
        raise SystemExit(f"shatter.dll needs {', '.join(_missing)} next to it. Run: python build_engine.py")
    _BINARIES += [(p, '.') for p in _deps.values()]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=_BINARIES,
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
