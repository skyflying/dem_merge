"""Writers: grid XYZ, ESRI ASCII Grid, merged point cloud."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

from .xyzio import iter_cache


def _strip_rows(width, target_cells=20_000_000):
    return max(1, min(4096, target_cells // max(width, 1)))


def write_prj(path: Path, crs):
    try:
        from pyproj import CRS
        wkt = CRS.from_user_input(crs.to_wkt()).to_wkt(version="WKT1_ESRI")
        path.with_suffix(".prj").write_text(wkt)
    except Exception:
        pass


def _fmt(sep, xy_dec, z_dec):
    return sep.join([f"%.{xy_dec}f", f"%.{xy_dec}f", f"%.{z_dec}f"])


def write_grid_xyz(tif: Path, out: Path, sep=" ", xy_dec=3, z_dec=3, header=False,
                   cancelled=None, progress_cb=None):
    fmt = _fmt(sep, xy_dec, z_dec)
    n = 0
    with rasterio.open(tif) as ds, open(out, "w", newline="\n") as f:
        if header:
            f.write(sep.join(["X", "Y", "Z"]) + "\n")
        T = ds.transform
        nd = ds.nodata
        step = _strip_rows(ds.width)
        xs = T.c + (np.arange(ds.width) + 0.5) * T.a
        for r0 in range(0, ds.height, step):
            if cancelled and cancelled():
                raise InterruptedError
            h = min(step, ds.height - r0)
            z = ds.read(1, window=Window(0, r0, ds.width, h))
            ok = ~np.isnan(z) if nd is None or np.isnan(nd) else (z != nd) & ~np.isnan(z)
            rr, cc = np.nonzero(ok)
            if len(rr):
                ys = T.f + (r0 + rr + 0.5) * T.e
                np.savetxt(f, np.column_stack([xs[cc], ys, z[rr, cc]]), fmt=fmt)
                n += len(rr)
            if progress_cb:
                progress_cb((r0 + h) / ds.height)
        crs = ds.crs
    write_prj(out, crs)
    return n


def write_ascii_grid(tif: Path, out: Path, nodata=-9999.0, z_dec=3, cancelled=None, progress_cb=None):
    with rasterio.open(tif) as ds, open(out, "w", newline="\n") as f:
        T = ds.transform
        f.write(f"ncols {ds.width}\nnrows {ds.height}\n")
        f.write(f"xllcorner {T.c:.12g}\nyllcorner {T.f + ds.height * T.e:.12g}\n")
        f.write(f"cellsize {T.a:.12g}\nNODATA_value {nodata:g}\n")
        step = _strip_rows(ds.width)
        nd = ds.nodata
        for r0 in range(0, ds.height, step):
            if cancelled and cancelled():
                raise InterruptedError
            h = min(step, ds.height - r0)
            z = ds.read(1, window=Window(0, r0, ds.width, h)).astype(np.float64)
            bad = np.isnan(z) if nd is None or np.isnan(nd) else (z == nd) | np.isnan(z)
            z[bad] = nodata
            np.savetxt(f, z, fmt=f"%.{z_dec}f", delimiter=" ")
            if progress_cb:
                progress_cb((r0 + h) / ds.height)
        crs = ds.crs
    write_prj(out, crs)


def write_point_cloud(xyz_layers, dem_ids, merged_tif: Path, src_tif: Path, g, out: Path,
                      sep=" ", xy_dec=3, z_dec=3, header=False, include_dem=True,
                      cancelled=None, progress_cb=None, log=None):
    """Merged point cloud: each XYZ file keeps only original points in cells it finally supplies;
    cells won by DEMs can optionally be filled with cell-centre points."""
    fmt = _fmt(sep, xy_dec, z_dec)
    n_total = 0
    parts = len(xyz_layers) + (1 if include_dem and dem_ids else 0)
    done = 0
    with open(out, "w", newline="\n") as f:
        if header:
            f.write(sep.join(["X", "Y", "Z"]) + "\n")
        for L in xyz_layers:
            keep = np.memmap(L.keep_path, dtype=np.uint8, mode="r", shape=(L.h, L.w))
            kept = 0
            for i, total, pts in iter_cache(L.cache_path):
                if cancelled and cancelled():
                    raise InterruptedError
                gc = g.col_of(pts["x"]); gr = g.row_of(pts["y"])
                lc = gc - L.col_off; lr = gr - L.row_off
                ok = (gc >= 0) & (gc < g.width) & (gr >= 0) & (gr < g.height) \
                    & (lc >= 0) & (lc < L.w) & (lr >= 0) & (lr < L.h)
                idx = np.flatnonzero(ok)
                if len(idx):
                    k = keep[lr[idx], lc[idx]].astype(bool)
                    idx = idx[k]
                if len(idx):
                    p = pts[idx]
                    np.savetxt(f, np.column_stack([p["x"], p["y"], p["z"].astype(np.float64) + L.offset]),
                               fmt=fmt)
                    kept += len(idx)
                if progress_cb:
                    progress_cb((done + (i + len(pts)) / max(total, 1)) / parts)
            del keep
            n_total += kept
            if log:
                log(f"  Points: {L.name} kept {kept:,}")
            done += 1
        if include_dem and dem_ids:
            dem_ids = np.array(sorted(dem_ids), np.uint16)
            n_dem = 0
            with rasterio.open(merged_tif) as dz, rasterio.open(src_tif) as ds:
                T = dz.transform
                xs = T.c + (np.arange(dz.width) + 0.5) * T.a
                step = _strip_rows(dz.width)
                for r0 in range(0, dz.height, step):
                    if cancelled and cancelled():
                        raise InterruptedError
                    h = min(step, dz.height - r0)
                    win = Window(0, r0, dz.width, h)
                    z = dz.read(1, window=win)
                    s = ds.read(1, window=win)
                    m = np.isin(s, dem_ids) & ~np.isnan(z)
                    if dz.nodata is not None and not np.isnan(dz.nodata):
                        m &= z != dz.nodata
                    rr, cc = np.nonzero(m)
                    if len(rr):
                        ys = T.f + (r0 + rr + 0.5) * T.e
                        np.savetxt(f, np.column_stack([xs[cc], ys, z[rr, cc]]), fmt=fmt)
                        n_dem += len(rr)
                    if progress_cb:
                        progress_cb((done + (r0 + h) / dz.height) / parts)
            n_total += n_dem
            if log:
                log(f"  Points: {n_dem:,} cell-centre points added from DEM areas")
    write_prj(out, g.crs)
    return n_total
