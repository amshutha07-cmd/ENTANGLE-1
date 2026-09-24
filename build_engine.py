#!/usr/bin/env python3
"""
build_engine.py — builds the native shard engine for the CURRENT OS.

  Linux    libshatter.so       g++ / clang++    + OpenSSL 3 and zlib dev packages
  macOS    libshatter.dylib    clang++          + OpenSSL 3 and zlib (e.g. Homebrew)
  Windows  shatter.dll         MinGW-w64/MSYS2 g++, clang++, or MSVC cl

OpenSSL/zlib are located, in order, via:  $OPENSSL_DIR (contains include/ and lib/),
pkg-config, then the compiler's default search paths.
Override the compiler with $CXX.   Usage:  python build_engine.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "shatter_engine.cpp")


def lib_name() -> str:
    if sys.platform.startswith("win"):
        return "shatter.dll"
    return "libshatter.dylib" if sys.platform == "darwin" else "libshatter.so"


def _pkg_config(*args: str) -> list[str]:
    if not shutil.which("pkg-config"):
        return []
    r = subprocess.run(["pkg-config", *args, "openssl", "zlib"], capture_output=True, text=True)
    return r.stdout.split() if r.returncode == 0 else []


def _find_compiler() -> str:
    cands = [os.environ["CXX"]] if os.environ.get("CXX") else ["c++", "g++", "clang++", "cl"]
    for c in cands:
        if shutil.which(c):
            return c
    sys.exit("No C++ compiler found. Install g++/clang++ (or MSVC/MSYS2 on Windows), or set $CXX.")


def build() -> str:
    out = os.path.join(ROOT, lib_name())
    cxx = _find_compiler()
    ossl = os.environ.get("OPENSSL_DIR", "")

    if os.path.basename(cxx).lower().startswith("cl"):          # MSVC
        cmd = [cxx, "/nologo", "/std:c++17", "/O2", "/EHsc", "/LD", SRC, f"/Fe:{out}"]
        if ossl:
            cmd += [f"/I{ossl}\\include", "/link", f"/LIBPATH:{ossl}\\lib"]
        else:
            cmd += ["/link"]
        cmd += ["libcrypto.lib", "zlib.lib"]
    else:                                                       # g++ / clang++ (all OSes)
        cmd = [cxx, "-std=c++17", "-O2", "-Wall", "-Wextra", "-shared", SRC, "-o", out]
        if not sys.platform.startswith("win"):
            cmd.insert(3, "-fPIC")
        if ossl:
            cmd += [f"-I{os.path.join(ossl, 'include')}", f"-L{os.path.join(ossl, 'lib')}"]
        cmd += _pkg_config("--cflags") + _pkg_config("--libs-only-L")
        cmd += ["-lcrypto", "-lz"]
        if sys.platform == "darwin" and not ossl and not _pkg_config("--cflags"):
            # Homebrew keg-only OpenSSL, only if the user did not point us elsewhere.
            r = subprocess.run(["brew", "--prefix", "openssl@3"], capture_output=True, text=True) \
                if shutil.which("brew") else None
            if r and r.returncode == 0:
                p = r.stdout.strip()
                cmd += [f"-I{p}/include", f"-L{p}/lib"]

    print(" ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)
    return out


if __name__ == "__main__":
    path = build()
    import ctypes
    print(f"built {os.path.basename(path)} (engine v{ctypes.CDLL(path).ansx_engine_version()})")
