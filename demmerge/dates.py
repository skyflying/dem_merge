"""Parse survey dates (timestamps) from file names."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

YEAR_MIN, YEAR_MAX = 1990, 2100

_SEP_RE = re.compile(r"(?<!\d)(\d{4})[-_.](\d{1,2})(?:[-_.](\d{1,2}))?(?!\d)")
_RUN_RE = re.compile(r"(?<!\d)(\d+)(?!\d)")


@dataclass(frozen=True)
class SurveyDate:
    year: int
    month: int | None = None
    day: int | None = None

    @property
    def precision(self) -> int:
        return 3 if self.day else 2 if self.month else 1

    def sort_key(self) -> tuple:
        # Year-only dates sort as the start of that year (2024 sorts before 202401)
        return (self.year, self.month or 0, self.day or 0)

    def decimal_year(self) -> float:
        m = (self.month or 1) - 1
        d = (self.day or 1) - 1
        return self.year + m / 12.0 + d / 365.25

    def __str__(self) -> str:
        if self.day:
            return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"
        if self.month:
            return f"{self.year:04d}-{self.month:02d}"
        return f"{self.year:04d}"


def _valid(y: int, m: int | None = None, d: int | None = None) -> bool:
    if not (YEAR_MIN <= y <= YEAR_MAX):
        return False
    if m is None:
        return True
    if not 1 <= m <= 12:
        return False
    if d is None:
        return True
    try:
        date(y, m, d)
        return True
    except ValueError:
        return False


def _candidates(text: str):
    out = []  # (precision, position, SurveyDate)
    for mt in _SEP_RE.finditer(text):
        y, m = int(mt.group(1)), int(mt.group(2))
        d = int(mt.group(3)) if mt.group(3) else None
        if _valid(y, m, d):
            out.append(SurveyDate(y, m, d))
    for mt in _RUN_RE.finditer(text):
        s = mt.group(1)
        n = len(s)
        if n in (8, 10, 12, 14):  # YYYYMMDD or YYYYMMDDhh(mm(ss))
            y, m, d = int(s[:4]), int(s[4:6]), int(s[6:8])
            if _valid(y, m, d):
                out.append(SurveyDate(y, m, d))
        elif n == 6:  # YYYYMM
            y, m = int(s[:4]), int(s[4:6])
            if _valid(y, m):
                out.append(SurveyDate(y, m))
        elif n == 4:  # YYYY
            y = int(s)
            if _valid(y):
                out.append(SurveyDate(y))
    return out


def parse_text(text: str) -> SurveyDate | None:
    """Pick the most precise date; on ties, the first occurrence in the string."""
    cands = _candidates(text)
    if not cands:
        return None
    best = max(c.precision for c in cands)
    return next(c for c in cands if c.precision == best)


def parse_path(path: Path, search_parents: bool = False, levels: int = 3) -> SurveyDate | None:
    d = parse_text(path.stem)
    if d is not None or not search_parents:
        return d
    p = path.parent
    for _ in range(levels):
        if not p.name:
            break
        d = parse_text(p.name)
        if d is not None:
            return d
        p = p.parent
    return None


def parse_user(text: str) -> SurveyDate | None:
    """Parse a date typed by the user. Empty -> None; invalid -> ValueError."""
    t = text.strip()
    if not t:
        return None
    m = re.fullmatch(r"(\d{4})(?:[-/._]?(\d{1,2})(?:[-/._]?(\d{1,2}))?)?", t)
    if not m:
        raise ValueError(f"Cannot parse date: {text}")
    y = int(m.group(1))
    mo = int(m.group(2)) if m.group(2) else None
    d = int(m.group(3)) if m.group(3) else None
    if not _valid(y, mo, d):
        raise ValueError(f"Invalid date: {text}")
    return SurveyDate(y, mo, d)
