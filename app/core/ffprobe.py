"""Per-file metadata and bounded, seek-sampled PTS cadence analysis."""
import json
import time
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from .process import capture, tool, ToolError, ToolTimeout, ProbeSkipped

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
    verified: bool = True
    issue: str = ''


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
    metadata_verified: bool = True
    analysis_seconds: float = 0

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
        return (f"Detected: {'Unverified: ' + self.cadence.issue if not self.cadence.verified else 'VFR / unstable' if self.cadence.unstable else 'CFR (sampled)'} · "
                f"avg {float(rate(v.get('avg_frame_rate'))):.3f} fps · "
                f"container {float(rate(v.get('r_frame_rate'))):.3f} fps · "
                f"measured {self.measured_fps:.3f} → {float(self.fps):.3f} CFR")


def unverified_media(path, issue, fps='25'):
    """Conservative, explicitly unverified fallback when stream metadata is unavailable."""
    video = dict(codec_type='video', width=1920, height=1080, sample_aspect_ratio='1:1',
                 r_frame_rate=str(rate(fps) or rate('25')), avg_frame_rate=str(rate(fps) or rate('25')))
    return Media(Path(path), [video, {'codec_type':'audio'}], {},
                 Cadence(rate(fps) or rate('25'), verified=False, issue=issue), False)


def probe(path, scan=True, cancel=None, step=None, fallback_fps='25'):
    started = time.monotonic()
    def timed(media):
        media.analysis_seconds = time.monotonic() - started
        return media
    step = step or (lambda message: None)
    path = Path(path)
    binary = tool('ffprobe')  # Missing executable is a clear blocking error, not a guessed probe.
    step('Reading streams…')
    try:
        raw = json.loads(capture([binary, '-v', 'error', '-show_streams', '-show_format',
                                  '-of', 'json', str(path)], timeout=15, cancel=cancel))
    except (ToolTimeout, ProbeSkipped) as exc:
        return timed(unverified_media(path, f'{exc}; stream metadata unavailable', fallback_fps))

    raw_streams = raw.get('streams', [])
    if len(raw_streams) > 64:
        raise ValueError('Too many streams (limit 64)')
    keep = {'index','codec_type','codec_name','codec_tag_string','profile','level',
            'width','height','pix_fmt','field_order','sample_aspect_ratio',
            'r_frame_rate','avg_frame_rate','nb_frames','duration','start_time',
            'time_base','color_space','color_transfer','color_primaries','color_range',
            'bit_rate','sample_rate','channels','channel_layout','refs','chroma_location','side_data_list'}
    streams = [{key:value for key,value in stream.items() if key in keep}
               for stream in raw_streams]
    video = next((s for s in streams if s.get('codec_type') == 'video'), None)
    if not video:
        raise ValueError('No video stream found')
    nominal, average = rate(video.get('r_frame_rate')), rate(video.get('avg_frame_rate'))
    duration = float(video.get('duration') or raw.get('format', {}).get('duration') or 0)
    windows, issue = [], ''
    if scan:
        step('Checking frame timing…')
        # First ~20s, then three short seek windows. Never scan the whole file.
        intervals = [(0, 20)]
        if duration > 20:
            intervals += [(duration / 2, 3), (duration * .75, 3), (max(0, duration - 3), 3)]
        for start, length in intervals:
            try:
                text = capture([binary, '-v', 'error', '-select_streams', 'v:0',
                                '-read_intervals', f'{start:.6f}%+{length}',
                                '-show_entries', 'packet=pts_time', '-of', 'csv=p=0', str(path)],
                               timeout=15, limit=1_000_000, cancel=cancel)
            except ToolError as exc:
                issue = str(exc)
                break  # Do not spend another 15s on each subsequent window.
            pts = []
            for line in text.splitlines()[:8192]:
                try:
                    pts.append(float(line.split(',')[0]))
                except ValueError:
                    pass
            windows.append(pts)
    else:
        issue = 'Frame timing analysis skipped'
    cadence = analyze_timestamps(windows, nominal, average)
    if issue or not cadence.samples:
        cadence.verified = False
        cadence.issue = issue or 'Insufficient sampled timestamps'
        # Prefer the nominal rational from metadata when the timing scan is inconclusive.
        cadence.dominant = snap(nominal or average)
    format_info = {key:value for key,value in raw.get('format',{}).items()
                   if key in ('duration','start_time','size','bit_rate','format_name')}
    return timed(Media(path, streams, format_info, cadence))



def verify_probe(path, cancel=None, step=None):
    """Output policy inspection uses only bounded seek samples, never a whole-file scan."""
    return probe(path, cancel=cancel, step=step)


def probe_values(media):
    """Compact actual output values for logs; no inferred encoder settings."""
    keys = ('codec_name','profile','level','pix_fmt','width','height','r_frame_rate',
            'avg_frame_rate','start_time','duration','codec_tag_string')
    audio_keys = ('codec_name','profile','channels','sample_rate','start_time','duration')
    return {'video': {key:media.video.get(key) for key in keys},
            'audio': [{key:audio.get(key) for key in audio_keys} for audio in media.audio],
            'metadata_verified':media.metadata_verified,
            'timing_verified':media.cadence.verified,
            'analysis_issue':media.cadence.issue}
