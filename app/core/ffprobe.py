"""Per-file metadata and bounded, seek-sampled PTS cadence analysis."""
import json
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from statistics import median
from .process import capture, tool

STANDARD_RATES = tuple(Fraction(x) for x in
                       ('24000/1001', '24', '25', '30000/1001', '30', '50', '60000/1001', '60'))


def rate(value):
    aliases = {'23.976': '24000/1001', '29.97': '30000/1001', '59.94': '60000/1001'}
    try:
        result = Fraction(aliases.get(str(value), str(value)))
        return result if result > 0 else Fraction(0)
    except (ValueError, ZeroDivisionError):
        return Fraction(0)


def snap(value):
    value = rate(value)
    if not value:
        return Fraction(25)
    nearest = min(STANDARD_RATES, key=lambda item: abs(float(item - value)))
    # Auto always selects a broadcast-standard rational. Explicit Custom bypasses snap().
    return nearest


@dataclass
class Cadence:
    dominant: Fraction = Fraction(25)
    unstable: bool = False
    histogram: dict = field(default_factory=dict)
    jitter: float = 0
    samples: int = 0
    negative: bool = False
    duplicate: bool = False


def analyze_timestamps(windows, nominal=Fraction(25), average=Fraction(25)):
    deltas, negative, duplicate = [], False, False
    for window in windows:
        values = sorted(window)
        negative |= any(x < -.000001 for x in values)
        for a, b in zip(values, values[1:]):
            delta = b - a
            if delta <= .000001:
                duplicate = True
            elif delta < 1:
                deltas.append(delta)
    if not deltas:
        mismatch = bool(nominal and average and abs(float(average / nominal) - 1) > .005)
        return Cadence(snap(average or nominal), mismatch, negative=negative)
    # 0.1 ms bins tolerate MP4 timebase quantisation (e.g. alternating 33/34ms).
    bins = Counter(round(x * 10000) for x in deltas)
    key = bins.most_common(1)[0][0]
    tolerance = max(.00105, key / 10000 * .01)
    group = [x for x in deltas if abs(x - key / 10000) <= tolerance]
    # Mean (not median) reconstructs fractional rates from coarse 1ms timebases.
    dominant_delta = sum(group) / len(group)
    outliers = sum(abs(x - dominant_delta) > tolerance for x in deltas)
    jitter = outliers / len(deltas)
    mismatch = bool(nominal and average and abs(float(average / nominal) - 1) > .005)
    return Cadence(snap(Fraction(1 / dominant_delta).limit_denominator(100000)),
                   mismatch or jitter > .005 or duplicate,
                   dict(bins.most_common(128)), jitter, len(deltas), negative, duplicate)


@dataclass
class Media:
    path: Path
    streams: list
    format: dict
    cadence: Cadence

    @property
    def video(self):
        return next((s for s in self.streams if s.get('codec_type') == 'video'), {})

    @property
    def audio(self):
        return [s for s in self.streams if s.get('codec_type') == 'audio']

    @property
    def duration(self):
        return float(self.video.get('duration') or self.format.get('duration') or 0)

    @property
    def fps(self):
        return self.cadence.dominant

    @property
    def measured_fps(self):
        count = int(self.video.get('nb_frames') or 0)
        return count / self.duration if count and self.duration else 0

    @property
    def interlaced(self):
        return self.video.get('field_order', 'unknown') not in ('unknown', 'progressive', '')

    def badge(self):
        v = self.video
        return (f"Detected: {'VFR / unstable' if self.cadence.unstable else 'CFR (sampled)'} · "
                f"avg {float(rate(v.get('avg_frame_rate'))):.3f} fps · "
                f"container {float(rate(v.get('r_frame_rate'))):.3f} fps · "
                f"measured {self.measured_fps:.3f} → {float(self.fps):.3f} CFR")


def probe(path, scan=True):
    path = Path(path)
    raw = json.loads(capture([tool('ffprobe'), '-v', 'error', '-show_streams', '-show_format',
                              '-of', 'json', str(path)]))
    raw_streams = raw.get('streams', [])
    if len(raw_streams) > 64:
        raise ValueError('Too many streams (limit 64)')
    keep = {'index','codec_type','codec_name','codec_tag_string','profile','level',
            'width','height','pix_fmt','field_order','sample_aspect_ratio',
            'r_frame_rate','avg_frame_rate','nb_frames','duration','start_time',
            'time_base','color_space','color_transfer','color_primaries','color_range',
            'bit_rate','sample_rate','channels','channel_layout','refs'}
    streams = [{key:value for key,value in stream.items() if key in keep}
               for stream in raw_streams]
    video = next((s for s in streams if s.get('codec_type') == 'video'), None)
    if not video:
        raise ValueError('No video stream found')
    nominal, average = rate(video.get('r_frame_rate')), rate(video.get('avg_frame_rate'))
    duration = float(video.get('duration') or raw.get('format', {}).get('duration') or 0)
    windows = []
    if scan:
        starts = sorted(set([0, max(0, duration / 2 - 4), max(0, duration - 10)]))
        for start in starts:
            # At most 512 packets per window, three windows. No unbounded packet JSON.
            text = capture([tool('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
                            '-read_intervals', f'{start:.6f}%+#512',
                            '-show_entries', 'packet=pts_time,dts_time', '-of', 'csv=p=0', str(path)],
                           timeout=45, limit=1_000_000)
            pts = []
            for line in text.splitlines():
                try:
                    pts.append(float(line.split(',')[0]))
                except ValueError:
                    pass
            windows.append(pts)
    cadence = analyze_timestamps(windows, nominal, average)
    format_info = {key:value for key,value in raw.get('format',{}).items()
                   if key in ('duration','start_time','size','bit_rate','format_name')}
    return Media(path, streams, format_info, cadence)


def scan_full_cadence(path, nominal, average, cancel=None, timeout=600):
    """Stream every packet with a small reorder heap, constant memory even for multi-hour media.

    AVC's supported TV path uses two B-frames. A 64-packet reorder window also tolerates
    common source reorder depths. We never retain a whole file's timestamps.
    """
    import heapq
    import threading
    from collections import deque
    from .process import spawn, terminate_tree, ToolError
    process = spawn([tool('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
                     '-show_entries', 'packet=pts_time,dts_time', '-of', 'csv=p=0', str(path)])
    errors = deque(maxlen=100)
    timed_out = threading.Event()
    def drain():
        for line in process.stderr:
            errors.append(line[-2048:])
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    def expire():
        timed_out.set()
        if process.poll() is None:
            process.kill()
    timer = threading.Timer(timeout, expire)
    timer.daemon = True
    timer.start()
    heap, bins, sums = [], Counter(), Counter()
    previous, negative, duplicate, out_of_order = None, False, False, False
    count = 0
    previous_dts = None
    def accept(value):
        nonlocal previous, duplicate, count, out_of_order
        if previous is not None:
            delta = value - previous
            if delta < -.000001:
                out_of_order = True
            elif delta <= .000001:
                duplicate = True
            else:
                key = round(delta * 10000)
                if len(bins) < 4096 or key in bins:
                    bins[key] += 1
                    sums[key] += delta
                else:
                    out_of_order = True
                count += 1
        previous = value
    try:
        for line in process.stdout:
            if cancel and cancel.is_set():
                raise ToolError('Verification cancelled')
            parts = line.strip().split(',')
            try:
                pts = float(parts[0])
                negative |= pts < -.000001
                if len(parts) > 1 and parts[1] != 'N/A':
                    dts = float(parts[1])
                    negative |= dts < -.000001
                    if previous_dts is not None and dts <= previous_dts:
                        out_of_order = True
                    previous_dts = dts
                heapq.heappush(heap, pts)
                if len(heap) > 64:
                    accept(heapq.heappop(heap))
            except ValueError:
                continue
        while heap:
            accept(heapq.heappop(heap))
        process.wait()
        if timed_out.is_set():
            raise ToolError('Full-file verification timed out')
        if process.returncode:
            raise ToolError(''.join(errors)[-12000:])
    finally:
        timer.cancel()
        if process.poll() is None:
            terminate_tree(process)
        reader.join(timeout=3)
        process.stdout.close()
        process.stderr.close()
    if not bins:
        return Cadence(snap(average or nominal), True, samples=0, negative=negative)
    mode = bins.most_common(1)[0][0]
    tolerance = max(10.5, mode * .01)
    cluster = [key for key in bins if abs(key - mode) <= tolerance]
    included = sum(bins[key] for key in cluster)
    delta = sum(sums[key] for key in cluster) / included
    jitter = (count - included) / max(1, count)
    mismatch = bool(nominal and average and abs(float(average / nominal) - 1) > .005)
    return Cadence(snap(Fraction(1 / delta).limit_denominator(100000)),
                   mismatch or jitter > 0 or duplicate or out_of_order,
                   dict(bins), jitter, count, negative, duplicate)


def verify_probe(path, cancel=None):
    media = probe(path, scan=False)
    media.cadence = scan_full_cadence(path, rate(media.video.get('r_frame_rate')),
                                     rate(media.video.get('avg_frame_rate')), cancel)
    return media
