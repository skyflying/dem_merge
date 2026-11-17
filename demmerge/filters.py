"""File-name include / exclude filtering."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


# Auxiliary surfaces that are usually not depth/height grids (uncertainty, density, backscatter)
DEFAULT_EXCLUDE = "TVU; TVH; THU; Density; Backscatter"


def split_terms(text: str) -> list[str]:
    return [t.strip() for t in re.split(r"[;,|]", text or "") if t.strip()]


@dataclass
class NameFilter:
    include: str = ""          # e.g. "DTM;DEM"
    exclude: str = ""          # e.g. "TVH;THU"
    match_all: bool = False    # include: all terms must match (else any)
    case_sensitive: bool = False
    regex: bool = False        # terms are regular expressions
    whole_word: bool = False   # term must be bounded by non-alphanumerics (_ - . space)
    full_path: bool = False    # test against the full path instead of the file name

    def _compile(self, terms):
        flags = 0 if self.case_sensitive else re.IGNORECASE
        out = []
        for t in terms:
            pat = t if self.regex else re.escape(t)
            if self.whole_word:
                pat = rf"(?<![A-Za-z0-9]){pat}(?![A-Za-z0-9])"
            out.append(re.compile(pat, flags))
        return out

    def compile(self):
        self._inc = self._compile(split_terms(self.include))
        self._exc = self._compile(split_terms(self.exclude))
        return self

    @property
    def active(self) -> bool:
        return bool(split_terms(self.include) or split_terms(self.exclude))

    def accepts(self, path: Path) -> bool:
        if not hasattr(self, "_inc"):
            self.compile()
        text = str(path) if self.full_path else Path(path).name
        if self._exc and any(p.search(text) for p in self._exc):
            return False
        if not self._inc:
            return True
        hits = (p.search(text) for p in self._inc)
        return all(hits) if self.match_all else any(hits)
