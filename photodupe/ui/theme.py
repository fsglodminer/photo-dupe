"""Colours, fonts and the application stylesheet.

Everything visual is derived from one palette so the dark and light themes
stay consistent, and so a colour is never hard-coded in two places.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6 import QtGui


@dataclass(frozen=True)
class Palette:
    name: str
    window: str          # app background
    surface: str         # panels, cards
    surface_alt: str     # hover / alternating rows
    sidebar: str
    border: str
    text: str
    text_dim: str
    accent: str
    accent_text: str
    good: str
    warn: str
    bad: str
    keep: str
    shadow: str

    def q(self, name: str) -> QtGui.QColor:
        return QtGui.QColor(getattr(self, name))


DARK = Palette(
    name="dark",
    window="#16181d",
    surface="#1e2129",
    surface_alt="#272b35",
    sidebar="#12141a",
    border="#333846",
    text="#e8eaf0",
    text_dim="#98a0b3",
    accent="#4c8dff",
    accent_text="#ffffff",
    good="#3fbf7f",
    warn="#e5a83c",
    bad="#e5544b",
    keep="#3fbf7f",
    shadow="#0d0f13",
)

LIGHT = Palette(
    name="light",
    window="#f4f5f8",
    surface="#ffffff",
    surface_alt="#eceef3",
    sidebar="#e6e9f0",
    border="#d0d5e0",
    text="#1a1d24",
    text_dim="#5f6779",
    accent="#2f6fe0",
    accent_text="#ffffff",
    good="#1f9d5e",
    warn="#c07b12",
    bad="#c8352c",
    keep="#1f9d5e",
    shadow="#c3c8d4",
)


def palette_for(theme: str) -> Palette:
    return LIGHT if theme == "light" else DARK


#: Score -> colour, used by the grade pills and the score bars.
def score_color(palette: Palette, score: float) -> QtGui.QColor:
    if score >= 72:
        return palette.q("good")
    if score >= 50:
        return palette.q("warn")
    return palette.q("bad")


def stylesheet(palette: Palette, assets: dict[str, str] | None = None) -> str:
    """Build the application stylesheet.

    ``assets`` carries generated image paths (spin-box and combo-box arrows);
    when omitted those controls simply keep Qt's default arrows.
    """
    p = palette
    assets = assets or {}
    arrow_down = assets.get("arrow_down", "")
    arrow_up = assets.get("arrow_up", "")
    arrows = f"""
    QComboBox::down-arrow {{ image: url({arrow_down}); width: 9px; height: 9px; }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
        image: url({arrow_down}); width: 8px; height: 8px;
    }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
        image: url({arrow_up}); width: 8px; height: 8px;
    }}
    """ if arrow_down and arrow_up else ""
    return arrows + f"""
    /* Plain containers stay transparent so a QWidget used purely for layout
       inside a card does not paint a window-coloured band over it. Every
       widget that needs its own surface names it explicitly below. */
    QWidget {{
        background: transparent;
        color: {p.text};
        font-size: 13px;
    }}
    QMainWindow, QDialog, QMessageBox {{ background: {p.window}; }}
    QMenu {{
        background: {p.surface};
        border: 1px solid {p.border};
        border-radius: 8px;
        padding: 5px;
    }}
    QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {p.accent}; color: {p.accent_text}; }}
    QMenu::separator {{ height: 1px; background: {p.border}; margin: 4px 8px; }}
    QToolTip {{
        background: {p.surface_alt};
        color: {p.text};
        border: 1px solid {p.border};
        padding: 5px 7px;
    }}

    /* ---- sidebar ---------------------------------------------------- */
    #Sidebar {{
        background: {p.sidebar};
        border-right: 1px solid {p.border};
    }}
    #SidebarTitle {{
        color: {p.text};
        font-size: 17px;
        font-weight: 700;
        padding: 18px 18px 2px 18px;
        background: transparent;
    }}
    #SidebarSubtitle {{
        color: {p.text_dim};
        font-size: 11px;
        padding: 0 18px 14px 18px;
        background: transparent;
    }}
    #NavButton {{
        background: transparent;
        border: none;
        border-left: 3px solid transparent;
        color: {p.text_dim};
        text-align: left;
        padding: 11px 16px;
        font-size: 13.5px;
        font-weight: 500;
    }}
    #NavButton:hover {{
        background: {p.surface};
        color: {p.text};
    }}
    #NavButton:checked {{
        background: {p.surface};
        color: {p.text};
        border-left: 3px solid {p.accent};
        font-weight: 600;
    }}

    /* ---- headings and cards ----------------------------------------- */
    #PageTitle   {{ font-size: 21px; font-weight: 700; background: transparent; }}
    #PageHint    {{ color: {p.text_dim}; font-size: 12.5px; background: transparent; }}
    #SectionTitle{{ font-size: 14px; font-weight: 600; background: transparent; }}
    #Muted       {{ color: {p.text_dim}; background: transparent; }}

    #Card {{
        background: {p.surface};
        border: 1px solid {p.border};
        border-radius: 10px;
    }}
    #Card QLabel {{ background: transparent; }}
    #StatValue {{ font-size: 22px; font-weight: 700; background: transparent; }}
    #StatLabel {{ color: {p.text_dim}; font-size: 11px; background: transparent; }}

    /* ---- buttons ----------------------------------------------------- */
    QPushButton {{
        background: {p.surface_alt};
        border: 1px solid {p.border};
        border-radius: 7px;
        padding: 7px 15px;
        color: {p.text};
        font-weight: 500;
    }}
    QPushButton:hover   {{ background: {p.border}; }}
    QPushButton:pressed {{ background: {p.surface}; }}
    QPushButton:disabled{{ color: {p.text_dim}; background: {p.surface}; }}
    QPushButton#Primary {{
        background: {p.accent};
        border: 1px solid {p.accent};
        color: {p.accent_text};
        font-weight: 600;
    }}
    QPushButton#Primary:hover    {{ background: {p.accent}; border-color: {p.text}; }}
    QPushButton#Primary:disabled {{ background: {p.surface_alt}; color: {p.text_dim};
                                    border-color: {p.border}; }}
    QPushButton#Danger {{
        background: {p.bad}; border: 1px solid {p.bad}; color: #ffffff; font-weight: 600;
    }}
    QPushButton#Danger:disabled {{ background: {p.surface_alt}; color: {p.text_dim};
                                   border-color: {p.border}; }}
    QPushButton#Link {{
        background: transparent; border: none; color: {p.accent};
        padding: 2px 4px; text-align: left;
    }}

    /* ---- inputs ------------------------------------------------------ */
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
        background: {p.window};
        border: 1px solid {p.border};
        border-radius: 6px;
        padding: 6px 8px;
        selection-background-color: {p.accent};
        selection-color: {p.accent_text};
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
        border: 1px solid {p.accent};
    }}
    QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
    QComboBox:disabled {{
        color: {p.text_dim};
        background: {p.surface};
        border: 1px dashed {p.border};
    }}
    QComboBox, QSpinBox, QDoubleSpinBox {{ min-width: 148px; }}
    QComboBox::drop-down {{
        border: none; width: 20px; subcontrol-position: center right;
        subcontrol-origin: padding; right: 4px;
    }}
    QSpinBox::up-button, QDoubleSpinBox::up-button,
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        background: transparent;
        border: none;
        width: 16px;
        subcontrol-origin: border;
        right: 3px;
    }}
    QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-position: top right; }}
    QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-position: bottom right; }}
    QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
    QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
        background: {p.surface_alt}; border-radius: 3px;
    }}
    QComboBox QAbstractItemView {{
        background: {p.surface};
        border: 1px solid {p.border};
        selection-background-color: {p.accent};
        selection-color: {p.accent_text};
        outline: none;
    }}
    QCheckBox, QRadioButton {{ background: transparent; spacing: 8px; }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 16px; height: 16px;
        border: 1px solid {p.border};
        border-radius: 4px;
        background: {p.window};
    }}
    QRadioButton::indicator {{ border-radius: 9px; }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background: {p.accent}; border-color: {p.accent};
    }}
    QGroupBox {{
        border: 1px solid {p.border};
        border-radius: 9px;
        margin-top: 14px;
        padding: 14px 12px 12px 12px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 5px;
        color: {p.text_dim};
    }}

    /* ---- sliders ----------------------------------------------------- */
    QSlider::groove:horizontal {{
        height: 4px; background: {p.border}; border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        background: {p.text}; width: 14px; height: 14px;
        margin: -6px 0; border-radius: 7px;
    }}

    /* ---- lists and views --------------------------------------------- */
    QListView, QTreeView, QTableView, QListWidget {{
        background: {p.surface};
        border: 1px solid {p.border};
        border-radius: 9px;
        outline: none;
    }}
    QListWidget::item {{ padding: 7px 9px; border-radius: 6px; }}
    QListWidget::item:selected {{ background: {p.accent}; color: {p.accent_text}; }}
    QListWidget::item:hover:!selected {{ background: {p.surface_alt}; }}
    QHeaderView::section {{
        background: {p.surface_alt};
        border: none;
        border-right: 1px solid {p.border};
        border-bottom: 1px solid {p.border};
        padding: 6px 8px;
        font-weight: 600;
    }}
    QSplitter::handle {{ background: {p.border}; }}
    QSplitter::handle:horizontal {{ width: 1px; }}
    QSplitter::handle:vertical {{ height: 1px; }}

    /* ---- scrollbars --------------------------------------------------- */
    QScrollBar:vertical {{
        background: transparent; width: 11px; margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {p.border}; border-radius: 5px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {p.text_dim}; }}
    QScrollBar:horizontal {{
        background: transparent; height: 11px; margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {p.border}; border-radius: 5px; min-width: 30px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    /* ---- progress and status ------------------------------------------ */
    QProgressBar {{
        background: {p.surface_alt};
        border: none;
        border-radius: 5px;
        height: 8px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{ background: {p.accent}; border-radius: 5px; }}
    #StatusBar {{
        background: {p.surface};
        border-top: 1px solid {p.border};
    }}
    #StatusBar QLabel {{ background: transparent; color: {p.text_dim}; }}

    QScrollArea {{ border: none; background: transparent; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    """
