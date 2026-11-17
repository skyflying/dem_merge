"""Entry point for `python main.py` and the PyInstaller executable."""
import os
import sys
import traceback
from pathlib import Path


def _frozen_setup():
    """Make a windowed (no-console) executable robust."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        gdal_data = base / "rasterio" / "gdal_data"
        if gdal_data.is_dir():
            os.environ.setdefault("GDAL_DATA", str(gdal_data))
        if sys.platform == "win32":
            _isolate_qt(base)


def _isolate_qt(base: Path):
    """Keep other Qt installations (QGIS, Anaconda, ArcGIS...) and old MSVC runtimes out of the way."""
    # 1. Drop PATH entries that contain another Qt, and Qt env vars set by other software
    parts = os.environ.get("PATH", "").split(os.pathsep)
    keep = [p for p in parts if p and not any(os.path.isfile(os.path.join(p, f))
                                              for f in ("Qt6Core.dll", "Qt5Core.dll"))]
    os.environ["PATH"] = os.pathsep.join([str(base)] + keep)
    for v in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH", "QT_API"):
        os.environ.pop(v, None)
    # 2. Search our own folders first
    for d in (base / "PySide6", base / "shiboken6", base):
        if d.is_dir():
            try:
                os.add_dll_directory(str(d))
            except OSError:
                pass
    # 3. Pre-load the bundled MSVC runtime (the build picks the newest available copy)
    import ctypes
    for name in ("vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "concrt140.dll"):
        for d in (base, base / "PySide6", base / "shiboken6"):
            f = d / name
            if f.is_file():
                try:
                    ctypes.WinDLL(str(f))
                except OSError:
                    pass
                break


def _crash_log(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    log = Path.home() / "DEMMerge_crash.log"
    try:
        log.write_text(text, encoding="utf-8")
    except OSError:
        pass
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance():
            QMessageBox.critical(None, "Unexpected error", f"{exc}\n\nDetails saved to:\n{log}")
    except Exception:
        pass


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    _frozen_setup()
    if "--selftest" in sys.argv:
        from demmerge.selftest import run
        lines = []
        ok = run(log=lambda s: (lines.append(s), print(s, flush=True)))
        if getattr(sys, "frozen", False):
            (Path(sys.executable).parent / "selftest.log").write_text("\n".join(lines), encoding="utf-8")
        sys.exit(0 if ok else 1)
    sys.excepthook = _crash_log
    from demmerge.gui import main
    main()
