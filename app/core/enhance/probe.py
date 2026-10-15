"""Real filter tests; hard ten-second timeout, bounded frames, existing scan worker."""
import re
from ..process import capture, ToolError, ProbeSkipped
from .model import effective

# Concrete bounded recipes; Custom values are checked again by exact-job preflight.
VIDEO_TESTS = {
    'hqdn3d': 'hqdn3d=1.5:1.125:2.25:1.6875',
    'nlmeans': 'nlmeans=s=2:p=7:r=9',
    'deband': 'deband=1thr=.02:2thr=.02:3thr=.02:4thr=.02:range=12:blur=1',
    'unsharp': 'unsharp=5:5:.35:5:5:0',
    'eq': 'eq=contrast=.99:gamma=1.01:saturation=1:brightness=0',
    'dither': 'format=yuv420p10le,zscale=dither=error_diffusion:range=limited:chromal=left,format=yuv420p',
    'chroma': 'zscale=chromal=left',
    'color_tags': 'setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709',
    'color_convert': 'zscale=matrixin=bt709:primariesin=bt709:transferin=bt709:rangein=full:matrix=smpte170m:primaries=smpte170m:transfer=smpte170m:range=limited,format=yuv420p',
    'hdr_tonemap': 'format=yuv420p10le,zscale=transferin=smpte2084:primariesin=bt2020:matrixin=bt2020nc:rangein=limited:transfer=linear:primaries=bt2020:matrix=gbr:range=full:npl=100,format=gbrpf32le,tonemap=tonemap=mobius:param=.3:desat=2,zscale=matrixin=gbr:transferin=linear:primariesin=bt2020:rangein=full:primaries=bt709:transfer=bt709:matrix=bt709:range=limited,sidedata=mode=delete:type=MASTERING_DISPLAY_METADATA,sidedata=mode=delete:type=CONTENT_LIGHT_LEVEL,sidedata=mode=delete:type=DYNAMIC_HDR_PLUS,format=yuv420p',
    'scale_lanczos': 'scale=640:360:flags=lanczos',
    'scale_spline': 'scale=640:360:flags=spline',
    'scale_bicubic': 'scale=640:360:flags=bicubic',
}
AUDIO_TESTS = {'loudnorm': 'loudnorm=I=-16:TP=-1.5:LRA=11', 'highpass': 'highpass=f=70',
               'lowpass': 'lowpass=f=16000', 'afftdn': 'afftdn=nf=-25'}
REQUIRED = {**{k: [k] for k in ('hqdn3d', 'nlmeans', 'deband', 'unsharp', 'eq')},
            'dither': ['format', 'zscale'], 'chroma': ['zscale'], 'color_tags': ['setparams'], 'color_convert': ['zscale'], 'hdr_tonemap': ['zscale', 'tonemap', 'sidedata'],
            **{k: ['scale'] for k in ('scale_lanczos', 'scale_spline', 'scale_bicubic')},
            **{k: [k] for k in AUDIO_TESTS}}


def probe_filters(binary, inventory, run=capture, progress=None, cancel=None):
    proof = {}
    filters = set(inventory.get('filters', []))
    for key, expr in {**VIDEO_TESTS, **AUDIO_TESTS}.items():
        if cancel is not None and cancel.is_set():
            raise ProbeSkipped('Enhancement scan cancelled')
        missing = [name for name in REQUIRED[key] if name not in filters]
        if missing:
            proof[key] = dict(available=False, reason='Active FFmpeg lacks ' + ', '.join(missing))
            continue
        if progress:
            progress('Testing enhancement: ' + key)
        audio = key in AUDIO_TESTS
        cmd = [binary, '-nostdin', '-hide_banner', '-v', 'warning', '-threads', '2', '-filter_threads', '2',
               '-progress', 'pipe:1', '-nostats', '-f', 'lavfi', '-i',
               'sine=frequency=440:sample_rate=48000' if audio else 'testsrc2=size=320x180:rate=30',
               '-t', '1', '-af' if audio else '-vf', expr, '-threads', '2', '-f', 'null', '-']
        item = dict(command=cmd, filter=expr, width=320, height=180, fps=30)
        try:
            output = run(cmd, timeout=10, cancel=cancel, include_stderr=True)
            speeds = re.findall(r'(?m)^speed=\s*([\d.]+)x', output)
            item.update(available=True, reason='', speed=float(speeds[-1]) if speeds else None)
        except ProbeSkipped:
            raise
        except (ToolError, OSError) as exc:
            item.update(available=False, reason=str(exc)[-2000:])
        proof[key] = item
    # No cross-API interop was proved for these filters; do not advertise a listing as usability.
    for name in ('nlmeans_opencl', 'unsharp_opencl', 'bilateral_cuda', 'libplacebo', 'cas', 'gradfun', 'colorlevels', 'vibrance'):
        proof[name] = dict(available=False, reason=('Listed, but no complete same-device enhancement recipe was proved' if name in filters else 'Absent from active FFmpeg build'))
    return proof


def probe_gpu_recipes(binary, cap, execute, inventory):
    """QSV VPP denoise/detail on real hardware surfaces, not encoder-name inference."""
    cap.features['enhance'] = {}
    for level in ('Low', 'Balanced', 'Excellent'):
        qsv = cap.vendor == 'Intel' and 'vpp_qsv' in inventory.get('filters', [])
        cuda = cap.vendor == 'NVIDIA' and 'bilateral_cuda' in inventory.get('filters', []) and 'scale_cuda' in inventory.get('filters', [])
        if not cap.gpu_filters.get('available') or not (qsv or cuda):
            cap.features['enhance'][level.lower()] = dict(available=False, reason='Complete same-device QSV / CUDA enhancement chain was not proven')
            continue
        settings = effective({'level': level})
        recipe = (f'vpp_qsv=denoise={round(settings.denoise*2)}:detail={round(settings.sharpen*20)}' if qsv else
                  f'scale_cuda=format=yuv444p,bilateral_cuda=sigmaS={settings.denoise:.2f}:sigmaR=0.05,scale_cuda=format=nv12')
        from ..hardware.probe import test_command
        from ..hardware.flagmap import render, QualityModel
        flags = render(cap.modes.get('VBR', next(iter(cap.modes.values()))), QualityModel(bitrate=4, maxrate=6, bufsize=12))
        command = test_command(binary, cap, flags)
        vf = command.index('-vf') + 1
        command[vf] += ',' + recipe + ',setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709'
        result = execute(command, cap, cap.name + ' enhancement / ' + level)
        cap.features['enhance'][level.lower()] = dict(available=result['success'], reason=result.get('reason', ''),
            filter=recipe, covers_sharpen=qsv, command=command, speed=result.get('speed'), width=1920, height=1080, fps=60)
