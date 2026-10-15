import os
import threading
import time
from collections import deque
from dataclasses import replace
from pathlib import Path
from datetime import date
from PySide6.QtCore import Qt, Signal, QTimer, QByteArray, QUrl
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QDesktopServices
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, QLineEdit,
                              QSystemTrayIcon, QMenu, QApplication, QMessageBox)
from app.core.capabilities import probe_engine as probe_capabilities
from app.core.process import tool, capture, terminate_all, configure_tools
from app.core.queue import QueueManager, FolderWatcher, TERMINAL, MAX_JOBS
from app.core.system_tweaks import SystemTweaks, cpu_temperature
from app.ui.widgets.common import GlassWindow, Card, Pills, label, button, row, scroll
from app.ui.pages.fixer import FixerPage
from app.ui.pages.hardware import HardwarePage
from app.ui.pages.dashboard import Dashboard
from app.ui.pages.tools import AdvancedPage, TweaksPage, SettingsPage, SupportPage
from app.ui.log_view import LogView
from app.ui.encoding_window import EncodingWindow
from app.ui import dialogs
from app.i18n import tr, set_language

PAGES = ['Dashboard', 'MP4 Profile Fixer', 'HQX Converter', 'QSV Advanced', 'Hardware', 'System Tweaks', 'Logs', 'Settings', 'Support']


class Monitor(GlassWindow):
    def __init__(self):
        super().__init__('EDIUS Turbo Monitor · Mohammad Irani', (320, 160))
        self.resize(350, 180)
        self.setWindowFlag(Qt.WindowStaysOnTopHint)
        self.reading = label('CPU · RAM · Temperature', 'title')
        self.root.addWidget(self.reading)


class MainWindow(GlassWindow):
    capabilities_ready = Signal(object)
    hardware_progress = Signal(str)
    scan_ready = Signal(object)
    metrics_ready = Signal(object)
    temperature_ready = Signal(object)
    background_log = Signal(str, str, str, str)
    cleanup_done = Signal()

    def __init__(self, settings):
        super().__init__('EDIUS Booster · Studio v7', (1100,720))
        self.settings = settings
        self.closing = False
        self.cleanup_done.connect(QApplication.instance().quit)
        self.resize(1400, 900)
        geometry = settings.get('geometry')
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode()))
        self.manager = QueueManager()
        self.scan_cancel = threading.Event()
        self.fallback_dialogs = {}
        self.manager.fallback_needed.connect(self.ask_hardware_fallback)
        self.sync_hardware_policy()
        # One-time migration: old Auto=two/GPU or explicit four must not carry unsafe defaults.
        if settings.get('safety_policy_version',0) < 1:
            for key,value in {'safe_mode':True,'high_priority':False,'parallel':'1',
                              'safety_policy_version':1}.items():
                settings.set(key,value)
        parallel = str(settings.get('parallel','1'))
        self.manager.limit = min(3,max(1,int(parallel) if parallel != 'Auto' else 1))
        settings.set('parallel',str(self.manager.limit))
        self.manager.safe_mode = settings.get('safe_mode',True)
        self.manager.high_priority = settings.get('high_priority',False)

        self.encoding = EncodingWindow(self.manager)
        self.monitor = Monitor()
        self.watcher = None
        self.scan_busy = False
        self.metric_busy = False
        self.temperature_busy = False
        self.last_temperature_poll = 0
        self.last_temperature = None
        self.cap_busy = False
        self.last_metrics = {}
        self.completed_ids = set()
        self.daily = settings.get('daily', {})
        if self.daily.get('date') != date.today().isoformat():
            self.daily = {'date':date.today().isoformat(),'files':0,'size':0,'saved':0}
        self.telemetry = {'Speed': deque(maxlen=1800), 'CPU': deque(maxlen=1800), 'GPU': deque(maxlen=1800)}
        self.logs = LogView()
        self.content = None
        self.build_ui()
        self.background_log.connect(self.logs.append)
        self.manager.log.connect(self.logs.append)
        self.manager.changed.connect(self.queue_changed)
        self.scan_ready.connect(self.scanned)
        self.capabilities_ready.connect(self.set_capabilities)
        self.hardware_progress.connect(self.show_hardware_progress)
        self.metrics_ready.connect(self.set_metrics)
        self.temperature_ready.connect(self.set_temperature)
        self.tweaks = None
        self.tweak_busy = False
        self.tweaks_ready = threading.Event()
        def make_tweaks():
            self.tweaks = SystemTweaks(lambda level,msg: self.background_log.emit(level,msg,'',''))
            self.tweaks_ready.set()
        threading.Thread(target=make_tweaks, daemon=True).start()
        self.encoding.completion_action.connect(self.complete)
        self.create_tray()
        self.manager.safety_alert.connect(self.safety_toast)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.poll_metrics)
        self.timer.start()
        self.probe_hardware()
        self.manager.finished.connect(lambda: self.tray.showMessage('EDIUS Booster', 'Queue finished. Check logs for verification results.', QSystemTrayIcon.Information, 6000) if self.tray.isVisible() else None)
        self.background_log.emit('INFO','Ready. Select a folder to analyze or convert.','','')

    def build_ui(self):
        old = self.content
        if old:
            for page in (self.fixer, self.hqx):
                page.options()
                page.enhance_card.dispose()
        # Keep the bounded log model alive across language changes.
        self.logs.setParent(None)
        self.content = QWidget()
        outer = QHBoxLayout(self.content)
        outer.setContentsMargins(6, 4, 6, 6)
        outer.setSpacing(15)
        sidebar = Card()
        sidebar.setFixedWidth(216)
        sidebar.box.setContentsMargins(12,20,12,16)
        sidebar.box.addWidget(label('H  EDIUS Booster', 'title'))
        sidebar.box.addWidget(label('   CREATIVE STUDIO / 07', 'muted'))
        sidebar.box.addSpacing(15)
        self.nav = {}
        glyphs = {'Dashboard':'◴', 'MP4 Profile Fixer':'▣', 'HQX Converter':'▤',
                  'QSV Advanced':'⌘', 'Hardware':'▦', 'System Tweaks':'◇',
                  'Logs':'≡', 'Settings':'⚙', 'Support':'◈'}
        for i, name in enumerate(PAGES):
            if name == 'Settings':
                sidebar.box.addStretch()
                sidebar.box.addWidget(button('Encoding queue', self.encoding.open))
            btn = button(f'{glyphs.get(name, "◇")}   {tr(name)}', lambda checked=False,n=name: self.navigate(n))
            btn.setProperty('nav',True)
            btn.setCheckable(True)
            sidebar.box.addWidget(btn)
            self.nav[name] = btn
        outer.addWidget(sidebar)
        right = QVBoxLayout()
        right.setSpacing(14)
        welcome = QWidget()
        welcome_box = QVBoxLayout(welcome)
        welcome_box.setContentsMargins(0,0,0,0)
        welcome_box.addWidget(label(f'{tr("Welcome,")} Mamad', 'title'))
        welcome_box.addWidget(label('Your studio. Faster, safer, beautifully in sync.', 'muted'))
        self.cap_badge = label('QSV · probing…', 'badge')
        header = row(welcome, None, self.cap_badge, button('◉', self.encoding.open), button('⚙', lambda: self.navigate('Settings')),
                     label('Mamad\nCreator workspace', 'muted'))
        right.addLayout(header)
        self.search = QLineEdit()
        self.search.setPlaceholderText('⌕   ' + tr('Search files / filter logs'))
        self.search.textChanged.connect(self.search_changed)
        self.search.setMaximumWidth(340)
        self.tabs = Pills(['Studio', 'Convert', 'Tools'], 'Studio')
        self.tabs.selected.connect(lambda name: self.navigate({'Studio':'Dashboard','Convert':'MP4 Profile Fixer','Tools':'System Tweaks'}[name]))
        right.addLayout(row(self.tabs, None, self.search))
        self.stack = QStackedWidget()
        self.dashboard = Dashboard(lambda: self.navigate('MP4 Profile Fixer'))
        self.fixer = FixerPage(self.settings, self.manager)
        self.hqx = FixerPage(self.settings, self.manager, True)
        self.advanced = AdvancedPage(self.settings)
        self.advanced.preset.currentIndexChanged.connect(lambda _: self.sync_hardware_policy())
        self.hardware_page = HardwarePage()
        self.hardware_page.rescan.connect(lambda: self.probe_hardware(True))
        self.hardware_page.cancel_scan.connect(self.scan_cancel.set)
        self.tweaks_page = TweaksPage()
        self.settings_page = SettingsPage(self.settings)
        self.settings_page.language_changed.connect(self.change_language)
        self.settings_page.reprobe.connect(lambda: self.probe_hardware(True))
        self.settings_page.concurrency_changed.connect(lambda value: setattr(self.manager,'limit',value))
        self.settings_page.execution_changed.connect(self.set_execution_policy)
        self.settings_page.hardware_policy_changed.connect(self.sync_hardware_policy)
        self.settings_page.tools_changed.connect(self.apply_tool_paths)
        self.settings_page.monitor_requested.connect(self.monitor.show)
        self.tweaks_page.apply_requested.connect(self.apply_tweaks)
        self.tweaks_page.restore_requested.connect(self.restore_tweaks)
        self.tweaks_page.monitor_requested.connect(self.monitor.show)
        self.pages = [self.dashboard, self.fixer, self.hqx, self.advanced, self.hardware_page, self.tweaks_page,
                      self.logs, self.settings_page, SupportPage()]
        for page in self.pages:
            self.stack.addWidget(scroll(page) if page is not self.logs else page)
        for page in (self.fixer, self.hqx):
            page.add_requested.connect(lambda folder, options, start, p=page: self.scan_folder(folder, options, start, p))
        self.hqx.watch_requested.connect(self.start_watch)
        self.hqx.stop_watch_requested.connect(self.stop_watch)
        right.addWidget(self.stack, 1)
        outer.addLayout(right,1)
        self.root.addWidget(self.content)
        if old:
            self.root.removeWidget(old)
            old.deleteLater()
        self.navigate('Dashboard')
        self.setLayoutDirection(Qt.RightToLeft if self.settings.get('language') == 'fa' else Qt.LeftToRight)
        self.advanced.apply_caps(self.manager.capabilities)
        scanning = self.cap_busy
        if self.manager.capabilities:
            self.set_capabilities(self.manager.capabilities)
        self.cap_busy = self.manager.hardware_scanning = scanning
        self.hardware_page.scanning(scanning)
        self.queue_changed()

    def navigate(self, name):
        self.stack.setCurrentIndex(PAGES.index(name))
        for key, btn in self.nav.items():
            btn.setChecked(key == name)

    def search_changed(self, query):
        if self.stack.currentIndex() == PAGES.index('Logs'):
            self.logs.search.setText(query)
        else:
            self.encoding.open()
            for i, job in enumerate(self.manager.jobs):
                self.encoding.table.setRowHidden(i, query.casefold() not in job.source.name.casefold())

    def change_language(self, value):
        self.settings.set('language',value)
        set_language(value)
        # Preserve the current queue and workers; recreate only page widgets.
        self.build_ui()
        self.logs.refresh()
        self.logs.append('INFO','Language changed. The encoding window uses the chosen language for live status labels.')

    def create_tray(self):
        pixmap = QPixmap(32,32)
        pixmap.fill(QColor('#71586e'))
        painter = QPainter(pixmap)
        painter.setPen(QColor('#ffd3f4'))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, 'H')
        painter.end()
        self.setWindowIcon(QIcon(pixmap))
        self.tray = QSystemTrayIcon(QIcon(pixmap), self)
        menu = QMenu(self)
        menu.addAction('EDIUS Booster', self.showNormal)
        menu.addAction(tr('Encoding queue'), self.encoding.open)
        menu.addAction(tr('Show monitor'), self.monitor.show)
        menu.addAction('Exit', self.close)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.encoding.open() if reason == QSystemTrayIcon.DoubleClick else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def sync_hardware_policy(self):
        self.manager.global_hardware = self.settings.get('hardware', 'Auto')
        old_preset = self.settings.get('encoder_preset', 'medium')
        self.manager.quality_speed = ('Fast' if old_preset in ('veryfast', 'faster', 'fast') else
                                      'High Quality' if old_preset == 'slow' else
                                      'Max Quality' if old_preset in ('slower', 'veryslow') else 'Balanced')
        self.manager.failure_policy = self.settings.get('failure_policy', 'Use next')
        self.manager.use_all_gpus = self.settings.get('use_all_gpus', False)
        if hasattr(self, 'fixer'):
            self.fixer.apply_hardware_caps(self.manager.capabilities)
            self.hqx.apply_hardware_caps(self.manager.capabilities)

    def apply_tool_paths(self, ffmpeg, ffprobe):
        if self.cap_busy or self.manager.running or self.manager.analysis_running or self.manager.preview_lock.locked():
            self.logs.append('WARN', 'Stop / finish the queue and hardware scan before changing tools.')
            return
        self.settings.set('ffmpeg_path', ffmpeg)
        self.settings.set('ffprobe_path', ffprobe)
        self.probe_hardware(True)

    def show_hardware_progress(self, text):
        self.hardware_page.status.setText(text)
        self.settings_page.capabilities.setText(text)
        self.cap_badge.setText('Hardware · testing…')

    def probe_hardware(self, force=False):
        if self.cap_busy:
            return
        if self.manager.running or self.manager.analysis_running or self.manager.preview_lock.locked():
            self.logs.append('WARN', 'Stop / finish the queue before re-scanning hardware (probes use GPU sessions).')
            return
        self.cap_busy = True
        self.manager.hardware_scanning = True
        self.scan_cancel.clear()
        self.hardware_page.scanning(True)
        paths = (self.settings.get('ffmpeg_path', ''), self.settings.get('ffprobe_path', ''))
        def run():
            try:
                configure_tools(*paths)
                version = capture([tool('ffprobe'), '-hide_banner', '-version'], timeout=10, cancel=self.scan_cancel)
                if 'ffprobe version' not in version:
                    raise ValueError('Selected FFprobe is not a usable FFprobe executable')
                result = probe_capabilities(force, self.hardware_progress.emit, self.scan_cancel)
            except Exception as exc:
                result = {'error':str(exc), 'codecs':{}}
            self.capabilities_ready.emit(result)
        threading.Thread(target=run, daemon=True).start()

    def set_capabilities(self, caps):
        self.cap_busy = False
        self.manager.hardware_scanning = False
        self.hardware_page.scanning(False)
        # Never replace a running job's immutable snapshot of the matrix.
        self.manager.capabilities = caps
        matrix = caps.get('matrix')
        if matrix:
            from app.core.hardware.matrix import HardwareMatrix
            parsed = HardwareMatrix.from_dict(matrix)
            ready = [c for c in parsed.encoders if c.available and c.hardware]
            inv = parsed.inventory
            self.cap_badge.setText(f'{len(ready)} HW encoders ✓' if ready else
                                   'CPU only · tested' if any(c.available for c in parsed.encoders) else 'No usable encoder')
            text = inv.get('version', 'Unknown FFmpeg') + '\n' + inv.get('ffmpeg', '') + '\n' + inv.get('ffprobe', '')
            text += '\nBuild flags: ' + ' '.join(inv.get('build_flags', []))
            text += '\nQSV device: ' + (caps.get('qsv_device') or 'unavailable')
            text += '\n' + '\n'.join(f'{c.name}: {"✓ tested" if c.available else "✗ " + c.reason[:300]}' for c in parsed.encoders)
            text += '\nSee Hardware cards for exact tested flags, driver and decode evidence. Unknown limits are not enabled.'
            available_codecs = {c.codec for c in parsed.encoders if c.available}
            for page in (self.fixer, self.hqx):
                page.apply_hardware_caps(caps)
            control = self.settings_page.hardware
            control.blockSignals(True)
            for gpu in parsed.gpus:
                if gpu.vendor != 'CPU' and any(c.gpu_id == gpu.id for c in parsed.encoders) and control.findData(gpu.id) < 0:
                    control.addItem(gpu.name + ' [tested runtime slot]', gpu.id)
            wanted = self.settings.get('hardware', 'Auto')
            if control.findData(wanted) >= 0:
                control.setCurrentIndex(control.findData(wanted))
            control.blockSignals(False)
            for i in range(control.count()):
                vendor = control.itemData(i)
                allowed = vendor == 'Auto' or any(c.available and (c.vendor == vendor or c.gpu_id == vendor) for c in parsed.encoders)
                control.model().item(i).setEnabled(allowed)
                control.setItemData(i, '' if allowed else 'No encoder for this vendor passed a test', Qt.ToolTipRole)
            for i in range(self.hqx.encoder_choice.count()):
                codec = self.hqx.encoder_choice.itemData(i).split('_')[0]
                allowed = codec in available_codecs
                self.hqx.encoder_choice.model().item(i).setEnabled(allowed)
                self.hqx.encoder_choice.setItemData(i, '' if allowed else 'No tested encoder in active FFmpeg', Qt.ToolTipRole)
            self.fixer.hevc.setEnabled('hevc' in available_codecs)
        else:
            text = caps.get('error', 'Hardware matrix unavailable')
            self.cap_badge.setText('Hardware scan failed')
        self.hardware_page.set_matrix(matrix)
        self.settings_page.capabilities.setText(text)
        self.advanced.apply_caps(caps)
        self.logs.append('ERROR' if caps.get('error') else 'INFO', text)
        if caps.get('error'):
            self.fixer.detected.setText(text)

    def ask_hardware_fallback(self, identity, request):
        job = self.manager.find(identity)
        if not job or not job.worker or self.closing:
            return
        box = QMessageBox(self)
        box.setWindowTitle('Hardware fallback · ' + job.source.name)
        box.setText(request['message'])
        box.setDetailedText(request['raw'])
        choices = {}
        for action in ('Use next', 'Use CPU', 'Stop'):
            choices[box.addButton(action, QMessageBox.ActionRole if action != 'Stop' else QMessageBox.RejectRole)] = action
        box.setDefaultButton(next(b for b, action in choices.items() if action == 'Stop'))
        def answered(*_):
            job.worker.answer_fallback(choices.get(box.clickedButton(), 'Stop'))
            self.fallback_dialogs.pop(identity, None)
            box.deleteLater()
        box.finished.connect(answered)
        self.fallback_dialogs[identity] = box
        box.open()  # Asynchronous; the worker waits on an event, never on the GUI thread.

    def scan_folder(self, folder, options, start, page=None):
        if self.scan_busy or self.manager.analysis_running:
            self.logs.append('WARN','A scan / analysis is already running')
            return
        root = Path(folder)
        if not folder or not root.is_dir():
            self.logs.append('ERROR','Please choose an existing folder / لطفاً یک پوشه انتخاب کنید!')
            self.navigate('Logs')
            return
        self.scan_busy = True
        is_hqx = bool(page and page.hqx)
        def scan():
            paths, error = [], ''
            try:
                suffixes = ('.avi','.mov','.mkv') if is_hqx else ('.mp4',)
                output = Path(options.output_folder).resolve() if options.output_folder else None
                for path in root.rglob('*'):
                    if (path.suffix.lower() not in suffixes or '_TV' in path.parts
                            or '.part.' in path.name or path.stem.endswith(('_fixed', options.suffix))
                            or (output and output != root.resolve() and output in path.resolve().parents)):
                        continue
                    paths.append(path)
                    if len(paths) >= MAX_JOBS:
                        break
            except OSError as exc:
                error = str(exc)
            self.scan_ready.emit((paths,options,start,page,error))
        threading.Thread(target=scan,daemon=True).start()

    def scanned(self, result):
        paths, options, start, page, error = result
        self.scan_busy = False
        if error:
            self.logs.append('ERROR',error)
        # A selected fixed rate applies to all waiting files in this batch, including pre-analyzed ones.
        for job in self.manager.jobs:
            if job.status == 'Waiting' and job.source in paths:
                job.options = replace(options)
        self.manager.add_paths(paths,options)
        self.logs.append('INFO',f'{len(paths)} files found / فایل پیدا شد')
        if start:
            self.manager.start()
        elif page and page.property('analyze_next'):
            page.setProperty('analyze_next',False)
            self.manager.analyze()
            self.encoding.open()

    def start_watch(self, folder, options):
        if not folder or not Path(folder).is_dir():
            self.logs.append('ERROR','Select an existing watch folder')
            return
        self.stop_watch()
        self.watcher = FolderWatcher(folder,options.suffix,lambda paths: self.manager.add_paths(paths,options))
        # Queue start is signaled on the GUI thread after incoming entries arrive.
        self.manager.incoming.connect(self.autostart_watch)
        self.watcher.start()
        self.logs.append('INFO',f'Watching recursively: {folder}')

    def autostart_watch(self, _):
        if self.watcher:
            self.manager.start()

    def stop_watch(self):
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
            try:
                self.manager.incoming.disconnect(self.autostart_watch)
            except RuntimeError:
                pass
            self.logs.append('INFO','Folder watch stopped')

    def queue_changed(self):
        self.completed_ids.intersection_update(job.id for job in self.manager.jobs)
        if self.daily.get('date') != date.today().isoformat():
            self.daily = {'date':date.today().isoformat(),'files':0,'size':0,'saved':0}
        changed = False
        for job in self.manager.jobs:
            if job.status in ('TV-Safe ✓','Done') and job.data.get('output') and job.id not in self.completed_ids:
                self.completed_ids.add(job.id)
                self.daily['files'] += 1
                self.daily['size'] += job.data.get('size',0)
                self.daily['saved'] += (int(job.media.format.get('size') or 0) if job.media else 0) - job.data.get('size',0)
                changed = True
        if changed:
            self.settings.set('daily', self.daily)
        self.dashboard.update_jobs(self.manager.jobs)
        self.dashboard.converted.setText(f'{self.daily["size"]/1e9:.2f} GB')
        self.dashboard.files.setText(f'{self.daily["files"]} files · today')
        self.dashboard.metrics['Space saved'].setText(f'{self.daily["saved"]/1e9:+.2f} GB')
        detected = [j.data.get('detected',j.media.badge() if j.media else '') for j in self.manager.jobs if j.media]
        rates = {j.media.fps for j in self.manager.jobs if j.media}
        if len(rates) > 1:
            self.fixer.detected.setText('Mixed frame rates detected across files. Unify all files to: choose a fixed Frame Rate above.')
        elif detected:
            self.fixer.detected.setText(detected[-1])

    def poll_metrics(self):
        if self.metric_busy:
            return
        self.metric_busy = True
        def run():
            import psutil
            values = {'cpu':psutil.cpu_percent(interval=.1), 'ram':psutil.virtual_memory().percent,
                      'temp':self.last_temperature}
            self.metrics_ready.emit(values)
        threading.Thread(target=run,daemon=True).start()
        # A slow Windows WMI temperature query must never delay the 1Hz CPU/RAM guard.
        now = time.monotonic()
        if not self.temperature_busy and now - self.last_temperature_poll >= 5:
            self.temperature_busy = True
            self.last_temperature_poll = now
            def temperature():
                self.temperature_ready.emit(cpu_temperature())
            threading.Thread(target=temperature,daemon=True).start()

    def set_temperature(self, value):
        self.temperature_busy = False
        self.last_temperature = value

    def set_metrics(self, values):
        self.metric_busy = False
        self.last_metrics = values
        self.manager.observe_resources(values['ram'], values['cpu'])
        self.dashboard.metrics['CPU load'].setText(f'{values["cpu"]:.0f}%')
        self.dashboard.metrics['GPU load'].setText('N/A')
        temperature = f'{values["temp"]:.1f}°C' if values['temp'] is not None else 'N/A'
        self.monitor.reading.setText(f'CPU  {values["cpu"]:.0f}%\nRAM  {values["ram"]:.0f}%\nTemp  {temperature}')
        self.manager.thermal_limit = bool(self.tweaks and self.tweaks.thermal and values['temp'] and
                                          values['temp'] > (80 if self.manager.thermal_limit else 85))
        speeds = [j.data.get('speed',0) for j in self.manager.jobs if j.status == 'Encoding']
        self.telemetry['Speed'].append(sum(speeds)/len(speeds) if speeds else 0)
        self.telemetry['CPU'].append(values['cpu'])
        self.telemetry['GPU'].append(0)
        metric = self.dashboard.series.value
        span = {'1M':60, '5M':300,'15M':900}[self.dashboard.ranges.value]
        self.dashboard.chart.values.clear()
        if metric != 'GPU':
            self.dashboard.chart.values.extend(list(self.telemetry[metric])[-span:])
        self.dashboard.chart.update()

    def set_execution_policy(self, safe_mode, high_priority):
        self.manager.safe_mode = safe_mode
        self.manager.high_priority = high_priority

    def safety_toast(self, reason):
        if self.tray.isVisible():
            self.tray.showMessage('EDIUS Booster — Safety pause', reason,
                                  QSystemTrayIcon.Warning, 10000)
        else:
            self.cap_badge.setText('Safety pause — see Encoding queue')
        self.encoding.open()

    def apply_tweaks(self, options):
        if self.tweak_busy or not self.tweaks:
            return
        self.tweak_busy = True
        def run():
            try:
                self.tweaks.apply(options)
            except Exception as exc:
                self.background_log.emit('ERROR',str(exc),'','')
            finally:
                self.tweak_busy = False
        threading.Thread(target=run,daemon=True).start()

    def restore_tweaks(self):
        if self.tweaks:
            threading.Thread(target=self.tweaks.restore,daemon=True).start()

    def complete(self, action):
        if action == 'Do nothing':
            return
        if not any(j.status in ('Done','TV-Safe ✓') for j in self.manager.jobs):
            self.logs.append('WARN','No successful files; finish power action ignored')
            return
        if self.watcher:
            self.logs.append('WARN','Finish action ignored while folder watch is enabled')
            return
        if any(j.status in ('Failed','Blocked','Warning') for j in self.manager.jobs):
            self.logs.append('WARN','Finish action cancelled because some files failed')
            return
        if os.name != 'nt':
            self.logs.append('WARN','Power actions are Windows-only')
            return
        # Explicit selection only. Shutdown has a 60-second cancel window.
        def run():
            if self.tweaks:
                self.tweaks.restore()
            try:
                if action == 'Shut down':
                    capture(['shutdown','/s','/t','60'])
                else:
                    import ctypes
                    ctypes.windll.powrprof.SetSuspendState(False,False,False)
            except Exception as exc:
                self.background_log.emit('ERROR',str(exc),'','')
        threading.Thread(target=run,daemon=True).start()
        self.logs.append('WARN','Shutdown in 60 seconds; run shutdown /a to cancel.' if action == 'Shut down' else 'Entering sleep')

    def closeEvent(self, event):
        self.settings.set('geometry',bytes(self.saveGeometry().toBase64()).decode())
        self.stop_watch()
        if self.closing:
            event.ignore()
            return
        self.closing = True
        self.scan_cancel.set()
        for page in (self.fixer,self.hqx):
            page.options()
            page.enhance_card.dispose()
        for dialog in list(self.fallback_dialogs.values()):
            dialog.reject()
        self.timer.stop()
        self.manager.stop_all()
        self.hide()
        self.encoding.explicit_close = True
        self.encoding.close()
        self.monitor.close()
        self.tray.hide()
        def cleanup():
            # Keep Qt alive until restoration has completed; never block its event thread.
            terminate_all()
            for job in self.manager.jobs:
                if job.thread and job.thread.is_alive():
                    job.thread.join(timeout=2)
            self.tweaks_ready.wait(timeout=10)
            if self.tweaks:
                self.tweaks.stop_event.set()
                self.tweaks.restore()
            self.cleanup_done.emit()
        threading.Thread(target=cleanup, daemon=True).start()
        event.ignore()
