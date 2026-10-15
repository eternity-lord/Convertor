"""Integrate hardware choices with the existing verified TV timing/audio/mux policy."""
from dataclasses import replace
from .matrix import HardwareMatrix
from .select import Request, choose_pipeline, codec_of
from .flagmap import QualityModel, mapped_flags
from ..tv_rules import choose_target


def request_for(media, options, cpu=False):
    from ..enhance.model import enabled
    if enabled(options):
        from ..enhance.planning import headroom
        options, _ = headroom(media, options)
    target = choose_target(media, options)
    return Request(codec=codec_of(options.codec), width=target.width, height=target.height,
                   fps=float(target.fps), rate_control=options.rc, device='CPU' if cpu else ('Auto' if options.hardware == 'Global' else options.hardware),
                   source_codec=media.video.get('codec_name', ''), source_format=media.video.get('pix_fmt', ''),
                   source_width=int(media.video.get('width', 0)), source_height=int(media.video.get('height', 0)),
                   source_fps=float(media.fps), gpu_filter_eligible=(options.method == 'Drop / Duplicate'
                   and not media.interlaced and media.metadata_verified))


def build_hardware_command(media, options, output, caps, software, executable, legacy_builder, selection=None):
    matrix = HardwareMatrix.from_dict(caps['matrix'])
    selection = selection or choose_pipeline(matrix, request_for(media, options, software))
    cap = selection.encoder
    if cap.codec not in ('h264', 'hevc', 'av1'):
        raise ValueError('The TV/HQX MP4 workflow supports H.264, HEVC and AV1 only. Other probed codecs are inventoried for the later Universal Converter phase.')
    # Keep the mature TV CFR, AAC, timestamps, atomic-output and verification engine.
    base = legacy_builder(media, options, output, {}, software=True, executable=executable)
    target = choose_target(media, options)
    if cap.codec != 'h264':
        target = replace(target, profile='main', level='5.1' if cap.codec == 'hevc' else 'Auto')
    cmd = base.command
    # Discard vendor-specific legacy controls; the mapping layer owns these now.
    remove = {'-preset', '-crf', '-global_quality', '-b:v', '-maxrate', '-minrate', '-bufsize',
              '-bf', '-refs', '-x264-params', '-pix_fmt', '-flags', '-profile:v', '-level:v'}
    cleaned, i = [], 0
    while i < len(cmd):
        if cmd[i] in remove:
            i += 2
        else:
            cleaned.append(cmd[i])
            i += 1
    cmd = cleaned
    cmd[cmd.index('-c:v') + 1] = cap.name
    cmd[cmd.index('-tag:v') + 1] = {'h264': 'avc1', 'hevc': 'hvc1', 'av1': 'av01'}[cap.codec]
    source_index = cmd.index('-i')
    before_input = list(cap.device_args)
    if selection.decoder:
        before_input += selection.decoder['args']
    cmd[source_index:source_index] = before_input
    filters = cmd[cmd.index('-vf') + 1]
    if selection.gpu_filters:
        if cap.vendor == 'Intel':
            filters = f'setpts=PTS-STARTPTS,vpp_qsv=w={target.width}:h={target.height}:format=nv12:framerate={target.fps},setsar=1'
        elif cap.vendor == 'NVIDIA':
            filters = f'setpts=PTS-STARTPTS,scale_cuda=w={target.width}:h={target.height}:format=nv12,fps=fps={target.fps}:round=near,setsar=1'
    else:
        if selection.decoder:
            filters = 'hwdownload,format=nv12,' + filters
        if cap.hardware:
            filters = filters.removesuffix('format=yuv420p') + 'format=nv12'
            if cap.vendor in ('Intel', 'NVIDIA'):
                filters += ',hwupload=extra_hw_frames=16'
    cmd[cmd.index('-vf') + 1] = filters
    speed = getattr(options, 'quality_speed', 'Balanced')
    model = QualityModel(speed=speed, rc=options.rc, quality=options.quality, bitrate=target.bitrate,
                         maxrate=target.maxrate, bufsize=target.bufsize,
                         bframes=0 if target.profile == 'baseline' else options.bframes)
    mapped, notes, rc = mapped_flags(cap, model, options.advanced)
    # Private profile/level controls must be advertised. Final job preflight tests their combination.
    flags = list(cap.encoder_args) + mapped
    if 'profile' in cap.help_options:
        flags += ['-profile:v', target.profile]
    if 'level' in cap.help_options and cap.codec != 'av1':
        flags += ['-level:v', target.level]
    if cap.vendor == 'CPU' and cap.name == 'libx264' and 'x264-params' in cap.help_options:
        flags += ['-x264-params', 'open-gop=0:force-cfr=1']
    if media.interlaced and not options.deinterlace:
        flags += ['-flags', '+cgop+ilme+ildct']
    cmd[-1:-1] = flags
    status = ('Using ' if cap.hardware else '') + selection.description + ' · ' + rc
    if cap.vendor == 'AMD':
        status += ' · NV12 CPU frames (AMF internal upload)'
    if (target.width, target.height) != (int(media.video['width']), int(media.video['height'])) or target.fps != media.fps:
        status += f' · TV target: {target.width}×{target.height}@{target.fps} (source {media.video["width"]}×{media.video["height"]}@{media.fps})'
    if notes:
        status += ' · ' + '; '.join(notes)
    base.command, base.target, base.codec = cmd, target, cap.codec
    base.pipeline = 'GPU full' if selection.gpu_filters else 'Hybrid' if cap.hardware else 'CPU fallback'
    base.encoding_status = status
    base.encoder_id, base.gpu_id = cap.id, cap.gpu_id
    return base


def preview_from_selection(media, selection, timestamp, output, executable):
    """Only the proven same-device decoder; JPEG preview remains software rendered."""
    cap, decoder = selection.encoder, selection.decoder
    cmd = [executable, '-nostdin', '-v', 'warning', '-y', '-threads', '1']
    if decoder:
        cmd += cap.device_args + decoder['args']
    cmd += ['-ss', str(max(0, timestamp)), '-i', str(media.path), '-frames:v', '1', '-vf',
            ('hwdownload,format=nv12,' if decoder else '') + 'scale=480:-2', '-threads', '1', str(output)]
    return cmd, decoder is not None
