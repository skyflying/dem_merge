"""Merge pipeline. Independent of the GUI; can be called from scripts."""
from __future__ import annotations

import csv
import math
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
from rasterio.windows import Window

from .crs_utils import parse_crs, short_name, to_rio
from .grid import GridSpec, grid_xyz, iter_tiles, tif_profile
from .layers import AlignedLayer, WarpLayer
from .merge import OverlapStats, combine, estimate_offsets, pair_stats
from .outputs import write_ascii_grid, write_grid_xyz, write_point_cloud
from .sources import SourceItem
from .xyzio import XYZReadOptions, cache_xyz

MAX_CELLS = 5e10


class Cancelled(Exception):
    pass


@dataclass
class JobConfig:
    sources: list[SourceItem]                 # priority order, top layer first (enabled only)
    default_crs: str = "EPSG:3826"
    target_crs: str = "EPSG:3826"
    resolution: float = 0.0                   # <=0: auto = finest DEM resolution
    extent: tuple | None = None               # (minx, miny, maxx, maxy); None = union
    tile_size: int = 1024
    xyz: XYZReadOptions = field(default_factory=XYZReadOptions)
    bin_method: str = "mean"
    fill_method: str = "none"
    fill_distance: int = 0
    dem_resampling: str = "bilinear"
    extra_nodata: float | None = None
    merge_method: str = "priority"
    feather_cells: int = 10
    tau_years: float = 2.0
    vertical_offset: bool = False
    qc: bool = True
    qc_stride: int = 1
    out_dir: str = "."
    prefix: str = "merged"
    out_tif: bool = True
    out_grid_xyz: bool = False
    out_points_xyz: bool = False
    points_include_dem: bool = True
    out_asc: bool = False
    out_srcmap: bool = True
    overviews: bool = True
    compress: str = "DEFLATE"
    nodata_out: float = -9999.0
    xy_decimals: int = 3
    z_decimals: int = 3
    delimiter: str = " "
    xyz_header: bool = False
    tmp_dir: str | None = None
    keep_tmp: bool = False


class MergeEngine:
    def __init__(self, cfg: JobConfig, log=print, progress=None, cancelled=None):
        self.cfg = cfg
        self._log = log
        self._progress = progress or (lambda pct, stage: None)
        self._cancelled = cancelled or (lambda: False)
        self._stage = ("", 0.0, 1.0)

    # ------------------------------------------------------------ helpers
    def log(self, msg):
        self._log(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def stage(self, name, start, end):
        self._stage = (name, start, end)
        self.sub(0.0)
        self.log(f"── {name}")

    def sub(self, frac):
        name, a, b = self._stage
        self._progress(int(100 * (a + (b - a) * min(max(frac, 0.0), 1.0))), name)

    def check(self):
        if self._cancelled():
            raise Cancelled()

    # ------------------------------------------------------------ pipeline
    def run(self) -> dict:
        cfg = self.cfg
        if not cfg.sources:
            raise ValueError("No files selected")
        out_dir = Path(cfg.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="demmerge_", dir=cfg.tmp_dir or out_dir))
        self.log(f"Temp folder: {tmp}")
        layers = []
        try:
            return self._run(tmp, out_dir, layers)
        except InterruptedError:
            raise Cancelled()
        finally:
            for L in layers:
                L.close()
            if not cfg.keep_tmp:
                shutil.rmtree(tmp, ignore_errors=True)

    def _run(self, tmp: Path, out_dir: Path, layers: list):
        cfg = self.cfg
        dst = parse_crs(cfg.target_crs)
        rio_dst = to_rio(dst)
        self.log(f"Output CRS: {short_name(dst)} {dst.name}")
        if dst.is_geographic and cfg.xy_decimals < 7:
            cfg.xy_decimals = 8
            self.log("Geographic output CRS: XY decimals set to 8")

        # 1. Read sources, cache XYZ as binary -----------------------------------
        self.stage("Reading sources", 0.0, 0.30)
        prepared = []   # dict(item, crs, bbox, cache, npts, native_res)
        total_mb = sum(max(s.size_mb, 0.01) for s in cfg.sources)
        done_mb = 0.0
        for s in cfg.sources:
            self.check()
            crs_txt = s.effective_crs(cfg.default_crs)
            if not crs_txt:
                raise ValueError(f"{s.path.name} has no CRS; set a default input CRS")
            scrs = parse_crs(crs_txt)
            w = max(s.size_mb, 0.01)
            if s.kind == "xyz":
                tr = None if scrs == dst else Transformer.from_crs(scrs, dst, always_xy=True)
                cache = tmp / f"pts_{len(prepared):04d}.bin"
                n, bbox = cache_xyz(s.path, cfg.xyz, tr, cache, self._cancelled,
                                    lambda f: self.sub((done_mb + f * w) / total_mb))
                if n == 0:
                    self.log(f"  ⚠ {s.path.name}: no valid points, skipped")
                else:
                    self.log(f"  {s.path.name}: {n:,} points ({short_name(scrs)})")
                    prepared.append(dict(item=s, crs=scrs, bbox=bbox, cache=cache, npts=n, res=None))
            else:
                with rasterio.open(s.path) as ds:
                    rio_src = to_rio(scrs)
                    bbox = transform_bounds(rio_src, rio_dst, *ds.bounds, densify_pts=21)
                    nres = min((bbox[2] - bbox[0]) / ds.width, (bbox[3] - bbox[1]) / ds.height)
                self.log(f"  {s.path.name}: DEM, ~{nres:.4g} units/cell ({short_name(scrs)})")
                prepared.append(dict(item=s, crs=scrs, bbox=bbox, cache=None, npts=0, res=nres))
            done_mb += w
            self.sub(done_mb / total_mb)
        if not prepared:
            raise ValueError("No valid data in any source")

        # 2. Build the global output grid ---------------------------------------------
        res = cfg.resolution
        if res <= 0:
            cands = [p["res"] for p in prepared if p["res"]]
            res = min(cands) if cands else 1.0
            self.log(f"Auto resolution: {res:.6g}")
        if cfg.extent:
            minx, miny, maxx, maxy = cfg.extent
        else:
            minx = min(p["bbox"][0] for p in prepared); miny = min(p["bbox"][1] for p in prepared)
            maxx = max(p["bbox"][2] for p in prepared); maxy = max(p["bbox"][3] for p in prepared)
        g = GridSpec.from_bounds(minx, miny, maxx, maxy, res, rio_dst)
        cells = g.width * g.height
        self.log(f"Output grid: {g.width:,} × {g.height:,} ({cells / 1e6:,.1f} M cells), resolution {res:g}")
        if cells > MAX_CELLS:
            raise ValueError("Output grid too large; use a coarser resolution or a smaller extent")

        # 3. Build layers (grid the XYZ files) ----------------------------------------
        self.stage("Gridding XYZ", 0.30, 0.50)
        nxyz = sum(1 for p in prepared if p["cache"]) or 1
        k = 0
        for rank, p in enumerate(prepared, start=1):
            self.check()
            s: SourceItem = p["item"]
            year = s.date.decimal_year() if s.date else None
            if p["cache"]:
                tif = tmp / f"layer_{rank:04d}.tif"
                ro, co, h, w = grid_xyz(p["cache"], p["bbox"], g, cfg.bin_method, cfg.fill_method,
                                        cfg.fill_distance, tif, tmp, "DEFLATE", cfg.tile_size,
                                        self._cancelled, lambda f: self.sub((k + f) / nxyz))
                L = AlignedLayer(id=rank, name=s.path.name, kind="xyz", year=year, path=tif,
                                 row_off=ro, col_off=co, h=h, w=w, cache_path=p["cache"])
                k += 1
            else:
                L = WarpLayer(id=rank, name=s.path.name, kind="dem", year=year, path=s.path,
                              src_crs=to_rio(p["crs"]), resampling=cfg.dem_resampling,
                              extra_nodata=cfg.extra_nodata)
            layers.append(L.open(g))
        ids = [L.id for L in layers]

        # 4. Overlap QC / vertical offsets ------------------------------------------
        qc_rows, pair_diffs = [], {}
        if cfg.qc or cfg.vertical_offset:
            self.stage("Overlap QC", 0.50, 0.60)
            stats = OverlapStats()
            ts = cfg.tile_size
            st = max(1, cfg.qc_stride)
            tiles = [t for t in iter_tiles(g.height, g.width, ts)
                     if (t[0] // ts) % st == 0 and (t[1] // ts) % st == 0]
            for n, (r0, c0, h, w) in enumerate(tiles):
                self.check()
                act = [L for L in layers if L.intersects(r0, c0, r0 + h, c0 + w)]
                if len(act) >= 2:
                    stats.add_tile([L.read(r0, c0, h, w) for L in act], [L.id for L in act])
                self.sub((n + 1) / len(tiles))
            pair_diffs = stats.finalize()
            if cfg.vertical_offset:
                offs = estimate_offsets(ids, pair_diffs)
                for L in layers:
                    L.offset = offs.get(L.id, 0.0)
                    if L.offset:
                        self.log(f"  Vertical offset: #{L.id} {L.name} {L.offset:+.3f}")
            byid = {L.id: L for L in layers}
            for (a, b), d in sorted(pair_diffs.items()):
                st_ = pair_stats(d)
                qc_rows.append(dict(layer_id=a, layer=byid[a].name, ref_id=b, ref=byid[b].name,
                                    **st_, applied_offset=byid[a].offset))
                self.log(f"  #{a} vs #{b}: n={st_['n']:,} mean diff {st_['mean']:+.3f} RMS {st_['rms']:.3f}")

        # 5. Tiled merge -----------------------------------------------------
        self.stage("Merging tiles", 0.60, 0.88)
        merged_tif = out_dir / f"{cfg.prefix}.tif" if cfg.out_tif else tmp / "merged.tif"
        need_src = cfg.out_srcmap or cfg.out_points_xyz
        src_tif = (out_dir / f"{cfg.prefix}_source.tif") if cfg.out_srcmap else tmp / "source.tif"
        xyz_layers = [L for L in layers if L.kind == "xyz"]
        keeps = {}
        if cfg.out_points_xyz:
            for L in xyz_layers:
                L.keep_path = tmp / f"keep_{L.id:04d}.dat"
                keeps[L.id] = np.memmap(L.keep_path, dtype=np.uint8, mode="w+", shape=(L.h, L.w))

        nd = cfg.nodata_out
        prof = tif_profile(g.width, g.height, g.crs, g.transform, "float32", nd, cfg.compress)
        sprof = tif_profile(g.width, g.height, g.crs, g.transform, "uint16", 0, cfg.compress)
        halo = cfg.feather_cells if cfg.merge_method == "feather" else 0
        tiles = list(iter_tiles(g.height, g.width, cfg.tile_size))
        valid_cells = 0
        with rasterio.open(merged_tif, "w", **prof) as dz, \
                (rasterio.open(src_tif, "w", **sprof) if need_src else _Null()) as dsrc:
            for n, (r0, c0, h, w) in enumerate(tiles):
                self.check()
                win = Window(c0, r0, w, h)
                act = [L for L in layers if L.intersects(r0, c0, r0 + h, c0 + w)]
                if not act:
                    dz.write(np.full((h, w), nd, np.float32), 1, window=win)
                    if need_src:
                        dsrc.write(np.zeros((h, w), np.uint16), 1, window=win)
                    self.sub((n + 1) / len(tiles))
                    continue
                hr0, hc0 = max(0, r0 - halo), max(0, c0 - halo)
                hr1, hc1 = min(g.height, r0 + h + halo), min(g.width, c0 + w + halo)
                arrs = []
                for L in act:
                    a = L.read(hr0, hc0, hr1 - hr0, hc1 - hc0)
                    if L.offset:
                        a += np.float32(L.offset)
                    arrs.append(a)
                out, src = combine(arrs, [L.id for L in act], [L.year for L in act],
                                   cfg.merge_method, cfg.feather_cells, cfg.tau_years)
                sl = (slice(r0 - hr0, r0 - hr0 + h), slice(c0 - hc0, c0 - hc0 + w))
                out, src = out[sl], src[sl]
                good = ~np.isnan(out)
                valid_cells += int(good.sum())
                dz.write(np.where(good, out, nd).astype(np.float32), 1, window=win)
                if need_src:
                    dsrc.write(src, 1, window=win)
                for L in act:
                    if L.id in keeps:
                        rr0, rr1 = max(r0, L.row_off), min(r0 + h, L.row_off + L.h)
                        cc0, cc1 = max(c0, L.col_off), min(c0 + w, L.col_off + L.w)
                        if rr0 < rr1 and cc0 < cc1:
                            keeps[L.id][rr0 - L.row_off:rr1 - L.row_off, cc0 - L.col_off:cc1 - L.col_off] = \
                                src[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0] == L.id
                self.sub((n + 1) / len(tiles))
        for m in keeps.values():
            m.flush()
        keeps.clear()
        self.log(f"Merge done: {valid_cells:,} / {g.width * g.height:,} valid cells")

        if cfg.overviews:
            for path, rs in ((merged_tif, Resampling.average), (src_tif, Resampling.nearest)):
                if (path == merged_tif and cfg.out_tif) or (path == src_tif and cfg.out_srcmap):
                    _build_overviews(path, rs)

        # 6. Other outputs -----------------------------------------------------
        self.stage("Writing outputs", 0.88, 1.0)
        result = dict(grid=g, merged_tif=str(merged_tif) if cfg.out_tif else None,
                      source_tif=str(src_tif) if cfg.out_srcmap else None, files=[])
        if cfg.out_tif:
            result["files"].append(str(merged_tif))
        if cfg.out_srcmap:
            result["files"].append(str(src_tif))
        jobs = [j for j in ("grid", "asc", "pts") if getattr(cfg, {"grid": "out_grid_xyz", "asc": "out_asc",
                                                                  "pts": "out_points_xyz"}[j])]
        for jn, j in enumerate(jobs):
            prog = lambda f, jn=jn: self.sub((jn + f) / len(jobs))
            if j == "grid":
                p = out_dir / f"{cfg.prefix}_grid.xyz"
                n = write_grid_xyz(merged_tif, p, cfg.delimiter, cfg.xy_decimals, cfg.z_decimals,
                                   cfg.xyz_header, self._cancelled, prog)
                self.log(f"Grid XYZ: {p.name} ({n:,} points)")
            elif j == "asc":
                p = out_dir / f"{cfg.prefix}.asc"
                write_ascii_grid(merged_tif, p, nd, cfg.z_decimals, self._cancelled, prog)
                self.log(f"ESRI ASCII Grid: {p.name}")
            else:
                p = out_dir / f"{cfg.prefix}_points.xyz"
                dem_ids = [L.id for L in layers if L.kind == "dem"]
                n = write_point_cloud(xyz_layers, dem_ids, merged_tif, src_tif, g, p, cfg.delimiter,
                                      cfg.xy_decimals, cfg.z_decimals, cfg.xyz_header,
                                      cfg.points_include_dem, self._cancelled, prog, self.log)
                self.log(f"Merged point cloud: {p.name} ({n:,} points)")
            result["files"].append(str(p))

        # Source table and QC report
        lp = out_dir / f"{cfg.prefix}_layers.csv"
        with open(lp, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.writer(f)
            wr.writerow(["source_id(priority)", "file", "type", "date", "input_crs", "vertical_offset", "path"])
            for L, p in zip(layers, prepared):
                s = p["item"]
                wr.writerow([L.id, L.name, L.kind, str(s.date or ""), short_name(p["crs"]),
                             f"{L.offset:.4f}", str(s.path)])
        result["files"].append(str(lp))
        if qc_rows:
            qp = out_dir / f"{cfg.prefix}_qc.csv"
            with open(qp, "w", newline="", encoding="utf-8-sig") as f:
                wr = csv.DictWriter(f, fieldnames=list(qc_rows[0].keys()))
                wr.writeheader()
                wr.writerows(qc_rows)
            result["files"].append(str(qp))
        # Low-res preview (read before temp files are deleted)
        result["preview_arrays"] = _preview_arrays(merged_tif, src_tif if need_src else None)
        self.sub(1.0)
        self.log("All done")
        return result


class _Null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _build_overviews(path, rs):
    with rasterio.open(path, "r+") as ds:
        f = [2 ** i for i in range(1, 12) if max(ds.width, ds.height) / 2 ** i >= 256]
        if f:
            ds.build_overviews(f, rs)
            ds.update_tags(ns="rio_overview", resampling=rs.name)


def read_preview(tif, src=None, max_px=1200):
    """Read a downsampled DEM and source map for the GUI preview."""
    out = {}
    with rasterio.open(tif) as ds:
        sc = max(ds.width, ds.height) / max_px
        sc = max(sc, 1.0)
        shp = (max(1, int(ds.height / sc)), max(1, int(ds.width / sc)))
        z = ds.read(1, out_shape=shp, resampling=Resampling.average if sc > 1 else Resampling.nearest)
        z = z.astype(np.float32)
        if ds.nodata is not None:
            z[z == ds.nodata] = np.nan
        out["z"] = z
        b = ds.bounds
        out["extent"] = (b.left, b.right, b.bottom, b.top)
    if src:
        with rasterio.open(src) as ds:
            out["src"] = ds.read(1, out_shape=shp, resampling=Resampling.nearest)
    return out


def _preview_arrays(tif, src):
    try:
        return read_preview(tif, src)
    except Exception:
        return None
