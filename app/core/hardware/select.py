"""Pure policy. No OS queries, subprocesses or UI state."""
from dataclasses import dataclass, field
from .matrix import HardwareMatrix


@dataclass(frozen=True)
class Request:
    codec: str = 'h264'
    width: int = 1920
    height: int = 1080
    fps: float = 30
    bit_depth: int = 8
    chroma: str = '420'
    device: str = 'Auto'
    rate_control: str = 'VBR'
    source_codec: str = 'h264'
    source_format: str = 'yuv420p'
    source_width: int = 1920
    source_height: int = 1080
    source_fps: float = 30
    gpu_filter_eligible: bool = True


@dataclass
class Selection:
    encoder: object
    decoder: dict | None = None
    gpu_filters: bool = False
    reasons: list = field(default_factory=list)

    @property
    def description(self):
        cap = self.encoder
        if cap.vendor == 'CPU':
            text = 'CPU ' + cap.name
        else:
            backend = next((v for v in cap.device_args if 'child_device_type=' in v), '')
            backend = ' (' + backend.split('child_device_type=')[1].split(',')[0] + ')' if backend else ''
            text = f'{cap.vendor} {cap.name}{backend} + ' + (self.decoder['backend'] + ' decode' if self.decoder else 'CPU decode')
            text += ' + GPU filters' if self.gpu_filters else ' + CPU filters'
        if self.reasons:
            text += ' · ' + '; '.join(self.reasons)
        return text


def codec_of(value):
    from .matrix import CODECS
    return CODECS.get(value, value.removesuffix('_qsv'))


def sample_matches(sample, request):
    return (sample.get('bit_depth') == request.bit_depth and sample.get('chroma') == request.chroma
            and sample.get('width', 0) >= request.width and sample.get('height', 0) >= request.height
            and sample.get('fps', 0) >= request.fps - .001)


def rank_encoders(matrix, request, excluded=()):
    """Best measured encoder for target codec -> other GPU -> tested CPU. No codec loss."""
    if isinstance(matrix, dict):
        matrix = HardwareMatrix.from_dict(matrix)
    rejected, valid = [], []
    for cap in matrix.encoders:
        if cap.codec != request.codec or cap.id in excluded:
            continue
        reason = ''
        if not cap.available:
            reason = cap.reason[:240]
        elif not cap.modes:
            reason = 'No tested rate-control mode'
        elif ('ICQ' if request.rate_control in ('Quality', 'CQP', 'CRF', 'ICQ') and cap.vendor == 'Intel' else
              'CQ' if request.rate_control in ('Quality', 'CQP', 'CRF', 'ICQ') else request.rate_control) not in cap.modes and 'VBR' not in cap.modes:
            reason = 'Requested rate control and VBR fallback are unproven'
        elif cap.hardware and not any(sample_matches(s, request) for s in cap.samples):
            reason = f'{request.width}×{request.height} / {request.fps:g} fps / {request.bit_depth}-bit {request.chroma} not proven'
        elif not cap.hardware and not any(s.get('bit_depth') == request.bit_depth and s.get('chroma') == request.chroma for s in cap.samples):
            reason = 'Output bit depth / chroma not proven'
        if cap.features.get('haswell') and (request.codec != 'h264' or request.bit_depth != 8 or request.chroma != '420'):
            reason = 'Haswell: H.264 8-bit 4:2:0 only'
        if reason:
            rejected.append(f'{cap.name}: {reason}')
            continue
        preferred = request.device in ('Auto', cap.gpu_id, cap.vendor) or (request.device == 'CPU' and not cap.hardware)
        if request.device == 'CPU' and cap.hardware:
            continue
        valid.append((0 if preferred else 1, 0 if cap.hardware else 1, -(cap.speed or 0), cap.id, cap))
    valid.sort(key=lambda v: v[:4])
    return [v[-1] for v in valid], rejected


def choose_pipeline(matrix, request, excluded=()):
    ranked, rejected = rank_encoders(matrix, request, excluded)
    if not ranked:
        raise ValueError('No tested encoder can preserve the selected target. ' + '; '.join(rejected))
    cap = ranked[0]
    notes = []
    if request.device not in ('Auto', cap.vendor, cap.gpu_id) and not (request.device == 'CPU' and not cap.hardware):
        notes.append(f'{request.device} unavailable for target; using {cap.vendor}')
    if not cap.hardware and request.device != 'CPU':
        notes.append('CPU fallback: ' + ('; '.join(rejected) if rejected else 'No remaining tested hardware encoder'))
    # High-bit-depth / 4:2:2 / editing intermediates intentionally stay CPU decoded.
    decoder = None
    safe_source = request.source_format in ('yuv420p', 'nv12') and request.source_codec in ('h264', 'hevc', 'vp9', 'av1', 'mpeg2video')
    if cap.hardware and safe_source:
        for dec in cap.decoders:
            if (dec.get('available') and dec['codec'] == request.source_codec and dec['bit_depth'] == 8
                    and dec.get('width', 0) >= request.source_width and dec.get('height', 0) >= request.source_height
                    and dec.get('fps', 0) >= request.source_fps - .001 and dec.get('same_device')):
                decoder = dec
                break
    if cap.hardware and not decoder:
        notes.append('GPU decode unproven or source requires CPU decode; explicit NV12 upload')
    use_filters = bool(decoder and request.gpu_filter_eligible and cap.gpu_filters.get('available')
                       and cap.gpu_filters.get('width', 0) >= request.width
                       and cap.gpu_filters.get('height', 0) >= request.height
                       and cap.gpu_filters.get('fps', 0) >= request.fps - .001)
    if decoder and not use_filters:
        notes.append('GPU filter chain not proven / unsuitable; download NV12 for CPU filters')
    return Selection(cap, decoder, use_filters, notes)


def distribute_devices(matrix, codec, active_counts, safe_mode=True):
    """A device slot is shared by all its codecs. Safe mode: max one job per device."""
    slots = {c.gpu_id for c in matrix.encoders if c.hardware and c.available and c.codec == codec}
    limit = 1 if safe_mode else 3
    choices = [g for g in sorted(slots) if active_counts.get(g, 0) < limit]
    return min(choices, key=lambda g: (active_counts.get(g, 0), g)) if choices else None
