"""Apply a shared enhancement plan to an existing video/audio pipeline, once."""
from dataclasses import replace
from .model import enabled
from .planning import headroom, plan_enhancement


def split_filters(text):
    result, start, depth, quote, escaped = [], 0, 0, '', False
    for i, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == '\\':
            escaped = True
        elif quote:
            if char == quote:
                quote = ''
        elif char in ("'", '"'):
            quote = char
        elif char in '([':
            depth += 1
        elif char in ')]':
            depth -= 1
        elif char == ',' and depth == 0:
            result.append(text[start:i]); start = i + 1
    result.append(text[start:])
    return result


def apply_chain(base, enhancement):
    chain = split_filters(base)
    if enhancement.gpu_full:
        # Add a separate, tested VPP stage to avoid inventing a merged option combination.
        at = next(i for i, f in enumerate(chain) if f.startswith(('vpp_qsv=', 'scale_cuda=', 'scale_npp=')))
        chain.insert(at, enhancement.gpu)
        return ','.join(chain + enhancement.tags)
    if enhancement.gpu:
        at = next((i for i, f in enumerate(chain) if f == 'hwdownload'), None)
        if at is None:
            raise ValueError('GPU enhancement requires the proven hardware frame path')
        chain.insert(at, enhancement.gpu)
    at = next((i for i, f in enumerate(chain) if f.startswith('scale=')), None)
    if at is None:
        raise ValueError('CPU enhancement requires a CPU scale/filter path')
    chain[at:at] = enhancement.pre
    at += len(enhancement.pre)
    chain[at] = chain[at].replace('flags=bicubic', 'flags=' + enhancement.scaler)
    # Last software format precedes hwupload, so quantization/dither happen on CPU before upload.
    end = next((i for i in range(len(chain)-1, -1, -1) if chain[i] in ('format=yuv420p', 'format=nv12')), len(chain))
    chain[end:end] = enhancement.post + enhancement.tags
    return ','.join(chain)


def build_enhanced_command(media, options, output, caps, software, executable, selection, base_builder):
    if not enabled(options):
        # Validate HDR→SDR even with Off: never silently retag HDR as SDR.
        from ..tv_rules import choose_target
        plan_enhancement(media, options, choose_target(media, options), caps, selection)
        return base_builder(media, options, output, caps, software, executable, selection)
    prepared, headroom_notes = headroom(media, options)
    from ..tv_rules import choose_target
    target = choose_target(media, prepared)
    if caps.get('matrix'):
        from ..hardware.matrix import HardwareMatrix
        from ..hardware.planning import request_for
        from ..hardware.select import choose_pipeline
        selection = selection or choose_pipeline(HardwareMatrix.from_dict(caps['matrix']), request_for(media, prepared, software))
    enhancement = plan_enhancement(media, prepared, target, caps, selection)
    if selection and not enhancement.gpu_full:
        selection = replace(selection, gpu_filters=False, reasons=list(selection.reasons))
    # Legacy QSV clients lack per-device enhancement evidence: request CPU filter path explicitly.
    legacy_caps = caps
    if not caps.get('matrix'):
        legacy_caps = dict(caps, vpp_qsv=False, vpp_framerate=False)
    plan = base_builder(media, prepared, output, legacy_caps, software, executable, selection)
    c = plan.command
    index = c.index('-vf') + 1
    c[index] = apply_chain(c[index], enhancement)
    if enhancement.audio and media.audio:
        for i, token in enumerate(c):
            if token == '-af' or token.startswith('-filter:a:'):
                # After timestamp repair but before resampling/padding (loudnorm changes sample rate).
                parts = c[i+1].split(',')
                position = next((j for j, f in enumerate(parts) if f.startswith('aresample=')), len(parts))
                parts[position:position] = enhancement.audio
                c[i+1] = ','.join(parts)
    if enhancement.audio and not media.audio:
        enhancement.notes.append('Audio enhancement skipped: source has no audio (silent TV track retained)')
    # Explicit tags and chroma location match the actual conversion / TV policy.
    if enhancement.chroma_location:
        c[-1:-1] = ['-chroma_sample_location', enhancement.chroma_location]
    plan.enhancement = dict(level=options.enhance.get('level', 'Off'), video_chain=c[index],
                            audio_filters=enhancement.audio, notes=headroom_notes + enhancement.notes,
                            applied=enhancement.applied, estimated_speed=enhancement.estimated_speed,
                            processing_depth=enhancement.processing_depth)
    plan.encoding_status += ' · Enhance ' + plan.enhancement['level'] + ' · ' + '; '.join(headroom_notes + enhancement.notes)
    return plan
