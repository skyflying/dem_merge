"""Folder scanning, file type and CRS detection."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .crs_utils import short_name
from .dates import SurveyDate, parse_path

DEFAULT_EXTS = ".xyz;.pts;.txt;.csv;.asc;.tif;.tiff"
RASTER_EXTS = {".tif", ".tiff"}


@dataclass
class SourceItem:
    path: Path
    kind: str          # 'dem' or 'xyz'
    fmt: str           # 'GeoTIFF' / 'ASCII Grid' / 'XYZ text'
    date: SurveyDate | None = None
    file_crs: str | None = None     # CRS from the file itself (or its .prj)
    crs_override: str | None = None  # set by the user
    enabled: bool = True
    info: str = ""                   # resolution / size info
    size_mb: float = 0.0

    def effective_crs(self, default: str | None) -> str | None:
        return self.crs_override or self.file_crs or default


def _is_ascii_grid(path: Path) -> bool:
    try:
        with open(path, "r", errors="ignore") as f:
            head = f.read(512).lower()
        return head.lstrip().startswith("ncols") or "\nncols" in head[:200]
    except OSError:
        return False


def _prj_crs(path: Path) -> str | None:
    prj = path.with_suffix(".prj")
    if not prj.exists():
        return None
    try:
        from pyproj import CRS
        c = CRS.from_wkt(prj.read_text(errors="ignore"))
        return short_name(c) if c.to_epsg() else c.to_wkt()
    except Exception:
        return None


def _probe_raster(item: SourceItem) -> None:
    import rasterio
    with rasterio.open(item.path) as ds:
        if ds.crs:
            item.file_crs = short_name(ds.crs) if ds.crs.to_epsg() else ds.crs.to_wkt()
        rx, ry = ds.res
        item.info = f"{ds.width}×{ds.height}, res {rx:g}"
    if item.file_crs is None:
        item.file_crs = _prj_crs(item.path)


def _wanted(exts: str) -> set[str]:
    out = set()
    for e in exts.replace(",", ";").split(";"):
        e = e.strip().lower()
        if e:
            out.add(e if e.startswith(".") else "." + e)
    return out


def collect_files(paths, exts: str = DEFAULT_EXTS, recursive: bool = True) -> list[Path]:
    """Expand a mix of folders and files into a sorted, de-duplicated file list."""
    wanted = _wanted(exts)
    seen, files = set(), []

    def add(p: Path):
        k = str(p.resolve()).lower()
        if k not in seen:
            seen.add(k)
            files.append(p)

    for p in map(Path, paths):
        if p.is_dir():
            if recursive:
                for dp, _dn, fn in os.walk(p):
                    for f in fn:
                        if Path(f).suffix.lower() in wanted:
                            add(Path(dp) / f)
            else:
                for q in p.iterdir():
                    if q.is_file() and q.suffix.lower() in wanted:
                        add(q)
        elif p.is_file() and p.suffix.lower() in wanted:
            add(p)
    files.sort(key=lambda q: str(q).lower())
    return files


def probe_file(p: Path, search_parents: bool = False) -> SourceItem:
    ext = p.suffix.lower()
    size_mb = p.stat().st_size / 1e6
    if ext in RASTER_EXTS:
        it = SourceItem(p, "dem", "GeoTIFF")
        _probe_raster(it)
    elif ext == ".asc" and _is_ascii_grid(p):
        it = SourceItem(p, "dem", "ASCII Grid")
        _probe_raster(it)
    else:
        it = SourceItem(p, "xyz", "XYZ text", file_crs=_prj_crs(p))
        it.info = f"{size_mb:,.1f} MB"
    it.size_mb = size_mb
    it.date = parse_path(p, search_parents)
    return it


def scan_paths(paths, exts: str = DEFAULT_EXTS, recursive: bool = True,
               search_parents: bool = False, progress_cb=None, cancelled=None, skip=None):
    """Scan folders and/or files. `skip` is a set of resolved lower-case paths already loaded."""
    files = collect_files(paths, exts, recursive)
    if skip:
        files = [f for f in files if str(f.resolve()).lower() not in skip]
    items, errors = [], []
    for i, p in enumerate(files):
        if cancelled and cancelled():
            break
        if progress_cb:
            progress_cb(i + 1, len(files))
        try:
            items.append(probe_file(p, search_parents))
        except Exception as e:  # unreadable file
            errors.append(f"{p}: {e}")
    return items, errors


def scan_folder(root, exts: str = DEFAULT_EXTS, recursive: bool = True,
                search_parents: bool = False, progress_cb=None, cancelled=None):
    return scan_paths([root], exts, recursive, search_parents, progress_cb, cancelled)


def path_key(p: Path) -> str:
    return str(Path(p).resolve()).lower()


def sort_by_date(items: list[SourceItem]) -> list[SourceItem]:
    """Newest first (priority 1); undated files last."""
    dated = [i for i in items if i.date is not None]
    undated = [i for i in items if i.date is None]
    dated.sort(key=lambda i: (i.date.sort_key(), str(i.path).lower()), reverse=True)
    undated.sort(key=lambda i: str(i.path).lower())
    return dated + undated
