"""PySide6 user interface."""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import (QAbstractTableModel, QItemSelectionModel, QModelIndex, QRectF, QSettings, Qt,
                            QThread, QTimer, Signal)
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QSplitter, QTableView, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from . import __version__, theme
from .crs_utils import describe, normalize_text, parse_crs, preset_labels
from .dates import parse_user
from .engine import Cancelled, JobConfig, MergeEngine
from .filters import DEFAULT_EXCLUDE, NameFilter
from .merge import MERGE_METHODS
from .sources import DEFAULT_EXTS, SourceItem, collect_files, path_key, scan_paths, sort_by_date
from .xyzio import XYZReadOptions

try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
    HAS_MPL = True
except Exception:  # pragma: no cover
    HAS_MPL = False

BIN_METHODS = {"mean": "Mean", "min": "Minimum (deepest)", "max": "Maximum (shoalest)",
               "nearest": "Point nearest the cell centre"}
FILL_METHODS = {"none": "No gap filling", "idw": "IDW (inverse distance)", "nearest": "Nearest neighbour",
                "tin": "TIN (linear)"}
RESAMPLE = {"bilinear": "Bilinear", "nearest": "Nearest", "cubic": "Cubic", "average": "Average"}
DELIMS = {"Space": " ", "Comma ,": ",", "Tab": "\t", "Semicolon ;": ";"}
METHOD_HELP = {
    "priority": "Cells take the value of the highest-priority layer that has data. Lower layers only fill gaps.",
    "feather": "Like 'newest on top', but within the feather width inside the newer layer's edge the two "
               "layers are blended linearly, removing seam steps.",
    "time_weighted": "Weighted mean of all overlapping layers; weight = exp(-Δt/τ). Undated layers are "
                     "treated as one year older than the oldest dated layer.",
    "mean": "Mean of all overlapping layers.",
    "median": "Median of overlapping layers; robust against a single bad survey.",
    "min": "Lowest value (deepest). Conservative for clearance analysis.",
    "max": "Highest value (shoalest). Conservative for navigation safety.",
}

COL_ON, COL_PRI, COL_TYPE, COL_DATE, COL_CRS, COL_NAME, COL_DIR, COL_INFO = range(8)
HEADERS = ["", "#", "Type", "Date", "CRS", "File", "Folder", "Info"]


# ====================================================================== model
class SourceModel(QAbstractTableModel):
    error = Signal(str)
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.items: list[SourceItem] = []
        self.view: list[int] = []
        self.rank: dict[int, int] = {}
        self.filter = NameFilter().compile()
        self.root: str | None = None
        self.colors = theme.THEMES["light"]

    # ---- structure
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.view)

    def columnCount(self, parent=QModelIndex()):
        return len(HEADERS)

    def headerData(self, s, o, role=Qt.DisplayRole):
        if o == Qt.Horizontal and role == Qt.DisplayRole:
            return HEADERS[s]
        if o == Qt.Horizontal and role == Qt.ToolTipRole:
            return {COL_ON: "Include in merge", COL_PRI: "Priority (1 = top layer)",
                    COL_DATE: "Survey date (double-click to edit)",
                    COL_CRS: "Input CRS (double-click to edit; empty = file CRS or default)"}.get(s)
        return None

    def item_at(self, row) -> SourceItem:
        return self.items[self.view[row]]

    def _recompute(self):
        self.view = [i for i, it in enumerate(self.items) if self.filter.accepts(it.path)]
        self.rank = {}
        k = 0
        for i in self.view:
            if self.items[i].enabled:
                k += 1
                self.rank[i] = k
        parents = [str(it.path.parent) for it in self.items]
        try:
            self.root = os.path.commonpath(parents) if parents else None
        except ValueError:
            self.root = None

    def refresh(self):
        self.beginResetModel()
        self._recompute()
        self.endResetModel()
        self.changed.emit()

    def soft_refresh(self):
        self._recompute()
        if self.view:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.view) - 1, len(HEADERS) - 1))
        self.changed.emit()

    # ---- data
    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        gi = self.view[idx.row()]
        it = self.items[gi]
        c = idx.column()
        if role == Qt.CheckStateRole and c == COL_ON:
            return Qt.Checked if it.enabled else Qt.Unchecked
        if role in (Qt.DisplayRole, Qt.EditRole):
            if c == COL_PRI:
                return str(self.rank[gi]) if gi in self.rank else "–"
            if c == COL_TYPE:
                return "DEM" if it.kind == "dem" else "XYZ"
            if c == COL_DATE:
                return str(it.date) if it.date else ("" if role == Qt.EditRole else "—")
            if c == COL_CRS:
                return it.crs_override or it.file_crs or ("" if role == Qt.EditRole else "default")
            if c == COL_NAME:
                return it.path.name
            if c == COL_DIR:
                if self.root:
                    try:
                        rel = os.path.relpath(it.path.parent, self.root)
                        return "." if rel == "." else rel
                    except ValueError:
                        pass
                return str(it.path.parent)
            if c == COL_INFO:
                return f"{it.fmt} · {it.info}"
        if role == Qt.ToolTipRole:
            if c == COL_NAME:
                return f"{it.path}\n{it.fmt} · {it.info}"
            if c == COL_CRS:
                return ("Set manually" if it.crs_override else
                        "From file" if it.file_crs else "Uses the default input CRS")
            if c == COL_DATE and not it.date:
                return "No date found in the name. Double-click to enter one (20240315, 2024-03, 2024)."
        if role == Qt.ForegroundRole:
            if not it.enabled:
                return QColor(self.colors["muted"])
            if c == COL_CRS and it.crs_override:
                return QColor(self.colors["accent"])
            if (c == COL_CRS and not it.file_crs and not it.crs_override) or (c == COL_DATE and not it.date):
                return QColor(self.colors["muted"])
            if c == COL_PRI:
                return QColor(self.colors["accent"])
        if role == Qt.BackgroundRole and not it.date:
            return QColor(self.colors["warn"])
        if role == Qt.FontRole and c in (COL_PRI, COL_NAME):
            f = QFont()
            f.setBold(c == COL_PRI)
            return f
        if role == Qt.TextAlignmentRole and c in (COL_PRI, COL_TYPE, COL_DATE):
            return int(Qt.AlignCenter)
        return None

    def flags(self, idx):
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if idx.column() == COL_ON:
            f |= Qt.ItemIsUserCheckable
        if idx.column() in (COL_DATE, COL_CRS):
            f |= Qt.ItemIsEditable
        return f

    def setData(self, idx, value, role=Qt.EditRole):
        if not idx.isValid():
            return False
        it = self.item_at(idx.row())
        c = idx.column()
        if c == COL_ON and role == Qt.CheckStateRole:
            v = value.value if hasattr(value, "value") else value
            it.enabled = v == Qt.Checked.value
            self.soft_refresh()
            return True
        if role != Qt.EditRole:
            return False
        text = str(value or "").strip()
        if c == COL_DATE:
            try:
                it.date = parse_user(text)
            except ValueError as e:
                self.error.emit(str(e))
                return False
        elif c == COL_CRS:
            if not text or text.lower() == "default":
                it.crs_override = None
            elif text != (it.crs_override or it.file_crs or ""):
                try:
                    parse_crs(text)
                except Exception:
                    self.error.emit(f"Unrecognised CRS: {text}")
                    return False
                it.crs_override = normalize_text(text)
        else:
            return False
        self.soft_refresh()
        return True

    # ---- operations (rows are visible-row indices)
    def keys(self):
        return {path_key(it.path) for it in self.items}

    def add(self, new_items, resort: bool):
        have = self.keys()
        new = [i for i in new_items if path_key(i.path) not in have]
        self.items.extend(new)
        if resort:
            self.items = sort_by_date(self.items)
        self.refresh()
        return len(new)

    def retain(self, keys: set):
        before = len(self.items)
        self.items = [it for it in self.items if path_key(it.path) in keys]
        self.refresh()
        return before - len(self.items)

    def clear(self):
        self.items = []
        self.refresh()

    def set_filter(self, f: NameFilter):
        self.filter = f.compile()
        self.refresh()

    def _reorder_visible(self, new_visible_order):
        slots = list(self.view)
        objs = [self.items[i] for i in new_visible_order]
        for s, o in zip(slots, objs):
            self.items[s] = o
        self.refresh()

    def move(self, rows, delta):
        order = list(self.view)
        sel = set(rows)
        for r in (sorted(rows) if delta < 0 else sorted(rows, reverse=True)):
            t = r + delta
            if 0 <= t < len(order) and t not in sel:
                order[t], order[r] = order[r], order[t]
                sel.discard(r)
                sel.add(t)
        self._reorder_visible(order)
        return sorted(sel)

    def move_end(self, rows, top):
        rs = set(rows)
        picked = [self.view[r] for r in sorted(rows)]
        rest = [g for r, g in enumerate(self.view) if r not in rs]
        self._reorder_visible(picked + rest if top else rest + picked)
        n = len(picked)
        return list(range(n)) if top else list(range(len(self.view) - n, len(self.view)))

    def reverse(self):
        self._reorder_visible(list(reversed(self.view)))

    def sort_date(self):
        self.items = sort_by_date(self.items)
        self.refresh()

    def remove(self, rows):
        drop = {self.view[r] for r in rows}
        self.items = [it for i, it in enumerate(self.items) if i not in drop]
        self.refresh()

    def set_enabled(self, pred):
        for i in self.view:
            self.items[i].enabled = bool(pred(i, self.items[i]))
        self.soft_refresh()

    def set_crs(self, rows, val):
        for r in rows:
            self.item_at(r).crs_override = val
        self.soft_refresh()

    def visible_enabled(self):
        return [self.items[i] for i in self.view if self.items[i].enabled]


# ====================================================================== drop-enabled table
class DropTable(QTableView):
    pathsDropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self._drag = False
        self.colors = theme.THEMES["light"]

    @staticmethod
    def _paths(e):
        md = e.mimeData()
        if not md.hasUrls():
            return []
        return [u.toLocalFile() for u in md.urls() if u.isLocalFile()]

    def dragEnterEvent(self, e):
        if self._paths(e):
            self._drag = True
            self.viewport().update()
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if self._drag:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self._drag = False
        self.viewport().update()

    def dropEvent(self, e):
        paths = self._paths(e)
        self._drag = False
        self.viewport().update()
        if paths:
            e.acceptProposedAction()
            self.pathsDropped.emit(paths)

    def paintEvent(self, e):
        super().paintEvent(e)
        empty = self.model() is None or self.model().rowCount() == 0
        if not (empty or self._drag):
            return
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.viewport().rect()).adjusted(10, 10, -10, -10)
        col = QColor(self.colors["accent"] if self._drag else self.colors["border2"])
        pen = QPen(col, 2, Qt.DashLine)
        p.setPen(pen)
        if self._drag:
            fill = QColor(self.colors["accent_soft"])
            fill.setAlpha(170)
            p.setBrush(fill)
        p.drawRoundedRect(r, 14, 14)
        p.setPen(QColor(self.colors["accent"] if self._drag else self.colors["muted"]))
        f = QFont(self.font())
        f.setPixelSize(17)
        f.setBold(True)
        p.setFont(f)
        msg = "Release to add" if self._drag else "Drop folders or files here"
        p.drawText(r.adjusted(0, -24, 0, -24), Qt.AlignCenter, msg)
        if not self._drag:
            f.setBold(False)
            f.setPixelSize(13)
            p.setFont(f)
            p.drawText(r.adjusted(0, 24, 0, 24), Qt.AlignCenter,
                       "or use “Add folder” / “Add files”.  .xyz .pts .txt .csv .asc .tif")
        p.end()


# ====================================================================== threads
class ScanThread(QThread):
    progress = Signal(int, int)
    result = Signal(object, object, object)

    def __init__(self, paths, exts, recursive, parents, skip):
        super().__init__()
        self.args = (paths, exts, recursive, parents, skip)
        self._cancel = False

    def run(self):
        paths, exts, rec, par, skip = self.args
        keys = {path_key(p) for p in collect_files(paths, exts, rec)}
        items, errs = scan_paths(paths, exts, rec, par, lambda i, n: self.progress.emit(i, n),
                                 lambda: self._cancel, skip)
        self.result.emit(items, errs, keys)


class JobThread(QThread):
    log = Signal(str)
    progress = Signal(int, str)
    done = Signal(bool, str, object)

    def __init__(self, cfg: JobConfig):
        super().__init__()
        self.cfg = cfg
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            res = MergeEngine(self.cfg, log=self.log.emit, progress=lambda p, s: self.progress.emit(p, s),
                              cancelled=lambda: self._cancel).run()
            self.done.emit(True, "Done", res)
        except Cancelled:
            self.done.emit(False, "Cancelled", None)
        except Exception as e:
            self.log.emit(traceback.format_exc())
            self.done.emit(False, f"Error: {e}", None)


# ====================================================================== helpers
def card(title: str | None = None, right: QWidget | None = None):
    fr = QFrame()
    fr.setObjectName("card")
    v = QVBoxLayout(fr)
    v.setContentsMargins(16, 14, 16, 14)
    v.setSpacing(10)
    if title:
        h = QHBoxLayout()
        lb = QLabel(title)
        lb.setObjectName("cardTitle")
        h.addWidget(lb)
        h.addStretch()
        if right is not None:
            h.addWidget(right)
        v.addLayout(h)
    return fr, v


def btn(text, slot=None, tip=None, primary=False, ghost=False, checkable=False, tool=False):
    b = QToolButton() if tool else QPushButton()
    b.setText(text)
    if tip:
        b.setToolTip(tip)
    if primary:
        b.setProperty("primary", True)
    if ghost:
        b.setProperty("ghost", True)
    if checkable:
        b.setCheckable(True)
    if slot:
        b.clicked.connect(slot)
    b.setCursor(Qt.PointingHandCursor)
    return b


def hint(text):
    lb = QLabel(text)
    lb.setWordWrap(True)
    lb.setProperty("muted", True)
    return lb


def scrollable(w: QWidget):
    sa = QScrollArea()
    sa.setWidget(w)
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")
    return sa


# ====================================================================== main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"DEM / XYZ Merge  v{__version__}")
        self.resize(1480, 920)
        self.setAcceptDrops(True)
        self.settings = QSettings("DEMMerge", "DEMMerge")
        self.model = SourceModel()
        self.model.error.connect(lambda m: QMessageBox.warning(self, "Invalid value", m))
        self.model.changed.connect(self._update_status)
        self.added_paths: list[str] = []
        self.manual_order = False
        self.scan_thread = None
        self.job = None
        self._pending_rescan = False
        self._preview = None
        self.theme_name = self.settings.value("theme", "light")
        self._filter_timer = QTimer(self, singleShot=True, interval=250)
        self._filter_timer.timeout.connect(self._apply_filter)
        self._build()
        self._load_settings()
        self._apply_theme(self.theme_name)
        self._update_status()

    # ------------------------------------------------------------ layout
    def _build(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(12)

        # header
        hdr = QHBoxLayout()
        tv = QVBoxLayout()
        tv.setSpacing(0)
        t = QLabel("DEM / XYZ Merge")
        t.setObjectName("title")
        st = QLabel("Merge terrain & bathymetry grids and point clouds by survey date")
        st.setObjectName("subtitle")
        tv.addWidget(t)
        tv.addWidget(st)
        hdr.addLayout(tv)
        hdr.addStretch()
        self.b_theme = btn("", self._toggle_theme, "Switch light / dark theme", ghost=True)
        hdr.addWidget(self.b_theme)
        outer.addLayout(hdr)

        vsplit = QSplitter(Qt.Vertical)
        vsplit.setChildrenCollapsible(False)
        hsplit = QSplitter(Qt.Horizontal)
        hsplit.setChildrenCollapsible(False)
        hsplit.setHandleWidth(12)
        hsplit.addWidget(self._sources_card())
        hsplit.addWidget(self._settings_card())
        hsplit.setStretchFactor(0, 3)
        hsplit.setStretchFactor(1, 2)
        hsplit.setSizes([880, 560])
        vsplit.addWidget(hsplit)

        # log
        self.log_card, lv = card()
        lv.setContentsMargins(10, 8, 10, 8)
        self.logbox = QPlainTextEdit()
        self.logbox.setObjectName("log")
        self.logbox.setReadOnly(True)
        self.logbox.setMaximumBlockCount(10000)
        lv.addWidget(self.logbox)
        vsplit.addWidget(self.log_card)
        vsplit.setStretchFactor(0, 4)
        vsplit.setStretchFactor(1, 1)
        vsplit.setSizes([700, 170])
        outer.addWidget(vsplit, 1)

        # footer
        foot = QHBoxLayout()
        foot.setSpacing(10)
        self.b_log = btn("Hide log", self._toggle_log, ghost=True)
        self.progress = QProgressBar()
        self.progress.setFormat("Ready")
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.lbl_stage = QLabel("")
        self.lbl_stage.setObjectName("status")
        self.b_cancel = btn("Cancel", self._cancel)
        self.b_cancel.setEnabled(False)
        self.b_run = btn("Run merge  ▶", self._run, primary=True)
        foot.addWidget(self.b_log)
        foot.addWidget(self.progress, 1)
        foot.addWidget(self.b_cancel)
        foot.addWidget(self.b_run)
        outer.addLayout(foot)

        QShortcut(QKeySequence("Ctrl+Up"), self, activated=lambda: self._move(-1))
        QShortcut(QKeySequence("Ctrl+Down"), self, activated=lambda: self._move(1))
        QShortcut(QKeySequence.Delete, self.table, activated=self._remove)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self._add_folder)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._run)

    def _sources_card(self):
        self.lbl_chip = QLabel("0 files")
        self.lbl_chip.setObjectName("chip")
        fr, v = card("Sources", self.lbl_chip)

        # add / scan row
        r1 = QHBoxLayout()
        r1.setSpacing(6)
        r1.addWidget(btn("+  Add folder", self._add_folder, "Scan a folder (Ctrl+O). You can also drag folders in."))
        r1.addWidget(btn("+  Add files", self._add_files))
        self.b_rescan = btn("⟳ Rescan", self._rescan, "Rescan all added folders: new files are added, "
                            "missing ones removed, your edits are kept")
        r1.addWidget(self.b_rescan)
        r1.addWidget(btn("Remove", self._remove, "Remove selected rows (Del)", ghost=True))
        r1.addWidget(btn("Clear", self._clear, ghost=True))
        r1.addStretch()
        self.cb_recursive = QCheckBox("Subfolders")
        self.cb_recursive.setChecked(True)
        self.cb_parents = QCheckBox("Date from parent folder")
        self.cb_parents.setToolTip("If a file name has no date, look in up to three parent folder names")
        r1.addWidget(self.cb_recursive)
        r1.addWidget(self.cb_parents)
        v.addLayout(r1)

        r1b = QHBoxLayout()
        r1b.setSpacing(6)
        lb = QLabel("Extensions")
        lb.setProperty("muted", True)
        r1b.addWidget(lb)
        self.ed_exts = QLineEdit(DEFAULT_EXTS)
        self.ed_exts.setToolTip("File extensions to pick up, separated by ;")
        r1b.addWidget(self.ed_exts, 1)
        v.addLayout(r1b)

        # filter row
        fl = QHBoxLayout()
        fl.setSpacing(6)
        self.ed_inc = QLineEdit()
        self.ed_inc.setPlaceholderText("Include: e.g. DTM; DEM")
        self.ed_inc.setClearButtonEnabled(True)
        self.ed_inc.setToolTip("Keep only files whose name contains these terms (separate with ; , or |)")
        self.ed_exc = QLineEdit()
        self.ed_exc.setPlaceholderText(f"Exclude: e.g. {DEFAULT_EXCLUDE}")
        self.ed_exc.setClearButtonEnabled(True)
        self.ed_exc.setToolTip("Hide files whose name contains any of these terms. Exclude wins over include.")
        self.cb_match = QComboBox()
        self.cb_match.addItems(["Any term", "All terms"])
        self.cb_match.setToolTip("Include rule: match any or all of the include terms")
        self.t_case = btn("Aa", tip="Case sensitive", checkable=True, tool=True)
        self.t_word = btn("ab", tip="Whole word (term must be separated by _ - . or space)", checkable=True,
                          tool=True)
        self.t_regex = btn(".*", tip="Terms are regular expressions", checkable=True, tool=True)
        self.t_path = btn("Path", tip="Match against the full path instead of the file name", checkable=True,
                          tool=True)
        fl.addWidget(QLabel("Filter"))
        fl.addWidget(self.ed_inc, 3)
        fl.addWidget(self.ed_exc, 3)
        self.b_exc_reset = btn("↺", lambda: self.ed_exc.setText(DEFAULT_EXCLUDE),
                               f"Restore default exclude list: {DEFAULT_EXCLUDE}", tool=True)
        fl.addWidget(self.b_exc_reset)
        fl.addWidget(self.cb_match)
        for t in (self.t_case, self.t_word, self.t_regex, self.t_path):
            fl.addWidget(t)
        for w in (self.ed_inc, self.ed_exc):
            w.textChanged.connect(lambda _=None: self._filter_timer.start())
        self.cb_match.currentIndexChanged.connect(lambda _=None: self._filter_timer.start())
        for t in (self.t_case, self.t_word, self.t_regex, self.t_path):
            t.toggled.connect(lambda _=None: self._filter_timer.start())
        v.addLayout(fl)

        # table
        self.table = DropTable()
        self.table.setModel(self.model)
        self.table.pathsDropped.connect(self._add_paths)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        hh = self.table.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        widths = {COL_ON: 34, COL_PRI: 40, COL_TYPE: 52, COL_DATE: 92, COL_CRS: 110, COL_NAME: 240, COL_DIR: 150}
        for c, w in widths.items():
            hh.resizeSection(c, w)
        hh.setSectionResizeMode(COL_ON, QHeaderView.Fixed)
        hh.setStretchLastSection(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        v.addWidget(self.table, 1)

        # order / selection row
        r3 = QHBoxLayout()
        r3.setSpacing(6)
        r3.addWidget(btn("Sort by date", self._sort_date, "Newest first; undated last"))
        r3.addWidget(btn("▲", lambda: self._move(-1), "Move up (Ctrl+↑)", tool=True))
        r3.addWidget(btn("▼", lambda: self._move(1), "Move down (Ctrl+↓)", tool=True))
        r3.addWidget(btn("⤒ Top", lambda: self._move_end(True), tool=True))
        r3.addWidget(btn("⤓ Bottom", lambda: self._move_end(False), tool=True))
        r3.addWidget(btn("Reverse", self._reverse, tool=True))
        r3.addStretch()
        b_sel = btn("Check  ▾", tool=True)
        b_sel.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(b_sel)
        m.addAction("Check all", lambda: self.model.set_enabled(lambda i, it: True))
        m.addAction("Uncheck all", lambda: self.model.set_enabled(lambda i, it: False))
        m.addAction("Check highlighted only", self._check_selected)
        m.addSeparator()
        m.addAction("Only DEM", lambda: self.model.set_enabled(lambda i, it: it.kind == "dem"))
        m.addAction("Only XYZ", lambda: self.model.set_enabled(lambda i, it: it.kind == "xyz"))
        m.addAction("Only dated", lambda: self.model.set_enabled(lambda i, it: it.date is not None))
        b_sel.setMenu(m)
        r3.addWidget(b_sel)
        r3.addWidget(btn("Set CRS…", self._set_crs_selected, "Set the input CRS for highlighted rows", tool=True))
        v.addLayout(r3)

        self.lbl_status = QLabel()
        self.lbl_status.setObjectName("status")
        v.addWidget(self.lbl_status)
        return fr

    def _settings_card(self):
        fr, v = card("Settings")
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(scrollable(self._tab_crs()), "Coordinates")
        self.tabs.addTab(scrollable(self._tab_input()), "Input")
        self.tabs.addTab(scrollable(self._tab_merge()), "Merge")
        self.tabs.addTab(scrollable(self._tab_output()), "Output")
        self.tabs.addTab(self._tab_preview(), "Preview")
        v.addWidget(self.tabs)
        return fr

    def _crs_combo(self):
        cb = QComboBox()
        cb.setEditable(True)
        cb.addItems(preset_labels())
        cb.setToolTip("Pick a preset or type an EPSG code (e.g. 3826), PROJ string or WKT")
        cb.setMinimumContentsLength(24)
        return cb

    def _tab_crs(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(2, 6, 2, 2)
        g = QGroupBox("Coordinate reference systems")
        f = QFormLayout(g)
        f.setLabelAlignment(Qt.AlignRight)
        self.cb_in_crs = self._crs_combo()
        self.cb_out_crs = self._crs_combo()
        self.lbl_in_crs, self.lbl_out_crs = hint(""), hint("")
        for cb, lb in ((self.cb_in_crs, self.lbl_in_crs), (self.cb_out_crs, self.lbl_out_crs)):
            cb.currentTextChanged.connect(lambda t, lb=lb: lb.setText(describe(t)))
            lb.setText(describe(cb.currentText()))
        f.addRow("Default input CRS", self.cb_in_crs)
        f.addRow("", self.lbl_in_crs)
        f.addRow("Output CRS", self.cb_out_crs)
        f.addRow("", self.lbl_out_crs)
        v.addWidget(g)
        v.addWidget(hint(
            "• GeoTIFFs and files with a .prj sidecar use their own CRS automatically.\n"
            "• Files without a CRS (most XYZ files, ASCII grids without .prj) use the default input CRS.\n"
            "• Override per file by double-clicking the CRS column, or highlight rows and use “Set CRS…”.\n"
            "• Only horizontal coordinates are transformed. Vertical datums are not; use the vertical "
            "offset correction on the Merge tab if needed.\n"
            "• For a geographic output CRS the resolution is in degrees (0.00001° ≈ 1 m)."))
        v.addStretch()
        return w

    def _tab_input(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(2, 6, 2, 2)
        g1 = QGroupBox("XYZ text columns")
        f = QFormLayout(g1)
        f.setLabelAlignment(Qt.AlignRight)
        self.sp_xc, self.sp_yc, self.sp_zc = QSpinBox(), QSpinBox(), QSpinBox()
        row = QHBoxLayout()
        for t, sp, d in (("X", self.sp_xc, 1), ("Y", self.sp_yc, 2), ("Z", self.sp_zc, 3)):
            sp.setRange(1, 50)
            sp.setValue(d)
            row.addWidget(QLabel(t))
            row.addWidget(sp)
        row.addStretch()
        f.addRow("Column numbers", row)
        self.sp_zscale = QDoubleSpinBox()
        self.sp_zscale.setRange(-1000, 1000)
        self.sp_zscale.setDecimals(4)
        self.sp_zscale.setValue(1.0)
        self.sp_zscale.setToolTip("Z is multiplied by this factor. Use -1 to turn positive-down depths into elevations")
        f.addRow("Z scale", self.sp_zscale)
        self.ed_nodata_in = QLineEdit()
        self.ed_nodata_in.setPlaceholderText("e.g. -9999 (empty = none)")
        self.ed_nodata_in.setToolTip("Extra value treated as no-data in XYZ and DEM inputs")
        f.addRow("Extra no-data value", self.ed_nodata_in)
        f.addRow(hint("Delimiter (space, tab, comma, semicolon) is detected automatically; header lines and the "
                      ".pts point-count line are skipped."))
        v.addWidget(g1)

        g2 = QGroupBox("XYZ gridding (points → grid)")
        f2 = QFormLayout(g2)
        f2.setLabelAlignment(Qt.AlignRight)
        self.cb_bin = QComboBox()
        for k, t in BIN_METHODS.items():
            self.cb_bin.addItem(t, k)
        f2.addRow("Cell statistic", self.cb_bin)
        self.cb_fill = QComboBox()
        for k, t in FILL_METHODS.items():
            self.cb_fill.addItem(t, k)
        f2.addRow("Gap filling", self.cb_fill)
        self.sp_fill = QSpinBox()
        self.sp_fill.setRange(0, 500)
        self.sp_fill.setValue(3)
        self.sp_fill.setSuffix(" cells")
        self.sp_fill.setToolTip("Only fill empty cells within this distance of data, to avoid over-extrapolation")
        f2.addRow("Max fill distance", self.sp_fill)
        v.addWidget(g2)

        g3 = QGroupBox("DEM resampling")
        f3 = QFormLayout(g3)
        f3.setLabelAlignment(Qt.AlignRight)
        self.cb_resamp = QComboBox()
        for k, t in RESAMPLE.items():
            self.cb_resamp.addItem(t, k)
        f3.addRow("Method", self.cb_resamp)
        v.addWidget(g3)
        v.addStretch()
        return w

    def _tab_merge(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(2, 6, 2, 2)
        g1 = QGroupBox("Merge algorithm")
        f = QFormLayout(g1)
        f.setLabelAlignment(Qt.AlignRight)
        self.cb_method = QComboBox()
        for k, t in MERGE_METHODS.items():
            self.cb_method.addItem(t, k)
        self.cb_method.currentIndexChanged.connect(self._method_changed)
        f.addRow("Method", self.cb_method)
        self.lbl_method = hint("")
        f.addRow("", self.lbl_method)
        self.sp_feather = QSpinBox()
        self.sp_feather.setRange(1, 1000)
        self.sp_feather.setValue(10)
        self.sp_feather.setSuffix(" cells")
        f.addRow("Feather width", self.sp_feather)
        self.sp_tau = QDoubleSpinBox()
        self.sp_tau.setRange(0.05, 100)
        self.sp_tau.setValue(2.0)
        self.sp_tau.setSuffix(" years")
        self.sp_tau.setToolTip("weight = exp(-(newest date - layer date) / τ)")
        f.addRow("Time decay τ", self.sp_tau)
        v.addWidget(g1)

        g2 = QGroupBox("Vertical consistency")
        f2 = QFormLayout(g2)
        f2.setLabelAlignment(Qt.AlignRight)
        self.cb_offset = QCheckBox("Apply vertical offset correction")
        self.cb_offset.setToolTip("Shift each older layer by the median height difference to the layers above it "
                                  "(newest layer is the reference)")
        f2.addRow(self.cb_offset)
        self.cb_qc = QCheckBox("Write overlap QC report (CSV)")
        self.cb_qc.setChecked(True)
        f2.addRow(self.cb_qc)
        self.sp_stride = QSpinBox()
        self.sp_stride.setRange(1, 50)
        self.sp_stride.setValue(1)
        self.sp_stride.setToolTip("Sample every Nth tile for QC; increase for very large datasets")
        f2.addRow("QC tile sampling", self.sp_stride)
        v.addWidget(g2)

        g3 = QGroupBox("Output grid")
        f3 = QFormLayout(g3)
        f3.setLabelAlignment(Qt.AlignRight)
        self.sp_res = QDoubleSpinBox()
        self.sp_res.setDecimals(8)
        self.sp_res.setRange(0, 1e6)
        self.sp_res.setValue(0)
        self.sp_res.setSpecialValueText("Auto (finest DEM)")
        f3.addRow("Resolution", self.sp_res)
        self.cb_extent = QCheckBox("Custom extent (output CRS)")
        f3.addRow(self.cb_extent)
        self.ed_ext = [QLineEdit() for _ in range(4)]
        eg = QGridLayout()
        for i, (t, e) in enumerate(zip(("X min", "Y min", "X max", "Y max"), self.ed_ext)):
            e.setEnabled(False)
            e.setPlaceholderText(t)
            eg.addWidget(e, i // 2, i % 2)
        self.cb_extent.toggled.connect(lambda on: [e.setEnabled(on) for e in self.ed_ext])
        f3.addRow("", eg)
        self.sp_tile = QSpinBox()
        self.sp_tile.setRange(256, 8192)
        self.sp_tile.setSingleStep(256)
        self.sp_tile.setValue(1024)
        self.sp_tile.setSuffix(" px")
        self.sp_tile.setToolTip("Processing tile size; reduce if memory is short")
        f3.addRow("Tile size", self.sp_tile)
        v.addWidget(g3)
        v.addStretch()
        self._method_changed()
        return w

    def _tab_output(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(2, 6, 2, 2)
        g0 = QGroupBox("Destination")
        f0 = QFormLayout(g0)
        f0.setLabelAlignment(Qt.AlignRight)
        r = QHBoxLayout()
        self.ed_out = QLineEdit()
        r.addWidget(self.ed_out, 1)
        r.addWidget(btn("Browse…", lambda: self._pick_dir(self.ed_out)))
        f0.addRow("Folder", r)
        self.ed_prefix = QLineEdit("merged")
        f0.addRow("File prefix", self.ed_prefix)
        v.addWidget(g0)

        g1 = QGroupBox("Products")
        f1 = QVBoxLayout(g1)
        self.cb_o_tif = QCheckBox("Merged DEM (GeoTIFF)")
        self.cb_o_tif.setChecked(True)
        self.cb_o_grid = QCheckBox("Grid XYZ (one point per cell centre)")
        self.cb_o_pts = QCheckBox("Merged point cloud XYZ (original points; older points under newer data removed)")
        self.cb_o_pts_dem = QCheckBox("Fill DEM-only areas of the point cloud with cell-centre points")
        self.cb_o_pts_dem.setChecked(True)
        self.cb_o_pts_dem.setStyleSheet("margin-left: 22px;")
        self.cb_o_pts.toggled.connect(self.cb_o_pts_dem.setEnabled)
        self.cb_o_pts_dem.setEnabled(False)
        self.cb_o_asc = QCheckBox("ESRI ASCII Grid (.asc)")
        self.cb_o_src = QCheckBox("Source map GeoTIFF (priority number of the layer used in each cell)")
        self.cb_o_src.setChecked(True)
        self.cb_o_ovr = QCheckBox("Build overviews (faster display in GIS)")
        self.cb_o_ovr.setChecked(True)
        for c in (self.cb_o_tif, self.cb_o_grid, self.cb_o_pts, self.cb_o_pts_dem, self.cb_o_asc,
                  self.cb_o_src, self.cb_o_ovr):
            f1.addWidget(c)
        v.addWidget(g1)

        g2 = QGroupBox("Format")
        f2 = QFormLayout(g2)
        f2.setLabelAlignment(Qt.AlignRight)
        self.cb_compress = QComboBox()
        self.cb_compress.addItems(["DEFLATE", "LZW", "ZSTD", "NONE"])
        f2.addRow("GeoTIFF compression", self.cb_compress)
        self.sp_nodata = QDoubleSpinBox()
        self.sp_nodata.setRange(-1e9, 1e9)
        self.sp_nodata.setValue(-9999)
        f2.addRow("Output no-data", self.sp_nodata)
        self.cb_delim = QComboBox()
        self.cb_delim.addItems(list(DELIMS))
        f2.addRow("XYZ delimiter", self.cb_delim)
        self.sp_xyd, self.sp_zd = QSpinBox(), QSpinBox()
        self.sp_xyd.setRange(0, 12)
        self.sp_xyd.setValue(3)
        self.sp_zd.setRange(0, 8)
        self.sp_zd.setValue(3)
        dr = QHBoxLayout()
        dr.addWidget(QLabel("XY"))
        dr.addWidget(self.sp_xyd)
        dr.addWidget(QLabel("Z"))
        dr.addWidget(self.sp_zd)
        dr.addStretch()
        f2.addRow("Decimals", dr)
        self.cb_header = QCheckBox("Header line in XYZ (X Y Z)")
        f2.addRow("", self.cb_header)
        v.addWidget(g2)

        g3 = QGroupBox("Temporary files")
        f3 = QFormLayout(g3)
        f3.setLabelAlignment(Qt.AlignRight)
        r3 = QHBoxLayout()
        self.ed_tmp = QLineEdit()
        self.ed_tmp.setPlaceholderText("Empty = inside the output folder")
        r3.addWidget(self.ed_tmp, 1)
        r3.addWidget(btn("Browse…", lambda: self._pick_dir(self.ed_tmp)))
        f3.addRow("Temp folder", r3)
        self.cb_keep_tmp = QCheckBox("Keep temporary files (debugging)")
        f3.addRow("", self.cb_keep_tmp)
        f3.addRow(hint("Needs roughly 20 bytes per XYZ point plus the gridded XYZ layers."))
        v.addWidget(g3)
        v.addStretch()
        return w

    def _tab_preview(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(2, 6, 2, 2)
        if HAS_MPL:
            self.fig = Figure(figsize=(6, 6))
            self.canvas = FigureCanvasQTAgg(self.fig)
            self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            v.addWidget(self.canvas)
            self.preview_hint = hint("The merged surface and the source map appear here after a run.")
            v.addWidget(self.preview_hint)
        else:
            v.addWidget(hint("matplotlib is not installed; preview unavailable."))
        return w

    # ------------------------------------------------------------ theme
    def _apply_theme(self, name):
        self.theme_name = name
        c = theme.apply(QApplication.instance(), name)
        self.model.colors = c
        self.table.colors = c
        self.model.soft_refresh()
        self.table.viewport().update()
        self.b_theme.setText("☀  Light" if name == "dark" else "☾  Dark")
        if self._preview:
            self._draw_preview()

    def _toggle_theme(self):
        self._apply_theme("dark" if self.theme_name == "light" else "light")

    def _toggle_log(self):
        vis = not self.log_card.isVisible()
        self.log_card.setVisible(vis)
        self.b_log.setText("Hide log" if vis else "Show log")

    # ------------------------------------------------------------ source list
    def _sel_rows(self):
        return sorted({i.row() for i in self.table.selectionModel().selectedRows()})

    def _reselect(self, rows):
        self.table.clearSelection()
        sm = self.table.selectionModel()
        for r in rows:
            sm.select(self.model.index(r, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        if rows:
            self.table.scrollTo(self.model.index(rows[0], 0))

    def _update_status(self):
        m = self.model
        n_all = len(m.items)
        shown = [m.items[i] for i in m.view]
        on = [it for it in shown if it.enabled]
        n_dem = sum(1 for it in on if it.kind == "dem")
        hidden = n_all - len(shown)
        undated = sum(1 for it in on if not it.date)
        parts = [f"{len(shown)} shown", f"{len(on)} checked (DEM {n_dem} · XYZ {len(on) - n_dem})"]
        if hidden:
            parts.append(f"{hidden} hidden by filter")
        if undated:
            parts.append(f"{undated} undated (highlighted)")
        self.lbl_status.setText("  ·  ".join(parts) + "    —  priority 1 = top layer" if n_all else
                                "No files yet. Drag folders or files onto the list.")
        self.lbl_chip.setText(f"{len(on)} / {n_all} files")
        self.table.viewport().update()

    def _apply_filter(self):
        f = NameFilter(self.ed_inc.text(), self.ed_exc.text(), self.cb_match.currentIndex() == 1,
                       self.t_case.isChecked(), self.t_regex.isChecked(), self.t_word.isChecked(),
                       self.t_path.isChecked())
        try:
            f.compile()
        except Exception as e:
            self.ed_inc.setToolTip(f"Invalid pattern: {e}")
            return
        self.model.set_filter(f)

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Add folder", self.settings.value("last_dir", ""))
        if d:
            self.settings.setValue("last_dir", d)
            self._add_paths([d])

    def _add_files(self):
        exts = " ".join(f"*{e.strip()}" for e in self.ed_exts.text().split(";") if e.strip())
        files, _ = QFileDialog.getOpenFileNames(self, "Add files", self.settings.value("last_dir", ""),
                                                f"Survey data ({exts});;All files (*)")
        if files:
            self.settings.setValue("last_dir", str(Path(files[0]).parent))
            self._add_paths(files)

    def _add_paths(self, paths, rescan=False):
        if self.scan_thread and self.scan_thread.isRunning():
            QMessageBox.information(self, "Busy", "A scan is already running.")
            return
        for p in paths:
            if p not in self.added_paths:
                self.added_paths.append(p)
        if not self.ed_out.text().strip() and paths:
            p = Path(paths[0])
            self.ed_out.setText(str((p if p.is_dir() else p.parent) / "merged_output"))
        self._pending_rescan = rescan
        self.progress.setFormat("Scanning %v / %m")
        self.scan_thread = ScanThread(list(paths), self.ed_exts.text(), self.cb_recursive.isChecked(),
                                      self.cb_parents.isChecked(), self.model.keys())
        self.scan_thread.progress.connect(lambda i, n: (self.progress.setMaximum(n), self.progress.setValue(i)))
        self.scan_thread.result.connect(self._scan_done)
        self.b_rescan.setEnabled(False)
        self.scan_thread.start()

    def _scan_done(self, items, errs, keys):
        self.b_rescan.setEnabled(True)
        self.progress.setMaximum(100)
        self.progress.setValue(0)
        self.progress.setFormat("Ready")
        removed = self.model.retain(keys) if self._pending_rescan else 0
        resort = not self.manual_order
        n = self.model.add(items, resort)
        msg = f"Added {n} file(s)"
        if removed:
            msg += f", removed {removed} missing"
        if n and not resort:
            msg += " — appended at the bottom to keep your manual order (use “Sort by date” to re-sort)"
        self._log(msg)
        for e in errs:
            self._log(f"⚠ Could not read: {e}")

    def _rescan(self):
        if not self.added_paths:
            QMessageBox.information(self, "Rescan", "Nothing to rescan yet. Add a folder first.")
            return
        self._add_paths([p for p in self.added_paths if Path(p).exists()], rescan=True)

    def _remove(self):
        rows = self._sel_rows()
        if rows:
            self.model.remove(rows)

    def _clear(self):
        self.model.clear()
        self.added_paths = []
        self.manual_order = False

    def _sort_date(self):
        self.model.sort_date()
        self.manual_order = False

    def _move(self, delta):
        rows = self._sel_rows()
        if rows:
            self._reselect(self.model.move(rows, delta))
            self.manual_order = True

    def _move_end(self, top):
        rows = self._sel_rows()
        if rows:
            self._reselect(self.model.move_end(rows, top))
            self.manual_order = True

    def _reverse(self):
        self.model.reverse()
        self.manual_order = True

    def _check_selected(self):
        rows = set(self._sel_rows())
        vis_index = {g: r for r, g in enumerate(self.model.view)}
        self.model.set_enabled(lambda i, it: vis_index.get(i) in rows)

    def _set_crs_selected(self):
        rows = self._sel_rows()
        if not rows:
            QMessageBox.information(self, "Set CRS", "Highlight one or more rows first.")
            return
        labels = ["(clear – use file CRS or default)"] + preset_labels()
        t, ok = QInputDialog.getItem(self, "Set CRS", f"CRS for {len(rows)} file(s) (or type an EPSG code):",
                                     labels, 1, True)
        if not ok:
            return
        val = None
        if not t.startswith("(clear"):
            try:
                parse_crs(t)
                val = normalize_text(t)
            except Exception:
                QMessageBox.warning(self, "Set CRS", f"Unrecognised CRS: {t}")
                return
        self.model.set_crs(rows, val)
        self._reselect(rows)

    def _context_menu(self, pos):
        m = QMenu(self)
        m.addAction("Move to top", lambda: self._move_end(True))
        m.addAction("Move to bottom", lambda: self._move_end(False))
        m.addSeparator()
        m.addAction("Set CRS…", self._set_crs_selected)
        m.addAction("Check highlighted only", self._check_selected)
        m.addSeparator()
        rows = self._sel_rows()
        if len(rows) == 1:
            path = self.model.item_at(rows[0]).path
            m.addAction("Copy path", lambda: QApplication.clipboard().setText(str(path)))
            m.addAction("Open containing folder", lambda: self._open_folder(path.parent))
        m.addAction("Remove", self._remove)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _open_folder(self, p):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    # window-level drop (anywhere in the window)
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self._add_paths(paths)

    # ------------------------------------------------------------ run
    def _pick_dir(self, edit):
        d = QFileDialog.getExistingDirectory(self, "Select folder", edit.text() or "")
        if d:
            edit.setText(d)

    def _method_changed(self):
        m = self.cb_method.currentData()
        self.sp_feather.setEnabled(m == "feather")
        self.sp_tau.setEnabled(m == "time_weighted")
        self.lbl_method.setText(METHOD_HELP.get(m, ""))

    def _collect(self) -> JobConfig:
        src = self.model.visible_enabled()
        if not src:
            raise ValueError("No files are checked (or all are hidden by the filter).")
        out = self.ed_out.text().strip()
        if not out:
            raise ValueError("Choose an output folder on the Output tab.")
        try:
            parse_crs(self.cb_out_crs.currentText())
        except Exception:
            raise ValueError("The output CRS is not valid.")
        if any(not (s.crs_override or s.file_crs) for s in src):
            try:
                parse_crs(self.cb_in_crs.currentText())
            except Exception:
                raise ValueError("Some files have no CRS and the default input CRS is not valid.")
        extent = None
        if self.cb_extent.isChecked():
            try:
                extent = tuple(float(e.text()) for e in self.ed_ext)
            except ValueError:
                raise ValueError("Custom extent: enter four numbers.")
            if extent[0] >= extent[2] or extent[1] >= extent[3]:
                raise ValueError("Custom extent: min must be smaller than max.")
        if not any(c.isChecked() for c in (self.cb_o_tif, self.cb_o_grid, self.cb_o_pts, self.cb_o_asc,
                                           self.cb_o_src)):
            raise ValueError("Select at least one output product.")
        t = self.ed_nodata_in.text().strip()
        try:
            nod = float(t) if t else None
        except ValueError:
            raise ValueError("Extra no-data value must be a number.")
        return JobConfig(
            sources=src,
            default_crs=normalize_text(self.cb_in_crs.currentText()),
            target_crs=normalize_text(self.cb_out_crs.currentText()),
            resolution=self.sp_res.value(), extent=extent, tile_size=self.sp_tile.value(),
            xyz=XYZReadOptions(self.sp_xc.value() - 1, self.sp_yc.value() - 1, self.sp_zc.value() - 1,
                               self.sp_zscale.value(), nod),
            bin_method=self.cb_bin.currentData(), fill_method=self.cb_fill.currentData(),
            fill_distance=self.sp_fill.value(), dem_resampling=self.cb_resamp.currentData(),
            extra_nodata=nod, merge_method=self.cb_method.currentData(),
            feather_cells=self.sp_feather.value(), tau_years=self.sp_tau.value(),
            vertical_offset=self.cb_offset.isChecked(), qc=self.cb_qc.isChecked(),
            qc_stride=self.sp_stride.value(), out_dir=out,
            prefix=self.ed_prefix.text().strip() or "merged",
            out_tif=self.cb_o_tif.isChecked(), out_grid_xyz=self.cb_o_grid.isChecked(),
            out_points_xyz=self.cb_o_pts.isChecked(), points_include_dem=self.cb_o_pts_dem.isChecked(),
            out_asc=self.cb_o_asc.isChecked(), out_srcmap=self.cb_o_src.isChecked(),
            overviews=self.cb_o_ovr.isChecked(), compress=self.cb_compress.currentText(),
            nodata_out=self.sp_nodata.value(), xy_decimals=self.sp_xyd.value(),
            z_decimals=self.sp_zd.value(), delimiter=DELIMS[self.cb_delim.currentText()],
            xyz_header=self.cb_header.isChecked(), tmp_dir=self.ed_tmp.text().strip() or None,
            keep_tmp=self.cb_keep_tmp.isChecked(),
        )

    def _run(self):
        if self.job and self.job.isRunning():
            return
        try:
            cfg = self._collect()
        except Exception as e:
            QMessageBox.warning(self, "Check settings", str(e))
            return
        self._save_settings()
        self.logbox.clear()
        if not self.log_card.isVisible():
            self._toggle_log()
        self._log(f"Merging {len(cfg.sources)} file(s) — {self.cb_method.currentText()}")
        hidden = len(self.model.items) - len(self.model.view)
        if hidden:
            self._log(f"  ({hidden} file(s) excluded by the name filter)")
        for i, s in enumerate(cfg.sources, 1):
            self._log(f"  #{i:<3} {s.path.name}   [{s.date or 'no date'}]")
        self.b_run.setEnabled(False)
        self.b_cancel.setEnabled(True)
        self.job = JobThread(cfg)
        self.job.log.connect(self._log)
        self.job.progress.connect(self._on_progress)
        self.job.done.connect(self._done)
        self.job.start()

    def _cancel(self):
        if self.job:
            self.job.cancel()
            self._log("Cancelling…")

    def _on_progress(self, pct, stage):
        self.progress.setMaximum(100)
        self.progress.setValue(pct)
        self.progress.setFormat(f"{stage}  ·  %p%")

    def _done(self, ok, msg, res):
        self.b_run.setEnabled(True)
        self.b_cancel.setEnabled(False)
        if ok:
            self.progress.setValue(100)
            self.progress.setFormat("Done  ·  100%")
            self._preview = res.get("preview_arrays")
            self._draw_preview()
            files = "\n".join("• " + Path(f).name for f in res.get("files", []))
            box = QMessageBox(self)
            box.setWindowTitle("Merge complete")
            box.setText(f"Output written to:\n{self.ed_out.text()}\n\n{files}")
            b_open = box.addButton("Open folder", QMessageBox.ActionRole)
            box.addButton(QMessageBox.Ok)
            box.exec()
            if box.clickedButton() is b_open:
                self._open_folder(self.ed_out.text())
        else:
            self.progress.setFormat(msg)
            self._log(msg)
            if msg != "Cancelled":
                QMessageBox.critical(self, "Merge failed", msg)

    def _draw_preview(self):
        if not HAS_MPL or not self._preview:
            return
        import numpy as np
        c = theme.THEMES[self.theme_name]
        p = self._preview
        self.fig.clear()
        self.fig.set_facecolor(c["surface"])
        n = 2 if "src" in p else 1
        axes = [self.fig.add_subplot(n, 1, 1)]
        im = axes[0].imshow(p["z"], extent=p["extent"], cmap="viridis", interpolation="nearest")
        cb = self.fig.colorbar(im, ax=axes[0], shrink=0.85)
        cb.set_label("Z", color=c["muted"])
        cbs = [cb]
        axes[0].set_title("Merged surface", color=c["text"], fontsize=11, loc="left")
        if n == 2:
            ax2 = self.fig.add_subplot(2, 1, 2, sharex=axes[0], sharey=axes[0])
            s = np.ma.masked_equal(p["src"].astype(float), 0)
            im2 = ax2.imshow(s, extent=p["extent"], cmap="tab20", interpolation="nearest")
            cb2 = self.fig.colorbar(im2, ax=ax2, shrink=0.85)
            cb2.set_label("Source priority #", color=c["muted"])
            cbs.append(cb2)
            ax2.set_title("Source map", color=c["text"], fontsize=11, loc="left")
            axes.append(ax2)
        for a in axes:
            a.set_facecolor(c["alt"])
            a.ticklabel_format(useOffset=False, style="plain")
            a.tick_params(labelsize=8, colors=c["muted"])
            for sp in a.spines.values():
                sp.set_color(c["border2"])
        for cb in cbs:
            cb.ax.tick_params(labelsize=8, colors=c["muted"])
            cb.outline.set_edgecolor(c["border2"])
        self.fig.tight_layout()
        self.canvas.draw()
        self.preview_hint.hide()
        self.tabs.setCurrentIndex(self.tabs.count() - 1)

    def _log(self, s):
        self.logbox.appendPlainText(s)

    # ------------------------------------------------------------ settings
    def _save_settings(self):
        st = self.settings
        for k, w in (("out", self.ed_out), ("exts", self.ed_exts), ("inc", self.ed_inc), ("exclude_terms", self.ed_exc),
                     ("prefix", self.ed_prefix)):
            st.setValue(k, w.text())
        st.setValue("in_crs", self.cb_in_crs.currentText())
        st.setValue("out_crs", self.cb_out_crs.currentText())
        st.setValue("res", self.sp_res.value())
        st.setValue("method", self.cb_method.currentIndex())
        st.setValue("theme", self.theme_name)
        st.setValue("match_all", self.cb_match.currentIndex())
        for k, t in (("f_case", self.t_case), ("f_word", self.t_word), ("f_regex", self.t_regex),
                     ("f_path", self.t_path)):
            st.setValue(k, t.isChecked())

    def _load_settings(self):
        st = self.settings

        def b(k):
            return str(st.value(k, "false")).lower() == "true"

        self.ed_out.setText(st.value("out", ""))
        self.ed_exts.setText(st.value("exts", DEFAULT_EXTS))
        self.ed_prefix.setText(st.value("prefix", "merged"))
        self.ed_inc.setText(st.value("inc", ""))
        self.ed_exc.setText(st.value("exclude_terms", DEFAULT_EXCLUDE))
        for k, t in (("f_case", self.t_case), ("f_word", self.t_word), ("f_regex", self.t_regex),
                     ("f_path", self.t_path)):
            t.setChecked(b(k))
        try:
            self.cb_match.setCurrentIndex(int(st.value("match_all", 0)))
            self.sp_res.setValue(float(st.value("res", 0)))
            self.cb_method.setCurrentIndex(int(st.value("method", 0)))
        except (TypeError, ValueError):
            pass
        for key, cb in (("in_crs", self.cb_in_crs), ("out_crs", self.cb_out_crs)):
            saved = st.value(key)
            if saved:
                code = normalize_text(saved)
                match = [l for l in preset_labels() if normalize_text(l) == code]
                cb.setCurrentText(match[0] if match else saved)
        self._apply_filter()

    def closeEvent(self, e):
        self._save_settings()
        if self.job and self.job.isRunning():
            self.job.cancel()
            self.job.wait(5000)
        super().closeEvent(e)


def main():
    if hasattr(Qt, "HighDpiScaleFactorRoundingPolicy"):
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("DEM / XYZ Merge")
    icon = Path(__file__).with_name("assets") / "icon.png"
    if icon.exists():
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(icon)))
    if sys.platform.startswith("win"):
        try:  # separate taskbar icon instead of the generic Python one
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DEMMerge.App")
        except Exception:
            pass
    f = app.font()
    if sys.platform.startswith("win"):
        f.setFamily("Segoe UI")
    app.setFont(f)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
