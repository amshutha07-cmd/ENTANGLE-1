"""Windows: shatter.dll's OpenSSL/zlib DLLs are found, copied next to it, and a missing one is named (runs on any OS)."""
import os
import struct

import pytest

import build_engine
import engine
import pe_imports


def fake_dll(path, imports):
    """A minimal 64-bit PE file whose import table lists `imports`."""
    section = bytearray(0x200)
    names_at = 20 * (len(imports) + 1)
    for i, name in enumerate(imports):
        struct.pack_into("<I", section, 20 * i + 12, 0x1000 + names_at)
        section[names_at:names_at + len(name) + 1] = name.encode() + b"\0"
        names_at += len(name) + 1
    head = bytearray(0x200)
    head[:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x40)
    head[0x40:0x44] = b"PE\0\0"
    struct.pack_into("<HH", head, 0x44, 0x8664, 1)                 # machine, one section
    struct.pack_into("<H", head, 0x40 + 20, 240)                    # optional header size (PE32+)
    struct.pack_into("<H", head, 0x58, 0x20B)                       # PE32+
    struct.pack_into("<I", head, 0x58 + 112 + 8, 0x1000)            # import table RVA
    struct.pack_into("<IIII", head, 0x58 + 240 + 8, 0x200, 0x1000, 0x200, 0x200)
    with open(path, "wb") as f:
        f.write(head + section)
    return str(path)


@pytest.fixture
def layout(tmp_path):
    """project/shatter.dll -> libcrypto + zlib + KERNEL32; msys/bin holds libcrypto (-> zlib) and zlib; System32 holds KERNEL32."""
    project, msys, system = (tmp_path / d for d in ("project", "msys_bin", "System32"))
    for d in (project, msys, system):
        d.mkdir()
    fake_dll(system / "KERNEL32.dll", [])
    fake_dll(msys / "libcrypto-3-x64.dll", ["zlib1.dll", "KERNEL32.dll", "api-ms-win-crt-runtime-l1-1-0.dll"])
    fake_dll(msys / "zlib1.dll", ["KERNEL32.dll"])
    dll = fake_dll(project / "shatter.dll", ["libcrypto-3-x64.dll", "zlib1.dll", "KERNEL32.dll"])
    return dll, str(project), str(msys), str(system)


def test_import_table_is_read():
    tmp = os.path.join(os.path.dirname(__file__), "..", ".pytest_cache")
    os.makedirs(tmp, exist_ok=True)
    p = fake_dll(os.path.join(tmp, "probe.dll"), ["libcrypto-3-x64.dll", "zlib1.dll"])
    assert pe_imports.imported_dlls(p) == ["libcrypto-3-x64.dll", "zlib1.dll"]
    with open(p, "wb") as f:
        f.write(b"\x7fELF not a windows file")
    with pytest.raises(ValueError):
        pe_imports.imported_dlls(p)


def test_dependencies_are_walked_and_windows_own_dlls_skipped(layout):
    dll, project, msys, system = layout
    found, missing = pe_imports.non_system_dependencies(dll, [project, msys], system)
    assert sorted(found) == ["libcrypto-3-x64.dll", "zlib1.dll"] and missing == []
    found, missing = pe_imports.non_system_dependencies(dll, [project], system)
    assert missing == ["libcrypto-3-x64.dll", "zlib1.dll"]


def test_build_copies_the_dlls_next_to_shatter(layout, monkeypatch):
    dll, project, msys, system = layout
    monkeypatch.setattr(build_engine, "dependency_folders", lambda cxx, ossl="": [msys])
    assert build_engine.copy_windows_dependencies(dll, "g++", system_dir=system) == ["libcrypto-3-x64.dll", "zlib1.dll"]
    assert sorted(os.listdir(project)) == ["libcrypto-3-x64.dll", "shatter.dll", "zlib1.dll"]
    found, missing = pe_imports.non_system_dependencies(dll, [project], system)
    assert missing == [] and all(os.path.dirname(p) == project for p in found.values())   # self-contained now


def test_build_stops_with_a_clear_message_when_a_dll_is_nowhere(layout, monkeypatch):
    dll, project, msys, system = layout
    os.remove(os.path.join(msys, "zlib1.dll"))
    monkeypatch.setattr(build_engine, "dependency_folders", lambda cxx, ossl="": [msys])
    with pytest.raises(SystemExit) as e:
        build_engine.copy_windows_dependencies(dll, "g++", system_dir=system)
    assert "zlib1.dll" in str(e.value) and "PATH" in str(e.value)


def test_loader_allows_path_folders_and_names_what_is_missing(layout, monkeypatch):
    """The teammate's case: an older build, OpenSSL in C:\\msys64\\ucrt64\\bin on PATH, zlib nowhere."""
    dll, project, msys, system = layout
    os.remove(os.path.join(msys, "zlib1.dll"))
    monkeypatch.setenv("SystemRoot", os.path.dirname(system))
    monkeypatch.setenv("PATH", msys)
    allowed = []
    monkeypatch.setattr(os, "add_dll_directory", allowed.append, raising=False)
    message = engine._windows_dll_help(dll)
    assert allowed == [msys]                                                   # OpenSSL's folder is searched now
    assert "needs zlib1.dll" in message and "build_engine.py" in message
    fake_dll(os.path.join(msys, "zlib1.dll"), [])
    assert engine._windows_dll_help(dll) == ""                                 # nothing missing: no extra advice
