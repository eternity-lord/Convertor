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
    if caps.get('matrix'):
        from .hardware.matrix import HardwareMatrix
        from .hardware.select import Request, choose_pipeline
        from .hardware.planning import preview_from_selection
        matrix = HardwareMatrix.from_dict(caps['matrix'])
        selected_id = caps.get('_preview_encoder_id')
        selected = next((c for c in matrix.encoders if c.id == selected_id), None)
        request = Request(codec=selected.codec if selected else 'h264', width=2, height=2, fps=1,
                          device=selected.gpu_id if selected else 'Auto', source_codec=media.video.get('codec_name', ''),
                          source_format=media.video.get('pix_fmt', ''), source_width=int(media.video.get('width', 0)),
                          source_height=int(media.video.get('height', 0)), source_fps=float(media.fps))
        choice = choose_pipeline(matrix, request, [c.id for c in matrix.encoders if selected and c.id != selected.id])
        return preview_from_selection(media, choice, timestamp, output, executable)
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
