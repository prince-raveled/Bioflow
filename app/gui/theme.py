"""BioFlow's visual identity: warm parchment, sage, and terracotta.

The palette is BioFlow's original one, kept verbatim so the application stays
recognisably itself. Surfaces are translucent over the DNA background, with a
warm parchment scrim rather than a dark one, so the animation reads as a
sepia-toned scientific texture behind the interface instead of a cold overlay.
"""

# ----------------------------------------------------------------------
# Core palette (original BioFlow values)
# ----------------------------------------------------------------------
INK = "#F5EFE3"             # parchment: the base ground of the application
SURFACE = "#FFF9EE"         # warm cream: pages and panels
SURFACE_RAISED = "#EEE5D6"  # warm beige: cards, rows, secondary controls
BORDER = "#D8C6A8"
BORDER_STRONG = "#C9B38E"

TEXT = "#2F2924"            # dark warm brown
TEXT_MUTED = "#62574D"
TEXT_FAINT = "#8A7D6B"

ACCENT = "#995C4A"          # terracotta: branding and primary actions
ACCENT_DEEP = "#734231"     # burnt rust: borders and pressed states
ACCENT_LIGHT = "#B76D57"    # terracotta hover
ACCENT_SOFT = "#F1D7AF"     # warm gold: selected navigation

# Sage is carried one shade deeper than the original #83916B so that cream
# navigation text clears WCAG AA (4.58:1); the original lighter sage is kept
# for hover, where the text turns dark brown exactly as it did before.
SAGE = "#6A7752"            # olive sage: navigation ground
SAGE_DEEP = "#4F5A3C"
SAGE_LIGHT = "#A7B18D"
SAGE_TEXT = "#66724F"       # sage on cream, darkened for label contrast

CREAM_TEXT = "#FFF9EE"      # text over terracotta or sage
LOG_INK = "#2F2924"         # the run log keeps its dark, high-contrast ground
LOG_TEXT = "#F7EAD3"

# Status colours, kept inside the warm family so state reads clearly without
# introducing a second, colder colour system.
OK = "#4A5D3A"
OK_SOFT = "#DDE4CC"
WARN = "#755518"
WARN_SOFT = "#F2E3C0"
ERROR = "#8C3B2E"
ERROR_SOFT = "#EFD5CE"
INFO = "#5A6B52"
INFO_SOFT = "#E4E8D8"
IDLE = "#665C52"
IDLE_SOFT = "#E9DDC9"

# ----------------------------------------------------------------------
# Translucent surfaces
#
# Alpha is high enough that dark brown body text keeps a comfortable contrast
# ratio, while the background animation stays perceptible through the panels.
# ----------------------------------------------------------------------
# The branded surfaces stay essentially solid so terracotta reads as terracotta
# and sage as sage; the animation breathes in the gaps between panels and, more
# faintly, through the large content page.
GLASS_PANEL = "rgba(255, 249, 238, 0.94)"        # cream pages
GLASS_PANEL_STRONG = "rgba(255, 249, 238, 0.98)"
GLASS_RAISED = "rgba(238, 229, 214, 0.95)"       # beige cards
GLASS_RAISED_HOVER = "rgba(233, 221, 201, 0.98)"
GLASS_SIDEBAR = "rgba(106, 119, 82, 0.97)"       # sage navigation
GLASS_HEADER = "rgba(153, 92, 74, 0.98)"         # terracotta banner
GLASS_INPUT = "rgba(255, 249, 238, 0.97)"
GLASS_LOG = "rgba(47, 41, 36, 0.97)"
GLASS_BORDER = "rgba(153, 118, 76, 0.35)"
GLASS_BORDER_STRONG = "rgba(140, 104, 62, 0.55)"

#: Warm scrim laid over the video so the animation never fights the text.
SCRIM = INK

FONT_STACK = "Georgia, 'DejaVu Serif', 'Times New Roman', serif"
MONO_STACK = "'DejaVu Sans Mono', 'Cascadia Mono', monospace"

#: Colour pairs for each resource / stage state, keyed by a semantic name.
STATE_COLOURS = {
    "ok": (OK, OK_SOFT),
    "running": (ACCENT, ACCENT_SOFT),
    "warn": (WARN, WARN_SOFT),
    "error": (ERROR, ERROR_SOFT),
    "info": (INFO, INFO_SOFT),
    "idle": (IDLE, IDLE_SOFT),
}


def state_colours(kind: str) -> tuple[str, str]:
    """Foreground and background for a status badge."""
    return STATE_COLOURS.get(kind, STATE_COLOURS["idle"])


STYLESHEET = f"""
/* ---------- base ----------
   The window paints nothing of its own: the animated background shows through
   every surface that does not explicitly opt into a colour. */
QWidget {{
    background: transparent;
    color: {TEXT};
    font-family: {FONT_STACK};
    font-size: 14px;
}}
QWidget#backgroundVideo {{ background: transparent; }}
QToolTip {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 6px 9px;
}}

/* ---------- header ---------- */
QFrame#appHeader {{
    background: {GLASS_HEADER};
    border: 2px solid {ACCENT_DEEP};
    border-radius: 14px;
}}
QLabel#brandMark {{
    background: transparent; color: {CREAM_TEXT};
    font-size: 30px; font-weight: bold; letter-spacing: 4px;
}}
QLabel#brandFlow {{
    background: transparent; color: {ACCENT_SOFT};
    font-size: 30px; font-weight: bold; letter-spacing: 4px;
}}
QLabel#brandTag {{
    background: transparent; color: #F8E2C4;
    font-size: 11px; font-weight: bold; letter-spacing: 3px;
}}

/* ---------- sidebar ---------- */
QFrame#sidebarPanel {{
    background: {GLASS_SIDEBAR};
    border: 2px solid {SAGE_DEEP};
    border-radius: 14px;
}}
QTreeWidget#sidebar {{
    background: transparent; border: 0; color: {CREAM_TEXT};
    font-size: 15px; outline: 0; padding: 8px 4px;
}}
QTreeWidget#sidebar::item {{
    background: transparent; border-radius: 7px;
    min-height: 32px; padding: 3px 9px; margin: 1px 4px;
}}
QTreeWidget#sidebar::item:hover {{ background: {SAGE_LIGHT}; color: {TEXT}; }}
QTreeWidget#sidebar::item:selected {{
    background: {ACCENT_SOFT}; color: #673D30; font-weight: bold;
}}
QTreeWidget#sidebar::branch,
QTreeWidget#sidebar::branch:selected,
QTreeWidget#sidebar::branch:hover,
QTreeWidget#sidebar::branch:has-children,
QTreeWidget#sidebar::branch:has-siblings {{
    background: transparent; border-image: none; image: none;
}}

/* ---------- pages ---------- */
QWidget#toolPage {{
    background: {GLASS_PANEL};
    border: 2px solid {BORDER};
    border-radius: 14px;
}}
QLabel#eyebrow {{
    background: transparent; color: {SAGE_TEXT};
    font-size: 11px; font-weight: bold; letter-spacing: 1.5px;
}}
QLabel#pageTitle {{
    background: transparent; color: {ACCENT};
    font-size: 31px; font-weight: bold;
}}
QLabel#pageDescription {{ background: transparent; color: {TEXT_MUTED}; font-size: 15px; }}
QLabel#logTitle {{
    background: transparent; color: {SAGE_TEXT};
    font-size: 11px; font-weight: bold; letter-spacing: 1.2px;
}}
QLabel#sectionHeading {{
    background: transparent; color: {TEXT}; font-size: 15px; font-weight: bold;
}}

/* ---------- cards ---------- */
QFrame#card, QFrame#componentRow {{
    background: {GLASS_RAISED};
    border: 1px solid {BORDER};
    border-radius: 9px;
}}
QFrame#componentRow:hover {{
    background: {GLASS_RAISED_HOVER};
    border-color: {ACCENT};
}}
QFrame#resultCard {{
    background: rgba(238, 240, 228, 0.96);
    border: 1px solid #A9B18E;
    border-radius: 9px;
}}
QLabel#resultLabel {{ background: transparent; color: #596548; font-size: 12px; }}
QLabel#componentTitle {{
    background: transparent; color: {TEXT}; font-size: 14px; font-weight: bold;
}}
QLabel#componentDescription {{ background: transparent; color: {TEXT_MUTED}; font-size: 12px; }}
QLabel#componentSize {{
    background: transparent; color: {TEXT_FAINT}; font-size: 12px; font-weight: bold;
}}

/* ---------- status badges ---------- */
QLabel#statusBadge {{
    border-radius: 9px; font-size: 11px; font-weight: bold;
    letter-spacing: 0.4px; padding: 5px 10px;
}}
QLabel#componentStatus {{
    background: {IDLE_SOFT}; border-radius: 9px; color: {TEXT_MUTED};
    font-size: 11px; font-weight: bold; padding: 5px 10px;
}}
QLabel#componentStatus[installed="true"] {{ background: {OK_SOFT}; color: {OK}; }}

/* ---------- buttons ---------- */
QPushButton {{
    background: rgba(233, 221, 201, 0.97);
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px; color: #43372F;
    font-weight: bold; padding: 9px 14px;
}}
QPushButton:hover {{ background: #D9C6A7; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: #CDB795; }}
QPushButton:disabled {{
    background: rgba(232, 225, 213, 0.85); color: #9B9184; border-color: #D4CABC;
}}
QPushButton#runButton {{
    background: {ACCENT}; border: 2px solid {ACCENT_DEEP};
    border-radius: 10px; color: {CREAM_TEXT};
    font-size: 15px; font-weight: bold; padding: 12px 18px;
}}
QPushButton#runButton:hover {{ background: {ACCENT_LIGHT}; }}
QPushButton#runButton:pressed {{ background: {ACCENT_DEEP}; }}
QPushButton#runButton:disabled {{
    background: rgba(232, 225, 213, 0.85); border-color: #D4CABC; color: #9B9184;
}}
QPushButton#openResultsButton {{
    background: {SAGE}; border: 1px solid {SAGE_DEEP};
    color: {CREAM_TEXT}; padding: 6px 12px;
}}
QPushButton#openResultsButton:hover {{ background: #98A57D; }}

/* ---------- inputs ---------- */
QLineEdit, QComboBox {{
    background: {GLASS_INPUT}; border: 1px solid {BORDER_STRONG};
    border-radius: 7px; color: {TEXT};
    min-height: 27px; padding: 3px 9px;
    selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT};
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: 0; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE}; border: 1px solid {BORDER_STRONG}; color: {TEXT};
    selection-background-color: {ACCENT_SOFT}; selection-color: #673D30; outline: 0;
}}
QCheckBox {{ background: transparent; color: {TEXT}; spacing: 8px; }}
QCheckBox:hover {{ color: {ACCENT}; }}
QCheckBox::indicator {{
    background: {SURFACE}; border: 2px solid {BORDER_STRONG};
    border-radius: 5px; height: 15px; width: 15px;
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {SAGE}; border-color: {SAGE_DEEP}; }}
QCheckBox::indicator:disabled {{ background: #E8E1D5; border-color: #D4CABC; }}

QSlider::groove:horizontal {{
    background: {BORDER}; border: 1px solid #C2A57B; border-radius: 5px; height: 8px;
}}
QSlider::sub-page:horizontal {{ background: {SAGE}; border-radius: 4px; }}
QSlider::handle:horizontal {{
    background: {ACCENT}; border: 2px solid {ACCENT_DEEP};
    border-radius: 10px; margin: -6px 0; width: 18px;
}}
QSlider::handle:horizontal:hover {{ background: {ACCENT_LIGHT}; }}
QLabel#threadCount {{
    background: {SAGE}; border: 1px solid {SAGE_DEEP}; border-radius: 11px;
    color: {CREAM_TEXT}; font-weight: bold; min-width: 27px; padding: 5px 4px;
}}

/* ---------- tables ---------- */
QTreeWidget#historyTable {{
    background: {GLASS_INPUT}; border: 1px solid {BORDER};
    border-radius: 8px; alternate-background-color: rgba(245, 239, 227, 0.9);
    outline: 0;
}}
QTreeWidget#historyTable::item {{
    border-bottom: 1px solid rgba(216, 198, 168, 0.5);
    color: {TEXT_MUTED}; min-height: 31px; padding: 4px 6px;
}}
QTreeWidget#historyTable::item:hover {{ background: #F1E8D8; color: {TEXT}; }}
QTreeWidget#historyTable::item:selected {{ background: #E9DDC9; color: #43372F; }}
QHeaderView::section {{
    background: transparent; border: 0; border-bottom: 1px solid {BORDER};
    color: {SAGE_TEXT}; font-size: 11px; font-weight: bold;
    letter-spacing: 0.8px; padding: 8px 6px;
}}

/* ---------- log ---------- */
QTextEdit#executionLog {{
    background: {GLASS_LOG}; border: 3px solid {SAGE};
    border-radius: 9px; color: {LOG_TEXT};
    font-family: {MONO_STACK}; font-size: 12px; padding: 8px;
    selection-background-color: {ACCENT};
}}

/* ---------- progress ---------- */
QProgressBar {{
    background: rgba(245, 239, 227, 0.94); border: 1px solid {BORDER};
    border-radius: 6px; color: {TEXT_MUTED};
    font-size: 11px; font-weight: bold; max-height: 14px; text-align: center;
}}
QProgressBar::chunk {{ background: {SAGE}; border-radius: 5px; }}

/* ---------- scrollbars ---------- */
QScrollArea {{ background: transparent; border: 0; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 3px; }}
QScrollBar::handle:vertical {{ background: {ACCENT}; border-radius: 5px; min-height: 26px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT_LIGHT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 3px; }}
QScrollBar::handle:horizontal {{ background: {ACCENT}; border-radius: 5px; min-width: 26px; }}

/* ---------- dialogs ---------- */
QMessageBox {{ background: {SURFACE}; }}
QFrame#divider {{ background: {BORDER}; border: 0; max-height: 1px; }}
"""
