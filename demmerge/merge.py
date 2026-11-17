"""Merge algorithms, overlap QC and vertical offset estimation."""
from __future__ import annotations

import warnings
from collections import defaultdict

import numpy as np
from scipy import ndimage

MERGE_METHODS = {
    "priority": "Newest on top (older data only fills gaps)",
    "feather": "Newest on top + feathered edges",
    "time_weighted": "Time-weighted mean (newer = heavier)",
    "mean": "Mean of overlaps",
    "median": "Median of overlaps",
    "min": "Minimum of overlaps",
    "max": "Maximum of overlaps",
}


def source_map(arrays, ids) -> np.ndarray:
    """Which layer supplies each cell (topmost with data). arrays are in priority order, top first."""
    src = np.zeros(arrays[0].shape, np.uint16)
    for a, i in zip(reversed(arrays), reversed(ids)):
        src[~np.isnan(a)] = i
    return src


def combine(arrays, ids, years, method, feather=10, tau=2.0):
    shape = arrays[0].shape
    src = source_map(arrays, ids)

    if method == "priority":
        out = np.full(shape, np.nan, np.float32)
        for a in reversed(arrays):
            v = ~np.isnan(a)
            out[v] = a[v]
        return out, src

    if method == "feather":
        out = np.full(shape, np.nan, np.float32)
        for a in reversed(arrays):
            v = ~np.isnan(a)
            if not v.any():
                continue
            both = v & ~np.isnan(out)
            if feather > 0 and both.any() and not v.all():
                dist = ndimage.distance_transform_edt(v)   # distance to this layer's edge
                wgt = np.clip(dist / feather, 0.0, 1.0).astype(np.float32)
                below = out[both]
                out[v] = a[v]
                out[both] = wgt[both] * a[both] + (1 - wgt[both]) * below
            else:
                out[v] = a[v]
        return out, src

    stack = np.stack(arrays)
    valid = ~np.isnan(stack)
    cnt = valid.sum(0)

    if method == "mean":
        s = np.where(valid, stack, 0).sum(0)
        with np.errstate(invalid="ignore", divide="ignore"):
            out = np.where(cnt > 0, s / np.maximum(cnt, 1), np.nan)
    elif method == "median":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            out = np.nanmedian(stack, axis=0)
    elif method == "min":
        out = np.fmin.reduce(stack, axis=0)
    elif method == "max":
        out = np.fmax.reduce(stack, axis=0)
    elif method == "time_weighted":
        known = [y for y in years if y is not None]
        tmax = max(known) if known else 0.0
        tmin = min(known) if known else 0.0
        w = np.array([np.exp(-((tmax - (y if y is not None else tmin - 1.0)) / max(tau, 1e-6)))
                      for y in years], np.float64)
        wv = valid * w[:, None, None]
        num = np.where(valid, stack, 0) * w[:, None, None]
        den = wv.sum(0)
        with np.errstate(invalid="ignore", divide="ignore"):
            out = np.where(den > 0, num.sum(0) / np.where(den > 0, den, 1), np.nan)
    else:
        raise ValueError(f"Unknown merge method: {method}")
    return out.astype(np.float32), src


class OverlapStats:
    """Collect height-difference samples of each layer vs the topmost layer above it with data. diff = reference - layer."""

    def __init__(self, cap_per_tile=20000, seed=0):
        self.pairs = defaultdict(list)
        self.cap = cap_per_tile
        self.rng = np.random.default_rng(seed)

    def add_tile(self, arrays, ids):
        if len(arrays) < 2:
            return
        stack = np.stack(arrays)
        valid = ~np.isnan(stack)
        anyv = valid.any(0)
        top = np.argmax(valid, axis=0)
        for k in range(1, len(arrays)):
            m = valid[k] & anyv & (top < k)
            if not m.any():
                continue
            rr, cc = np.nonzero(m)
            ref = top[rr, cc]
            d = stack[ref, rr, cc] - stack[k, rr, cc]
            for j in np.unique(ref):
                dj = d[ref == j]
                if len(dj) > self.cap:
                    dj = self.rng.choice(dj, self.cap, replace=False)
                self.pairs[(ids[k], ids[j])].append(dj.astype(np.float64))

    def finalize(self):
        """Return {(layer, ref): ndarray}."""
        return {k: np.concatenate(v) for k, v in self.pairs.items() if v}


def estimate_offsets(ordered_ids, pair_diffs):
    """Estimate offsets top-down: offset_i = Σ n_j (median_ij + offset_j) / Σ n_j."""
    off = {}
    for i in ordered_ids:
        num = den = 0.0
        for (a, j), d in pair_diffs.items():
            if a != i or len(d) == 0:
                continue
            num += len(d) * (float(np.median(d)) + off.get(j, 0.0))
            den += len(d)
        off[i] = num / den if den > 0 else 0.0
    return off


def pair_stats(d: np.ndarray) -> dict:
    return dict(n=len(d), mean=float(np.mean(d)), median=float(np.median(d)),
                std=float(np.std(d)), rms=float(np.sqrt(np.mean(d ** 2))),
                p05=float(np.percentile(d, 5)), p95=float(np.percentile(d, 95)))
