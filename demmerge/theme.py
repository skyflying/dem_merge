"""Light / dark themes (palette + Qt style sheet)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtGui import QColor, QPalette

THEMES = {
    "light": dict(bg="#f3f5f9", surface="#ffffff", alt="#f8f9fc", border="#e2e6ee", border2="#cfd6e2",
                  text="#1d2330", muted="#6b7385", accent="#2f6fed", accent_hover="#2459c9",
                  accent_soft="#e7efff", accent_text="#ffffff", warn="#fff5d6", danger="#d64545",
                  hover="#eef1f7"),
    "dark": dict(bg="#14161b", surface="#1c1f26", alt="#21252d", border="#2b303a", border2="#3a414e",
                 text="#e5e8ef", muted="#98a1b3", accent="#4d8dff", accent_hover="#6aa0ff",
                 accent_soft="#1f3157", accent_text="#ffffff", warn="#3a3222", danger="#ff6b6b",
                 hover="#262b34"),
}

QSS = """
* {{ font-size: 13px; }}
QMainWindow, QWidget#root {{ background: {bg}; }}
QWidget {{ color: {text}; }}
QToolTip {{ background: {surface}; color: {text}; border: 1px solid {border2}; padding: 6px; border-radius: 6px; }}

QFrame#card {{ background: {surface}; border: 1px solid {border}; border-radius: 14px; }}
QLabel#title {{ font-size: 20px; font-weight: 700; }}
QLabel#subtitle {{ color: {muted}; }}
QLabel#cardTitle {{ font-size: 14px; font-weight: 700; }}
QLabel#muted, QLabel[muted="true"] {{ color: {muted}; }}
QLabel#status {{ color: {muted}; padding: 2px 4px; }}
QLabel#chip {{ background: {accent_soft}; color: {accent}; border-radius: 9px; padding: 2px 10px; font-weight: 600; }}

QGroupBox {{ background: {surface}; border: 1px solid {border}; border-radius: 10px;
             margin-top: 18px; padding: 12px 10px 8px 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; top: 2px; color: {muted}; }}

QPushButton, QToolButton {{ background: {surface}; border: 1px solid {border2}; border-radius: 8px;
                            padding: 6px 12px; }}
QPushButton:hover, QToolButton:hover {{ background: {hover}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {accent_soft}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {muted}; background: {alt}; border-color: {border}; }}
QPushButton[primary="true"] {{ background: {accent}; color: {accent_text}; border: 1px solid {accent};
                               font-weight: 700; padding: 8px 22px; }}
QPushButton[primary="true"]:hover {{ background: {accent_hover}; border-color: {accent_hover}; }}
QPushButton[primary="true"]:disabled {{ background: {border2}; border-color: {border2}; color: {surface}; }}
QPushButton[ghost="true"], QToolButton[ghost="true"] {{ background: transparent; border: 1px solid transparent; }}
QPushButton[ghost="true"]:hover, QToolButton[ghost="true"]:hover {{ background: {hover}; }}
QToolButton:checked {{ background: {accent_soft}; color: {accent}; border-color: {accent}; font-weight: 700; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {surface}; border: 1px solid {border2};
    border-radius: 7px; padding: 4px 8px; min-height: 22px; selection-background-color: {accent}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {accent}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {muted}; background: {alt}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {surface}; border: 1px solid {border2}; border-radius: 6px;
    selection-background-color: {accent_soft}; selection-color: {text}; outline: 0; padding: 4px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    border: none; width: 16px; }}

QTableView {{ background: {surface}; alternate-background-color: {alt}; border: none; gridline-color: transparent;
              selection-background-color: {accent_soft}; selection-color: {text}; outline: 0; }}
QTableView::item {{ padding: 4px 6px; border: none; }}
QHeaderView::section {{ background: {surface}; color: {muted}; border: none; border-bottom: 1px solid {border};
                        padding: 7px 6px; font-weight: 600; }}
QTableCornerButton::section {{ background: {surface}; border: none; }}

QTabWidget::pane {{ border: none; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {muted}; padding: 7px 14px; margin: 0 4px 6px 0;
                border-radius: 8px; font-weight: 600; }}
QTabBar::tab:hover {{ background: {hover}; color: {text}; }}
QTabBar::tab:selected {{ background: {accent_soft}; color: {accent}; }}

QProgressBar {{ background: {border}; border: none; border-radius: 6px; height: 12px; text-align: center;
                color: {text}; font-size: 11px; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 6px; }}

QPlainTextEdit#log {{ background: {surface}; border: 1px solid {border}; border-radius: 10px; padding: 6px;
                      font-family: "Cascadia Mono", Consolas, "SF Mono", Menlo, monospace; font-size: 12px; }}

QSplitter::handle {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {border2}; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {border2}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QMenu {{ background: {surface}; border: 1px solid {border2}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {accent_soft}; color: {text}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 6px; }}
QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator, QTableView::indicator {{ width: 15px; height: 15px; border: 1px solid {border2};
    border-radius: 4px; background: {surface}; }}
QCheckBox::indicator:hover, QTableView::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked, QTableView::indicator:checked {{ background: {accent}; border-color: {accent};
    image: url({check}); }}
QCheckBox::indicator:disabled {{ background: {alt}; border-color: {border}; }}
QCheckBox::indicator:checked:disabled {{ background: {border2}; border-color: {border2}; }}
QComboBox {{ padding-right: 24px; }}
QComboBox::down-arrow {{ image: url({down}); width: 10px; height: 10px; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 22px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right;
    width: 20px; border: none; border-top-right-radius: 7px; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right;
    width: 20px; border: none; border-bottom-right-radius: 7px; }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: {hover}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({up}); width: 8px; height: 8px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({down}); width: 8px; height: 8px; }}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled,
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{ image: none; }}
"""

_SVG = {
    "down": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 12 12"><path d="M2.5 4.5 6 8l3.5-3.5" '
            'fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "up": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 12 12"><path d="M2.5 7.5 6 4l3.5 3.5" '
          'fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "check": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 12 12"><path d="M2.5 6.2 5 8.6l4.5-5" '
             'fill="none" stroke="{c}" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>',
}


def _icons(name: str) -> dict:
    """Write tiny SVG icons for the current theme and return their paths for the style sheet."""
    c = THEMES[name]
    d = Path(tempfile.gettempdir()) / "demmerge_theme"
    d.mkdir(exist_ok=True)
    out = {}
    for key, svg in _SVG.items():
        col = c["accent_text"] if key == "check" else c["muted"]
        p = d / f"{key}_{name}.svg"
        p.write_text(svg.format(c=col), encoding="utf-8")
        out[key] = p.as_posix()
    return out


def palette(name: str) -> QPalette:
    c = THEMES[name]
    p = QPalette()
    p.setColor(QPalette.Window, QColor(c["bg"]))
    p.setColor(QPalette.WindowText, QColor(c["text"]))
    p.setColor(QPalette.Base, QColor(c["surface"]))
    p.setColor(QPalette.AlternateBase, QColor(c["alt"]))
    p.setColor(QPalette.Text, QColor(c["text"]))
    p.setColor(QPalette.Button, QColor(c["surface"]))
    p.setColor(QPalette.ButtonText, QColor(c["text"]))
    p.setColor(QPalette.Highlight, QColor(c["accent"]))
    p.setColor(QPalette.HighlightedText, QColor(c["accent_text"]))
    p.setColor(QPalette.ToolTipBase, QColor(c["surface"]))
    p.setColor(QPalette.ToolTipText, QColor(c["text"]))
    p.setColor(QPalette.PlaceholderText, QColor(c["muted"]))
    p.setColor(QPalette.Mid, QColor(c["border2"]))
    p.setColor(QPalette.Dark, QColor(c["border2"]))
    p.setColor(QPalette.Light, QColor(c["surface"]))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(c["muted"]))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(c["muted"]))
    p.setColor(QPalette.Disabled, QPalette.WindowText, QColor(c["muted"]))
    return p


def apply(app, name: str) -> dict:
    c = THEMES[name]
    app.setPalette(palette(name))
    app.setStyleSheet(QSS.format(**c, **_icons(name)))
    return c
