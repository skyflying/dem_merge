"""Streaming XYZ text reader and binary cache."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

PT_DTYPE = np.dtype([("x", "<f8"), ("y", "<f8"), ("z", "<f4")])
_TOKEN_RE = re.compile(r"[,;\s]+")


@dataclass
class XYZReadOptions:
    xcol: int = 0        # 0-based
    ycol: int = 1
    zcol: int = 2
    zscale: float = 1.0  # e.g. -1 to turn depths into elevations
    nodata: float | None = None
    chunksize: int = 2_000_000


def _is_num(tok: str) -> bool:
    try:
        float(tok)
        return True
    except ValueError:
        return False


def sniff(path: Path, need_cols: int = 3, max_lines: int = 500):
    """Find the first data line and delimiter, skipping headers, .pts point counts, etc."""
    with open(path, "r", errors="ignore") as f:
        for i in range(max_lines):
            line = f.readline()
            if not line:
                break
            s = line.strip()
            if not s:
                continue
            toks = [t for t in _TOKEN_RE.split(s) if t]
            if len(toks) >= need_cols and sum(_is_num(t) for t in toks[:need_cols]) == need_cols:
                if "," in s:
                    sep = ","
                elif ";" in s:
                    sep = ";"
                else:
                    sep = r"\s+"
                return sep, i
    raise ValueError(f"No numeric row with at least {need_cols} columns found: {path.name}")


def iter_xyz_chunks(path: Path, opt: XYZReadOptions):
    need = max(opt.xcol, opt.ycol, opt.zcol) + 1
    sep, skip = sniff(path, need)
    cols = sorted({opt.xcol, opt.ycol, opt.zcol})
    reader = pd.read_csv(path, sep=sep, header=None, skiprows=skip, usecols=cols,
                         chunksize=opt.chunksize, engine="c", on_bad_lines="skip",
                         skip_blank_lines=True, comment="#")
    for df in reader:
        x = pd.to_numeric(df[opt.xcol], errors="coerce").to_numpy(np.float64)
        y = pd.to_numeric(df[opt.ycol], errors="coerce").to_numpy(np.float64)
        z = pd.to_numeric(df[opt.zcol], errors="coerce").to_numpy(np.float64)
        ok = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
        if opt.nodata is not None:
            ok &= z != opt.nodata
        if not ok.all():
            x, y, z = x[ok], y[ok], z[ok]
        if opt.zscale != 1.0:
            z = z * opt.zscale
        yield x, y, z


def cache_xyz(path: Path, opt: XYZReadOptions, transformer, cache_path: Path,
              cancelled=None, progress_cb=None):
    """Read XYZ -> transform -> write binary cache. Returns (n_points, (minx, miny, maxx, maxy))."""
    n = 0
    bb = [np.inf, np.inf, -np.inf, -np.inf]
    total = max(path.stat().st_size, 1)
    with open(cache_path, "wb") as fout:
        for x, y, z in iter_xyz_chunks(path, opt):
            if cancelled and cancelled():
                raise InterruptedError
            if transformer is not None and len(x):
                x, y = transformer.transform(x, y)
                x = np.asarray(x); y = np.asarray(y)
                ok = np.isfinite(x) & np.isfinite(y)
                if not ok.all():
                    x, y, z = x[ok], y[ok], z[ok]
            if not len(x):
                continue
            rec = np.empty(len(x), PT_DTYPE)
            rec["x"], rec["y"], rec["z"] = x, y, z
            rec.tofile(fout)
            n += len(x)
            bb = [min(bb[0], x.min()), min(bb[1], y.min()), max(bb[2], x.max()), max(bb[3], y.max())]
            if progress_cb:
                progress_cb(min(fout.tell() / (total * 0.6), 0.99))  # rough estimate
    return n, tuple(float(v) for v in bb)


def iter_cache(cache_path: Path, chunk: int = 5_000_000):
    mm = np.memmap(cache_path, dtype=PT_DTYPE, mode="r")
    for i in range(0, len(mm), chunk):
        yield i, len(mm), np.asarray(mm[i:i + chunk])
    del mm
