"""Short sequential hardware tests; cache tied to the actual FFmpeg binary."""
import json
import os
import time
import tempfile
import re
from pathlib import Path
from .process import capture, tool, ToolError
from .settings import data_dir


def probe_capabilities(force=False):
    binary = tool('ffmpeg')
    stat = Path(binary).stat()
    key = f'{binary}:{stat.st_size}:{stat.st_mtime_ns}'
    cache = data_dir() / 'capabilities.json'
    if not force:
        try:
            data = json.loads(cache.read_text())
            if data['key'] == key and time.time() - data['timestamp'] < 7 * 86400:
                return data
        except (OSError, ValueError, KeyError):
            pass
    encoders = capture([binary, '-hide_banner', '-encoders'])
    hwaccels = capture([binary, '-hide_banner', '-hwaccels'])
    vpp = capture([binary, '-hide_banner', '-h', 'filter=vpp_qsv'])
    data = {'key': key, 'timestamp': time.time(), 'codecs': {}, 'hwaccels': hwaccels,
            'vpp_qsv': 'VPP' in vpp or 'framerate' in vpp, 'vpp_framerate': 'framerate' in vpp,
            'decode': False, 'adapter': 'Intel QSV (generation not reported)'}
    if os.name == 'nt':
        try:
            data['adapter'] = capture(['powershell', '-NoProfile', '-Command',
                                      '(Get-CimInstance Win32_VideoController).Name'], timeout=12).strip()
        except ToolError:
            pass
    for codec in ('h264_qsv', 'hevc_qsv', 'av1_qsv'):
        tests = []
        if codec in encoders:
            for low in (0, 1):
                begin = time.monotonic()
                try:
                    capture([binary, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
                             '-i', 'color=size=320x180:rate=25', '-t', '1', '-vf', 'format=nv12',
                             '-c:v', codec, '-low_power', str(low), '-f', 'null', '-'], timeout=18)
                    tests.append((time.monotonic() - begin, low))
                except ToolError:
                    pass
        best = min(tests) if tests else None
        data['codecs'][codec] = {'available': bool(best), 'low_power': best[1] if best else 0,
                                 'tested_options': []}
    if data['codecs']['h264_qsv']['available']:
        # Prove decode + VPP + rate conversion using an actual AVC file, not encoder names.
        with tempfile.TemporaryDirectory(prefix='edius-qsv-') as directory:
            sample = str(Path(directory) / 'probe.mp4')
            try:
                capture([binary, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                         'color=size=320x180:rate=25', '-t', '1', '-vf', 'format=nv12',
                         '-c:v', 'h264_qsv', '-low_power',
                         str(data['codecs']['h264_qsv']['low_power']), sample], timeout=18)
                capture([binary, '-v', 'error', '-init_hw_device', 'qsv=hw',
                         '-hwaccel', 'qsv', '-hwaccel_output_format', 'qsv', '-i', sample,
                         '-vf', 'vpp_qsv=w=320:h=180:format=nv12:framerate=25',
                         '-c:v', 'h264_qsv', '-t', '1', '-f', 'null', '-'], timeout=18)
                data['decode'] = True
                data['vpp_framerate'] = True
            except ToolError:
                data['decode'] = False
    # Independently test optional encoder flags. Unsupported controls remain disabled.
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
                capture([binary, '-v', 'error', '-f', 'lavfi', '-i',
                         'color=size=320x180:rate=25', '-t', '1', '-vf', 'format=nv12',
                         '-c:v', codec, '-low_power', str(values['low_power']),
                         '-' + flag, value, '-f', 'null', '-'], timeout=12)
                values['tested_options'].append(flag)
            except ToolError:
                pass
    temp = cache.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    temp.replace(cache)
    return data
