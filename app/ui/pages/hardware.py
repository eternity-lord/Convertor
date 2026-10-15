"""Read-only evidence cards. Failed / unproven capabilities never look enabled."""
import json
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPlainTextEdit
from app.ui.widgets.common import Card, label, button, row
from app.ui import dialogs
from app.core.hardware.matrix import HardwareMatrix


class HardwarePage(QWidget):
    rescan = Signal()
    cancel_scan = Signal()

    def __init__(self):
        super().__init__()
        self.box = QVBoxLayout(self)
        self.box.addWidget(label('Hardware', 'title'))
        self.status = label('Waiting for hardware scan…', 'muted')
        self.box.addWidget(self.status)
        self.scan_button = button('Re-scan hardware', self.rescan.emit, True)
        self.cancel_button = button('Cancel scan', self.cancel_scan.emit)
        self.cancel_button.setEnabled(False)
        self.box.addLayout(row(self.scan_button, self.cancel_button))
        self.box.addWidget(label('✓ = this exact test passed. ✗ = absent / failed. ? = untested, not enabled. '
                                 'Measured speed is synthetic encode speed, not a prediction for your filters. '
                                 'Tested sizes are NOT maximum hardware limits. Driver / physical GPU binding may be unknown.', 'muted'))
        self.cards = QWidget()
        self.cards_box = QVBoxLayout(self.cards)
        self.cards_box.setContentsMargins(0, 0, 0, 0)
        self.box.addWidget(self.cards)
        self.box.addStretch()
        self.matrix = None

    def scanning(self, value):
        self.scan_button.setEnabled(not value)
        self.cancel_button.setEnabled(value)

    def set_matrix(self, data):
        self.scanning(False)
        while self.cards_box.count():
            item = self.cards_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not data:
            self.status.setText('Hardware scan failed; see Settings / Logs')
            return
        self.matrix = HardwareMatrix.from_dict(data)
        self.status.setText('Scan complete · unavailable options remain disabled · cache follows FFmpeg + driver fingerprint')
        for gpu in self.matrix.gpus:
            card = Card(gpu.name, True)
            ram = f'{gpu.ram / 1024**3:.1f} GB (reported; WMI may truncate)' if gpu.ram is not None else 'unknown'
            card.box.addWidget(label(f'{gpu.vendor} · PCI {gpu.vendor_id}:{gpu.device_id} · {gpu.generation}\n'
                                     f'Driver: {gpu.driver or "unknown"} · RAM: {ram}\n{gpu.affinity}', 'muted'))
            entries = [cap for cap in self.matrix.encoders if cap.gpu_id == gpu.id]
            if not entries:
                card.box.addWidget(label('No separately bound encoder test. See runtime-selected adapter card; physical adapter ordinal is unknown.', 'muted'))
            for cap in entries:
                speed = f'{cap.speed:.2f}×' if cap.speed is not None else 'speed unreported'
                title = f'{"✓" if cap.available else "✗"} {cap.name} · {speed}'
                btn = button(title, lambda _, c=cap: dialogs.details(self, c.name, json.dumps(c.to_dict(), ensure_ascii=False, indent=2)))
                btn.setToolTip('Open exact probe commands, measured flags and failure reasons')
                card.box.addWidget(btn)
                if cap.available:
                    samples = ', '.join(f'{s["width"]}×{s["height"]}@{s["fps"]} / {s["bit_depth"]}-bit {s["chroma"]}' for s in cap.samples)
                    info = label('Tested: ' + samples + '\nRate control: ' + ', '.join(cap.modes) +
                                 '\nB-frames: ' + ', '.join(cap.tested_values.get('bf', [])) +
                                 ' · lookahead: ' + ', '.join(cap.tested_values.get('look_ahead', cap.tested_values.get('rc-lookahead', []))) +
                                 ' · low_power: ' + ', '.join(cap.tested_values.get('low_power', [])) +
                                 '\nHDR: ? untested · Max resolution/fps: ? not established', 'muted')
                else:
                    info = label(cap.reason, 'muted')
                    info.setEnabled(False)
                card.box.addWidget(info)
                for key, feature in cap.features.items():
                    if isinstance(feature, dict) and not feature.get('available'):
                        disabled = label('✗ ' + key + ': ' + feature.get('reason', 'Not proven'), 'muted')
                        disabled.setEnabled(False)
                        card.box.addWidget(disabled)
            self.cards_box.addWidget(card)
        details = button('FFmpeg build / decoder test details', lambda: dialogs.details(
            self, 'Active FFmpeg inventory', json.dumps(self.matrix.inventory, ensure_ascii=False, indent=2)))
        self.cards_box.addWidget(details)
