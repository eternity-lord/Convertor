import math
import os
import shutil
import threading
import time
import uuid
from collections import deque
from dataclasses import replace
from pathlib import Path
from .ffprobe import probe, verify_probe
from .pipeline import build_command, output_path
from .process import spawn, terminate_tree, ToolError, tool
from .tv_rules import verify, faststart_ok, estimate_bytes


class Cancelled(Exception):
    pass


class EncodeWorker:
    def __init__(self, job, options, capabilities, emit):
        self.job, self.options, self.capabilities, self.emit = job, options, capabilities, emit
        self.cancel = threading.Event()
        self.process = None
        self.paused = False
        self.stderr = deque(maxlen=200)
        self.pause_at = 0
        self.paused_seconds = 0
        self.speed = 0

    def stop(self):
        self.cancel.set()
        if self.process:
            terminate_tree(self.process)

    def pause(self, value):
        import psutil
        if not self.process or self.process.poll() is not None or value == self.paused:
            return
        try:
            parent = psutil.Process(self.process.pid)
            for proc in parent.children(recursive=True) + [parent]:
                proc.suspend() if value else proc.resume()
            self.paused = value
            if value:
                self.pause_at = time.monotonic()
            else:
                self.paused_seconds += time.monotonic() - self.pause_at
            self.emit(self.job.id, {'status': 'Paused' if value else 'Encoding'})
        except psutil.Error:
            pass

    def _encode(self, plan):
        self.emit(self.job.id, {'status': 'Encoding', 'command': plan.command,
                               'pipeline': plan.pipeline, 'target': plan.target})
        self.process = spawn(plan.command, priority=True)
        try:
            import psutil
            process = psutil.Process(self.process.pid)
            try:
                process.cpu_affinity(process.cpu_affinity())
                if os.name == 'nt':
                    process.ionice(psutil.IOPRIO_HIGH)
            except (psutil.Error, AttributeError):
                pass
        except ImportError:
            pass
        self.stderr.clear()
        def errors():
            for line in iter(lambda: self.process.stderr.readline(4096), ''):
                self.stderr.append(line[-2048:])
        reader = threading.Thread(target=errors, daemon=True)
        reader.start()
        begin = time.monotonic()
        self.paused_seconds = 0
        last_emit = 0
        values = {}
        try:
            for line in self.process.stdout:
                if self.cancel.is_set():
                    raise Cancelled()
                key, sep, value = line.strip().partition('=')
                if sep:
                    values[key] = value
                if key == 'progress':
                    now = time.monotonic()
                    try:
                        media_time = max(0, float(values.get('out_time_us', 0)) / 1e6)
                        instantaneous = float(values.get('speed', '0').rstrip('x'))
                        if instantaneous > 0 and math.isfinite(instantaneous):
                            self.speed = instantaneous if not self.speed else .2 * instantaneous + .8 * self.speed
                        elapsed = now - begin - self.paused_seconds
                        if now - last_emit >= .35 or value == 'end':
                            duration = plan.duration or self.job.media.duration
                            progress = min(99.9, media_time / duration * 100) if duration else 0
                            size = int(values.get('total_size', 0))
                            self.emit(self.job.id, {'progress': progress, 'media_time': media_time,
                                      'elapsed': elapsed, 'remaining': max(0, duration - media_time) / self.speed if self.speed else None,
                                      'speed': self.speed, 'frame': int(values.get('frame', 0)),
                                      'fps': values.get('fps', '0'), 'bitrate': values.get('bitrate', '—'),
                                      'size': size, 'projected': size * 100 / max(progress, .1)})
                            last_emit = now
                    except (ValueError, TypeError):
                        pass
            self.process.wait()
            if self.cancel.is_set():
                raise Cancelled()
            if self.process.returncode:
                raise ToolError(''.join(self.stderr)[-24000:] or 'Encoder exited without output')
        finally:
            if self.process.poll() is None:
                terminate_tree(self.process)
            reader.join(timeout=3)
            self.process.stdout.close()
            self.process.stderr.close()

    def run(self):
        temporary = None
        try:
            self.emit(self.job.id, {'status': 'Analyzing'})
            media = self.job.media or probe(self.job.source)
            self.job.media = media
            self.emit(self.job.id, {'media': media, 'detected': media.badge()})
            if self.cancel.is_set():
                raise Cancelled()
            current_errors = verify(media, allow_all_audio=self.options.audio == 'Keep all')
            if self.options.simulation or self.options.skip_safe:
                checked = verify_probe(media.path, self.cancel)
                current_errors = verify(checked, allow_all_audio=self.options.audio == 'Keep all')
            if not faststart_ok(media.path):
                current_errors.append('MP4 faststart is missing')
            if self.options.simulation:
                self.emit(self.job.id, {'status': 'Failed' if current_errors else 'TV-Safe ✓',
                          'errors': current_errors, 'progress': 100})
                return
            if self.options.skip_safe and not current_errors:
                self.emit(self.job.id, {'status': 'Skipped', 'reason': 'Already TV-safe'})
                return
            destination = Path(self.job.data['reserved_output']) if self.job.data.get('reserved_output') else output_path(self.job.source, self.options)
            if destination.exists() and self.options.overwrite == 'Skip':
                self.emit(self.job.id, {'status': 'Skipped', 'reason': 'Output exists'})
                return
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.stem + f'.{uuid.uuid4().hex[:8]}.part.mp4')
            if self.options.scratch:
                scratch = Path(self.options.scratch)
                scratch.mkdir(parents=True, exist_ok=True)
                temporary = scratch / temporary.name
            self.emit(self.job.id, {'output': str(destination)})
            options = self.options
            for attempt in range(2):
                plan = build_command(media, options, temporary, self.capabilities, software=attempt > 0)
                projected = estimate_bytes(media.duration, plan.target)
                if projected > 3.9e9:
                    self.emit(self.job.id, {'warning': 'Estimated output exceeds FAT32 3.9 GB; use exFAT/NTFS or select split.'})
                try:
                    self._encode(plan)
                    if self.cancel.is_set():
                        raise Cancelled()
                    self.emit(self.job.id, {'status': 'Verifying'})
                    result = verify_probe(temporary, self.cancel)
                    violations = verify(result, plan.target, options.audio == 'Keep all', plan.codec)
                    if not faststart_ok(temporary):
                        violations.append('MP4 faststart missing')
                    if violations:
                        raise ToolError('; '.join(violations))
                    break
                except ToolError as exc:
                    self.emit(self.job.id, {'warning': str(exc), 'details': ''.join(self.stderr),
                                           'command': plan.command})
                    if attempt:
                        raise
                    # Exactly one software retry, retaining explicit fps, resolution, and TV rules.
                    options = replace(options, audio='TV-safe', advanced={})
                    temporary.unlink(missing_ok=True)
                    self.emit(self.job.id, {'warning': 'Retry once: CPU maximum compatibility'})
            if temporary.parent != destination.parent:
                staging = destination.with_name(destination.stem + '.part.mp4')
                try:
                    shutil.copyfile(temporary, staging)
                    os.replace(staging, destination)
                finally:
                    staging.unlink(missing_ok=True)
                temporary.unlink(missing_ok=True)
            else:
                os.replace(temporary, destination)
            parts = []
            if options.fat32 == 'Split into parts' and destination.stat().st_size > 3.9e9:
                parts = self._split(destination, media.duration, plan.target, plan.codec)
            self.emit(self.job.id, {'status': 'TV-Safe ✓' if plan.codec == 'h264' else 'Done', 'progress': 100,
                      'badge': f'CFR ✓ {float(plan.target.fps):.3f} fps · ' + ('TV-Safe ✓' if plan.codec == 'h264' else plan.codec.upper() + ' verified (modern devices only)'),
                      'output': str(destination), 'parts': parts, 'errors': [],
                      'size': destination.stat().st_size})
            if options.delete_source:
                self.job.source.unlink()
        except Cancelled:
            self.emit(self.job.id, {'status': 'Skipped', 'reason': 'Stopped by user'})
        except Exception as exc:
            if self.cancel.is_set():
                self.emit(self.job.id, {'status': 'Skipped', 'reason': 'Stopped by user'})
            else:
                self.emit(self.job.id, {'status': 'Failed', 'errors': [str(exc)], 'details': ''.join(self.stderr)})
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def _split(self, destination, duration, target, codec='h264'):
        # Segments cut at closed GOPs; reserve 10% for GOP boundary / bitrate variability.
        from .process import capture
        seconds = max(2, int(duration * 3.5e9 / destination.stat().st_size))
        folder = destination.parent / (destination.stem + '_parts')
        folder.mkdir(exist_ok=True)
        pattern = folder / 'part_%03d.mp4'
        capture([tool('ffmpeg'), '-v', 'error', '-nostdin', '-y', '-i', str(destination),
                 '-map', '0:v:0', '-map', '0:a:0?', '-c', 'copy', '-f', 'segment',
                 '-segment_time', str(seconds), '-reset_timestamps', '1',
                 '-segment_format_options', 'movflags=+faststart', str(pattern)], timeout=3600)
        parts = sorted(folder.glob('part_*.mp4'))
        for part in parts:
            if part.stat().st_size > 3.9e9:
                raise ToolError('A split part exceeds FAT32; lower bitrate or segment duration')
            violations = verify(verify_probe(part, self.cancel), target, expected_codec=codec)
            if violations or not faststart_ok(part):
                raise ToolError(f'Split verification failed for {part.name}: ' + '; '.join(violations))
        return [str(p) for p in parts]
