"""Internal quality model -> only successfully probed encoder flags."""
from dataclasses import dataclass

SPEEDS = ('Fast', 'Balanced', 'High Quality', 'Max Quality')


@dataclass(frozen=True)
class QualityModel:
    speed: str = 'Balanced'
    rc: str = 'VBR'
    quality: int = 23
    bitrate: float = 15
    maxrate: float = 20
    bufsize: float = 30
    bframes: int = 2


def mode_candidates(name, vendor, help_options):
    """Candidates only; callers MUST execute them before adding them to the matrix."""
    allowed = set(help_options)
    rates = ['-b:v', '{bitrate}M', '-maxrate', '{maxrate}M', '-bufsize', '{bufsize}M']
    modes = {'VBR': rates, 'CBR': ['-b:v', '{bitrate}M', '-minrate', '{bitrate}M',
                                  '-maxrate', '{bitrate}M', '-bufsize', '{bufsize}M']}
    if vendor == 'NVIDIA':
        if 'rc' in allowed:
            modes = {k: ['-rc', 'vbr' if k == 'VBR' else 'cbr'] + v for k, v in modes.items()}
        if {'rc', 'cq'} <= allowed:
            modes['CQ'] = ['-rc', 'vbr', '-cq', '{quality}'] + rates
    elif vendor == 'AMD':
        if 'rc' in allowed:
            modes = {k: ['-rc', 'vbr_peak' if k == 'VBR' else 'cbr'] + v for k, v in modes.items()}
        if {'rc', 'qp_i', 'qp_p'} <= allowed:
            modes['CQ'] = ['-rc', 'cqp', '-qp_i', '{quality}', '-qp_p', '{quality}']
    elif vendor == 'Intel':
        # global_quality is an AVCodecContext option, not a private help option.
        modes['ICQ'] = ['-global_quality', '{quality}'] + (['-look_ahead', '0'] if 'look_ahead' in allowed else [])
    elif vendor == 'CPU':
        if 'crf' in allowed:
            modes['CQ'] = ['-crf', '{quality}'] + ['-maxrate', '{maxrate}M', '-bufsize', '{bufsize}M']
        elif 'qp' in allowed:
            modes['CQ'] = ['-qp', '{quality}']
    return modes


def render(flags, model):
    values = dict(quality=model.quality, bitrate=model.bitrate, maxrate=model.maxrate, bufsize=model.bufsize)
    return [str(v).format(**values) for v in flags]


def mapped_flags(cap, model, advanced=None):
    """Return flags + visible substitutions. Unknown/failed flags are never emitted."""
    notes = []
    wanted = model.rc
    if wanted in ('Quality', 'CQP', 'CRF'):
        wanted = 'ICQ' if cap.vendor == 'Intel' else 'CQ'
    if wanted == 'ICQ' and cap.vendor != 'Intel':
        wanted = 'CQ'
    if wanted not in cap.modes:
        notes.append(f'{wanted} was not proven for {cap.name}; using tested VBR')
        wanted = 'VBR'
    if wanted not in cap.modes:
        raise ValueError(f'{cap.name}: no tested rate-control mode')
    flags = render(cap.modes[wanted], model)
    # Preset preference is translated, never copied between vendors.
    index = SPEEDS.index(model.speed) if model.speed in SPEEDS else 1
    if cap.vendor == 'NVIDIA':
        preferred, key = ('p1', 'p4', 'p6', 'p7')[index], 'preset'
    elif cap.vendor == 'AMD':
        preferred, key = ('speed', 'balanced', 'quality', 'quality')[index], 'quality'
    elif cap.name == 'libsvtav1':
        preferred, key = ('12', '10', '8', '6')[index], 'preset'
    else:
        preferred, key = ('veryfast', 'medium', 'slow', 'veryslow')[index], 'preset'
    tested = cap.tested_values.get(key, []) if key in cap.help_options else []
    if preferred in tested:
        flags += ['-' + key, preferred]
    elif tested:
        flags += ['-' + key, tested[0]]
        notes.append(f'{preferred} preset not proven; using {tested[0]}')
    # B-frames are global options but still require a successful hardware test.
    bvalues = cap.tested_values.get('bf', [])
    if str(model.bframes) in bvalues:
        flags += ['-bf', str(model.bframes)]
    elif model.bframes:
        notes.append(f'{model.bframes} B-frames not proven; using tested default')
    for key, value in (advanced or {}).items():
        if key == 'low_power' and cap.features.get('haswell'):
            notes.append('Haswell: low_power is forbidden')
            continue
        if wanted == 'ICQ' and key in ('look_ahead', 'look_ahead_depth'):
            notes.append('ICQ keeps lookahead disabled')
            continue
        if str(value) in cap.tested_values.get(key, []) and key in cap.help_options:
            flags += ['-' + key, str(value)]
        else:
            notes.append(f'{key}={value} not proven; omitted')
    return flags, notes, wanted
