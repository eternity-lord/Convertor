import json
from collections import deque
from datetime import datetime
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QScrollArea, QPlainTextEdit, QApplication
from app.ui.widgets.common import Card, Pills, Toggle, label, button, row
from app.ui import dialogs
from app.i18n import tr


class LogView(QWidget):
    def __init__(self):
        super().__init__()
        self.records = deque(maxlen=400)
        self.level = 'All'
        self.box = QVBoxLayout(self)
        pills = Pills(['All', 'Info', 'Warn', 'Error'])
        pills.selected.connect(self.set_level)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr('Search files / filter logs'))
        self.search.textChanged.connect(lambda _: self.refresh())
        self.auto = Toggle('Auto-scroll', True)
        self.box.addLayout(row(pills, self.search, button('Export', self.export), button('Clear', self.clear)))
        self.box.addWidget(self.auto)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.content = QWidget()
        self.cards = QVBoxLayout(self.content)
        self.cards.addStretch()
        self.area.setWidget(self.content)
        self.box.addWidget(self.area)
        self.dirty = False
        self.timer = QTimer(self)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self.refresh_if_dirty)
        self.timer.start()

    def append(self, level, message, file='', details=''):
        self.records.append(dict(time=datetime.now().isoformat(timespec='seconds'), level=level,
                                 message=message[:2000], file=file, details=details[-32000:]))
        self.dirty = True

    def refresh_if_dirty(self):
        if self.dirty and self.isVisible():
            self.refresh()

    def set_level(self, value):
        self.level = value
        self.refresh()

    def refresh(self):
        self.dirty = False
        position = self.area.verticalScrollBar().value()
        while self.cards.count() > 1:
            item = self.cards.takeAt(0)
            item.widget().deleteLater()
        query = self.search.text().casefold()
        matches = [r for r in self.records if (self.level == 'All' or r['level'] == self.level.upper())
                   and query in (r['message'] + ' ' + r['file']).casefold()][-100:]
        for record in matches:
            card = Card()
            card.box.setContentsMargins(16, 12, 16, 12)
            chip = label(record['level'], 'badge')
            chip.setStyleSheet(f'color: {"#f87171" if record["level"] == "ERROR" else "#d0aec7"};')
            file = label(record['file'], 'muted')
            file.setMaximumWidth(330)
            card.box.addLayout(row(label(record['time'][11:], 'muted'), chip, file, None))
            message = label(record['message'])
            message.setTextFormat(__import__('PySide6.QtCore', fromlist=['Qt']).Qt.PlainText)
            card.box.addWidget(message)
            if record['details']:
                detail = QPlainTextEdit(record['details'])
                detail.setReadOnly(True)
                detail.setMaximumHeight(170)
                detail.setStyleSheet('font-family: monospace; font-size: 11px;')
                detail.hide()
                toggle = button('Details', lambda checked=False, w=detail: w.setVisible(not w.isVisible()))
                copy = button('Copy', lambda checked=False, r=record: QApplication.clipboard().setText(r['details']))
                card.box.addLayout(row(toggle, copy, None))
                card.box.addWidget(detail)
            self.cards.insertWidget(self.cards.count() - 1, card)
        QTimer.singleShot(0, lambda: self.area.verticalScrollBar().setValue(
            self.area.verticalScrollBar().maximum() if self.auto.isChecked() else position))

    def clear(self):
        self.records.clear()
        self.refresh()

    def export(self):
        name = dialogs.save_file(self, 'edius-log.json', 'JSON (*.json);;Text (*.txt)')
        if name:
            try:
                from pathlib import Path
                text = json.dumps(list(self.records), ensure_ascii=False, indent=2) if name.endswith('.json') else '\n\n'.join(
                    f"{r['time']} [{r['level']}] {r['file']} {r['message']}\n{r['details']}" for r in self.records)
                Path(name).write_text(text, encoding='utf-8')
            except OSError as exc:
                self.append('ERROR', str(exc))
