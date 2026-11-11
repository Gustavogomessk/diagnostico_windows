PRIMARY = "#1F5FBF"
PRIMARY_DARK = "#174A94"
BG = "#F5F7FB"
CARD = "#FFFFFF"
BORDER = "#DDE3EE"
TEXT = "#1F2937"
MUTED = "#6B7280"
OK = "#15803D"
WARN = "#B45309"
ERR = "#B91C1C"

QSS = f"""
* {{ font-family: 'Segoe UI', 'Noto Sans', sans-serif; font-size: 13px; color: {TEXT}; }}
QMainWindow, QWidget#page {{ background: {BG}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px; }}
QFrame#sidebar {{ background: {CARD}; border-right: 1px solid {BORDER}; }}
QListWidget#nav {{ background: transparent; border: none; outline: none; }}
QListWidget#nav::item {{ padding: 9px 12px; border-radius: 6px; margin: 1px 8px; }}
QListWidget#nav::item:selected {{ background: #E6EEFB; color: {PRIMARY_DARK}; }}
QListWidget#nav::item:hover {{ background: #F0F4FB; }}
QLabel#h1 {{ font-size: 20px; font-weight: 600; }}
QLabel#h2 {{ font-size: 14px; font-weight: 600; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#banner {{ background: #FEF3C7; border: 1px solid #F5D98B; border-radius: 6px; padding: 8px 12px; color: #78350F; }}
QPushButton {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 6px; padding: 7px 14px; }}
QPushButton:hover {{ background: #F0F4FB; }}
QPushButton:disabled {{ color: #9CA3AF; background: #F3F4F6; }}
QPushButton#primary {{ background: {PRIMARY}; border: 1px solid {PRIMARY}; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: {PRIMARY_DARK}; }}
QPushButton#primary:disabled {{ background: #A9BEE3; border-color: #A9BEE3; color: white; }}
QLineEdit, QSpinBox {{ background: white; border: 1px solid {BORDER}; border-radius: 6px; padding: 7px 9px; }}
QLineEdit:focus, QSpinBox:focus {{ border: 1px solid {PRIMARY}; }}
QTableWidget {{ background: white; border: 1px solid {BORDER}; border-radius: 6px; gridline-color: #EEF1F6; alternate-background-color: #F8FAFD; }}
QHeaderView::section {{ background: #EEF3FB; border: none; border-bottom: 1px solid {BORDER}; padding: 6px; font-weight: 600; }}
QProgressBar {{ background: #E5EAF3; border: none; border-radius: 4px; height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {PRIMARY}; border-radius: 4px; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ padding: 8px 14px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ border-bottom: 2px solid {PRIMARY}; color: {PRIMARY_DARK}; font-weight: 600; }}
QTextEdit {{ background: white; border: 1px solid {BORDER}; border-radius: 6px; }}
"""
