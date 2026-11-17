"""Quick end-to-end check of the processing chain (GDAL, PROJ, gridding, merge, outputs).
Run:  DEMMerge.exe --selftest   or   python main.py --selftest"""
from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

import numpy as np


def _win_dll_probe(log):
    """Load the Qt chain one DLL at a time and report which copy of each runtime DLL is in use."""
    import ctypes
    import sys
    from ctypes import wintypes
    if not sys.platform.startswith("win"):
        return
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetModuleHandleW.restype = wintypes.HMODULE
    k32.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]

    def loaded(name):
        h = k32.GetModuleHandleW(name)
        if not h:
            return "-"
        buf = ctypes.create_unicode_buffer(1024)
        k32.GetModuleFileNameW(h, buf, 1024)
        return buf.value

    chain = [base / "python3.dll", base / "shiboken6" / "shiboken6.abi3.dll",
             base / "PySide6" / "Qt6Core.dll", base / "PySide6" / "pyside6.abi3.dll"]
    log("DLL probe:")
    for f in chain:
        if not f.exists():
            log(f"  missing   {f.relative_to(base)}")
            continue
        try:
            ctypes.WinDLL(str(f))
            log(f"  ok        {f.relative_to(base)}")
        except OSError as e:
            log(f"  FAILED    {f.relative_to(base)}  ->  {e}")
    for n in ("python3.dll", "python312.dll", "vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll",
              "msvcp140_1.dll", "msvcp140_2.dll", "Qt6Core.dll"):
        log(f"  loaded {n:<20} {loaded(n)}")


def run(log=print) -> bool:
    import rasterio
    from pyproj import Transformer
    from rasterio.transform import from_origin

    from .engine import JobConfig, MergeEngine
    from .filters import DEFAULT_EXCLUDE, NameFilter
    from .sources import scan_paths, sort_by_date

    t0 = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="demmerge_selftest_"))
    try:
        _win_dll_probe(log)
        from PySide6 import QtCore, QtWidgets  # noqa: F401  (the GUI needs these DLLs)
        log(f"Qt {QtCore.qVersion()} · PySide6 import OK")
        log(f"GDAL {rasterio.__gdal_version__} · rasterio {rasterio.__version__}")
        import pyproj
        log(f"PROJ {pyproj.proj_version_str} · pyproj {pyproj.__version__} · data {pyproj.datadir.get_data_dir()}")
        tr = Transformer.from_crs(3826, 4326, always_xy=True)
        lon, lat = tr.transform(170000, 2620000)
        assert 120 < lon < 121 and 23 < lat < 24, (lon, lat)
        log(f"TWD97 → WGS84 OK ({lon:.6f}, {lat:.6f})")

        x0, y0, res, w, h = 170000.0, 2621000.0, 2.0, 200, 150
        z = np.fromfunction(lambda r, c: -20 + 0.01 * c - 0.005 * r, (h, w), dtype=np.float32)
        prof = dict(driver="GTiff", width=w, height=h, count=1, dtype="float32", crs="EPSG:3826",
                    transform=from_origin(x0, y0, res, res), nodata=-9999)
        with rasterio.open(tmp / "area_DEM_20230101.tif", "w", **prof) as d:
            d.write(z, 1)
        with rasterio.open(tmp / "area_TVU_20230101.tif", "w", **prof) as d:
            d.write(np.zeros_like(z), 1)
        tu = Transformer.from_crs(3826, 32651, always_xy=True)
        rng = np.random.default_rng(0)
        px = rng.uniform(x0 + 50, x0 + 250, 20000)
        py = rng.uniform(y0 - 250, y0 - 50, 20000)
        ux, uy = tu.transform(px, py)
        with open(tmp / "line_20250102.pts", "w") as f:
            f.write("20000\n")
            np.savetxt(f, np.column_stack([ux, uy, -19 + 0 * px]), fmt="%.3f")

        items, errs = scan_paths([tmp])
        assert not errs, errs
        flt = NameFilter(exclude=DEFAULT_EXCLUDE).compile()
        items = [i for i in sort_by_date(items) if flt.accepts(i.path)]
        assert [i.path.name for i in items] == ["line_20250102.pts", "area_DEM_20230101.tif"], items
        items[0].crs_override = "EPSG:32651"
        log("Scan, date parsing and name filter OK")

        cfg = JobConfig(sources=items, default_crs="EPSG:3826", target_crs="EPSG:3826", resolution=2.0,
                        merge_method="feather", vertical_offset=True, out_dir=str(tmp / "out"),
                        out_grid_xyz=True, out_points_xyz=True, out_asc=True, fill_method="idw",
                        fill_distance=2)
        res_ = MergeEngine(cfg, log=lambda s: None).run()
        for f in res_["files"]:
            assert Path(f).exists() and Path(f).stat().st_size > 0, f
        with rasterio.open(res_["merged_tif"]) as d:
            a = d.read(1)
            assert (a != -9999).sum() > 0
        log(f"Merge + {len(res_['files'])} outputs OK")
        log(f"SELFTEST PASSED in {time.time() - t0:.1f} s")
        return True
    except Exception:
        import traceback
        log(traceback.format_exc())
        log("SELFTEST FAILED")
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
