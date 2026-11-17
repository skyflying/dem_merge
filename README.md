# DEM / XYZ Merge

Desktop tool that merges DEMs (GeoTIFF, ESRI ASCII Grid) and XYZ point files by survey date and writes merged GeoTIFF / XYZ / ASCII grid outputs.

## Install and run

```
pip install -r requirements.txt
python main.py
```

On Windows the rasterio wheel ships with GDAL, so nothing else is needed.

## Build the Windows .exe

**Option A – on a Windows PC** (Python 3.10–3.12, 64-bit): double-click `build_exe.bat`.
It creates a virtual environment, installs everything, builds with `DEMMerge.spec` and runs a self-test.
Result: `dist\DEMMerge\DEMMerge.exe`. Copy the whole `dist\DEMMerge` folder to other PCs; they do not need Python.
`build_exe.bat onefile` produces a single `dist\DEMMerge.exe` instead (easier to share, slower to start).

**Option B – GitHub Actions** (no Python needed locally): push this folder to a GitHub repository, open
*Actions → Build Windows exe → Run workflow*, and download the `DEMMerge-windows-x64` artifact when it finishes.
Pushing a tag such as `v1.2.0` also attaches the zip to a release.

**Self-test:** `DEMMerge.exe --selftest` checks GDAL, PROJ, scanning, filtering, merging and all outputs, and
writes `selftest.log` next to the exe. Unexpected errors in the GUI are saved to `%USERPROFILE%\DEMMerge_crash.log`.

## Workflow

1. **Add data.** Drag folders or files onto the list (or anywhere in the window), or use *Add folder* / *Add files*. Folders are scanned recursively for `.xyz .pts .txt .csv .asc .tif .tiff`. `.asc` files are detected as either ESRI ASCII Grid or text points. *Rescan* re-reads every added folder: new files are added, missing files removed, and your edits are kept.
2. **Filter by file name.** The exclude list defaults to `TVU; TVH; THU; Density; Backscatter` (press ↺ to restore it). *Include* keeps only names that contain one of the terms (e.g. `DTM; DEM`). *Exclude* hides names that contain any term (e.g. `TVH; THU`). Exclude wins over include. Toggles: `Aa` case-sensitive, `ab` whole word (term bounded by `_ - .` or space), `.*` regular expressions, `Path` match the full path. Hidden files are never merged.
3. **Check the order.** The list is sorted newest first; priority 1 is the top layer. Undated rows are highlighted. Double-click *Date* or *CRS* to edit. Reorder with ▲ ▼ (Ctrl+↑ / Ctrl+↓), Top, Bottom or the right-click menu. Files added after a manual reorder are appended at the bottom; *Sort by date* re-sorts everything.
4. **Configure** coordinates, input columns, merge method and outputs in the tabs on the right, then press *Run merge* (Ctrl+Enter).

## Date detection

Patterns tried: `YYYY-MM-DD` / `YYYY_MM_DD`, `YYYYMMDD` (optionally followed by time), `YYYYMM`, `YYYY`. Years must be 1990–2100 and month/day must be valid. When a name contains several dates, the most precise one wins; ties go to the first occurrence. A year-only date sorts as the start of that year (2024 before 202401).

## Processing (large data)

1. XYZ files are streamed in chunks, transformed and cached as binary.
2. Each XYZ file is gridded onto the global grid using disk-backed arrays (never fully loaded into RAM), with optional gap filling.
3. DEMs are reprojected on the fly with a GDAL WarpedVRT (no intermediate files).
4. Merging runs tile by tile (default 1024 × 1024) and writes a tiled GeoTIFF (BigTIFF when needed).

Temporary disk space is roughly 20 bytes per XYZ point plus the gridded XYZ layers.

## Merge methods

| Method | Description |
|---|---|
| Newest on top | The highest-priority layer with data wins; lower layers only fill gaps |
| Newest on top + feathered edges | Linear blend within the feather width inside the newer layer's edge |
| Time-weighted mean | weight = exp(−Δt/τ) |
| Mean / median / min / max | Statistics of overlapping layers |

*Vertical offset correction* shifts older layers by the median overlap difference to the layers above them (the newest layer is the reference).

## Outputs

- `prefix.tif` merged DEM
- `prefix_source.tif` source map: priority number of the layer used in each cell
- `prefix_grid.xyz` grid XYZ (cell centres)
- `prefix_points.xyz` merged point cloud: original points, minus those in cells taken over by a higher-priority layer; DEM-only areas can be filled with cell-centre points
- `prefix.asc` ESRI ASCII Grid
- `prefix_layers.csv` source table (date, CRS, vertical offset)
- `prefix_qc.csv` overlap statistics (n, mean, median, std, RMS, P5/P95)

## Notes

- Only horizontal coordinates are transformed; vertical datums are not.
- TWD67 ↔ TWD97 accuracy depends on the transformation PROJ selects and may be off by metres. Convert with official parameters first if you need high accuracy.
- For a geographic output CRS the resolution is in degrees, and XY decimals are raised to 8 automatically.
