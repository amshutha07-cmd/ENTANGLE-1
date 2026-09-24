"""
engine.py — A.N.Sx Vault | ctypes wrapper around libshatter (format v2).

Fails loudly: if the native library is missing or is a stale build, callers get
EngineError instead of a silently "successful" no-op.
"""
from __future__ import annotations
import ctypes
import logging
import os
import sys

if sys.platform.startswith("win"):
    dll_dirs = []

    # Allow the user to specify their MSYS2 MinGW64 bin directory.
    if os.environ.get("MSYS2_MINGW64"):
        dll_dirs.append(os.environ["MSYS2_MINGW64"])

    # Common MSYS2 installation locations.
    dll_dirs.extend([
        r"C:\msys64\mingw64\bin",
        r"C:\msys64\ucrt64\bin",
        r"C:\msys64\clang64\bin",
    ])

    for dll_dir in dll_dirs:
        if os.path.isdir(dll_dir):
            os.add_dll_directory(dll_dir)
            break
logger = logging.getLogger(__name__)

EXPECTED_VERSION = 2
_LIB_NAME = ("shatter.dll" if sys.platform.startswith("win")
             else "libshatter.dylib" if sys.platform == "darwin" else "libshatter.so")
_ROOT = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, "frozen", False):                      # PyInstaller bundle
    _ROOT = getattr(sys, "_MEIPASS", _ROOT)
_LIB_PATH = os.path.join(_ROOT, _LIB_NAME)
DEFAULT_SHARD_DIR = os.path.join(os.path.expanduser("~"), ".ansx_vault", "shards")

ERRORS = {
    -1:  "Input file could not be read.",
    -2:  "Compression failed.",
    -3:  "Encryption failed.",
    -4:  "Could not write shard files.",
    -5:  "Input is too large (limit 2 GiB).",
    -10: "Fewer than 8 intact shards are available.",
    -11: "Authentication failed: wrong key, or the shards were tampered with / corrupted.",
    -12: "Reconstructed payload is corrupt.",
    -13: "Could not write the output file.",
    -14: "Shard set is mathematically singular.",
    -99: "Unexpected engine error.",
}


class EngineError(RuntimeError):
    def __init__(self, code: int, message: str | None = None):
        self.code = code
        super().__init__(message or ERRORS.get(code, f"Engine error {code}"))


_dll_dirs: list = []                                    # keep the add_dll_directory handles alive


def _windows_dll_help(path: str) -> str:
    """
    Python does not search PATH for the DLLs shatter.dll needs. Allow the PATH folders that hold them (an older build
    whose OpenSSL/zlib were not copied next to it), and say exactly which files are missing if some are nowhere.
    """
    import pe_imports
    here = os.path.dirname(path)
    path_dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    try:
        found, missing = pe_imports.non_system_dependencies(path, [here] + path_dirs)
    except (OSError, ValueError):
        return ""
    for folder in {os.path.dirname(p) for p in found.values()} - {here}:
        try:
            _dll_dirs.append(os.add_dll_directory(folder))
        except (OSError, AttributeError):
            pass
    if missing:
        return (f"{os.path.basename(path)} needs {', '.join(missing)}, which is not next to it or on PATH. "
                "Run: python build_engine.py (it copies these files next to shatter.dll)")
    return ""


def load_library(path: str = _LIB_PATH):
    """ctypes handle for the engine at `path`, or EngineError with a message that says how to fix it."""
    help_text = _windows_dll_help(path) if sys.platform.startswith("win") and os.path.exists(path) else ""
    try:
        return ctypes.CDLL(path)
    except OSError as exc:
        raise EngineError(-99, f"Native shatter engine unavailable ({exc}). "
                               f"{help_text or 'Run: python build_engine.py'}") from exc


def _load():
    lib = load_library()
    try:
        version = lib.ansx_engine_version()
    except AttributeError as exc:
        raise EngineError(-99, f"Native shatter engine unavailable ({exc}). Run: python build_engine.py") from exc
    if version != EXPECTED_VERSION:
        raise EngineError(-99, f"libshatter is v{version}, expected v{EXPECTED_VERSION}. Run: python build_engine.py")
    lib.run_shatter_engine_to.argtypes = [ctypes.c_char_p] * 4
    lib.run_shatter_engine_to.restype = ctypes.c_int
    lib.unshatter_engine.argtypes = [ctypes.c_char_p] * 4
    lib.unshatter_engine.restype = ctypes.c_int
    return lib


_lib = None


def _get():
    global _lib
    if _lib is None:
        _lib = _load()
    return _lib


def available() -> bool:
    try:
        _get()
        return True
    except EngineError:
        return False


def _b(s: str) -> bytes:
    return s.encode("utf-8")


def shatter(input_path: str, key: str, second_factor: str = "", out_dir: str | None = None) -> None:
    """Shatter `input_path` into 12 shards. Default output dir: ~/.ansx_vault/shards (created if missing)."""
    code = _get().run_shatter_engine_to(
        _b(input_path), _b(out_dir or DEFAULT_SHARD_DIR), _b(key), _b(second_factor))
    if code != 0:
        raise EngineError(code)


def unshatter(shard_dir: str, output_path: str, key: str, second_factor: str = "") -> None:
    code = _get().unshatter_engine(_b(shard_dir), _b(output_path), _b(key), _b(second_factor))
    if code != 0:
        raise EngineError(code)
