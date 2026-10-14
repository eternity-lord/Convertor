import copy
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from PySide6.QtCore import QObject, Signal, QTimer, Slot
from .worker import EncodeWorker
from .ffprobe import probe
from .pipeline import output_path

TERMINAL = {'TV-Safe ✓', 'Done', 'Failed', 'Skipped'}
ACTIVE = {'Analyzing', 'Encoding', 'Paused', 'Verifying'}
MAX_JOBS = 5000


@dataclass
class Job:
    source: Path
    options: object
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    media: object = None
    data: dict = field(default_factory=lambda: {'status': 'Waiting', 'progress': 0})
    queued_at: float = field(default_factory=time.monotonic)
    worker: object = None
    thread: object = None

    @property
    def status(self):
        return self.data.get('status', 'Waiting')


class QueueManager(QObject):
    updated = Signal(str, dict)
    changed = Signal()
    started = Signal()
    finished = Signal()
    log = Signal(str, str, str, str)
    incoming = Signal(object)
    analyzed = Signal(str, object)
    analysis_done = Signal()

    def __init__(self):
        super().__init__()
        self.jobs = []
        self.capabilities = {}
        self.running = False
        self.paused = False
        self.limit = 0
        self.thermal_limit = False
        self.started_at = None
        self.finished_at = None
        self.analysis_running = False
        self.pending_start = False
        self.analysis_cancel = threading.Event()
        self.updated.connect(self._update)
        self.incoming.connect(self._add)
        self.analyzed.connect(self._analyzed)
        self.analysis_done.connect(self._analysis_complete)
        self.timer = QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.baselines = {}

    def add_paths(self, paths, options):
        """May be called by a watch thread; mutation always happens in Qt's thread."""
        self.incoming.emit([(Path(p), copy.deepcopy(options)) for p in paths][:MAX_JOBS])

    @Slot(object)
    def _add(self, entries):
        known = {str(j.source.resolve()) for j in self.jobs if j.status not in TERMINAL}
        for path, options in entries:
            if len(self.jobs) >= MAX_JOBS:
                self.log.emit('WARN', 'Queue limit reached (5000). Clear finished jobs first.', '', '')
                break
            if str(path.resolve()) not in known:
                self.jobs.append(Job(path, options))
                known.add(str(path.resolve()))
        self.changed.emit()

    @Slot(str, dict)
    def _update(self, identity, changes):
        job = self.find(identity)
        if not job:
            return
        changes = dict(changes)
        for key in ('details','warning','reason'):
            if key in changes:
                changes[key] = str(changes[key])[-12000:]
        if 'errors' in changes:
            changes['errors'] = [str(x)[:2000] for x in changes['errors'][:20]]
        job.data.update(changes)
        if 'media' in changes:
            job.media = changes['media']
        details = changes.get('details', '')
        command = changes.get('command', job.data.get('command', []))
        if command:
            import subprocess
            details = subprocess.list2cmdline(command) + '\n\n' + details
        if 'warning' in changes:
            self.log.emit('WARN', changes['warning'][:1200], job.source.name, details)
        if 'errors' in changes and changes['errors']:
            self.log.emit('ERROR', '; '.join(changes['errors'])[:1600], job.source.name, details)
        if 'detected' in changes:
            self.log.emit('INFO', changes['detected'], job.source.name, details)
        if changes.get('status') in TERMINAL:
            self.log.emit('DONE' if job.status != 'Failed' else 'ERROR',
                          changes.get('badge', changes.get('reason', job.status)), job.source.name, details)
        self.changed.emit()

    def find(self, identity):
        return next((j for j in self.jobs if j.id == identity), None)

    def analyze(self):
        if self.analysis_running or self.running:
            return
        self.analysis_running = True
        self.analysis_cancel.clear()
        waiting = [j for j in self.jobs if j.status == 'Waiting' and j.media is None]
        def run():
            try:
                for job in waiting:
                    if self.analysis_cancel.is_set():
                        break
                    try:
                        self.analyzed.emit(job.id, probe(job.source))
                    except Exception as exc:
                        self.updated.emit(job.id, {'errors': [str(exc)], 'status': 'Failed'})
            finally:
                self.analysis_done.emit()
        threading.Thread(target=run, daemon=True).start()

    @Slot(str, object)
    def _analyzed(self, identity, media):
        job = self.find(identity)
        if job:
            job.media = media
            job.data['detected'] = media.badge()
            self.log.emit('INFO', media.badge(), job.source.name, '')
            self.changed.emit()

    @Slot()
    def _analysis_complete(self):
        self.analysis_running = False
        rates = {j.media.fps for j in self.jobs if j.media}
        if len(rates) > 1:
            self.log.emit('WARN', 'Mixed frame rates detected across files. Select a fixed Frame Rate to unify all files.', '', '')
        self.changed.emit()
        if self.pending_start:
            self.pending_start = False
            self.start()

    def start(self):
        self.finished_at = None
        if self.analysis_running:
            self.log.emit('WARN', 'Wait for analysis to finish before starting.', '', '')
            return
        if any(j.status == 'Waiting' and j.media is None for j in self.jobs):
            self.pending_start = True
            self.started_at = time.monotonic()
            self.started.emit()
            self.analyze()
            return
        if not self.running:
            self.running = True
            self.started_at = time.monotonic()
            self.started.emit()
        self.tick()

    def tick(self):
        if not self.running or self.paused:
            return
        active = [j for j in self.jobs if j.thread and j.thread.is_alive()]
        waiting = [j for j in self.jobs if j.status == 'Waiting']
        if not active and not waiting:
            self.finished_at = time.monotonic()
            self.running = False
            self.finished.emit()
            return
        import psutil
        mem = psutil.virtual_memory()
        hardware = self.capabilities.get('codecs', {}).get('h264_qsv', {}).get('available', False)
        limit = self.limit or (2 if hardware else 1)
        # RAM guard: never queue many full-frame surfaces on a low-memory machine.
        if self.thermal_limit or mem.available < 1_500_000_000 or mem.percent > 85:
            limit = 1
        if any(j.data.get('pipeline') == 'CPU fallback' for j in active) and not self.limit:
            limit = 1
        # Learn a serial baseline; only throttle parallel work when a real reference exists.
        for job in active:
            elapsed, speed = job.data.get('elapsed', 0), job.data.get('speed', 0)
            if 8 < elapsed < 20 and speed and len(active) == 1:
                self.baselines[job.data.get('pipeline')] = speed
            base = self.baselines.get(job.data.get('pipeline'))
            if len(active) > 1 and elapsed > 20 and base and speed < base * .8:
                limit = 1
        for job in waiting[:max(0, limit - len(active))]:
            if not job.options.simulation:
                try:
                    destination = output_path(job.source, job.options)
                    reserved = {j.data.get('reserved_output') for j in self.jobs if j is not job}
                    base, index = destination, 2
                    while str(destination) in reserved:
                        destination = base.with_stem(base.stem + f'_{index}')
                        index += 1
                    job.data['reserved_output'] = str(destination)
                except Exception as exc:
                    self._update(job.id, {'status':'Failed', 'errors':[str(exc)]})
                    continue
            job.data['status'] = 'Analyzing'
            job.worker = EncodeWorker(job, job.options, self.capabilities, self.updated.emit)
            job.thread = threading.Thread(target=job.worker.run, daemon=True)
            job.thread.start()
        self.changed.emit()

    def stop_all(self):
        self.finished_at = time.monotonic()
        self.running = False
        self.paused = False
        self.pending_start = False
        self.analysis_cancel.set()
        for job in self.jobs:
            if job.worker and job.thread and job.thread.is_alive():
                job.worker.cancel.set()
                threading.Thread(target=job.worker.stop, daemon=True).start()
            elif job.status == 'Waiting':
                job.data.update(status='Skipped', reason='Stopped by user')
        self.changed.emit()

    def pause_all(self, paused):
        self.paused = paused
        for job in self.jobs:
            if job.worker:
                job.worker.pause(paused)

    def retry(self, identity):
        job = self.find(identity)
        if job and job.status in TERMINAL and not (job.thread and job.thread.is_alive()):
            job.data = {'status': 'Waiting', 'progress': 0}
            job.worker = None
            self.changed.emit()

    def reorder(self, identities):
        order = {identity: i for i, identity in enumerate(identities)}
        self.jobs.sort(key=lambda j: order.get(j.id, len(order)))
        self.changed.emit()

    def summary(self):
        counts = {s: sum(j.status == s for j in self.jobs) for s in
                  ('Waiting', 'Analyzing', 'Encoding', 'Paused', 'Verifying', 'Done', 'TV-Safe ✓', 'Failed', 'Skipped')}
        durations = [(j.media.duration if j.media else 0) for j in self.jobs]
        total_duration = sum(durations)
        complete = sum(d * (1 if j.status in TERMINAL else j.data.get('progress', 0) / 100)
                       for d, j in zip(durations, self.jobs))
        remaining = sum(d * (1 - j.data.get('progress', 0) / 100)
                        for d, j in zip(durations, self.jobs) if j.status not in TERMINAL)
        throughput = sum(j.data.get('speed', 0) for j in self.jobs if j.status == 'Encoding')
        # Metadata is gathered off-thread; never stat thousands of network paths in Qt.
        size = sum(int(j.media.format.get('size') or 0) for j in self.jobs if j.media)
        return dict(counts=counts, total=len(self.jobs), size=size,
                    progress=100 * complete / total_duration if total_duration else 0,
                    elapsed=(self.finished_at or time.monotonic()) - self.started_at if self.started_at else 0,
                    remaining=remaining / throughput if throughput else None)


class FolderWatcher:
    def __init__(self, folder, suffix, callback):
        self.folder, self.suffix, self.callback = Path(folder), suffix, callback
        self.stop_event = threading.Event()
        self.seen = {}
        self.processed = set()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def run(self):
        while not self.stop_event.is_set():
            now, found, current = time.monotonic(), [], {}
            try:
                for path in self.folder.rglob('*'):
                    if self.stop_event.is_set():
                        return
                    if len(current) >= MAX_JOBS:
                        break
                    if (path.suffix.lower() not in ('.avi', '.mov', '.mkv')
                            or '_TV' in path.parts or '.part.' in path.name):
                        continue
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
                    old = self.seen.get(str(path))
                    stable_since = old[1] if old and old[0] == key else now
                    current[str(path)] = (key, stable_since)
                    if stat.st_size and now - stable_since >= 6 and key not in self.processed:
                        self.processed.add(key)
                        found.append(path)
                self.seen = current
                keys = {value[0] for value in current.values()}
                self.processed.intersection_update(keys)
                if found:
                    self.callback(found)
            except OSError:
                pass
            self.stop_event.wait(3)
