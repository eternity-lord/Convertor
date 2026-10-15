import math
import re
import json
import subprocess
import os
import shutil
import threading
import time
import uuid
import tempfile
from collections import deque
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from .ffprobe import probe, verify_probe, probe_values
from .pipeline import build_command, output_path
from .process import spawn, terminate_tree, ToolError, tool
from .tv_rules import verify, faststart_ok, estimate_bytes
from .safety import reserved_affinity
from .hardware.errors import classify_error
from .enhance.model import enabled as enhancement_enabled


class Cancelled(Exception):
    pass


class EncodeFailure(ToolError):
    pass


class EncodeWorker:
    def __init__(self, job, options, capabilities, emit, device_slots=None):
        self.job, self.options, self.capabilities, self.emit = job, options, capabilities, emit
        self.device_slots = device_slots
        self.cancel = threading.Event()
        self.process = None
        self.paused = False
        self.pause_requested = threading.Event()
        self.stderr = deque(maxlen=200)
        self.pause_at = 0
        self.paused_seconds = 0
        self.speed = 0
        self.fallback_answer = None
        self.fallback_event = threading.Event()
        self.preflight_record = None

    def stop(self):
        self.cancel.set()
        if self.process:
            terminate_tree(self.process)

    def pause(self, value):
        self.pause_requested.set() if value else self.pause_requested.clear()
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
                               'pipeline': plan.pipeline, 'encoding_status': plan.encoding_status, 'target': plan.target,
                               'encoder_id':plan.encoder_id, 'gpu_id':plan.gpu_id, 'enhancement':plan.enhancement})
        try:
            self.process = spawn(plan.command, priority=self.options.high_priority)
        except OSError as exc:
            raise EncodeFailure(f'Unable to start FFmpeg: {exc}') from exc
        try:
            import psutil
            process = psutil.Process(self.process.pid)
            try:
                if self.options.safe_mode:
                    allowed = reserved_affinity(process.cpu_affinity())
                    if allowed:
                        process.cpu_affinity(allowed)
                if os.name == 'nt':
                    process.ionice(psutil.IOPRIO_NORMAL)
            except (psutil.Error, AttributeError):
                pass
        except ImportError:
            pass
        if self.pause_requested.is_set():
            self.pause(True)
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
                        if now - last_emit >= .5 or value == 'end':
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
                raise EncodeFailure(f'FFmpeg exited with code {self.process.returncode}: ' + (''.join(self.stderr)[-24000:] or 'no diagnostic output'))
        finally:
            if self.process.poll() is None:
                terminate_tree(self.process)
            reader.join(timeout=3)
            self.process.stdout.close()
            self.process.stderr.close()

    def answer_fallback(self, action):
        self.fallback_answer = action
        self.fallback_event.set()

    def _fallback_action(self, error):
        message = classify_error(error).describe(getattr(self.options, 'language', 'en'))
        policy = getattr(self.options, 'failure_policy', 'Use next')
        self.emit(self.job.id, {'warning':message, 'hardware_error':classify_error(error).code})
        if policy != 'Ask':
            return policy
        self.fallback_answer = None
        self.fallback_event.clear()
        self.emit(self.job.id, {'status':'Paused', 'step':'Awaiting fallback decision',
                               'fallback_request':{'message':message, 'raw':str(error)[-12000:]}})
        while not self.fallback_event.wait(.1):
            if self.cancel.is_set():
                raise Cancelled()
        return self.fallback_answer or 'Stop'

    def _preflight_hardware(self, plan, folder):
        from .process import capture
        path = folder / (uuid.uuid4().hex + '.preflight.part.mp4')
        cmd = list(plan.command)
        cmd[-1] = str(path)
        if '-t' in cmd:
            cmd[cmd.index('-t') + 1] = '0.2'
        else:
            cmd[-1:-1] = ['-t', '0.2']
        self.preflight_record = {'command':cmd, 'stderr':'', 'success':False}
        try:
            self.emit(self.job.id, {'status':'Analyzing', 'step':'Testing exact source / enhancement / encoder chain…',
                                   'encoding_status':plan.encoding_status, 'pipeline':plan.pipeline,
                                   'encoder_id':plan.encoder_id, 'gpu_id':plan.gpu_id, 'enhancement':plan.enhancement, 'command':cmd})
            text = capture(cmd, timeout=10, cancel=self.cancel, include_stderr=True)
            self.preflight_record.update(stderr=text[-5000:], success=True)
            if re.search(r'has not been used for any stream|falling back to.*software|default.*CQP|CQP.{0,80}default|constant quantization.*default', text, re.I):
                raise ToolError('Requested hardware controls were ignored: ' + text[-2000:])
        except ToolError as exc:
            self.preflight_record.update(stderr=str(exc)[-5000:], success=False)
            if self.cancel.is_set():
                raise Cancelled()
            label = 'enhancement / encoder' if plan.enhancement else 'hardware'
            raise EncodeFailure(f'Exact-job {label} preflight failed: ' + str(exc)) from exc
        finally:
            path.unlink(missing_ok=True)

    def _step(self, message):
        self.emit(self.job.id, {'step':message})

    @staticmethod
    def _details(attempts):
        blocks = []
        for index, attempt in enumerate(attempts, 1):
            blocks.append(f'Attempt {index} — exact FFmpeg command:\n' +
                          subprocess.list2cmdline(attempt['command']) +
                          '\n\n' + attempt.get('encoding_status', '') +
                          '\n\nEnhancement:\n' + json.dumps(attempt.get('enhancement', {}), ensure_ascii=False, indent=2) +
                          '\n\nExact-job preflight:\n' + json.dumps(attempt.get('preflight', {}), ensure_ascii=False, indent=2) +
                          '\n\nStderr tail:\n' + attempt['stderr'] +
                          '\n\nOUTPUT ffprobe values:\n' +
                          json.dumps(attempt['probe'], ensure_ascii=False, indent=2))
        return '\n\n'.join(blocks)

    def _inspect(self, path, plan, options):
        try:
            result = verify_probe(path, self.cancel, self._step)
            if self.cancel.is_set():
                raise Cancelled()
            violations = verify(result, plan.target, options.audio == 'Keep all', plan.codec)
            if not faststart_ok(path):
                violations.append('MP4 faststart missing')
            return result, violations, probe_values(result)
        except Cancelled:
            raise
        except Exception as exc:
            if self.cancel.is_set():
                raise Cancelled()
            return None, [f'Unverified output: {exc}'], {'error':str(exc)}

    def run(self):
        temporaries, attempts = [], []
        ai_folder = None
        valid = None
        try:
            self.emit(self.job.id, {'status':'Analyzing'})
            fallback_fps = self.options.fps if self.options.fps != 'Auto' else '25'
            media = self.job.media or probe(self.job.source, cancel=self.cancel,
                                           step=self._step, fallback_fps=fallback_fps)
            self.job.media = media
            self.emit(self.job.id, {'media':media, 'detected':media.badge(),
                                    'analysis_seconds':media.analysis_seconds})
            if self.cancel.is_set():
                raise Cancelled()
            current_errors = verify(media, allow_all_audio=self.options.audio == 'Keep all')
            if self.options.simulation:
                if not faststart_ok(media.path):
                    current_errors.append('MP4 faststart is missing')
                self.emit(self.job.id, {'status':'Warning' if current_errors else 'TV-Safe ✓',
                          'violations':current_errors, 'reason':'; '.join(current_errors),
                          'badge':'Warning: ' + '; '.join(current_errors) if current_errors else 'TV-Safe ✓',
                          'details':'OUTPUT ffprobe values:\n' + json.dumps(probe_values(media),indent=2),
                          'progress':100})
                return
            if self.options.skip_safe and not current_errors and faststart_ok(media.path) and not enhancement_enabled(self.options):
                self.emit(self.job.id, {'status':'Skipped', 'reason':'Already TV-safe'})
                return
            destination = Path(self.job.data['reserved_output']) if self.job.data.get('reserved_output') else output_path(self.job.source, self.options)
            if destination.exists() and self.options.overwrite == 'Skip':
                self.emit(self.job.id, {'status':'Skipped', 'reason':'Output exists'})
                return
            destination.parent.mkdir(parents=True, exist_ok=True)
            folder = Path(self.options.scratch) if self.options.scratch else destination.parent
            folder.mkdir(parents=True, exist_ok=True)
            self.emit(self.job.id, {'output':str(destination)})
            from .enhance.ai import ai_preprocess
            from .enhance.model import effective
            source_media = media
            ai_notes = []
            settings = effective(self.options.enhance)
            if settings.level != 'Off' and settings.ai_enabled:
                from .enhance.planning import headroom
                headroom(media, self.options)  # Reject TV/upscale/Excellent cap conflicts before expensive external work.
                ai_folder = tempfile.TemporaryDirectory(prefix='edius-ai-', dir=folder)
                media, ai_notes = ai_preprocess(media, self.options, ai_folder.name, self.cancel, self._step)
                self.emit(self.job.id, {'warning':'; '.join(ai_notes), 'step':'External AI intermediate ready'})
            options = self.options
            fallback_reason = ''
            universal = bool(self.capabilities.get('matrix'))
            excluded, force_cpu, repair = [], False, False
            decode_disabled = set()
            budget = 2 * len(self.capabilities['matrix']['encoders']) + 2 if universal else 2
            for attempt in range(budget):
                # Keep the first successful encode intact while trying a timing repair.
                temporary = folder / f'{destination.stem}.{uuid.uuid4().hex[:8]}.part.mp4'
                temporaries.append(temporary)
                if repair or (attempt and not universal):
                    options = replace(options, audio='TV-safe', advanced={}, bframes=0)
                selection = None
                if universal:
                    from .hardware.matrix import HardwareMatrix
                    from .hardware.planning import request_for
                    from .hardware.select import choose_pipeline
                    matrix = HardwareMatrix.from_dict(self.capabilities['matrix'])
                    try:
                        selection = choose_pipeline(matrix, request_for(media, options, force_cpu), excluded)
                        if selection.encoder.id in decode_disabled:
                            selection.decoder, selection.gpu_filters = None, False
                            selection.reasons.append('Previous GPU decode/filter attempt failed; using CPU decode + NV12 upload')
                    except ValueError:
                        if valid is not None:
                            valid[3].append('No further tested encoder; first output retained')
                            break
                        raise
                plan = build_command(media, options, temporary, self.capabilities,
                                     software=force_cpu if universal else attempt > 0, **({'selection':selection} if universal else {}))
                if ai_notes:
                    plan.enhancement['external_ai'] = ai_notes
                    plan.encoding_status += ' · ' + '; '.join(ai_notes[:2])
                if attempt and fallback_reason:
                    plan.encoding_status += ' · Fallback reason: ' + fallback_reason
                if estimate_bytes(media.duration, plan.target) > 3.9e9:
                    self.emit(self.job.id, {'warning':'Estimated output exceeds FAT32 3.9 GB; use exFAT/NTFS or select split.'})
                record = {'command':plan.command, 'encoding_status':plan.encoding_status, 'enhancement':plan.enhancement, 'stderr':'', 'probe':{'not_run':True}}
                attempts.append(record)
                try:
                    claim = (self.device_slots.claim(plan.gpu_id, 1 if options.safe_mode or plan.gpu_id == 'cpu' else 3, self.cancel)
                             if self.device_slots and universal else nullcontext())
                    with claim:
                        if (universal and selection.encoder.hardware) or plan.enhancement:
                            self.preflight_record = None
                            try:
                                self._preflight_hardware(plan, folder)
                            finally:
                                record['preflight'] = self.preflight_record
                        self._encode(plan)
                    if self.cancel.is_set():
                        raise Cancelled()
                    if not temporary.is_file() or temporary.stat().st_size == 0:
                        raise EncodeFailure('FFmpeg produced no output file')
                except EncodeFailure as exc:
                    record['stderr'] = ''.join(self.stderr)[-5000:]
                    record['probe'] = {'encode_error':str(exc)}
                    self.emit(self.job.id, {'warning':str(exc), 'details':self._details(attempts),
                                           'command':plan.command})
                    if universal:
                        action = self._fallback_action(exc)
                        if action == 'Stop':
                            raise EncodeFailure(classify_error(exc).describe(getattr(options, 'language', 'en'))) from exc
                        if action == 'Use next' and selection.decoder and classify_error(exc).code in ('hardware_failure', 'memory'):
                            decode_disabled.add(plan.encoder_id)
                        else:
                            excluded.append(plan.encoder_id)
                        force_cpu = action == 'Use CPU'
                        fallback_reason = classify_error(exc).describe(getattr(options, 'language', 'en'))
                        continue
                    if attempt == 0:
                        fallback_reason = f'{plan.pipeline} encode failed: {exc}'
                        self.emit(self.job.id, {'warning':'Retry once: CPU maximum compatibility, -bf 0'})
                        continue
                    if valid is None:
                        raise
                    valid[3].append(f'Timestamp repair retry failed; first encoded output retained: {exc}')
                    break
                record['stderr'] = ''.join(self.stderr)[-5000:]
                self.emit(self.job.id, {'status':'Verifying', 'step':'Inspecting output…'})
                result, violations, values = self._inspect(temporary, plan, options)
                record['probe'] = values
                if settings.level == 'Excellent' and result:
                    from .enhance.planning import source_bitrate
                    original_rate, actual_rate = source_bitrate(source_media), source_bitrate(result)
                    if original_rate is None or actual_rate is None or actual_rate < original_rate:
                        violations.append('Excellent actual bitrate floor not verified: source '
                                          f'{original_rate if original_rate else "unknown"} Mbps; output {actual_rate if actual_rate else "unknown"} Mbps. '
                                          'VBR budget is not a guaranteed measured bitrate; source retained.')
                if not media.metadata_verified:
                    violations.append('Source metadata unverified: conservative dimensions / frame rate were used')
                valid = [temporary, plan, result, violations]
                if violations:
                    self.emit(self.job.id, {'warning':'; '.join(violations), 'details':self._details(attempts),
                                           'command':plan.command, 'output_probe':values})
                timing_bad = any(value.startswith(('Video start_time','Audio/video start offset','Audio/video duration drift'))
                                 for value in violations)
                if timing_bad and not repair and (universal or attempt == 0):
                    repair = True
                    fallback_reason = 'Output timestamp verification failed: ' + '; '.join(violations)
                    self.emit(self.job.id, {'warning':'Retry once for timestamp alignment with -bf 0; first output preserved'})
                    continue
                break
            if valid is None:
                raise EncodeFailure('No successful FFmpeg output was produced')
            temporary, plan, result, violations = valid
            if self.cancel.is_set():
                raise Cancelled()
            if temporary.parent != destination.parent:
                staging = destination.with_name(destination.stem + f'.{uuid.uuid4().hex[:8]}.part.mp4')
                try:
                    shutil.copyfile(temporary, staging)
                    os.replace(staging, destination)
                finally:
                    staging.unlink(missing_ok=True)
            else:
                os.replace(temporary, destination)
            parts = []
            if options.fat32 == 'Split into parts' and destination.stat().st_size > 3.9e9:
                try:
                    parts = self._split(destination, media.duration, plan.target, plan.codec)
                except Exception as exc:
                    violations.append(f'Split warning (master retained): {exc}')
            status = 'Warning' if violations else 'TV-Safe ✓' if plan.codec == 'h264' else 'Done'
            reason = '; '.join(violations)
            badge = ('Warning: ' + reason) if violations else f'CFR ✓ {float(plan.target.fps):.3f} fps · ' + (
                'TV-Safe ✓' if plan.codec == 'h264' else plan.codec.upper() + ' verified (modern devices only)')
            self.emit(self.job.id, {'status':status, 'progress':100, 'badge':badge,
                      'reason':reason, 'violations':violations, 'errors':[],
                      'output':str(destination), 'parts':parts, 'details':self._details(attempts),
                      'output_probe':probe_values(result) if result else {'error':reason},
                      'command':plan.command, 'pipeline':plan.pipeline,
                      'encoding_status':plan.encoding_status, 'enhancement':plan.enhancement, 'size':destination.stat().st_size})
            # A warning/unknown analysis can never authorize destructive source deletion.
            if options.delete_source and not violations and media.cadence.verified:
                self.job.source.unlink()
        except Cancelled:
            self.emit(self.job.id, {'status':'Skipped', 'reason':'Stopped by user'})
        except Exception as exc:
            if self.cancel.is_set():
                self.emit(self.job.id, {'status':'Skipped', 'reason':'Stopped by user'})
            else:
                status = 'Failed' if isinstance(exc, EncodeFailure) else 'Blocked'
                self.emit(self.job.id, {'status':status, 'reason':str(exc), 'errors':[str(exc)],
                                       'details':self._details(attempts) or ''.join(self.stderr)[-12000:]})
        finally:
            if ai_folder is not None:
                ai_folder.cleanup()
            for path in temporaries:
                path.unlink(missing_ok=True)

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
