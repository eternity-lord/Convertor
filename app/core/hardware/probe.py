"""Sequential, cancellable, ten-second-bounded real encoder/decoder tests.

Listings are evidence of build availability only. Failed/untested features carry a reason.
Upper limits are deliberately UNKNOWN; samples are lower bounds, not advertised maxima.
"""
import json
import re
import tempfile
import time
from pathlib import Path
from ..process import capture, ToolError, ProbeSkipped, tool
from ..settings import data_dir
from ..qsv import DEVICE_CANDIDATES
from .detect import detect_gpus, detect_ffmpeg, fingerprint
from .matrix import HardwareMatrix, EncoderCapabilities, GpuProfile, ENCODERS, CODECS
from .flagmap import mode_candidates, render, QualityModel
from .errors import classify_error

PROBE_REVISION = 4
QSV_DEVICES = DEVICE_CANDIDATES + ('qsv=hw:hw_any',)


def parse_encoder_help(text):
    options = re.findall(r'(?m)^\s*-([A-Za-z0-9_-]+)\s', text)
    match = re.search(r'Supported pixel formats:\s*([^\n]+)', text)
    return sorted(set(options)), match.group(1).split() if match else []


def backend_devices(gpu, name, help_options):
    if gpu.vendor == 'Intel':
        return [(['-init_hw_device', value, '-filter_hw_device', 'hw'], []) for value in QSV_DEVICES]
    if gpu.vendor == 'NVIDIA':
        ordinal = gpu.ordinal if gpu.ordinal is not None else 0
        enc = ['-gpu', str(ordinal)] if 'gpu' in help_options else []
        return [(['-init_hw_device', f'cuda=hw:{ordinal}', '-filter_hw_device', 'hw'], enc)]
    if gpu.vendor == 'Media Foundation':
        # MF defaults may use a software MFT; do not advertise that as hardware.
        return [([], ['-hw_encoding', '1'])] if 'hw_encoding' in help_options else []
    return [([], [])]


def test_command(binary, cap, flags, width=1920, height=1080, fps=60, pix_fmt='nv12', output=None):
    if cap.vendor == 'CPU':
        pix_fmt = 'yuv420p10le' if pix_fmt == 'p010le' else 'yuv420p' if pix_fmt == 'nv12' else pix_fmt
    vf = 'format=' + pix_fmt
    if cap.vendor in ('Intel', 'NVIDIA'):
        vf += ',hwupload=extra_hw_frames=16'
    return ([binary, '-hide_banner', '-nostdin', '-y', '-v', 'warning', '-progress', 'pipe:1',
             '-nostats', '-threads', '2', '-filter_threads', '2'] + cap.device_args +
            ['-f', 'lavfi', '-i', f'testsrc2=size={width}x{height}:rate={fps}', '-t', '1',
             '-vf', vf, '-an', '-c:v', cap.name, '-threads', '2'] + cap.encoder_args + flags +
            ([str(output)] if output else ['-f', 'null', '-']))


def scan_hardware(force=False, progress=None, cancel=None, run=capture):
    progress = progress or (lambda text: None)
    binary, probe_binary = tool('ffmpeg'), tool('ffprobe')
    progress('Reading GPU and FFmpeg inventory…')
    gpus, errors = detect_gpus(run)
    inventory = detect_ffmpeg(binary, probe_binary, run)
    key = str(PROBE_REVISION) + ':' + fingerprint(inventory, gpus)
    cache = data_dir() / 'hardware-matrix.json'
    if not force:
        try:
            matrix = HardwareMatrix.from_dict(json.loads(cache.read_text(encoding='utf-8')))
            if matrix.fingerprint == key and time.time() - matrix.timestamp < 7 * 86400:
                progress('Loaded tested hardware matrix (FFmpeg / driver fingerprint matched)')
                return matrix
        except (OSError, ValueError, KeyError, TypeError):
            pass
    matrix = HardwareMatrix(gpus=list(gpus), inventory=inventory, errors=errors + inventory['errors'],
                            fingerprint=key, timestamp=time.time())
    present = set(inventory.get('encoders', []))
    # A runtime fallback entry is explicit; never invent a detected physical GPU.
    groups = []
    for vendor, names in ENCODERS.items():
        actual = [g for g in gpus if g.vendor == vendor]
        if vendor == 'NVIDIA' and any(g.ordinal is not None for g in actual):
            actual = [g for g in actual if g.ordinal is not None]
        if len(actual) == 1 or (vendor == 'NVIDIA' and actual and all(g.ordinal is not None for g in actual)):
            groups.extend(actual)
        elif vendor == 'CPU':
            groups.extend(actual)
        else:
            gpu = GpuProfile(vendor.lower().replace(' ', '-') + '-runtime',
                             vendor + (' runtime-selected adapter (multiple devices; binding unknown)' if actual
                                       else ' runtime (no physical adapter enumerated)'), vendor)
            groups.append(gpu)
            matrix.gpus.append(gpu)

    def execute(command, cap=None, label=''):
        if cancel is not None and cancel.is_set():
            raise ProbeSkipped('Hardware scan cancelled')
        progress(label or ('Testing ' + cap.name if cap else 'Testing hardware decode'))
        start = time.monotonic()
        record = {'command': command, 'label': label, 'success': False}
        try:
            text = run(command, timeout=10, cancel=cancel, include_stderr=True)
            # Successful exit with software decode / ignored options is not hardware proof.
            if re.search(r'has not been used for any stream|falling back to.*software|default.*CQP|default.*constant quant|CQP.{0,80}default|constant quantization.*default', text, re.I):
                raise ToolError('Requested controls were ignored or silently substituted: ' + text[-2000:])
            matches = re.findall(r'(?m)^speed=\s*([\d.]+)x', text)
            record.update(success=True, seconds=round(time.monotonic() - start, 4),
                          speed=float(matches[-1]) if matches else None, stderr=text[-3000:])
        except ProbeSkipped:
            raise
        except (ToolError, OSError) as exc:
            record.update(reason=str(exc)[-3000:], hint=classify_error(exc).describe(),
                          seconds=round(time.monotonic() - start, 4))
        if cap:
            cap.attempts.append(record)
        return record

    model = QualityModel(bitrate=4, maxrate=6, bufsize=12)
    for gpu in groups:
        for name in ENCODERS[gpu.vendor]:
            cap = EncoderCapabilities(gpu.id + '/' + name, gpu.id, name, gpu.vendor, CODECS[name])
            matrix.encoders.append(cap)
            haswell = gpu.generation == 'Haswell'
            cap.features['haswell'] = haswell
            if haswell and name != 'h264_qsv':
                cap.reason = 'Haswell: H.264 8-bit encode only; low_power disabled'
                continue
            if name not in present:
                cap.reason = f'Active FFmpeg build lacks {name}'
                continue
            try:
                help_text = run([binary, '-hide_banner', '-h', f'encoder={name}'], timeout=10, cancel=cancel)
                cap.help_options, cap.advertised_formats = parse_encoder_help(help_text)
            except (ToolError, OSError) as exc:
                cap.reason = str(exc)
                continue
            modes = mode_candidates(name, gpu.vendor, cap.help_options)
            # CPU software is tested at a modest frame size, not falsely capped at it.
            size = (320, 180, 30) if gpu.vendor == 'CPU' else (1920, 1080, 60)
            baseline = render(modes['VBR'], model)
            # Keep CPU AV1 tests bounded on weak machines.
            quick = {'libsvtav1': ['-preset', '12'], 'libaom-av1': ['-cpu-used', '8'],
                     'librav1e': ['-speed', '10'], 'libvpx-vp9': ['-deadline', 'realtime', '-cpu-used', '8'],
                     'libx264': ['-preset', 'veryfast'], 'libx265': ['-preset', 'veryfast']}.get(name, [])
            quick = [v for pair in zip(quick[::2], quick[1::2]) if pair[0][1:] in cap.help_options for v in pair]
            if name == 'libsvtav1' and 'svtav1-params' in cap.help_options:
                quick += ['-svtav1-params', 'lp=2']
            if name == 'libx265' and 'x265-params' in cap.help_options:
                quick += ['-x265-params', 'pools=2:frame-threads=2']
            baseline_mode = 'VBR'
            for dev_args, enc_args in backend_devices(gpu, name, cap.help_options):
                cap.device_args, cap.encoder_args = dev_args, enc_args + quick
                for mode, template in modes.items():
                    result = execute(test_command(binary, cap, render(template, model), *size), cap,
                                     gpu.name + ': ' + name + ' / initial ' + mode)
                    if result['success']:
                        baseline_mode, baseline = mode, render(template, model)
                        cap.available, cap.reason, cap.speed = True, '', result['speed']
                        cap.modes[mode] = template
                        cap.samples.append(dict(width=size[0], height=size[1], fps=size[2], bit_depth=8, chroma='420'))
                        break
                    cap.reason = result['hint'] + ': ' + result['reason']
                    if classify_error(result['reason']).code in ('mfx_session', 'cuda_driver', 'amf_driver', 'no_device'):
                        break  # Another RC mode cannot repair device initialization.
                if cap.available:
                    break
            if not cap.available:
                cap.device_args, cap.encoder_args = [], []
                if not cap.attempts:
                    cap.reason = 'Hardware-only Media Foundation control absent; cannot prove hardware encoding'
                continue
            for mode, flags in modes.items():
                if mode in cap.modes:
                    continue
                result = execute(test_command(binary, cap, render(flags, model), *size), cap, name + ' / ' + mode)
                if result['success']:
                    cap.modes[mode] = flags
                else:
                    cap.features[mode] = {'available': False, 'reason': result['reason']}
            values = {}
            if gpu.vendor == 'Intel':
                values = {'preset': ['veryfast', 'medium', 'slow', 'veryslow'], 'look_ahead': ['0', '1'],
                          'extbrc': ['1'], 'async_depth': ['4'], 'look_ahead_depth': ['40'],
                          'trellis': ['1'], 'rdo': ['1'], 'b_strategy': ['1'], 'low_delay_brc': ['1'],
                          'max_frame_size': ['100000'], 'scenario': ['archive'], 'gpb': ['1']}
                if not haswell:
                    values['low_power'] = ['0', '1']
            elif gpu.vendor == 'NVIDIA':
                values = {'preset': [f'p{i}' for i in range(1, 8)], 'tune': ['hq', 'll', 'ull', 'lossless'],
                          'spatial-aq': ['0', '1'], 'temporal-aq': ['0', '1'], 'rc-lookahead': ['16']}
            elif gpu.vendor == 'AMD':
                values = {'quality': ['speed', 'balanced', 'quality']}
            elif gpu.vendor == 'CPU' and 'preset' in cap.help_options:
                values = {'preset': ['12', '10', '8', '6'] if name == 'libsvtav1' else
                          ['veryfast', 'medium', 'slow', 'veryslow']}
            values['bf'] = ['0', '2']
            for flag, candidates in values.items():
                if flag != 'bf' and flag not in cap.help_options:
                    cap.features[flag] = {'available': False, 'reason': 'Not advertised by encoder help'}
                    continue
                for value in candidates:
                    # Replace any baseline private option instead of adding contradictory flags.
                    args = render(cap.modes[baseline_mode], model) + ['-' + flag, value]
                    result = execute(test_command(binary, cap, args, *size), cap, f'{name} / {flag}={value}')
                    if result['success']:
                        cap.tested_values.setdefault(flag, []).append(value)
                cap.features[flag] = {'available': bool(cap.tested_values.get(flag)),
                                      'reason': '' if cap.tested_values.get(flag) else 'All tested values failed'}
            if not haswell:
                for fmt, chroma in [('p010le', '420'), ('yuv422p', '422'), ('yuv444p', '444')]:
                    # CUDA/QSV hwupload of these formats must actually succeed to be usable.
                    if fmt not in cap.advertised_formats and not (fmt == 'p010le' and 'yuv420p10le' in cap.advertised_formats):
                        cap.features[fmt] = {'available': False, 'reason': 'Not advertised by encoder help'}
                        continue
                    result = execute(test_command(binary, cap, baseline, *size, pix_fmt=fmt), cap, name + ' / ' + fmt)
                    cap.features[fmt] = {'available': result['success'], 'reason': result.get('reason', '')}
                    if result['success']:
                        cap.samples.append(dict(width=size[0], height=size[1], fps=size[2], bit_depth=10 if fmt == 'p010le' else 8, chroma=chroma))
            if cap.hardware:
                result = execute(test_command(binary, cap, baseline, 3840, 2160, 30), cap, name + ' / UHD 30fps')
                if result['success']:
                    cap.samples.append(dict(width=3840, height=2160, fps=30, bit_depth=8, chroma='420'))

    _probe_decoders(matrix, binary, execute)
    from ..enhance.probe import probe_filters, probe_gpu_recipes
    matrix.inventory['enhancement'] = probe_filters(binary, inventory, run, progress, cancel)
    for cap in matrix.encoders:
        if cap.hardware and cap.available:
            probe_gpu_recipes(binary, cap, execute, inventory)
    temp = cache.with_suffix('.tmp')
    temp.write_text(json.dumps(matrix.to_dict(), ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(cache)
    progress('Hardware scan complete')
    return matrix


def _probe_decoders(matrix, binary, execute):
    """Generate real compressed inputs, not lavfi frames falsely called GPU decode."""
    inventory = matrix.inventory
    hwaccels = inventory.get('hwaccels', [])
    tests = inventory.setdefault('decode_tests', [])
    gpu_caps = [c for c in matrix.encoders if c.available and c.hardware]
    # A codec can be decoded even if that GPU cannot encode it. Use any proven producer.
    with tempfile.TemporaryDirectory(prefix='edius-hardware-') as directory:
        for codec in ('h264', 'hevc', 'vp9', 'av1', 'mpeg2'):
            for depth in (8, 10):
                candidates = [c for c in matrix.encoders if c.available and c.codec == codec and
                              any(s['bit_depth'] == depth and s['chroma'] == '420' for s in c.samples)]
                source = Path(directory) / f'{codec}-{depth}.mkv'
                producer = sorted(candidates, key=lambda c: -(c.speed or 0))[:1]
                if producer:
                    cap = producer[0]
                    result = execute(test_command(binary, cap, render(cap.modes.get('VBR', next(iter(cap.modes.values()))), QualityModel(bitrate=4, maxrate=6, bufsize=12)),
                                                   1920, 1080, 60, pix_fmt='nv12' if depth == 8 else 'p010le', output=source),
                                     label=f'Generate real {codec} / {depth}-bit decode sample')
                elif codec == 'mpeg2' and depth == 8 and 'mpeg2video' in inventory.get('encoders', []):
                    result = execute([binary, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                                      'testsrc2=size=1920x1080:rate=60', '-t', '1', '-threads', '2',
                                      '-c:v', 'mpeg2video', '-b:v', '4M', '-pix_fmt', 'yuv420p', str(source)], label='Generate MPEG-2 decode sample')
                else:
                    tests.append(dict(codec=codec, bit_depth=depth, available=False, reason='No tested sample producer; decode untested'))
                    continue
                if not result['success']:
                    tests.append(dict(codec=codec, bit_depth=depth, available=False, reason='Sample generation failed: ' + result.get('reason', '')))
                    continue
                contexts = {}
                for cap in gpu_caps:
                    if cap.vendor in ('Intel', 'NVIDIA'):
                        backend = 'qsv' if cap.vendor == 'Intel' else 'cuda'
                        contexts[(cap.gpu_id, backend)] = (cap, cap.device_args)
                # Decode inventory is independent of hardware encode support.
                for vendor, backend in (('Intel', 'qsv'), ('NVIDIA', 'cuda')):
                    if backend in hwaccels and not any(key[1] == backend for key in contexts):
                        adapters = [g for g in matrix.gpus if g.vendor == vendor]
                        for gpu in adapters:
                            if gpu.generation == 'Haswell' and (codec != 'h264' or depth != 8):
                                continue
                            for index, (dev_args, _) in enumerate(backend_devices(gpu, '', [])):
                                contexts[(gpu.id + ':decode-only:' + str(index), backend)] = (None, dev_args)
                for backend in ('d3d11va', 'dxva2', 'd3d12va', 'vulkan'):
                    if backend in hwaccels:
                        contexts[('runtime', backend)] = (None, [])
                for (gpu_id, backend), (cap, device) in contexts.items():
                    record = dict(gpu_id=gpu_id, backend=backend, codec='mpeg2video' if codec == 'mpeg2' else codec,
                                  bit_depth=depth, width=1920, height=1080, fps=60, same_device=bool(cap))
                    if backend not in hwaccels or (cap and cap.features.get('haswell') and (codec != 'h264' or depth != 8)):
                        record.update(available=False, reason='Backend absent / Haswell source restricted to 8-bit H.264')
                        tests.append(record)
                        continue
                    outformat = {'dxva2': 'dxva2_vld', 'd3d11va': 'd3d11'}.get(backend, backend)
                    args = ['-hwaccel', backend] + (['-hwaccel_device', 'hw'] if device else []) + ['-hwaccel_output_format', outformat, '-extra_hw_frames', '16']
                    vf = 'hwdownload,format=' + ('nv12' if depth == 8 else 'p010le')
                    command = [binary, '-v', 'warning', '-nostdin', '-threads', '2'] + device + args + [
                        '-i', str(source), '-vf', vf, '-t', '1', '-an', '-f', 'null', '-']
                    result = execute(command, label=f'{backend} decode / {codec} / {depth}-bit')
                    record.update(available=result['success'], reason=result.get('reason', ''), command=command, args=args,
                                  download_format='nv12' if depth == 8 else 'p010le')
                    tests.append(record)
                    for target_cap in gpu_caps:
                        if target_cap.gpu_id == gpu_id:
                            target_cap.decoders.append(dict(record))
                    if not (result['success'] and cap and depth == 8 and codec == 'h264'):
                        continue
                    for filter_cap in [c for c in gpu_caps if c.gpu_id == gpu_id]:
                        filter_name = 'vpp_qsv' if backend == 'qsv' else 'scale_cuda'
                        if filter_name not in inventory.get('filters', []):
                            continue
                        vf = ('setpts=PTS-STARTPTS,vpp_qsv=w=1920:h=1080:format=nv12:framerate=60,setsar=1' if backend == 'qsv' else
                              'setpts=PTS-STARTPTS,scale_cuda=w=1920:h=1080:format=nv12,fps=60,setsar=1')
                        command = [binary, '-v', 'warning', '-nostdin', '-threads', '2'] + device + args + [
                            '-i', str(source), '-vf', vf, '-t', '1', '-an', '-c:v', filter_cap.name] + filter_cap.encoder_args + render(
                                filter_cap.modes.get('VBR', next(iter(filter_cap.modes.values()))), QualityModel(bitrate=4, maxrate=6, bufsize=12)) + ['-f', 'null', '-']
                        result = execute(command, filter_cap, filter_name + ' complete decode/filter/encode chain')
                        filter_cap.gpu_filters = dict(available=result['success'], name=filter_name,
                                               reason=result.get('reason', ''), command=command,
                                               width=1920, height=1080, fps=60)
