from dataclasses import asdict, fields
from pathlib import Path
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QGridLayout, QLineEdit, QComboBox,
                              QDoubleSpinBox, QSpinBox, QFormLayout)
from app.core.tv_rules import Options, PRESETS, DESCRIPTIONS
from app.ui.widgets.common import Card, Pills, Toggle, label, button, row
from app.ui import dialogs
from app.i18n import tr


def combo(items, value=None):
    widget = QComboBox()
    for item in items:
        widget.addItem(tr(item), item)
    if value is not None:
        idx = widget.findData(value)
        if idx >= 0:
            widget.setCurrentIndex(idx)
    return widget


def selected(widget):
    return widget.currentData()


class FixerPage(QWidget):
    add_requested = Signal(str, object, bool)
    watch_requested = Signal(str, object)
    stop_watch_requested = Signal()

    def __init__(self, settings, manager, hqx=False):
        super().__init__()
        self.settings, self.manager, self.hqx = settings, manager, hqx
        stored = settings.get('hqx_options' if hqx else 'fixer_options', {})
        allowed = {f.name for f in fields(Options)}
        opts = Options(**{k: v for k, v in stored.items() if k in allowed})
        self.controls = {}
        self.encoder_choice = combo(['h264_qsv', 'hevc_qsv', 'av1_qsv'], opts.codec)
        self.encoder_choice.setToolTip('HEVC / AV1 require modern playback devices. The hardware engine preserves the target codec across GPUs / CPU. Missing encoders are disabled.')
        self.encoder_choice.setVisible(hqx)
        self.box = QVBoxLayout(self)
        self.box.setSpacing(15)
        folder_card = Card('HQX Converter' if hqx else 'MP4 Profile Fixer', True)
        folder_card.box.addWidget(label('Recursive watch folder · stable size + modification time · source files preserved by default'
                                         if hqx else 'Rebuild timing. Remove unsafe streams. Deliver one verified TV-ready MP4.', 'muted'))
        self.folder = QLineEdit(settings.get('watch_folder' if hqx else 'fixer_folder', ''))
        self.folder.setPlaceholderText(tr('Folder'))
        folder_card.box.addLayout(row(self.folder, button('Browse', self.browse)))
        self.box.addWidget(folder_card)
        grid = QGridLayout()
        grid.setSpacing(15)
        preset_card = Card('TV preset')
        self.preset = combo(list(PRESETS), opts.preset)
        preset_card.box.addWidget(self.preset)
        preset_card.box.addWidget(self.encoder_choice)
        self.hevc = Toggle('HEVC Main (older TVs cannot play HEVC)', opts.codec == 'hevc_qsv' and not hqx)
        self.hevc.setEnabled(False)
        preset_card.box.addWidget(self.hevc)
        self.description = label(DESCRIPTIONS[opts.preset], 'muted')
        preset_card.box.addWidget(self.description)
        self.preset.currentIndexChanged.connect(self.preset_changed)
        self.simulation = Toggle('Test with TV simulation (verify only)', opts.simulation)
        self.skip = Toggle('Skip already TV-safe files', opts.skip_safe)
        preset_card.box.addWidget(self.simulation)
        preset_card.box.addWidget(self.skip)
        grid.addWidget(preset_card, 0, 0)
        hardware_card = Card('Hardware')
        hardware_form = QFormLayout()
        self.field(hardware_form, 'Hardware for these jobs', 'hardware', combo(
            ['Global', 'Auto', 'Intel', 'NVIDIA', 'AMD', 'CPU'], opts.hardware))
        self.field(hardware_form, 'Quality / speed', 'quality_speed', combo(
            ['Global', 'Fast', 'Balanced', 'High Quality', 'Max Quality'], opts.quality_speed))
        self.controls['hardware'].setToolTip('Global follows Settings when queued work starts; vendor choices prefer that vendor, then visibly try the next proven option.')
        self.controls['quality_speed'].setToolTip('Global follows the existing QSV Advanced preset, translated for each encoder. Only presets that passed a hardware test are used; substitutions are logged.')
        hardware_card.box.addLayout(hardware_form)
        self.box.addWidget(hardware_card)
        video = Card('Video')
        form = QFormLayout()
        self.field(form, 'Profile', 'profile', combo(['baseline', 'main', 'high'], opts.profile))
        self.controls['profile'].setToolTip('TV mode disables High10 / High422 / High444: only 8-bit 4:2:0 is safe.')
        self.field(form, 'Level', 'level', combo(['Auto', '3.0', '3.1', '3.2', '4.0', '4.1', '4.2', '5.0', '5.1', '5.2'], opts.level))
        self.field(form, 'Rate control', 'rc', combo(['VBR', 'CBR', 'Quality', 'CQ', 'ICQ'], 'Quality' if opts.rc in ('CQP', 'ICQ') else opts.rc))
        self.controls['rc'].setToolTip('Quality uses tested ICQ; if unsupported, uses explicit VBR with the selected bitrate/maxrate/buffer. Software uses CRF.')
        for title, key in [('Bitrate (Mbps)', 'bitrate'), ('Maxrate (Mbps)', 'maxrate'), ('Buffer (Mbps)', 'bufsize')]:
            spin = QDoubleSpinBox()
            spin.setRange(.1, 100)
            spin.setValue(getattr(opts, key))
            self.field(form, title, key, spin)
        quality = QSpinBox()
        quality.setRange(1, 51)
        quality.setValue(opts.quality)
        self.field(form, 'Quality', 'quality', quality)
        video.box.addLayout(form)
        grid.addWidget(video, 0, 1)
        timing = Card('Frame Rate', True)
        choices = ['Auto', '23.976', '24', '25', '29.97', '30', '50', '59.94', '60', 'Custom']
        chosen = opts.fps if opts.fps in choices else 'Custom'
        self.fps = Pills(choices, chosen)
        timing.box.addWidget(self.fps)
        self.custom_fps = QLineEdit(opts.fps if chosen == 'Custom' else '30000/1001')
        self.custom_fps.setPlaceholderText('e.g. 30000/1001 or 27.5')
        self.custom_fps.setVisible(chosen == 'Custom')
        self.fps.selected.connect(lambda value: self.custom_fps.setVisible(value == 'Custom'))
        timing.box.addWidget(self.custom_fps)
        form = QFormLayout()
        self.field(form, 'Conversion method', 'method', combo(['Drop / Duplicate', 'Frame blend', 'Motion interpolation'], opts.method))
        self.controls['method'].setToolTip('Motion interpolation is CPU-intensive and may create artifacts. Integer multiples use clean duplication.')
        self.field(form, 'Resolution cap', 'resolution', combo(['keep', '1080p', '720p'], opts.resolution))
        self.field(form, 'FPS cap', 'fps_cap', combo(['keep', '30', '25'], opts.fps_cap))
        timing.box.addLayout(form)
        self.deinterlace = Toggle('Deinterlace', opts.deinterlace)
        self.double = Toggle('Double rate', opts.double_rate)
        timing.box.addLayout(row(self.deinterlace, self.double))
        self.detected = label('Auto selects each file’s own dominant rational cadence. Choose a fixed rate to unify the folder.', 'muted')
        timing.box.addWidget(self.detected)
        grid.addWidget(timing, 1, 0, 1, 2)
        output = Card('Output')
        form = QFormLayout()
        self.field(form, 'Output', 'output_mode', combo(['Same folder', '_TV subfolder', 'Custom'], opts.output_mode))
        custom_row = QWidget()
        self.custom_output = QLineEdit(opts.output_folder)
        layout = row(self.custom_output, button('Browse', lambda: self.choose_path(self.custom_output)))
        layout.setContentsMargins(0, 0, 0, 0)
        custom_row.setLayout(layout)
        form.addRow(tr('Custom'), custom_row)
        self.field(form, 'Suffix', 'suffix', QLineEdit(opts.suffix))
        self.field(form, 'Overwrite policy', 'overwrite', combo(['Skip', 'Rename', 'Replace'], opts.overwrite))
        self.field(form, 'FAT32 guard', 'fat32', combo(['Warn / ignore', 'Lower bitrate to fit', 'Split into parts'], opts.fat32))
        output.box.addLayout(form)
        self.delete = Toggle('Delete source only after verified success', opts.delete_source)
        output.box.addWidget(self.delete)
        grid.addWidget(output, 2, 0)
        audio = Card('Audio')
        form = QFormLayout()
        self.field(form, 'Audio', 'audio', combo(['TV-safe', 'Keep all'], 'TV-safe' if opts.audio == 'Copy' else opts.audio))
        audio.box.addLayout(form)
        audio.box.addWidget(label('AAC-LC · 48 kHz · stereo · 192 kbps\nAsync resampling, start-offset repair, duration verification <40 ms.\nMissing audio → silent stereo track.', 'muted'))
        audio.box.addWidget(label('TV audio is always re-encoded to AAC-LC stereo 48 kHz, 192 kbps. Legacy Copy settings cannot bypass safety. Multiple audio tracks may not play on older TVs.', 'muted'))
        grid.addWidget(audio, 2, 1)
        self.box.addLayout(grid)
        controls = [button('Add folder', self.add_only), button('Analyze files', self.analyze),
                    button('Start Watching' if hqx else 'Start Fix', self.start, True),
                    button('Stop Watching' if hqx else 'Stop all', self.stop)]
        self.box.addLayout(row(*controls))
        self.box.addStretch()
        self._saved_hardware = opts.hardware
        self._hardware_caps = None
        self.encoder_choice.currentIndexChanged.connect(lambda _: self.apply_hardware_caps(self._hardware_caps))
        self.controls['hardware'].currentIndexChanged.connect(lambda _: self.apply_hardware_caps(self._hardware_caps))
        self.hevc.toggled.connect(lambda _: self.apply_hardware_caps(self._hardware_caps))
        self.preset_changed()

    def apply_hardware_caps(self, caps):
        if not caps or not caps.get('matrix'):
            return
        from app.core.hardware.select import codec_of
        from app.core.hardware.matrix import HardwareMatrix
        self._hardware_caps = caps
        matrix = HardwareMatrix.from_dict(caps['matrix'])
        codec = codec_of(selected(self.encoder_choice)) if self.hqx else ('hevc' if selected(self.preset) == '4K TV' and self.hevc.isChecked() else 'h264')
        control = self.controls['hardware']
        control.blockSignals(True)
        for gpu in matrix.gpus:
            if gpu.vendor != 'CPU' and any(c.gpu_id == gpu.id for c in matrix.encoders) and control.findData(gpu.id) < 0:
                control.addItem(gpu.name + ' [tested runtime slot]', gpu.id)
        if self._saved_hardware and control.findData(self._saved_hardware) >= 0:
            control.setCurrentIndex(control.findData(self._saved_hardware))
            self._saved_hardware = None
        control.blockSignals(False)
        for i in range(control.count()):
            vendor = control.itemData(i)
            available = vendor in ('Global', 'Auto') or any(c.available and (c.vendor == vendor or c.gpu_id == vendor) and c.codec == codec for c in matrix.encoders)
            control.model().item(i).setEnabled(available)
            control.setItemData(i, '' if available else 'No tested ' + codec + ' encoder for this vendor', 3)
        device = selected(control)
        if device == 'Global':
            device = self.settings.get('hardware', 'Auto')
        choices = [c for c in matrix.encoders if c.available and c.codec == codec and (device == 'Auto' or c.vendor == device or c.gpu_id == device)]
        rc = self.controls['rc']
        for i in range(rc.count()):
            value = rc.itemData(i)
            available = any((bool(set(c.modes) & {'CQ', 'ICQ'}) if value == 'Quality' else
                             ('CQ' in c.modes if value == 'ICQ' and c.vendor != 'Intel' else
                              'ICQ' in c.modes if value == 'CQ' and c.vendor == 'Intel' else value in c.modes)) for c in choices)
            rc.model().item(i).setEnabled(available)
            rc.setItemData(i, '' if available else 'This rate control has not passed a test on the selected vendor / codec', 3)

    def field(self, layout, title, key, widget):
        self.controls[key] = widget
        layout.addRow(tr(title), widget)

    def preset_changed(self):
        preset = selected(self.preset)
        self.description.setText(DESCRIPTIONS[preset])
        self.hevc.setVisible(preset == '4K TV' and not self.hqx)
        self.controls['profile'].setEnabled(preset == 'Custom')
        self.apply_hardware_caps(self._hardware_caps)

    def choose_path(self, edit):
        value = dialogs.folder(self, edit.text())
        if value:
            edit.setText(value)

    def browse(self):
        self.choose_path(self.folder)

    def options(self):
        opts = Options()
        for key, widget in self.controls.items():
            value = selected(widget) if isinstance(widget, QComboBox) else widget.text() if isinstance(widget, QLineEdit) else widget.value()
            setattr(opts, key, value)
        opts.preset = selected(self.preset)
        opts.codec = selected(self.encoder_choice) if self.hqx else ('hevc_qsv' if opts.preset == '4K TV' and self.hevc.isChecked() else 'h264_qsv')
        opts.fps = self.custom_fps.text().strip() if self.fps.value == 'Custom' else self.fps.value
        opts.output_folder = self.custom_output.text().strip()
        opts.deinterlace = self.deinterlace.isChecked()
        opts.double_rate = self.double.isChecked()
        opts.skip_safe = self.skip.isChecked()
        opts.simulation = self.simulation.isChecked()
        opts.delete_source = self.delete.isChecked()
        opts.scratch = self.settings.get('scratch', '')
        opts.safe_mode = self.settings.get('safe_mode',True)
        opts.high_priority = self.settings.get('high_priority',False)
        opts.encoder_preset = self.settings.get('encoder_preset', 'medium')
        opts.advanced = self.settings.get('advanced', {})
        opts.failure_policy = self.settings.get('failure_policy', 'Use next')
        opts.language = self.settings.get('language', 'en')
        self.settings.set('hqx_options' if self.hqx else 'fixer_options', asdict(opts))
        self.settings.set('watch_folder' if self.hqx else 'fixer_folder', self.folder.text())
        return opts

    def add_only(self):
        self.add_requested.emit(self.folder.text(), self.options(), False)

    def analyze(self):
        # Set the intent before the signal, including for same-thread/direct connections.
        self.setProperty('analyze_next', True)
        self.add_requested.emit(self.folder.text(), self.options(), False)

    def start(self):
        opts = self.options()
        if self.hqx:
            self.watch_requested.emit(self.folder.text(), opts)
        else:
            self.add_requested.emit(self.folder.text(), opts, True)

    def stop(self):
        self.stop_watch_requested.emit() if self.hqx else self.manager.stop_all()
