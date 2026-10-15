"""Pure enhancement decisions. Listings alone never enable a filter."""
import math
from dataclasses import dataclass, field, replace
from .model import effective


@dataclass
class EnhancementPlan:
    active: bool = False
    pre: list = field(default_factory=list)
    post: list = field(default_factory=list)
    audio: list = field(default_factory=list)
    gpu: str = ''
    tags: list = field(default_factory=list)
    chroma_location: str = ''
    gpu_full: bool = False
    scaler: str = 'bicubic'
    notes: list = field(default_factory=list)
    applied: list = field(default_factory=list)
    estimated_speed: float | None = None
    processing_depth: int = 8


def is_hdr(media):
    return media.video.get('color_transfer') in ('smpte2084', 'arib-std-b67')


def source_bitrate(media):
    try:
        # Container bitrate includes audio/overhead and is not a video bitrate measurement.
        value = float(media.video.get('bit_rate') or 0) / 1e6
        return value if math.isfinite(value) and value > 0 else None
    except (ValueError, TypeError):
        return None


def headroom(media, options):
    """Raise headroom, never silently cap Excellent below measured source bitrate."""
    settings = effective(getattr(options, 'enhance', None))
    if settings.level == 'Off':
        return options, []
    from ..tv_rules import choose_target
    target = choose_target(media, options)
    factor = {'Low': 1.05, 'Balanced': 1.15, 'Excellent': 1.3, 'Custom': 1.15}[settings.level]
    requested = max(options.bitrate, target.bitrate * factor)
    source = source_bitrate(media)
    notes = []
    if settings.level == 'Excellent':
        if source is None:
            notes.append('Source video bitrate unknown; cannot prove an Excellent bitrate floor')
        elif source > target.maxrate + .0001:
            raise ValueError(f'Excellent needs at least source video bitrate {source:.2f} Mbps, but TV/output cap is '
                             f'{target.maxrate:.2f} Mbps. Raise the explicit cap / TV preset or choose Balanced. No silent reduction.')
        else:
            requested = max(requested, source)
    requested = min(requested, target.maxrate)
    quality = max(1, options.quality - {'Low': 1, 'Balanced': 2, 'Excellent': 3, 'Custom': 2}[settings.level])
    speed = options.quality_speed
    if settings.level == 'Excellent':
        speed = 'Max Quality'
    elif settings.level in ('Balanced', 'Custom') and speed in ('Global', 'Fast', 'Balanced'):
        speed = 'High Quality'
    if settings.level == 'Excellent' and options.rc in ('Quality', 'CQ', 'ICQ', 'CRF', 'CQP'):
        # A constant-quality mode has no minimum bitrate guarantee.
        notes.append('Excellent uses explicit VBR to retain the measured source bitrate budget (actual encoded rate is verified separately); CQ has no bitrate floor')
        rc = 'VBR'
    else:
        rc = options.rc
    notes.append(f'Headroom: {requested:.3f} Mbps budget (max {target.maxrate:.3f}), quality {quality}, {speed}')
    return replace(options, bitrate=requested, quality=quality, quality_speed=speed, rc=rc), notes


def filter_evidence(caps):
    if not caps:
        return {}
    return caps.get('enhancement') or caps.get('matrix', {}).get('inventory', {}).get('enhancement', {})


def estimate_speed(records, target, encoder_speed=None):
    costs = []
    if encoder_speed and encoder_speed > 0:
        costs.append(1 / encoder_speed)
    for record in records:
        speed = record.get('speed')
        if speed and speed > 0:
            adjusted = speed * record.get('width', 320) * record.get('height', 180) * record.get('fps', 30)
            adjusted /= target.width * target.height * float(target.fps)
            costs.append(1 / adjusted)
        else:
            return None  # Never invent a filter performance measurement.
    return 1 / sum(costs) if costs else None


def plan_enhancement(media, options, target, caps=None, selection=None):
    settings = effective(getattr(options, 'enhance', None))
    plan = EnhancementPlan(active=settings.level != 'Off')
    if not plan.active:
        if is_hdr(media):
            raise ValueError('HDR source requires an explicit HDR→SDR conversion for TV output. Enable Enhance Quality with Auto color conversion.')
        return plan
    if not media.metadata_verified:
        raise ValueError('Enhancement needs verified source video metadata for safe color/depth processing. Analyze again or select Off.')
    if media.interlaced and not options.deinterlace:
        raise ValueError('Enhancement of interlaced input needs explicit deinterlacing. Enable Deinterlace or choose Off; fields are never silently blended.')
    evidence = filter_evidence(caps)
    cap = selection.encoder if selection else None
    measured = []

    def use(key, required=False):
        proof = evidence.get(key, {})
        if proof.get('available'):
            measured.append(proof)
            return True
        reason = proof.get('reason', 'No successful runtime probe; re-scan hardware')
        if required:
            raise ValueError(f'Enhance requires {key}: {reason}. No unsafe color/bit-depth substitution was made.')
        plan.notes.append(f'{key} unavailable: {reason}; effect omitted')
        return False

    hdr = is_hdr(media)
    source_space = ('gbr' if media.video.get('pix_fmt', '').startswith(('rgb', 'bgr', 'gbr')) else media.video.get('color_space'))
    source_range = settings.input_range if settings.input_range != 'Auto' else media.video.get('color_range')
    source_range = {'tv': 'limited', 'pc': 'full'}.get(source_range, source_range)
    if source_space == 'gbr' and source_range not in ('limited', 'full'):
        source_range = 'full'
        plan.notes.append('Untagged RGB input: assuming full range before real RGB→YUV conversion')
    target_space = 'bt709' if target.height > 576 else 'smpte170m'
    requested_space = {'bt601': 'smpte170m'}.get(settings.color, settings.color)
    if settings.color != 'Auto' and requested_space != target_space:
        raise ValueError(f'TV output at this resolution uses {target_space}. Choose Auto color; arbitrary retagging is not conversion.')
    source_primaries = media.video.get('color_primaries')
    source_transfer = media.video.get('color_transfer')
    known_primaries = ('bt709','smpte170m','bt470bg','bt2020')
    known_transfers = ('bt709','smpte170m','bt2020-10','bt2020-12','gamma22','gamma28','iec61966-2-1')
    for name, value, supported in (('matrix',source_space,('gbr','bt709','smpte170m','bt470bg','bt2020nc','bt2020c')),
                                    ('primaries',source_primaries,known_primaries),
                                    ('transfer',source_transfer,known_transfers+('smpte2084','arib-std-b67'))):
        if value not in (None,'','unknown','unspecified','reserved') and value not in supported:
            raise ValueError(f'Enhancement color {name} {value} is not supported by a verified recipe; no silent color substitution was made')
    needs_conversion = (source_primaries in known_primaries and source_primaries != target_space) or (
        source_transfer in known_transfers and source_transfer != target_space) or hdr or source_space == 'gbr' or source_range == 'full' or source_space in ('bt2020nc', 'bt2020c') or (
        source_space in ('bt709', 'smpte170m', 'bt470bg') and source_space != target_space)
    gpu_key = settings.level.lower()
    gpu_proof = cap.features.get('enhance', {}).get(gpu_key, {}) if cap else {}
    # Only whole tested recipes are eligible for GPU execution. Custom arbitrary strengths stay on CPU.
    high_source = any(s in media.video.get('pix_fmt', '') for s in ('9', '10', '12', '14', '16'))
    use_gpu = bool(selection and selection.decoder and gpu_proof.get('available') and
                   settings.level in ('Low', 'Balanced', 'Excellent') and not needs_conversion and not settings.processing_10bit and not media.interlaced and not high_source)
    if use_gpu:
        plan.gpu = gpu_proof['filter']
        measured.append(gpu_proof)
        plan.notes.append('Using proven GPU denoise/detail recipe: ' + plan.gpu)
    elif cap and cap.hardware:
        plan.notes.append('Enhancement GPU chain unproven / precision-color work required: using CPU enhancement filters; hardware encode retained')

    if hdr:
        use('hdr_tonemap', required=True)
        # Input HDR transfer/primaries/range are passed explicitly, not replaced by SDR tags.
        transfer = media.video['color_transfer']
        hdr_matrix = source_space if source_space in ('bt709','smpte170m','bt470bg','bt2020nc','bt2020c','gbr') else 'bt2020nc'
        hdr_primaries = source_primaries if source_primaries in known_primaries else 'bt2020'
        plan.notes.append(f'HDR input color: {hdr_primaries}/{hdr_matrix}; missing HDR primaries/matrix inferred as BT.2020')
        plan.pre += [f'zscale=transferin={transfer}:primariesin={hdr_primaries}:matrixin={hdr_matrix}:rangein={source_range or "limited"}:transfer=linear:primaries=bt2020:matrix=gbr:range=full:npl=100',
                     'format=gbrpf32le', 'tonemap=tonemap=mobius:param=0.3:desat=2',
                     f'zscale=matrixin=gbr:transferin=linear:primariesin=bt2020:rangein=full:primaries={target_space}:transfer={target_space}:matrix={target_space}:range=limited:dither=error_diffusion',
                     'sidedata=mode=delete:type=MASTERING_DISPLAY_METADATA', 'sidedata=mode=delete:type=CONTENT_LIGHT_LEVEL',
                     'sidedata=mode=delete:type=DYNAMIC_HDR_PLUS', 'format=yuv420p10le']
        plan.processing_depth = 10
        plan.notes.append('HDR→SDR: CPU float tonemap (100-nit reference); HDR metadata will be removed')
    elif needs_conversion:
        use('color_convert', required=True)
        # Missing SDR matrix is a visible inference, never an unannounced HDR assumption.
        source_space = source_space if source_space in ('bt709', 'smpte170m', 'bt470bg', 'bt2020nc', 'bt2020c', 'gbr') else ('bt709' if int(media.video['height']) > 576 else 'smpte170m')
        primaries = media.video.get('color_primaries')
        fallback_primaries = ('bt2020' if source_space in ('bt2020nc','bt2020c') else
                              ('bt709' if int(media.video['height']) > 576 else 'smpte170m') if source_space == 'gbr' else source_space)
        primaries = primaries if primaries in known_primaries else fallback_primaries
        transfer = media.video.get('color_transfer')
        transfer = transfer if transfer in known_transfers else ('bt709' if int(media.video['height']) > 576 else 'smpte170m')
        transfer = {'gamma22':'bt470m','gamma28':'bt470bg'}.get(transfer,transfer)
        plan.pre.append(f'zscale=matrixin={source_space}:primariesin={primaries}:transferin={transfer}:rangein={source_range or "limited"}:'
                        f'matrix={target_space}:primaries={target_space}:transfer={target_space}:range=limited')
        plan.notes.append(f'Color conversion: {source_space}/{source_range or "assumed limited"} → {target_space}/limited')
    elif source_range not in ('limited', 'full') or source_space not in ('bt709', 'smpte170m', 'bt470bg'):
        plan.notes.append(f'SDR color metadata incomplete: assuming {target_space}/limited; use Custom input-range override if needed')

    wants_10 = settings.processing_10bit or hdr or high_source
    if wants_10 and use('dither', required=hdr or high_source):
        plan.processing_depth = 10
        if not hdr:
            if any(depth in media.video.get('pix_fmt', '') for depth in ('12','14','16')):
                plan.pre.append('zscale=dither=error_diffusion:range=limited')
            plan.pre.append('format=yuv420p10le')
    if settings.denoise and not use_gpu:
        heavy = settings.denoiser == 'nlmeans' or (settings.level == 'Excellent' and settings.denoiser == 'Auto')
        projection = estimate_speed([evidence.get('nlmeans', {})], target)
        weak = options.safe_mode or (cap and cap.features.get('haswell')) or projection is None or projection < .5
        if heavy and weak:
            plan.notes.append('nlmeans skipped: Safe mode / weak hardware / projected <0.5× or unmeasured; using lighter hqdn3d')
        if heavy and not weak and use('nlmeans'):
            plan.pre.append(f'nlmeans=s={min(settings.denoise, 3):.3f}:p=7:r=9')
        elif use('hqdn3d'):
            s = settings.denoise
            plan.pre.append(f'hqdn3d={s:.3f}:{s*.75:.3f}:{s*1.5:.3f}:{s*1.125:.3f}')
    if settings.deband and use('deband'):
        plan.post.append(f'deband=1thr={settings.deband:.4f}:2thr={settings.deband:.4f}:3thr={settings.deband:.4f}:4thr={settings.deband:.4f}:range=12:blur=1')
    if settings.sharpen and not (use_gpu and gpu_proof.get('covers_sharpen', True)) and use('unsharp'):
        plan.post.append(f'unsharp=5:5:{settings.sharpen:.3f}:5:5:0')  # bounded luma only; no chroma halos
    if plan.processing_depth == 10:
        use('dither', required=True)
        plan.post += ['zscale=dither=error_diffusion:range=limited:chromal=left', 'format=yuv420p']
        plan.chroma_location = 'left'
    if (settings.gamma, settings.contrast, settings.saturation) != (1, 1, 1) and use('eq'):
        plan.post.append(f'eq=contrast={settings.contrast:.5f}:gamma={settings.gamma:.5f}:saturation={settings.saturation:.5f}:brightness=0')
    if use('scale_' + settings.scaler):
        plan.scaler = settings.scaler
    else:
        plan.notes.append('Using existing bicubic scaler; requested scaler was not proven')
    if settings.loudnorm and use('loudnorm'):
        plan.audio.append('loudnorm=I=-16:TP=-1.5:LRA=11')
        plan.notes.append('Audio loudnorm: one-pass EBU R128 target; not a measured two-pass guarantee')
    if settings.audio_cleanup:
        for key, expr in [('highpass', 'highpass=f=70'), ('lowpass', 'lowpass=f=16000'), ('afftdn', 'afftdn=nf=-25')]:
            if use(key):
                plan.audio.append(expr)
    if plan.processing_depth != 10 and not use_gpu and use('chroma'):
        plan.post += ['zscale=chromal=left']
        plan.chroma_location = 'left'
    use('color_tags', required=True)
    plan.tags = [f'setparams=range=limited:color_primaries={target_space}:color_trc={target_space}:colorspace={target_space}']
    # Existing GPU scale/VPP is retained only when no CPU-only spatial/color work is required.
    plan.gpu_full = bool(use_gpu and not plan.pre and not plan.post and selection.gpu_filters)
    if plan.gpu_full:
        plan.scaler = 'tested GPU scaler default'
        plan.notes.append('Hardware VPP scaling retained; no unsupported Lanczos claim for the GPU scaler')
    plan.applied = plan.pre + ([plan.gpu] if plan.gpu else []) + plan.post + plan.tags + plan.audio
    plan.notes.append(f'Processing {plan.processing_depth}-bit; TV output remains 8-bit 4:2:0 / limited range; scaler={plan.scaler}')
    encoder_speed = (cap.speed * 1920 * 1080 * 60 / (target.width * target.height * float(target.fps))
                     if cap and cap.hardware and cap.speed else None)
    plan.estimated_speed = estimate_speed(measured, target, encoder_speed)
    plan.notes.append('Estimate excludes disk/seek, changed encoder presets, external AI, and unbenchmarked CPU encode; it is not a full-job speed guarantee')
    if plan.estimated_speed is None:
        plan.notes.append('Speed estimate unknown: complete benchmark evidence is unavailable')
    else:
        plan.notes.append(f'Rough measured-filter/baseline-hardware estimate: {plan.estimated_speed:.2f}× realtime (synthetic pixel-cost model, not a guarantee)')
        if plan.estimated_speed < .5:
            plan.notes.append('Warning: enhancement may be very slow on this source / hardware')
    return plan
