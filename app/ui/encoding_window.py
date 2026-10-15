import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QImage, QPixmap, QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QGridLayout, QTableWidget, QTableWidgetItem,
                              QHeaderView, QProgressBar, QMenu, QApplication)
from app.core.queue import ACTIVE, TERMINAL
from app.core.process import capture, tool
from app.core.settings import data_dir
from app.core.qsv import preview_command
from app.ui.widgets.common import GlassWindow, Card, Chart, Toggle, label, button, row
from app.ui.pages.fixer import combo, selected
from app.ui import dialogs
from app.i18n import tr


def clock(seconds):
    if seconds is None:
        return '—'
    seconds = max(0, int(seconds))
    return f'{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}'


class QueueTable(QTableWidget):
    reordered = Signal(str, str)

    def __init__(self):
        super().__init__(0, 5)
        self.setHorizontalHeaderLabels(['Source / file', 'Source info', 'Preset / output', 'Status', 'Progress'])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(3, QHeaderView.Interactive)
        self.setColumnWidth(3,300)
        self.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.verticalHeader().hide()
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setSelectionMode(QTableWidget.SingleSelection)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QTableWidget.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setMinimumHeight(150)

    def dropEvent(self, event):
        source = self.currentRow()
        dest = self.indexAt(event.position().toPoint()).row()
        if source >= 0 and dest >= 0:
            self.reordered.emit(self.item(source, 0).data(Qt.UserRole), self.item(dest, 0).data(Qt.UserRole))
        event.accept()


class EncodingWindow(GlassWindow):
    preview_ready = Signal(object)
    preview_status = Signal(str, str)
    completion_action = Signal(str)

    def __init__(self, manager):
        super().__init__('EDIUS Booster · Encoding queue', (1000, 710))
        self.resize(1240, 840)
        self.manager = manager
        self.preview_busy = False
        self.last_preview = 0
        self.last_refresh = 0
        self.current_id = None
        self.last_speed_sample = 0
        self.last_order = []
        self.paused = False
        self.explicit_close = False
        self.preview_ready.connect(self.show_preview)
        self.preview_status.connect(self.report_preview_status)
        self.preview_messages = {}
        self.content = QWidget()
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(10, 3, 10, 10)
        self.summary_card = Card()
        self.summary = label('', 'muted')
        self.overall = QProgressBar()
        self.summary_card.box.addWidget(self.summary)
        self.summary_card.box.addWidget(self.overall)
        layout.addWidget(self.summary_card)
        self.table = QueueTable()
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        self.table.itemSelectionChanged.connect(self.select_job)
        self.table.reordered.connect(self.reorder)
        layout.addWidget(self.table, 2)
        current = Card('Encoding', True)
        grid = QGridLayout()
        self.preview = label('Preview · waiting', 'muted')
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedSize(285, 160)
        self.preview.setStyleSheet('background:#09080b;border-radius:14px;')
        grid.addWidget(self.preview, 0, 0, 3, 1)
        self.current_title = label('No files yet', 'title')
        self.metrics = label('Waiting', 'muted')
        self.current_progress = QProgressBar()
        self.pipeline = label('CPU / QSV', 'badge')
        self.chart = Chart(120)
        self.chart.setMinimumHeight(65)
        self.chart.setMaximumHeight(90)
        grid.addWidget(self.current_title, 0, 1)
        grid.addWidget(self.pipeline, 0, 2)
        grid.addWidget(self.current_progress, 1, 1, 1, 2)
        grid.addWidget(self.metrics, 2, 1, 1, 2)
        grid.addWidget(self.chart, 3, 0, 1, 3)
        current.box.addLayout(grid)
        layout.addWidget(current, 2)
        self.pause_button = button('Pause', self.toggle_pause)
        layout.addLayout(row(self.pause_button, button('Stop current', self.stop_current),
                             button('Skip file', self.skip_selected), button('Skip analysis', self.skip_analysis), button('Stop all', manager.stop_all),
                             button('Open output folder', self.open_output), button('Clear finished', self.clear_finished)))
        self.topmost = Toggle('Always on top')
        self.topmost.toggled.connect(self.set_topmost)
        self.autoclose = Toggle('Auto-close when finished')
        self.action = combo(['Do nothing', 'Sleep', 'Shut down'])
        layout.addLayout(row(self.topmost, self.autoclose, label('When finished', 'muted'), self.action))
        self.root.addWidget(self.content)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        manager.started.connect(self.open)
        manager.finished.connect(self.on_finished)

    def open(self):
        self.show()
        self.raise_()
        self.refresh()

    def set_topmost(self, value):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, value)
        self.show()

    def closeEvent(self, event):
        if self.explicit_close:
            event.accept()
        else:
            event.ignore()
            self.hide()

    def select_job(self):
        index = self.table.currentRow()
        item = self.table.item(index, 0) if index >= 0 else None
        if item:
            identity = item.data(Qt.UserRole)
            if identity != self.current_id:
                self.chart.values.clear()
            self.current_id = identity

    def current(self):
        job = self.manager.find(self.current_id)
        if job and job.status in ACTIVE:
            return job
        active = next((j for j in self.manager.jobs if j.status in ACTIVE), None)
        return active or job or next((j for j in reversed(self.manager.jobs) if j.status in TERMINAL),None)

    def refresh(self):
        now = time.monotonic()
        if now - self.last_refresh < .5:
            return
        self.last_refresh = now
        self.paused = self.manager.paused
        self.pause_button.setText(tr('Resume' if self.paused else 'Pause'))
        if not self.isVisible():
            return
        summary = self.manager.summary()
        c = summary['counts']
        remaining = summary['remaining']
        finish = (datetime.now() + timedelta(seconds=remaining)).strftime('%H:%M:%S') if remaining is not None else '—'
        self.summary.setText(
            f"{tr('Found')} {summary['total']}  ·  {tr('Waiting')} {c['Waiting']}  ·  "
            f"{tr('Encoding')} {sum(c[x] for x in ACTIVE)}  ·  {tr('Done')} {c['Done']+c['TV-Safe ✓']}  ·  "
            f"{tr('Warning')} {c['Warning']}  ·  Blocked {c['Blocked']}  ·  {tr('Failed')} {c['Failed']}  ·  {tr('Skipped')} {c['Skipped']}  ·  {summary['size']/1e9:.2f} GB\n"
            f"{summary['progress']:.1f}%  ·  {tr('Elapsed')} {clock(summary['elapsed'])}  ·  "
            f"{tr('Remaining')} {clock(remaining)}  ·  {tr('Finish')} {finish}")
        if self.manager.safety_reason:
            self.summary.setText(self.summary.text() + '\n' + self.manager.safety_reason)
        self.overall.setValue(int(summary['progress']))
        jobs = self.manager.jobs
        identities = [j.id for j in jobs]
        if identities != self.last_order:
            self.table.blockSignals(True)
            self.table.setRowCount(len(jobs))
            self.last_order = identities
            self.table.blockSignals(False)
        self.table.blockSignals(True)
        for index, job in enumerate(jobs):
            v = job.media.video if job.media else {}
            info = (f"{v.get('width','?')}×{v.get('height','?')} · "
                    f"{float(job.media.fps):.3f} fps · {v.get('codec_name','?')}") if job.media else 'Waiting for analysis'
            state = tr(job.status)
            if job.status in ('Warning','Failed','Blocked'):
                state += ': ' + job.data.get('reason','; '.join(job.data.get('errors',[])))
            elif job.status == 'Analyzing':
                state += ': ' + tr(job.data.get('step','Reading streams…'))
            elif job.data.get('analysis_unverified') and job.status == 'Waiting':
                state += ' · ' + tr('Unverified')
            values = [job.source.name, info, job.options.preset + '\n' + job.data.get('output', ''),
                      state, f"{job.data.get('progress',0):.1f}%"]
            for col, value in enumerate(values):
                item = self.table.item(index, col)
                if item is None:
                    item = QTableWidgetItem()
                    self.table.setItem(index, col, item)
                if item.text() != value:
                    item.setText(value)
                if col == 0:
                    item.setData(Qt.UserRole, job.id)
                    item.setToolTip(str(job.source) + '\n' + job.data.get('detected', ''))
                if col == 3:
                    item.setToolTip(job.data.get('badge') or job.data.get('reason') or job.data.get('step',''))
                    item.setForeground(QColor('#8ecba5' if job.status == 'TV-Safe ✓' else '#e8c278' if job.status == 'Warning' else '#f87171' if job.status in ('Failed','Blocked') else '#baa4ba'))
        self.table.blockSignals(False)
        job = self.current()
        if not job:
            return
        d = job.data
        self.current_title.setText(job.source.name)
        self.current_progress.setValue(int(d.get('progress', 0)))
        self.pipeline.setText(d.get('encoding_status', d.get('pipeline', tr(job.status))))
        self.pipeline.setWordWrap(True)
        self.pipeline.setToolTip(d.get('encoding_status','') + '\n' + d.get('reason',''))
        self.pipeline.setStyleSheet('color:#e8c278; background:#302715;' if job.status == 'Warning' else '')
        total_frames = round(job.media.duration * float(d['target'].fps)) if job.media and d.get('target') else '?'
        target = d.get('target')
        target_text = f'{target.width}×{target.height} · {float(target.fps):.3f} CFR · {target.profile}@{target.level} · 8-bit 4:2:0 · AAC 48k stereo' if target else ''
        output = d.get('output_probe',{})
        if output.get('video'):
            video = output['video']
            audio = next(iter(output.get('audio',[])),{})
            target_text = (f"Output (ffprobe): {video.get('width')}×{video.get('height')} · "
                           f"{video.get('r_frame_rate')} fps · {video.get('profile')} / level {video.get('level')} · "
                           f"{video.get('pix_fmt')} · {audio.get('codec_name','no audio')} "
                           f"{audio.get('channels','?')}ch / {audio.get('sample_rate','?')} Hz")
        elif output.get('error'):
            target_text = 'Output unverified: ' + str(output['error'])
        else:
            target_text = 'Target: ' + target_text
        self.metrics.setText(
            f"Encoding frame {d.get('frame',0):,} of {total_frames}\n"
            f"{tr('Elapsed')} {clock(d.get('elapsed',0))}   ·   {tr('Remaining')} {clock(d.get('remaining'))}\n"
            f"{d.get('speed',0):.2f}×  ·  {d.get('fps','—')} fps  ·  {d.get('bitrate','—')}\n"
            f"{d.get('size',0)/1e6:.1f} MB  /  ~{d.get('projected',0)/1e6:.1f} MB final\n{target_text}")
        now = time.monotonic()
        if now - self.last_speed_sample > .5:
            self.chart.append(d.get('speed', 0))
            self.last_speed_sample = now
        if job.status == 'Encoding' and now - self.last_preview > 3 and not self.preview_busy:
            self.last_preview = now
            self.preview_busy = True
            threading.Thread(target=self.grab_preview, args=(job.id, job.media, dict(self.manager.capabilities), d.get('media_time',0)), daemon=True).start()

    def report_preview_status(self, identity, message):
        if self.preview_messages.get(identity) != message:
            self.preview_messages[identity] = message
            job = self.manager.find(identity)
            self.manager.log.emit('WARN' if 'CPU' in message else 'INFO', message,
                                  job.source.name if job else '', '')
        self.preview.setToolTip(message)

    def grab_preview(self, identity, media, caps, timestamp):
        path = data_dir() / 'preview.jpg'
        try:
            command, gpu = preview_command(media, caps, timestamp, path, tool('ffmpeg'))
            status = ('Preview: Using QSV · ' + command[command.index('-init_hw_device') + 1]
                      if gpu else 'Preview: CPU decode (source codec/pixel format or QSV decode self-test unsupported)')
            try:
                capture(command, timeout=8)
            except Exception as exc:
                if not gpu:
                    raise
                status = 'Preview: CPU fallback after QSV decode failure: ' + str(exc)[-1000:]
                command, _ = preview_command(media, {}, timestamp, path, tool('ffmpeg'))
                self.preview_status.emit(identity, status)
                capture(command, timeout=8)
            self.preview_status.emit(identity, status)
            self.preview_ready.emit(QImage(str(path)))
        except Exception as exc:
            self.preview_status.emit(identity, 'Preview failed: ' + str(exc)[-1000:])
            self.preview_ready.emit(None)

    def show_preview(self, image):
        self.preview_busy = False
        if image is not None and not image.isNull():
            self.preview.setPixmap(QPixmap.fromImage(image).scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def toggle_pause(self):
        self.paused = not self.manager.paused
        self.manager.pause_all(self.paused)
        self.pause_button.setText(tr('Resume' if self.paused else 'Pause'))

    def stop_current(self):
        job = self.current()
        if job and job.worker:
            threading.Thread(target=job.worker.stop, daemon=True).start()

    def skip_analysis(self):
        index = self.table.currentRow()
        identity = self.table.item(index,0).data(Qt.UserRole) if index >= 0 else self.current_id
        if not identity:
            job = next((j for j in self.manager.jobs if j.status == 'Analyzing'),None)
            identity = job.id if job else None
        if identity:
            self.manager.skip_analysis(identity)

    def skip_selected(self):
        index = self.table.currentRow()
        identity = self.table.item(index, 0).data(Qt.UserRole) if index >= 0 else self.current_id
        job = self.manager.find(identity)
        if job and job.status == 'Waiting':
            self.manager.updated.emit(job.id, {'status':'Skipped', 'reason':'Skipped by user'})
        elif job and job.worker:
            threading.Thread(target=job.worker.stop, daemon=True).start()

    def open_output(self):
        job = self.current()
        if job:
            dialogs.open_folder(job.data.get('output', str(job.source.parent)))

    def context_menu(self, position):
        index = self.table.indexAt(position).row()
        if index < 0:
            return
        identity = self.table.item(index, 0).data(Qt.UserRole)
        job = self.manager.find(identity)
        menu = QMenu(self)
        for text in ['Remove', 'Move to top', 'Retry', 'Skip analysis', 'Open folder', 'Show ffmpeg command']:
            action = menu.addAction(tr(text))
            action.setData(text)
            if text in ('Remove', 'Move to top'):
                action.setEnabled(job.status == 'Waiting' or (text == 'Remove' and job.status in TERMINAL))
            if text == 'Retry':
                action.setEnabled(job.status in TERMINAL)
            if text == 'Skip analysis':
                action.setEnabled(job.status in ('Waiting','Analyzing'))
        chosen = menu.exec(self.table.viewport().mapToGlobal(position))
        if not chosen:
            return
        text = chosen.data()
        if text == 'Remove':
            self.manager.jobs.remove(job)
        elif text == 'Move to top':
            self.manager.jobs.remove(job)
            self.manager.jobs.insert(0, job)
        elif text == 'Retry':
            self.manager.retry(identity)
        elif text == 'Skip analysis':
            self.manager.skip_analysis(identity)
        elif text == 'Open folder':
            dialogs.open_folder(job.data.get('output', str(job.source)))
        else:
            import subprocess
            dialogs.details(self, job.source.name, subprocess.list2cmdline(job.data.get('command',[])) + '\n' + job.data.get('details',''))
        self.manager.changed.emit()
        self.refresh()

    def reorder(self, source, dest):
        a, b = self.manager.find(source), self.manager.find(dest)
        if a and b and a.status == b.status == 'Waiting':
            self.manager.jobs.remove(a)
            self.manager.jobs.insert(self.manager.jobs.index(b), a)
            self.manager.changed.emit()
            self.refresh()

    def clear_finished(self):
        self.manager.jobs = [job for job in self.manager.jobs if job.status not in TERMINAL
                             or (job.thread and job.thread.is_alive())]
        self.manager.changed.emit()
        self.refresh()

    def on_finished(self):
        QApplication.beep()
        self.completion_action.emit(selected(self.action))
        if self.autoclose.isChecked():
            self.hide()
