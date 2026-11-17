"""Output grid definition, XYZ gridding and gap filling."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from scipy import ndimage

from .xyzio import iter_cache

EPS = 1e-9


@dataclass
class GridSpec:
    x0: float      # upper-left X
    y0: float      # upper-left Y
    res: float
    width: int
    height: int
    crs: object    # rasterio CRS

    @property
    def transform(self) -> Affine:
        return Affine(self.res, 0, self.x0, 0, -self.res, self.y0)

    @staticmethod
    def from_bounds(minx, miny, maxx, maxy, res, crs) -> "GridSpec":
        x0 = math.floor(minx / res + EPS) * res
        y0 = math.ceil(maxy / res - EPS) * res
        width = int(math.floor((maxx - x0) / res)) + 1
        height = int(math.floor((y0 - miny) / res)) + 1
        return GridSpec(x0, y0, res, width, height, crs)

    def col_of(self, x):
        return np.floor((np.asarray(x) - self.x0) / self.res).astype(np.int64)

    def row_of(self, y):
        return np.floor((self.y0 - np.asarray(y)) / self.res).astype(np.int64)

    def rowcol_range(self, minx, miny, maxx, maxy, margin=1):
        c0 = int(math.floor((minx - self.x0) / self.res)) - margin
        c1 = int(math.floor((maxx - self.x0) / self.res)) + 1 + margin
        r0 = int(math.floor((self.y0 - maxy) / self.res)) - margin
        r1 = int(math.floor((self.y0 - miny) / self.res)) + 1 + margin
        return r0, c0, r1, c1


def iter_tiles(height, width, ts):
    for r0 in range(0, height, ts):
        for c0 in range(0, width, ts):
            yield r0, c0, min(ts, height - r0), min(ts, width - c0)


def tif_profile(width, height, crs, transform, dtype, nodata, compress="DEFLATE"):
    tiled = width >= 512 and height >= 512
    prof = dict(driver="GTiff", width=width, height=height, count=1, dtype=dtype,
                crs=crs, transform=transform, nodata=nodata, BIGTIFF="IF_SAFER")
    if tiled:
        prof.update(tiled=True, blockxsize=512, blockysize=512)
    if compress and compress.upper() != "NONE":
        prof["compress"] = compress.upper()
        prof["predictor"] = 3 if np.dtype(dtype).kind == "f" else 2
    return prof


# ---------------------------------------------------------------- gap filling

def fill_gaps(arr: np.ndarray, method: str, max_dist: float) -> np.ndarray:
    """arr: float32 with NaN as nodata. max_dist is in cells."""
    if method == "none" or max_dist <= 0:
        return arr
    valid = ~np.isnan(arr)
    if valid.all() or not valid.any():
        return arr
    if method == "idw":
        from rasterio.fill import fillnodata
        tmp = np.where(valid, arr, 0).astype(np.float32)
        out = fillnodata(tmp, mask=valid.astype(np.uint8), max_search_distance=float(max_dist),
                         smoothing_iterations=0)
        # cells fillnodata could not reach stay 0; restore them to NaN by distance
        dist = ndimage.distance_transform_edt(~valid)
        out = out.astype(np.float32)
        out[(dist > max_dist) & ~valid] = np.nan
        return out
    if method == "nearest":
        dist, (ri, ci) = ndimage.distance_transform_edt(~valid, return_indices=True)
        out = arr[ri, ci]
        out[dist > max_dist] = np.nan
        out[valid] = arr[valid]
        return out
    if method == "tin":
        from scipy.interpolate import griddata
        dist_hole = ndimage.distance_transform_edt(~valid)
        targets = (~valid) & (dist_hole <= max_dist)
        if not targets.any():
            return arr
        # only valid cells near holes become TIN nodes (much faster)
        dist_valid = ndimage.distance_transform_edt(valid)
        nodes = valid & (dist_valid <= max_dist + 1)
        pr, pc = np.nonzero(nodes)
        if len(pr) < 3:
            return arr
        tr, tc = np.nonzero(targets)
        try:
            vals = griddata(np.column_stack([pr, pc]), arr[pr, pc], (tr, tc), method="linear")
        except Exception:
            return arr
        out = arr.copy()
        out[tr, tc] = vals.astype(np.float32)
        return out
    raise ValueError(f"Unknown gap-fill method: {method}")


# ---------------------------------------------------------------- XYZ gridding

def grid_xyz(cache_path: Path, bbox, g: GridSpec, method: str, fill_method: str,
             fill_dist: int, out_tif: Path, tmpdir: Path, compress="DEFLATE",
             tile=1024, cancelled=None, progress_cb=None):
    """Grid a cached point cloud onto the global grid and write an aligned GeoTIFF.
    Returns (row_off, col_off, h, w)."""
    r0, c0, r1, c1 = g.rowcol_range(*bbox, margin=0)
    h, w = r1 - r0, c1 - c0
    n = h * w
    stem = out_tif.stem
    mm = {}

    def mk(name, dtype, fill=None):
        a = np.memmap(tmpdir / f"{stem}_{name}.dat", dtype=dtype, mode="w+", shape=(n,))
        if fill is not None:
            for i in range(0, n, 20_000_000):
                a[i:i + 20_000_000] = fill
        mm[name] = a
        return a

    if method == "mean":
        acc_s = mk("sum", np.float64)
        acc_n = mk("cnt", np.uint32)
    elif method in ("min", "max"):
        acc_v = mk("val", np.float32, np.inf if method == "min" else -np.inf)
    elif method == "nearest":
        acc_v = mk("val", np.float32, np.nan)
        acc_d = mk("dist", np.float32, np.inf)
    else:
        raise ValueError(f"Unknown gridding method: {method}")

    for i, total, pts in iter_cache(cache_path):
        if cancelled and cancelled():
            raise InterruptedError
        x, y, z = pts["x"], pts["y"], pts["z"].astype(np.float64)
        col = g.col_of(x) - c0
        row = g.row_of(y) - r0
        ok = (col >= 0) & (col < w) & (row >= 0) & (row < h)
        if not ok.all():
            x, y, z, col, row = x[ok], y[ok], z[ok], col[ok], row[ok]
        lin = row * w + col
        if method == "mean":
            u, inv = np.unique(lin, return_inverse=True)
            acc_s[u] += np.bincount(inv, weights=z)
            acc_n[u] += np.bincount(inv).astype(np.uint32)
        elif method in ("min", "max"):
            order = np.argsort(lin, kind="stable")
            ls, zs = lin[order], z[order]
            starts = np.flatnonzero(np.r_[True, ls[1:] != ls[:-1]])
            u = ls[starts]
            if method == "min":
                acc_v[u] = np.minimum(acc_v[u], np.minimum.reduceat(zs, starts))
            else:
                acc_v[u] = np.maximum(acc_v[u], np.maximum.reduceat(zs, starts))
        else:  # nearest: point closest to the cell centre
            cx = g.x0 + (c0 + col + 0.5) * g.res
            cy = g.y0 - (r0 + row + 0.5) * g.res
            d = ((x - cx) ** 2 + (y - cy) ** 2).astype(np.float32)
            order = np.lexsort((d, lin))
            ls = lin[order]
            first = order[np.r_[True, ls[1:] != ls[:-1]]]
            u, du, zu = lin[first], d[first], z[first]
            better = du < acc_d[u]
            acc_d[u[better]] = du[better]
            acc_v[u[better]] = zu[better]
        if progress_cb:
            progress_cb(0.7 * (i + len(pts)) / max(total, 1))

    # convert accumulators to elevations
    val = np.memmap(tmpdir / f"{stem}_z.dat", dtype=np.float32, mode="w+", shape=(n,))
    step = 20_000_000
    for i in range(0, n, step):
        if method == "mean":
            s, c = acc_s[i:i + step], acc_n[i:i + step]
            with np.errstate(invalid="ignore", divide="ignore"):
                val[i:i + step] = np.where(c > 0, s / np.maximum(c, 1), np.nan)
        elif method in ("min", "max"):
            v = acc_v[i:i + step]
            val[i:i + step] = np.where(np.isfinite(v), v, np.nan)
        else:
            val[i:i + step] = acc_v[i:i + step]
    val2 = val.reshape(h, w)

    transform = g.transform * Affine.translation(c0, r0)
    prof = tif_profile(w, h, g.crs, transform, "float32", np.nan, compress)
    halo = int(math.ceil(fill_dist)) + 1 if fill_method != "none" and fill_dist > 0 else 0
    tiles = list(iter_tiles(h, w, tile))
    with rasterio.open(out_tif, "w", **prof) as dst:
        for k, (tr, tc, th, tw) in enumerate(tiles):
            if cancelled and cancelled():
                raise InterruptedError
            hr0, hc0 = max(0, tr - halo), max(0, tc - halo)
            hr1, hc1 = min(h, tr + th + halo), min(w, tc + tw + halo)
            block = np.array(val2[hr0:hr1, hc0:hc1], dtype=np.float32)
            block = fill_gaps(block, fill_method, fill_dist)
            block = block[tr - hr0:tr - hr0 + th, tc - hc0:tc - hc0 + tw]
            dst.write(block, 1, window=rasterio.windows.Window(tc, tr, tw, th))
            if progress_cb:
                progress_cb(0.7 + 0.3 * (k + 1) / len(tiles))
    del val, val2
    acc_s = acc_n = acc_v = acc_d = None  # noqa: F841  release memmaps (needed on Windows before deleting)
    mm.clear()
    for f in tmpdir.glob(f"{stem}_*.dat"):
        try:
            f.unlink()
        except OSError:
            pass
    return r0, c0, h, w
