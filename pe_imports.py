"""
pe_imports.py — which DLLs does a Windows DLL need, and where are they?

Since Python 3.8, Windows no longer uses PATH to find the DLLs that a DLL loaded with ctypes depends on: only the
DLL's own folder, System32 and folders registered with os.add_dll_directory are searched. A shatter.dll built with
MinGW needs OpenSSL, zlib and (unless linked statically) the MinGW runtime, which live in e.g. C:\\msys64\\ucrt64\\bin.
This module reads a DLL's import table (pure Python, works on any OS) so the build can copy those files next to
shatter.dll and the loader can explain exactly what is missing.
"""
from __future__ import annotations

import os
import struct

_SYSTEM_PREFIXES = ("api-ms-win-", "ext-ms-")


def imported_dlls(path: str) -> list[str]:
    """Names of the DLLs in the PE import table of `path` (e.g. ["libcrypto-3-x64.dll", "KERNEL32.dll"])."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] != b"MZ":
        raise ValueError(f"{path} is not a Windows DLL/EXE.")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError(f"{path} has no PE header.")
    n_sections, opt_size = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    dirs = opt + (112 if magic == 0x20B else 96)            # PE32+ (64-bit) or PE32
    import_rva = struct.unpack_from("<I", data, dirs + 8)[0]  # data directory 1 = import table
    if not import_rva:
        return []
    sections = [struct.unpack_from("<IIII", data, opt + opt_size + i * 40 + 8) for i in range(n_sections)]

    def offset(rva: int) -> int:
        for vsize, vaddr, rawsize, rawptr in sections:
            if vaddr <= rva < vaddr + max(vsize, rawsize):
                return rva - vaddr + rawptr
        raise ValueError("Import table points outside every section.")

    names, desc = [], offset(import_rva)
    while True:
        name_rva = struct.unpack_from("<I", data, desc + 12)[0]
        if not name_rva:
            break
        start = offset(name_rva)
        names.append(data[start:data.index(b"\0", start)].decode("ascii", "replace"))
        desc += 20
    return names


def is_system_dll(name: str, system_dir: str | None = None) -> bool:
    """Part of Windows itself (always found by the loader), as opposed to something a compiler or library installed."""
    if name.lower().startswith(_SYSTEM_PREFIXES):
        return True
    system_dir = system_dir if system_dir is not None else os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    return os.path.isfile(os.path.join(system_dir, name))


def find_in(name: str, folders: list[str]) -> str | None:
    for d in folders:
        p = os.path.join(d, name)
        if d and os.path.isfile(p):
            return p
    return None


def non_system_dependencies(path: str, folders: list[str], system_dir: str | None = None) -> tuple[dict, list]:
    """
    Walk the dependency tree of `path`. Returns ({dll name: where it was found}, [names found nowhere]),
    leaving out Windows' own DLLs. `folders` is where to look (e.g. the DLL's folder, the compiler's bin, PATH).
    """
    found, missing, todo, seen = {}, [], [path], set()
    while todo:
        for name in imported_dlls(todo.pop()):
            key = name.lower()
            if key in seen or is_system_dll(name, system_dir):
                continue
            seen.add(key)
            where = find_in(name, folders)
            if where:
                found[name] = where
                todo.append(where)
            else:
                missing.append(name)
    return found, missing
