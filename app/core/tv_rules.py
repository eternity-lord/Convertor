"""Pure TV policy, independent of Qt, FFmpeg and Windows."""
import math
from dataclasses import dataclass, field
from fractions import Fraction
from .ffprobe import rate

# Max macroblocks/frame and macroblocks/second from Annex A, with conservative TV floors.
LEVELS = [('3.0', 1620, 40500), ('3.1', 3600, 108000), ('3.2', 5120, 216000),
          ('4.0', 8192, 245760), ('4.1', 8192, 245760), ('4.2', 8704, 522240),
          ('5.0', 22080, 589824), ('5.1', 36864, 983040), ('5.2', 36864, 2073600)]
PRESETS = {
    'Universal TV': (1920, 1080, 60, 20, 'high', 192),
    'Old TV / USB 720p': (1280, 720, 30, 8, 'main', 128),
    'Samsung / LG / Sony': (1920, 1080, 60, 20, 'high', 192),
    '4K TV': (3840, 2160, 30, 40, 'high', 256),
    'Phone / Tablet': (1920, 1080, 30, 12, 'high', 192),
    'Custom': (3840, 2160, 60, 40, 'high', 192),
}
DESCRIPTIONS = {
    'Universal TV': 'AVC compatibility · preserve source cadence up to 1080p60 · H.264 High · AAC stereo',
    'Old TV / USB 720p': 'Older USB players · 720p30 · Main · 8 Mbps maximum',
    'Samsung / LG / Sony': 'Modern webOS, Tizen and Android TV · up to 1080p60',
    '4K TV': 'H.264 High · 2160p30 · 40 Mbps maximum',
    'Phone / Tablet': 'Portable sharing · 1080p30 · smaller files',
    'Custom': 'Manual profile, level and bitrate; safety checks still apply',
}


@dataclass
class Options:
    preset: str = 'Universal TV'
    fps: str = 'Auto'
    method: str = 'Drop / Duplicate'
    resolution: str = 'keep'
    fps_cap: str = 'keep'
    profile: str = 'high'
    level: str = 'Auto'
    bitrate: float = 15
    maxrate: float = 20
    bufsize: float = 30
    rc: str = 'VBR'
    quality: int = 20
    deinterlace: bool = True
    double_rate: bool = False
    audio: str = 'TV-safe'
    output_mode: str = '_TV subfolder'
    output_folder: str = ''
    suffix: str = '_TV'
    overwrite: str = 'Skip'
    skip_safe: bool = False
    simulation: bool = False
    fat32: str = 'Warn / ignore'
    scratch: str = ''
    delete_source: bool = False
    codec: str = 'h264_qsv'
    encoder_preset: str = 'medium'
    advanced: dict = field(default_factory=dict)
    safe_mode: bool = True
    high_priority: bool = False
    bframes: int = 2


def auto_level(width, height, fps):
    mb = math.ceil(width / 16) * math.ceil(height / 16)
    required = next((level for level, fs, mbs in LEVELS if mb <= fs and mb * float(fps) <= mbs + .01), None)
    if required is None:
        raise ValueError('Resolution / frame rate exceeds H.264 Level 5.2')
    # TVs commonly expect 4.1 for full HD and 5.1 for UHD even if Annex A permits lower.
    floor = '5.1' if width * height > 1920 * 1080 else '4.1' if width * height >= 1920 * 1080 else '3.0'
    return max(required, floor, key=float)


@dataclass
class Target:
    width: int
    height: int
    fps: Fraction
    level: str
    profile: str
    bitrate: float
    maxrate: float
    bufsize: float
    audio_kbps: int
    notes: list = field(default_factory=list)


def choose_target(media, options):
    pw, ph, cap, max_mbps, profile, _ = PRESETS[options.preset]
    audio = 192
    notes = []
    fps = media.fps if options.fps == 'Auto' else rate(options.fps)
    if not fps or fps > 120:
        raise ValueError('Frame rate must be greater than 0 and no more than 120')
    if options.double_rate and media.interlaced and options.deinterlace and options.fps == 'Auto':
        fps *= 2
    # Explicit selection wins over preset FPS caps, never silently turn 29.97 into 30.
    if options.fps == 'Auto' and float(fps) > cap:
        fps /= math.ceil(float(fps) / cap)
        notes.append(f'Preset frame-rate cap: {float(fps):.3f} fps')
    if options.fps_cap != 'keep' and fps > rate(options.fps_cap):
        fps = rate(options.fps_cap)
    if options.resolution != 'keep':
        rw, rh = (1920, 1080) if options.resolution == '1080p' else (1280, 720)
        pw, ph = min(pw, rw), min(ph, rh)
    w, h = int(media.video['width']), int(media.video['height'])
    sar = rate(media.video.get('sample_aspect_ratio', '1:1').replace(':', '/')) or Fraction(1)
    w = round(w * float(sar))
    ratio = min(1, pw / w, ph / h)
    w, h = max(2, int(w * ratio) // 2 * 2), max(2, int(h * ratio) // 2 * 2)
    level = auto_level(w, h, fps)
    if options.level != 'Auto':
        if float(options.level) < float(level):
            notes.append(f'Level {options.level} raised to {level}: resolution × fps requires it')
        else:
            level = options.level
    if options.preset == 'Custom':
        profile = options.profile
    if profile not in ('baseline', 'main', 'high'):
        raise ValueError('TV mode supports only Baseline, Main or High (8-bit 4:2:0)')
    maximum = min(max_mbps, max(.1, options.maxrate))
    bitrate = min(max(.1, options.bitrate), maximum)
    if options.fat32 == 'Lower bitrate to fit' and media.duration:
        budget = 3.9e9 * 8 / media.duration / 1e6 - audio / 1000 - .15
        if budget < .1:
            raise ValueError('Too long to fit FAT32 at a useful bitrate. Select split instead.')
        maximum, bitrate = min(maximum, budget), min(bitrate, budget)
    return Target(w, h, fps, level, profile, bitrate, maximum,
                  min(max(.1, options.bufsize), maximum * 2), audio, notes)


def timescale(fps):
    return fps.numerator if fps.denominator != 1 else fps.numerator * 1000


def estimate_bytes(duration, target):
    return duration * (target.bitrate * 1e6 + target.audio_kbps * 1000) / 8 * 1.015


def verify(media, target=None, allow_all_audio=False, expected_codec='h264'):
    errors = []
    v = media.video
    videos = [s for s in media.streams if s.get('codec_type') == 'video']
    if len(videos) != 1:
        errors.append('Exactly one video stream required')
    if any(s.get('codec_type') not in ('video', 'audio') for s in media.streams):
        errors.append('Extra data / subtitle streams')
    expected_tag = {'h264':'avc1', 'hevc':'hvc1', 'av1':'av01'}[expected_codec]
    if v.get('codec_name') != expected_codec or v.get('codec_tag_string') != expected_tag:
        errors.append('Requires H.264 / avc1' if expected_codec == 'h264' else f'Requires {expected_codec.upper()} / {expected_tag}')
    if v.get('pix_fmt') not in ('yuv420p', 'nv12'):
        errors.append('Not 8-bit 4:2:0')
    profiles = ('High', 'Main', 'Baseline', 'Constrained Baseline') if expected_codec == 'h264' else ('Main',)
    if v.get('profile') not in profiles:
        errors.append('Unsupported AVC profile')
    width, height = int(v.get('width', 0)), int(v.get('height', 0))
    if width % 2 or height % 2:
        errors.append('Odd dimensions')
    if v.get('sample_aspect_ratio') not in ('1:1', '1/1'):
        errors.append('Non-square pixels')
    nominal, average = rate(v.get('r_frame_rate')), rate(v.get('avg_frame_rate'))
    if nominal != average or (target and nominal != target.fps) or media.cadence.unstable:
        errors.append('Frame rate is not the selected clean CFR')
    if not media.cadence.samples:
        errors.append('Not enough timestamps to verify cadence')
    if not media.metadata_verified:
        errors.append('Unverified: output stream metadata unavailable')
    if not media.cadence.verified:
        errors.append('Unverified: ' + media.cadence.issue)
    video_start = float(v.get('start_time') or 0)
    if video_start < -.1:
        errors.append(f'Video start_time {video_start:.6f}s is below -0.1s')
    required = auto_level(width, height, nominal or 25)
    required_level = round(float(required) * (30 if expected_codec == 'hevc' else 10))
    if expected_codec != 'av1' and int(v.get('level', 0)) < required_level:
        errors.append(f'AVC level below required {required}')
    color = 'bt709' if height > 576 else 'smpte170m'
    for key in ('color_space', 'color_transfer', 'color_primaries'):
        if v.get(key) != color:
            errors.append(f'Missing / wrong {key}')
    if v.get('color_range') != 'tv':
        errors.append('Color range is not limited / TV')
    if not media.audio:
        errors.append('No audio stream (TV preset requires AAC stereo)')
    if not allow_all_audio and len(media.audio) > 1:
        errors.append('More than one audio stream')
    for a in media.audio:
        if (a.get('codec_name') != 'aac' or a.get('profile') != 'LC'
                or int(a.get('sample_rate', 0)) != 48000 or not 1 <= int(a.get('channels', 0)) <= 2):
            errors.append('Audio must be AAC-LC, ≤2 channels, 48 kHz')
        if abs(float(a.get('duration') or 0) - media.duration) >= .040:
            errors.append('Audio/video duration drift ≥40 ms')
        audio_start = float(a.get('start_time') or 0)
        if abs(video_start - audio_start) >= .04:
            errors.append(f'Audio/video start offset {abs(video_start - audio_start):.6f}s ≥40 ms')
    if target:
        if width != target.width or height != target.height:
            errors.append('Unexpected output dimensions')
        bitrate = int(v.get('bit_rate') or 0)
        if bitrate > target.maxrate * 1e6 * 1.05:
            errors.append('Video bitrate exceeds TV cap')
    return errors


def faststart_ok(path):
    """Read MP4 top-level headers only; no loading mdat into memory."""
    import struct
    with open(path, 'rb') as stream:
        while True:
            header = stream.read(8)
            if len(header) < 8:
                return False
            size, kind = struct.unpack('>I4s', header)
            head = 8
            if size == 1:
                extended = stream.read(8)
                if len(extended) < 8:
                    return False
                size, head = struct.unpack('>Q', extended)[0], 16
            if kind == b'moov':
                return True
            if kind == b'mdat' or size < head:
                return False
            stream.seek(size - head, 1)
