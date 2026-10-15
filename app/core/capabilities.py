"""Short sequential hardware tests; cache tied to FFmpeg and probe policy."""
import json
import os
import time
import tempfile
import re
from pathlib import Path
from .process import capture, tool, ToolError
from .settings import data_dir
from .qsv import DEVICE_CANDIDATES, device_args, is_haswell

PROBE_VERSION = 2
VBR = ['-b:v', '2M', '-maxrate', '3M', '-bufsize', '6M']


def probe_capabilities(force=False):
    binary = tool('ffmpeg')
    stat = Path(binary).stat()
    key = f'{PROBE_VERSION}:{binary}:{stat.st_size}:{stat.st_mtime_ns}'
    cache = data_dir() / 'capabilities.json'
    if not force:
        try:
            data = json.loads(cache.read_text())
            if data['key'] == key and time.time() - data['timestamp'] < 7 * 86400:
                return data
        except (OSError, ValueError, KeyError):
            pass
    encoders = capture([binary, '-hide_banner', '-encoders'])
    vpp = capture([binary, '-hide_banner', '-h', 'filter=vpp_qsv'])
    data = {'key': key, 'timestamp': time.time(), 'codecs': {},
            'hwaccels': capture([binary, '-hide_banner', '-hwaccels']),
            'vpp_qsv': 'VPP' in vpp or 'framerate' in vpp,
            'vpp_framerate': False, 'decode': False, 'decode_codecs': [],
            'qsv_device': None, 'device_errors': {},
            'adapter': 'Intel QSV (generation not reported)'}
    if os.name == 'nt':
        try:
            data['adapter'] = capture(['powershell', '-NoProfile', '-Command',
                                      '(Get-CimInstance Win32_VideoController).Name'], timeout=12).strip()
        except ToolError:
            pass
    data['haswell'] = is_haswell(data)

    def encode_test(codec, extra=None, rate_control=None, output=None):
        return capture([binary, '-hide_banner', '-loglevel', 'error', '-y']
                       + device_args(data) + ['-f', 'lavfi', '-i',
                       'color=size=320x180:rate=25', '-t', '1', '-vf', 'format=nv12',
                       '-c:v', codec] + (VBR if rate_control is None else rate_control)
                       + (extra or []) + ([output] if output else ['-f', 'null', '-']), timeout=18)

    # Select the FIRST working backend, never the fastest. Test an actual encode.
    if 'h264_qsv' in encoders:
        for device in DEVICE_CANDIDATES:
            data['qsv_device'] = device
            try:
                encode_test('h264_qsv')
                break
            except ToolError as exc:
                data['device_errors'][device] = str(exc)[-2000:]
        else:
            data['qsv_device'] = None
    for codec in ('h264_qsv', 'hevc_qsv', 'av1_qsv'):
        values = {'available': False, 'tested_options': [], 'icq': False}
        data['codecs'][codec] = values
        if data['haswell'] and codec != 'h264_qsv':
            values['reason'] = 'Haswell supports H.264 QSV encode only (8-bit NV12)'
            continue
        if not data['qsv_device'] or codec not in encoders:
            values['reason'] = ('No working QSV device: ' + '; '.join(data['device_errors'].values())
                                if codec in encoders else f'FFmpeg lacks {codec}')
            continue
        try:
            encode_test(codec)
            values['available'] = True
        except ToolError as exc:
            values['reason'] = str(exc)[-2000:]
            continue
        try:
            # Disable lookahead: global_quality must select ICQ, not default CQP.
            encode_test(codec, ['-look_ahead', '0'], ['-global_quality', '23'])
            values['icq'] = True
        except ToolError:
            pass
    if data['codecs']['h264_qsv']['available']:
        with tempfile.TemporaryDirectory(prefix='edius-qsv-') as directory:
            sample = str(Path(directory) / 'probe.mp4')
            try:
                encode_test('h264_qsv', output=sample)
                base = [binary, '-v', 'error'] + device_args(data) + [
                    '-hwaccel', 'qsv', '-hwaccel_device', 'hw',
                    '-hwaccel_output_format', 'qsv', '-i', sample]
                capture(base + ['-vf', 'hwdownload,format=nv12', '-t', '1',
                                '-f', 'null', '-'], timeout=18)
                data['decode'] = True
                data['decode_codecs'] = ['h264']
                if data['vpp_qsv']:
                    capture(base + ['-vf', 'vpp_qsv=w=320:h=180:format=nv12:framerate=25',
                                    '-c:v', 'h264_qsv'] + VBR + ['-t', '1', '-f', 'null', '-'], timeout=18)
                    data['vpp_framerate'] = True
            except ToolError:
                pass
    optional = {'look_ahead': '1', 'look_ahead_depth': '40', 'extbrc': '1',
                'b_strategy': '1', 'trellis': '1', 'rdo': '1', 'low_delay_brc': '1',
                'max_frame_size': '100000', 'max_frame_size_i': '100000',
                'max_frame_size_p': '100000', 'max_frame_size_b': '100000',
                'scenario': 'archive', 'gpb': '1'}
    for codec, values in data['codecs'].items():
        if not values['available']:
            continue
        help_text = capture([binary, '-hide_banner', '-h', f'encoder={codec}'])
        for flag, value in optional.items():
            if not re.search(r'(?m)^\s*-' + re.escape(flag) + r'\s', help_text):
                continue
            try:
                encode_test(codec, ['-' + flag, value])
                values['tested_options'].append(flag)
            except ToolError:
                pass
    temp = cache.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    temp.replace(cache)
    return data
