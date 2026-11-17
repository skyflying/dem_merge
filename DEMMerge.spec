# PyInstaller spec for DEM / XYZ Merge.
#   pyinstaller --noconfirm --clean DEMMerge.spec
# Set the environment variable DEMMERGE_ONEFILE=1 for a single .exe (slower start-up).
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

ONEFILE = os.environ.get("DEMMERGE_ONEFILE") == "1"

datas, binaries, hidden = [], [], []
for pkg in ("rasterio", "pyproj"):           # includes GDAL/PROJ DLLs and data (gdal_data, proj.db)
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hidden += h
hidden += collect_submodules("scipy.ndimage") + collect_submodules("scipy.interpolate")
hidden += ["affine", "matplotlib.backends.backend_qtagg"]
datas += [("demmerge/assets", "demmerge/assets")]

excludes = ["tkinter", "PyQt5", "PyQt6", "PySide2", "IPython", "jupyter", "notebook", "pytest",
            "PySide6.Qt3DCore", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
            "PySide6.QtMultimedia", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtPdf",
            "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtBluetooth"]

a = Analysis(["main.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hidden,
             excludes=excludes, noarchive=False)
# --- Bundle the NEWEST MSVC runtime found on the build PC -----------------------------
# Qt (PySide6) may need a newer msvcp140.dll than the copy shipped in its own wheel. Python 3.8+
# never searches PATH for DLLs, so the frozen app uses whatever copy sits in _internal. If that
# copy is too old, QtCore fails with "The specified procedure could not be found".
# We compare System32, the Python folder, PySide6 and shiboken6, and bundle the newest copy of
# each runtime DLL everywhere it is used (the runtime is backward compatible).
import sys
_RUNTIME = ["msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_codecvt_ids.dll",
            "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll"]


def _file_version(path):
    import ctypes
    from ctypes import wintypes
    ver = ctypes.windll.version
    size = ver.GetFileVersionInfoSizeW(path, None)
    if not size:
        return (0, 0, 0, 0)
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(path, 0, size, buf):
        return (0, 0, 0, 0)
    ptr, ln = ctypes.c_void_p(), wintypes.UINT()
    if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(ln)):
        return (0, 0, 0, 0)

    class _FFI(ctypes.Structure):
        _fields_ = [(n, wintypes.DWORD) for n in ("sig", "struc", "ms", "ls", "pms", "pls", "mask", "flags",
                                                   "os", "type", "sub", "dms", "dls")]
    v = ctypes.cast(ptr, ctypes.POINTER(_FFI)).contents
    return (v.ms >> 16, v.ms & 0xFFFF, v.ls >> 16, v.ls & 0xFFFF)


if sys.platform == "win32":
    import PySide6
    import shiboken6
    _dirs = [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"), sys.base_prefix,
             os.path.dirname(PySide6.__file__), os.path.dirname(shiboken6.__file__)]
    _best = {}
    for n in _RUNTIME:
        cands = [os.path.join(d, n) for d in _dirs if os.path.isfile(os.path.join(d, n))]
        if cands:
            vers = [(_file_version(c), c) for c in cands]
            v, path = max(vers)
            _best[n] = path
            print(f"[DEMMerge] {n}: using {'.'.join(map(str, v))} from {path}")
            for vv, cc in vers:
                print(f"[DEMMerge]     candidate {'.'.join(map(str, vv))}  {cc}")
    _fixed, _dests = [], set()
    for dest, src, typ in a.binaries:
        name = os.path.basename(dest).lower()
        if name in _best:
            src = _best[name]
        key = dest.replace("\\", "/").lower()
        if key in _dests:
            continue
        _dests.add(key)
        _fixed.append((dest, src, typ))
    for n, path in _best.items():           # make sure every runtime DLL is at the top level too
        if n not in _dests:
            _fixed.append((n, path, "BINARY"))
    a.binaries = _fixed

# --- Audit: DLLs picked up from outside Python / the venv / Windows ---------------------
# PyInstaller resolves DLL dependencies through PATH at build time, so DLLs from Anaconda, GMT,
# QGIS, ... can leak into the build. Replace them with same-named copies from Python itself when
# possible and write everything to build_audit.txt.
import site
_allowed = [os.path.normcase(os.path.abspath(p)) for p in
            [sys.base_prefix, sys.prefix, os.environ.get("SystemRoot", r"C:\Windows")] + site.getsitepackages()]


def _is_allowed(p):
    p = os.path.normcase(os.path.abspath(p))
    return any(p.startswith(r + os.sep) for r in _allowed)


_py_dirs = [sys.base_prefix, os.path.join(sys.base_prefix, "DLLs")]
_audit, _clean = [], []
for dest, src, typ in a.binaries:
    if src and not _is_allowed(src):
        alt = next((os.path.join(d, os.path.basename(src)) for d in _py_dirs
                    if os.path.isfile(os.path.join(d, os.path.basename(src)))), None)
        if alt:
            _audit.append(f"REPLACED  {dest}  {src}  ->  {alt}")
            src = alt
        else:
            _audit.append(f"FOREIGN   {dest}  {src}")
    _clean.append((dest, src, typ))
# python3.dll must always be the one that belongs to this Python
_p3 = os.path.join(sys.base_prefix, "python3.dll")
if os.path.isfile(_p3):
    _clean = [(d, _p3 if os.path.basename(d).lower() == "python3.dll" else s, t) for d, s, t in _clean]
a.binaries = _clean
with open("build_audit.txt", "w", encoding="utf-8") as _f:
    _f.write(f"python: {sys.version}\nbase_prefix: {sys.base_prefix}\n\n")
    _f.write("\n".join(_audit) or "No foreign DLLs.")
    _f.write("\n\nAll binaries:\n" + "\n".join(f"{d}  <-  {s}" for d, s, t in a.binaries))
for line in _audit:
    print("[DEMMerge] " + line)

pyz = PYZ(a.pure)

common = dict(name="DEMMerge", debug=False, strip=False, upx=False, console=False,
              icon="demmerge/assets/icon.ico", version="version_info.txt")
if ONEFILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], runtime_tmpdir=None, **common)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **common)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="DEMMerge")
