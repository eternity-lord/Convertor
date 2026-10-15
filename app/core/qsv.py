"""Shared QSV device and source policy for probes, encodes and previews."""
import re

DEVICE_CANDIDATES = (
    'qsv=hw,child_device_type=dxva2',
    'qsv=hw,child_device_type=d3d11va',
    'qsv=hw',
)


def is_haswell(caps):
    return bool(caps.get('haswell') or re.search(
        r'haswell|(?:HD|Iris(?:\s+Pro)?)\s*(?:Graphics\s*)?(?:4200|4400|4600|5000|5100|5200)\b',
        caps.get('adapter', ''), re.I))


def device_args(caps):
    return ['-init_hw_device', caps.get('qsv_device') or DEVICE_CANDIDATES[0],
            '-filter_hw_device', 'hw']


def can_decode(media, caps):
    codecs = ('h264',) if is_haswell(caps) else caps.get('decode_codecs', ['h264'])
    return bool(caps.get('decode') and media.video.get('codec_name') in codecs
                and media.video.get('pix_fmt') in ('yuv420p', 'nv12'))


def preview_command(media, caps, timestamp, output, executable):
    gpu = can_decode(media, caps)
    cmd = [executable, '-nostdin', '-v', 'error', '-y']
    if gpu:
        cmd += device_args(caps) + ['-hwaccel', 'qsv', '-hwaccel_device', 'hw',
                                   '-hwaccel_output_format', 'qsv', '-extra_hw_frames', '16']
    cmd += ['-ss', str(max(0, timestamp)), '-threads', '1', '-i', str(media.path),
            '-frames:v', '1', '-vf',
            ('hwdownload,format=nv12,' if gpu else '') + 'scale=480:-2',
            '-threads', '1', str(output)]
    return cmd, gpu
