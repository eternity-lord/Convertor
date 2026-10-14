import threading
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QGridLayout, QFormLayout, QLineEdit, QSpinBox, QComboBox
from app.ui.widgets.common import Card, Toggle, label, button, row
from app.ui.pages.fixer import combo, selected
from app.ui import dialogs
from app.core.system_tweaks import elevate
from app.i18n import tr

ADVANCED = {
    'look_ahead': 'Look-ahead', 'look_ahead_depth': 'Look-ahead depth', 'trellis': 'Trellis',
    'rdo': 'Rate-distortion optimization', 'extbrc': 'Extended bitrate control',
    'low_delay_brc': 'Low-delay bitrate control', 'max_frame_size': 'Maximum frame size',
    'max_frame_size_i': 'Maximum I-frame size', 'max_frame_size_p': 'Maximum P-frame size',
    'max_frame_size_b': 'Maximum B-frame size', 'scenario': 'Usage scenario',
    'b_strategy': 'B-frame strategy', 'gpb': 'HEVC GPB',
}


class AdvancedPage(QWidget):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.box = QVBoxLayout(self)
        self.box.addWidget(label('QSV Advanced', 'title'))
        self.box.addWidget(label('Only options proven by the capability probe can be enabled. TV profile, GOP, B-frames and references stay safety-locked.', 'muted'))
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search parameter…')
        self.search.textChanged.connect(self.filter)
        self.box.addWidget(self.search)
        self.encoder = combo(['h264_qsv', 'hevc_qsv', 'av1_qsv'])
        self.encoder.setToolTip('TV mode uses AVC only. HEVC / AV1 availability is informational.')
        self.encoder.currentIndexChanged.connect(lambda _: self.apply_caps(self.caps))
        self.box.addWidget(self.encoder)
        card = Card('Encoder preset')
        self.preset = combo(['veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow'], settings.get('encoder_preset','medium'))
        self.preset.currentIndexChanged.connect(lambda _: settings.set('encoder_preset', selected(self.preset)))
        card.box.addWidget(self.preset)
        card.box.addWidget(label('Medium / slow is recommended. Veryslow may add latency with little TV-visible improvement.', 'muted'))
        self.box.addWidget(card)
        grid = QGridLayout()
        self.entries = {}
        self.cards = {}
        self.caps = {}
        stored = settings.get('advanced', {})
        for i, (key, title) in enumerate(ADVANCED.items()):
            card = Card(title)
            edit = QLineEdit(str(stored.get(key, '')))
            edit.setPlaceholderText('Auto / unsupported')
            edit.setEnabled(False)
            edit.setToolTip('Disabled until this exact parameter passes a hardware encode test.')
            card.box.addWidget(label(key, 'muted'))
            card.box.addWidget(edit)
            grid.addWidget(card, i//2, i%2)
            self.entries[key], self.cards[key] = edit, card
        self.box.addLayout(grid)
        self.box.addWidget(button('Save settings', self.save))
        self.box.addStretch()

    def filter(self, query):
        for key, card in self.cards.items():
            card.setVisible(query.casefold() in (key + ' ' + ADVANCED[key]).casefold())

    def apply_caps(self, caps):
        self.caps = caps
        supported = caps.get('codecs', {}).get(selected(self.encoder), {})
        for key, edit in self.entries.items():
            edit.setEnabled(key in supported.get('tested_options', []))
        for i in range(self.encoder.count()):
            codec = self.encoder.itemData(i)
            self.encoder.model().item(i).setEnabled(caps.get('codecs',{}).get(codec,{}).get('available',False))

    def save(self):
        self.settings.set('advanced', {key: edit.text().strip() for key, edit in self.entries.items()
                                        if edit.isEnabled() and edit.text().strip()})


class TweaksPage(QWidget):
    apply_requested = Signal(object)
    restore_requested = Signal()
    monitor_requested = Signal()

    def __init__(self):
        super().__init__()
        box = QVBoxLayout(self)
        box.addWidget(label('System Tweaks', 'title'))
        box.addWidget(label('Nothing is applied automatically. Changes are journaled before modification and restored on exit / next start.', 'muted'))
        grid = QGridLayout()
        self.toggles = {}
        values = [
            ('power', 'Performance power plan', 'Clone the current plan; AC CPU min/max 100%. Original plan stays untouched.'),
            ('parking', 'Disable core parking', 'Unpark AC-powered CPU cores in a temporary power plan.'),
            ('priority', 'Priority guard', 'Opt-in EDIUS priority only. FFmpeg priority is controlled by Settings → High priority; originals are restored.'),
            ('thermal', 'Thermal guard', 'Reduce encode concurrency and process priority above 85°C. Sensors may be unavailable.'),
            ('timer', '1 ms timer resolution', 'Request fine-grained Windows scheduling until exit.'),
            ('visual', 'Reduce visual effects', 'Temporarily select Windows performance visual settings.'),
            ('services', 'Pause background services', 'Opt-in only: stop SysMain, Search, Maps and location; startup type is not changed.'),
            ('apps', 'Suspend resource-heavy apps', 'Reversible suspension instead of killing browsers / losing unsaved work.'),
            ('boris', 'BorisFX GPU integration', 'Back up s_config.text before enabling host_gpu_integration.'),
            ('ramdisk', 'Create 2 GB RAM disk (R:)', 'Requires ImDisk. Uses physical RAM; avoid on low-memory PCs. Removed on restore.'),
            ('defender', 'Defender folder exclusion', 'Security trade-off: excludes only the selected folder until restore.'),
        ]
        for i, (key,title,description) in enumerate(values):
            card = Card()
            toggle = Toggle(title, key == 'thermal')
            card.box.addWidget(toggle)
            card.box.addWidget(label(description, 'muted'))
            self.toggles[key] = toggle
            grid.addWidget(card, i//2, i%2)
        box.addLayout(grid)
        self.folder = QLineEdit()
        self.folder.setPlaceholderText('Defender exclusion folder (optional)')
        box.addLayout(row(self.folder, button('Browse', self.browse)))
        box.addLayout(row(button('Apply selected tweaks', self.apply, True),
                          button('Restore originals', self.restore_requested.emit),
                          button('Show monitor', self.monitor_requested.emit)))
        box.addStretch()

    def browse(self):
        value = dialogs.folder(self)
        if value:
            self.folder.setText(value)

    def apply(self):
        options = {key: toggle.isChecked() for key, toggle in self.toggles.items()}
        options['folder'] = self.folder.text()
        self.apply_requested.emit(options)


class SettingsPage(QWidget):
    language_changed = Signal(str)
    reprobe = Signal()
    concurrency_changed = Signal(int)
    monitor_requested = Signal()
    execution_changed = Signal(bool, bool)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        box = QVBoxLayout(self)
        card = Card('Settings', True)
        self.lang = combo(['EN', 'فارسی'], 'فارسی' if settings.get('language') == 'fa' else 'EN')
        self.lang.currentIndexChanged.connect(lambda _: self.language_changed.emit('fa' if selected(self.lang) == 'فارسی' else 'en'))
        form = QFormLayout()
        form.addRow('Language / زبان', self.lang)
        self.parallel = combo(['1', '2', '3'], str(settings.get('parallel','1')))
        self.parallel.currentIndexChanged.connect(self.change_parallel)
        form.addRow('Parallel jobs', self.parallel)
        self.scratch = QLineEdit(settings.get('scratch',''))
        scratch_row = QWidget()
        layout = row(self.scratch, button('Browse', self.browse))
        layout.setContentsMargins(0,0,0,0)
        scratch_row.setLayout(layout)
        form.addRow('NVMe / RAM disk scratch', scratch_row)
        card.box.addLayout(form)
        self.safe = Toggle('Safe mode', settings.get('safe_mode',True))
        self.performance = Toggle('Performance mode', not settings.get('safe_mode',True))
        self.high_priority = Toggle('High priority (opt-in)', settings.get('high_priority',False))
        self.safe.toggled.connect(self.safe_changed)
        self.performance.toggled.connect(lambda value:self.safe.setChecked(not value))
        self.high_priority.toggled.connect(lambda _:self.execution_changed_now())
        card.box.addLayout(row(self.safe,self.performance))
        card.box.addWidget(self.high_priority)
        card.box.addWidget(label('Default: one job, NORMAL priority, leave two logical cores free, 16 GPU upload frames. Free RAM <3 GB forces one job. Sustained RAM >90% / CPU >95% pauses the queue after 10 seconds in either mode.', 'muted'))
        self.warning = label('Warning: 2–3 jobs, High priority or Performance mode can make the PC unresponsive. Changes apply to newly started jobs. Performance mode uses all encoder threads; resource guards remain enabled.', 'muted')
        self.warning.setStyleSheet('color:#e8c278;')
        card.box.addWidget(self.warning)
        self.update_warning()

        card.box.addLayout(row(button('Save settings', lambda: settings.set('scratch',self.scratch.text())),
                                button('Re-probe hardware', self.reprobe.emit)))
        card.box.addLayout(row(button('Restart as administrator', self.admin), button('Show monitor', self.monitor_requested.emit)))
        self.capabilities = label('Checking FFmpeg / QSV…', 'muted')
        card.box.addWidget(self.capabilities)
        box.addWidget(card)
        box.addStretch()

    def browse(self):
        value = dialogs.folder(self)
        if value:
            self.scratch.setText(value)

    def change_parallel(self):
        value = selected(self.parallel)
        self.settings.set('parallel',value)
        self.concurrency_changed.emit(int(value))
        self.update_warning()

    def safe_changed(self, value):
        self.performance.blockSignals(True)
        self.performance.setChecked(not value)
        self.performance.blockSignals(False)
        self.execution_changed_now()

    def execution_changed_now(self):
        self.settings.set('safe_mode',self.safe.isChecked())
        self.settings.set('high_priority',self.high_priority.isChecked())
        self.execution_changed.emit(self.safe.isChecked(),self.high_priority.isChecked())
        self.update_warning()

    def update_warning(self):
        self.warning.setVisible(int(selected(self.parallel) or '1') > 1 or
                                not self.safe.isChecked() or self.high_priority.isChecked())

    def admin(self):
        if elevate():
            self.window().close()


class SupportPage(QWidget):
    def __init__(self):
        super().__init__()
        box = QVBoxLayout(self)
        card = Card('EDIUS Ultimate Render Booster', True)
        card.box.addWidget(label('7.0 · Intel QSV / verified TV compatibility', 'number'))
        card.box.addWidget(label('Designed for Mohammad Irani\nOriginal application and credit: Mohammad Irani', 'title'))
        card.box.addWidget(label('TV simulation is a policy checker, not a physical television emulator. Playback depends on the television firmware, USB filesystem and supported codec limits.\n\nQSV availability depends on Intel hardware, drivers and the installed FFmpeg build. GPU utilisation may be unavailable without vendor performance counters.\n\nSource media is never deleted unless you explicitly select deletion and the output passes verification.', 'muted'))
        card.box.addWidget(button('Project on GitHub', self.project))
        box.addWidget(card)
        box.addStretch()

    def project(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl('https://github.com/eternity-lord/Convertor'))
