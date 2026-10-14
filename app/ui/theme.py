from pathlib import Path
from PySide6.QtGui import QFont, QFontDatabase

TOKENS = dict(page='#050505', shell_top='#5d535b', shell_bottom='#0f0d10',
              sidebar='#0a0a0c', card='#0a0a0c', card_glow='#423441', accent='#71586e',
              pill_text='#b19fad', glow='#cba8c6', highlight='#ffd3f4', primary='#ffffff',
              secondary='#989697', muted='#585359', positive='#4ade80', negative='#f87171', grid='#181216')

QSS = '''
* { color: #eee9ed; font-family: "Poppins", "Vazirmatn", "DejaVu Sans"; font-size: 12px; }
QWidget { background: transparent; }
QToolTip { background: #211a22; color: #eee9ed; border: 1px solid #71586e; border-radius: 8px; padding: 8px; }
QLabel[role="title"] { font-size: 16px; font-weight: 500; }
QLabel[role="number"] { font-size: 30px; font-weight: 600; }
QLabel[role="muted"] { color: #989697; font-size: 11px; }
QLabel[role="positive"] { color: #81c89b; }
QLabel[role="badge"] { background: #211b22; color: #d6bbd1; border-radius: 10px; padding: 5px 9px; }
QPushButton { background: #0b0a0d; border: 1px solid #393139; border-radius: 17px; padding: 8px 15px; }
QPushButton:hover { background: #342936; border-color: #9b7c96; }
QPushButton:pressed { background: #4b394b; }
QPushButton:checked, QPushButton[primary="true"] { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #8a6a85,stop:1 #5f4a5c); border-color: #9a7a93; }
QPushButton:disabled { color: #625b62; border-color: #2a242b; background: #151216; }
QPushButton[nav="true"] { text-align: left; border: 0; padding: 12px 17px; border-radius: 14px; }
QPushButton[nav="true"]:checked { border: 1px solid #80657c; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit { background: #100d12; border: 1px solid #39303a; border-radius: 14px; padding: 7px 12px; selection-background-color: #71586e; min-height: 20px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #b28cab; background: #1b151d; }
QComboBox::drop-down { border: 0; width: 24px; }
QComboBox QAbstractItemView { background: #171218; border: 1px solid #71586e; selection-background-color: #71586e; padding: 6px; }
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button { width: 0; }
QScrollArea, QStackedWidget { border: 0; background: transparent; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 3px; }
QScrollBar::handle:vertical { background: #71586e; border-radius: 3px; min-height: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QProgressBar { border: 0; background: #251c27; border-radius: 4px; height: 7px; text-align: center; color: transparent; }
QProgressBar::chunk { border-radius: 4px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #71586e,stop:1 #ffd3f4); }
QTableWidget { background: #0c0a0e; border: 0; gridline-color: #211923; selection-background-color: #3c2c40; alternate-background-color: #120e15; border-radius: 15px; }
QTableWidget::item { padding: 8px; border-bottom: 1px solid #221b24; }
QHeaderView::section { background: #151018; color: #9e8fa0; border: 0; padding: 8px; }
QMenu, QMessageBox, QFileDialog, QDialog { background: #19131d; border: 1px solid #665069; }
QMenu::item { padding: 9px 22px; }
QMenu::item:selected { background: #71586e; border-radius: 6px; }
QSlider::groove:horizontal { height: 4px; background: #332736; }
QSlider::handle:horizontal { background: #d4afd0; width: 14px; margin: -5px 0; border-radius: 7px; }
'''


def apply_theme(app):
    roots = [Path(__file__).resolve().parents[2] / 'assets' / 'fonts']
    import sys
    if getattr(sys, '_MEIPASS', None):
        roots.insert(0, Path(sys._MEIPASS) / 'assets' / 'fonts')
    for root in roots:
        for path in root.glob('*.ttf'):
            QFontDatabase.addApplicationFont(str(path))
    font = QFont('Poppins', 10)
    font.setFamilies(['Poppins', 'Vazirmatn', 'DejaVu Sans'])
    app.setFont(font)
    app.setStyle('Fusion')
    app.setStyleSheet(QSS)
