"""Opt-in Real-ESRGAN/Waifu2x-ncnn CLI hook. No bundled model or implicit downloads."""
import math
import shutil
import struct
from pathlib import Path
from .model import effective
from .planning import is_hdr
from ..process import capture, tool
from ..ffprobe import probe, rate

MAX_FRAMES = 200_000


def png_size(path):
    with Path(path).open('rb') as file:
        header = file.read(24)
    if len(header) != 24 or header[:8] != b'\x89PNG\r\n\x1a\n' or header[12:16] != b'IHDR':
        raise ValueError('External AI output is not a PNG frame: ' + str(path))
    return struct.unpack('>II', header[16:24])


def ai_preprocess(media, options, folder, cancel=None, progress=None, start=0, duration=None):
    settings = effective(options.enhance)
    if not settings.ai_enabled or settings.level == 'Off':
        return media, []
    exe = Path(settings.ai_executable).expanduser()
    if not exe.is_file():
        raise ValueError('External AI executable is missing. Select your own ncnn-compatible tool or disable the opt-in hook.')
    if settings.upscale == 'Keep':
        raise ValueError('External AI requires an explicit Upscale to target; it never changes resolution silently.')
    if not media.metadata_verified or not media.cadence.verified or media.cadence.unstable or media.interlaced or is_hdr(media) or media.video.get('pix_fmt') not in ('yuv420p', 'nv12'):
        raise ValueError('External AI hook accepts verified progressive CFR 8-bit SDR only. Convert/deinterlace HDR, 10-bit or unknown input first, or disable the hook.')
    seconds = min(duration if duration is not None else media.duration, max(0, media.duration-start))
    frames = math.ceil(seconds * float(media.fps))
    if not 0 < frames <= MAX_FRAMES:
        raise ValueError('External AI duration unknown or frame budget exceeds 200,000. Use a shorter local file.')
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    width, height = int(media.video['width']), int(media.video['height'])
    # PNG may be uncompressible. Reserve source+AI frames, intermediate, and normal output headroom.
    estimate = frames * width * height * 4 * (1 + settings.ai_scale**2) * 2
    if shutil.disk_usage(folder).free < estimate + 512*1024**2:
        raise ValueError(f'External AI scratch space may need {estimate/1024**3:.1f} GB plus 0.5 GB reserve. Choose a larger scratch disk or shorter input.')
    incoming, outgoing = folder/'ai-input', folder/'ai-output'
    incoming.mkdir(); outgoing.mkdir()
    commands = []
    def run(cmd, timeout):
        commands.append(cmd)
        if progress:
            progress('External AI: ' + ('frame extraction' if len(commands)==1 else 'user-provided executable' if len(commands)==2 else 'lossless intermediate'))
        capture(cmd, timeout=timeout, cancel=cancel)
    run([tool('ffmpeg'), '-nostdin', '-v', 'error', '-y', '-threads', '2', '-filter_threads', '2', '-ss', str(start),
         '-i', str(media.path), '-t', str(seconds), '-an', '-sn', '-vf', f'fps={media.fps},format=rgb24',
         '-start_number', '1', '-threads', '2', str(incoming/'%08d.png')], max(30, seconds*5))
    files = sorted(incoming.glob('*.png'))
    if not files or len(files) > MAX_FRAMES:
        raise ValueError('External AI extraction produced no frames or exceeded its budget')
    run([str(exe.resolve()), '-i', str(incoming), '-o', str(outgoing), '-s', str(settings.ai_scale), '-f', 'png'], min(86400, max(120, len(files)*30)))
    outputs = sorted(outgoing.glob('*.png'))
    if [f.name for f in outputs] != [f.name for f in files]:
        raise ValueError('External AI must preserve all numbered PNG filenames. Missing/extra frames were rejected; source is untouched.')
    for image in outputs:
        if png_size(image) != (width*settings.ai_scale, height*settings.ai_scale):
            raise ValueError('External AI frame dimensions do not match the explicit scale; source is untouched')
    intermediate = folder/'ai-lossless.mkv'
    default_color = 'bt709' if height > 576 else 'smpte170m'
    primaries = media.video.get('color_primaries')
    primaries = primaries if primaries in ('bt709','smpte170m','bt470bg','bt2020') else default_color
    transfer = media.video.get('color_transfer')
    transfer = transfer if transfer in ('bt709','smpte170m','bt2020-10','bt2020-12','gamma22','gamma28','iec61966-2-1') else default_color
    sar = rate(media.video.get('sample_aspect_ratio','1:1').replace(':','/')) or rate('1')
    run([tool('ffmpeg'), '-nostdin', '-v', 'error', '-y', '-threads', '2', '-filter_threads', '2',
         '-framerate', str(media.fps), '-start_number', '1', '-i', str(outgoing/'%08d.png'),
         '-ss', str(start), '-i', str(media.path), '-map', '0:v:0', '-map', '1:a?', '-t', str(seconds),
         '-vf', f'setsar={sar}', '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'bgr0', '-threads', '2', '-c:a', 'copy',
         '-color_range', 'pc', '-color_primaries', primaries,
         '-color_trc', transfer, str(intermediate)], max(60, seconds*10))
    result = probe(intermediate, cancel=cancel, fallback_fps=str(media.fps))
    result.video['bit_rate'] = media.video.get('bit_rate')  # budget refers to original media, not lossless scratch bitrate
    if result.fps != media.fps:
        raise ValueError('External AI intermediate changed frame rate; output rejected')
    notes = ['Opt-in external AI: user-provided executable, not a bundled/verified AI model; may invent visual detail',
             f'AI {width}×{height} → {width*settings.ai_scale}×{height*settings.ai_scale}; final explicit TV target/scaler still applies',
             'AI commands: ' + repr(commands)]
    return result, notes
