"""Merge layers: aligned XYZ grids and on-the-fly reprojected DEMs."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from rasterio.windows import Window

from .grid import GridSpec

RESAMPLING = {
    "nearest": Resampling.nearest,
    "bilinear": Resampling.bilinear,
    "cubic": Resampling.cubic,
    "average": Resampling.average,
}


@dataclass
class LayerBase:
    id: int                 # priority (1 = top)
    name: str
    kind: str               # 'dem' / 'xyz'
    year: float | None      # decimal year, for time weighting
    r_lo: int = 0
    c_lo: int = 0
    r_hi: int = 0
    c_hi: int = 0
    offset: float = 0.0     # vertical offset correction

    def intersects(self, r0, c0, r1, c1) -> bool:
        return not (r1 <= self.r_lo or r0 >= self.r_hi or c1 <= self.c_lo or c0 >= self.c_hi)

    def read(self, r0, c0, h, w) -> np.ndarray:
        raise NotImplementedError

    def close(self):
        pass


@dataclass
class AlignedLayer(LayerBase):
    """GeoTIFF aligned with the global grid (gridded XYZ); read by window offset."""
    path: Path | None = None
    row_off: int = 0
    col_off: int = 0
    h: int = 0
    w: int = 0
    cache_path: Path | None = None    # point cache (for merged point-cloud output)
    keep_path: Path | None = None     # keep mask
    _ds: object = field(default=None, repr=False)

    def open(self, g: GridSpec):
        self._ds = rasterio.open(self.path)
        self.r_lo, self.c_lo = max(0, self.row_off), max(0, self.col_off)
        self.r_hi, self.c_hi = min(g.height, self.row_off + self.h), min(g.width, self.col_off + self.w)
        return self

    def read(self, r0, c0, h, w):
        out = np.full((h, w), np.nan, np.float32)
        rr0, rr1 = max(r0, self.row_off), min(r0 + h, self.row_off + self.h)
        cc0, cc1 = max(c0, self.col_off), min(c0 + w, self.col_off + self.w)
        if rr0 >= rr1 or cc0 >= cc1:
            return out
        win = Window(cc0 - self.col_off, rr0 - self.row_off, cc1 - cc0, rr1 - rr0)
        out[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0] = self._ds.read(1, window=win).astype(np.float32)
        return out

    def close(self):
        if self._ds is not None:
            self._ds.close()
            self._ds = None


@dataclass
class WarpLayer(LayerBase):
    """DEM (GeoTIFF / ASCII Grid) reprojected to the global grid through a WarpedVRT."""
    path: Path | None = None
    src_crs: object = None           # rasterio CRS
    resampling: str = "bilinear"
    extra_nodata: float | None = None
    _ds: object = field(default=None, repr=False)
    _vrt: object = field(default=None, repr=False)
    _nodata: float | None = None

    def open(self, g: GridSpec):
        self._ds = rasterio.open(self.path)
        self._nodata = self._ds.nodata
        kw = dict(crs=g.crs, transform=g.transform, width=g.width, height=g.height,
                  resampling=RESAMPLING[self.resampling], src_crs=self.src_crs)
        if self._nodata is not None:
            kw["nodata"] = self._nodata
        else:
            kw["add_alpha"] = True
        self._vrt = WarpedVRT(self._ds, **kw)
        b = transform_bounds(self.src_crs, g.crs, *self._ds.bounds, densify_pts=21)
        r0, c0, r1, c1 = g.rowcol_range(*b, margin=2)
        self.r_lo, self.c_lo = max(0, r0), max(0, c0)
        self.r_hi, self.c_hi = min(g.height, r1), min(g.width, c1)
        return self

    def read(self, r0, c0, h, w):
        win = Window(c0, r0, w, h)
        data = self._vrt.read(1, window=win).astype(np.float32)
        if self._nodata is not None:
            bad = data == np.float32(self._nodata)
            if np.isnan(self._nodata):
                bad = np.isnan(data)
        else:
            bad = self._vrt.read(self._vrt.count, window=win) == 0
        if self.extra_nodata is not None:
            bad |= data == np.float32(self.extra_nodata)
        data[bad] = np.nan
        return data

    def close(self):
        for o in (self._vrt, self._ds):
            if o is not None:
                o.close()
        self._vrt = self._ds = None
