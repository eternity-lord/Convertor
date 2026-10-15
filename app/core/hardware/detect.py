"""Inventory discovery. Device names and ffmpeg listings are not proof of usability."""
import csv
import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict
from pathlib import Path
from ..process import capture, ToolError
from ..qsv import is_haswell
from .matrix import GpuProfile


def vendor_of(name, vendor_id=''):
    text = name.lower()
    if vendor_id.upper() == '8086' or 'intel' in text:
        return 'Intel'
    if vendor_id.upper() == '10DE' or 'nvidia' in text or 'geforce' in text:
        return 'NVIDIA'
    if vendor_id.upper() == '1002' or any(x in text for x in ('amd', 'radeon', 'ati ')):
        return 'AMD'
    return 'Unknown'


def generation_of(name, vendor_id='', device_id=''):
    if is_haswell({'adapter': name}) or (vendor_id == '8086' and device_id[:2] in ('04', '0A', '0C', '0D')):
        return 'Haswell'
    for pattern, generation in [
        (r'RTX\s*50[0-9]{2}', 'Blackwell'), (r'RTX\s*40[0-9]{2}', 'Ada Lovelace'),
        (r'RTX\s*30[0-9]{2}', 'Ampere'), (r'RTX\s*20[0-9]{2}|GTX\s*16[0-9]{2}', 'Turing'),
        (r'GTX\s*10[0-9]{2}', 'Pascal'), (r'RX\s*7[0-9]{3}', 'RDNA 3'),
        (r'RX\s*6[0-9]{3}', 'RDNA 2'), (r'RX\s*5[0-9]{3}', 'RDNA'),
        (r'HD\s*Graphics\s*5[0-9]{2}\b', 'Skylake'),
        (r'Arc.*(?:A[357][0-9]{2})', 'Alchemist'), (r'Arc.*B[0-9]{3}', 'Battlemage')]:
        if re.search(pattern, name, re.I):
            return generation
    return 'Unknown'


def gpu_from_wmi(row, index):
    pnp = row.get('PNPDeviceID') or ''
    ven, dev = re.search(r'VEN_([0-9A-F]{4})', pnp, re.I), re.search(r'DEV_([0-9A-F]{4})', pnp, re.I)
    vid, did = ven.group(1).upper() if ven else '', dev.group(1).upper() if dev else ''
    name = row.get('Name') or 'Unnamed display adapter'
    generation = generation_of(name, vid, did)
    return GpuProfile(pnp or f'display-{index}', name, vendor_of(name, vid), vid, did,
                      str(row.get('DriverVersion') or ''), int(row['AdapterRAM']) if row.get('AdapterRAM') else None,
                      generation)


def detect_gpus(run=capture, platform=None):
    gpus, errors = [], []
    if (platform or os.name) == 'nt':
        try:
            text = run(['powershell', '-NoProfile', '-NonInteractive', '-Command',
                        '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; '
                        'Get-CimInstance Win32_VideoController | Select-Object Name,PNPDeviceID,'
                        'DriverVersion,AdapterRAM | ConvertTo-Json -Compress'], timeout=10)
            rows = json.loads(text.lstrip('\ufeff'))
            if isinstance(rows, dict):
                rows = [rows]
            gpus = [gpu_from_wmi(row, i) for i, row in enumerate(rows or [])]
        except (ToolError, OSError, ValueError, TypeError) as exc:
            errors.append(f'Windows GPU enumeration: {exc}')
    else:
        # Read-only Linux inventory for CPU-only / development installations.
        for path in sorted(Path('/sys/class/drm').glob('card[0-9]*/device')):
            try:
                vendor = (path / 'vendor').read_text().strip().removeprefix('0x').upper()
                device = (path / 'device').read_text().strip().removeprefix('0x').upper()
                gpus.append(GpuProfile(str(path.resolve()), f'{vendor_of("", vendor)} PCI {device}',
                                       vendor_of('', vendor), vendor, device))
            except OSError:
                pass
    smi = shutil.which('nvidia-smi')
    if smi:
        try:
            text = run([smi, '--query-gpu=index,name,pci.bus_id,driver_version,memory.total',
                        '--format=csv,noheader,nounits'], timeout=10)
            for row in csv.reader(text.splitlines()):
                if len(row) != 5:
                    continue
                ordinal, name, bus, driver, memory = [v.strip() for v in row]
                matches = [g for g in gpus if g.vendor == 'NVIDIA' and g.name == name and g.ordinal is None]
                if len(matches) == 1:
                    gpu = matches[0]
                else:
                    gpu = GpuProfile('nvidia-' + bus, name, 'NVIDIA')
                    gpus.append(gpu)
                gpu.driver, gpu.ram, gpu.ordinal = driver, int(float(memory) * 1024**2), int(ordinal)
                gpu.generation = generation_of(name)
                gpu.affinity = 'nvidia-smi index; CUDA ordinal verified by per-device encode (CUDA_VISIBLE_DEVICES may remap)'
        except (ToolError, OSError, ValueError) as exc:
            errors.append(f'nvidia-smi: {exc}')
    gpus.append(GpuProfile('cpu', 'CPU software encoders', 'CPU', affinity='CPU'))
    return gpus, errors


def parse_listing(text, kind):
    names = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r'[A-Z.]{1,8}', parts[0]) and parts[1] != '=':
            names.extend(parts[1].split(','))
    return sorted(set(names))


def detect_ffmpeg(binary, probe_binary, run=capture):
    inventory = {'ffmpeg': binary, 'ffprobe': probe_binary, 'raw': {}, 'errors': []}
    for kind in ('version', 'encoders', 'decoders', 'hwaccels', 'filters', 'muxers', 'demuxers', 'pix_fmts'):
        try:
            text = run([binary, '-hide_banner', '-' + kind], timeout=10)
            inventory['raw'][kind] = text
            if kind == 'version':
                inventory['version'] = text.splitlines()[0] if text else 'Unknown'
                inventory['build_flags'] = re.findall(r'--[\w-]+(?:=[^\s]+)?', text)
            elif kind == 'hwaccels':
                inventory[kind] = [s.strip() for s in text.splitlines() if re.fullmatch(r'[a-z0-9_]+', s.strip())]
            else:
                inventory[kind] = parse_listing(text, kind)
        except (ToolError, OSError) as exc:
            inventory[kind] = []
            inventory['errors'].append(f'{kind}: {exc}')
    if not str(inventory.get('version', '')).startswith('ffmpeg version') or not inventory.get('encoders'):
        raise ToolError('Selected FFmpeg cannot report its version / encoders: ' + '; '.join(inventory['errors']))
    return inventory


def fingerprint(inventory, gpus):
    files = []
    for key in ('ffmpeg', 'ffprobe'):
        path = Path(inventory[key])
        stat = path.stat()
        files.append((str(path.resolve()), stat.st_size, stat.st_mtime_ns))
    content = dict(files=files, version=inventory.get('raw', {}).get('version'),
                   gpus=[asdict(g) for g in gpus], cuda_visible=os.environ.get('CUDA_VISIBLE_DEVICES'),
                   schema=1)
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
