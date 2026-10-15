"""Build explicit, auditable commands. Hardware filters are gated by capability results."""
import math
from dataclasses import dataclass
from pathlib import Path
from .process import tool
from .safety import encoding_threads
from .qsv import device_args, is_haswell, can_decode
from .tv_rules import choose_target, timescale


@dataclass
class Plan:
    command: list
    target: object
    pipeline: str
    duration: float
    codec: str = 'h264'
    encoding_status: str = ''


def build_command(media, options, output, capabilities=None, software=False, executable=None):
    caps = capabilities or {}
    target = choose_target(media, options)
    fps = str(target.fps)
    codec = options.codec
    supported = caps.get('codecs', {}).get(codec, {})
    gpu = not software and codec in ('h264_qsv', 'hevc_qsv', 'av1_qsv') and supported.get('available', False)
    if is_haswell(caps) and codec != 'h264_qsv':
        gpu = False
    decode = gpu and can_decode(media, caps)
    output_codec = codec.removesuffix('_qsv') if gpu else 'h264'
    if output_codec != 'h264':
        from dataclasses import replace
        target = replace(target, profile='main', level='5.1' if output_codec == 'hevc' else 'Auto')
    full = (decode and caps.get('vpp_qsv')
            and options.method == 'Drop / Duplicate'
            and not (media.interlaced and options.double_rate))
    gpu_fps = bool(caps.get('vpp_framerate'))
    if full and not gpu_fps:
        full = False
    cmd = [executable or tool('ffmpeg'), '-hide_banner', '-nostdin', '-y',
           '-loglevel', 'warning', '-progress', 'pipe:1', '-nostats',
           '-fflags', '+genpts', '-threads', str(encoding_threads(options.safe_mode)), '-filter_threads', '2']
    if gpu:
        cmd += device_args(caps)
    if decode:
        cmd += ['-hwaccel', 'qsv', '-hwaccel_device', 'hw', '-hwaccel_output_format', 'qsv',
                '-extra_hw_frames', '16' if options.safe_mode else '64']
    cmd += ['-i', str(media.path)]
    if not media.audio:
        cmd += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
    cmd += ['-map', '0:v:0', '-map', ('0:a?' if options.audio == 'Keep all' else '0:a:0?')
            if media.audio else '1:a:0', '-sn', '-dn', '-map_metadata', '-1', '-map_chapters', '-1']
    keep_interlace = media.interlaced and not options.deinterlace
    top_first = media.video.get('field_order') in ('tt', 'tb')
    filters = ['setpts=PTS-STARTPTS']
    if full:
        vpp = f'vpp_qsv=w={target.width}:h={target.height}:format=nv12:framerate={fps}'
        if media.interlaced and options.deinterlace:
            # CPU bob is used for explicit double-rate deinterlacing for predictable cadence.
            if options.double_rate:
                full = False
            else:
                vpp += ':deinterlace=1'
        filters.append(vpp)
    if not full:
        filters = (['hwdownload', 'format=nv12'] if decode else []) + ['setpts=PTS-STARTPTS']
        if media.interlaced and options.deinterlace:
            filters += ['bwdif=mode=' + ('send_field' if options.double_rate else 'send_frame') + ':parity=auto:deint=all']
        scaling = f'scale={target.width}:{target.height}:flags=bicubic' + (':interl=1' if keep_interlace else '')
        if not media.metadata_verified:
            scaling = f"scale=w='min(iw,{target.width})':h='min(ih,{target.height})':flags=bicubic:force_original_aspect_ratio=decrease:force_divisible_by=2"
        filters += [scaling, 'setsar=1']
        if keep_interlace:
            filters += ['setfield=' + ('tff' if top_first else 'bff')]
        ratio = target.fps / media.fps
        duplication = ratio.denominator == 1
        if options.method == 'Frame blend' and not duplication:
            filters += [f'minterpolate=fps={fps}:mi_mode=blend']
        elif options.method == 'Motion interpolation' and not duplication:
            filters += [f'minterpolate=fps={fps}:mi_mode=mci']
        filters += [f'fps=fps={fps}:round=near']
        filters += ['format=nv12', 'hwupload=extra_hw_frames=16'] if gpu else ['format=yuv420p']
    else:
        filters.append('setsar=1')
    # Finite output length, integral number of frames. AAC is padded/trimmed to the same timeline.
    duration = round(media.duration * float(target.fps)) / float(target.fps) if media.duration else 0
    cmd += ['-vf', ','.join(filters), '-c:v', codec if gpu else 'libx264',
            '-preset', options.encoder_preset if gpu else 'medium',
            '-profile:v', target.profile, '-level:v', target.level,
            '-fps_mode', 'cfr', '-r', fps, '-g', str(round(float(target.fps) * 2)),
            '-bf', '0' if target.profile == 'baseline' else str(options.bframes), '-refs', '3',
            '-flags', '+cgop+ilme+ildct' if keep_interlace else '+cgop']
    quality_mode = options.rc in ('CQP', 'ICQ', 'Quality')
    icq = gpu and quality_mode and supported.get('icq', False)
    if icq:
        cmd += ['-global_quality', str(options.quality), '-look_ahead', '0']
    else:
        cmd += ['-b:v', f'{target.bitrate:.4f}M', '-maxrate', f'{target.maxrate:.4f}M',
                '-bufsize', f'{target.bufsize:.4f}M']
    if keep_interlace:
        cmd += ['-top', '1' if top_first else '0']
    if options.rc == 'CBR':
        cmd += ['-minrate', f'{target.bitrate:.4f}M', '-maxrate', f'{target.bitrate:.4f}M']
    elif quality_mode and not gpu:
        cmd += ['-crf', str(options.quality)]
    if gpu:
        cmd += ['-async_depth', '4']
        # Do not pass arbitrary user flags. Only probed options can be enabled.
        for key, val in options.advanced.items():
            if key in supported.get('tested_options', []) and not (icq and key in ('look_ahead', 'look_ahead_depth')) and key != 'low_power':
                cmd += [f'-{key}', str(val)]
    else:
        cmd += ['-pix_fmt', 'yuv420p', '-threads', str(encoding_threads(options.safe_mode)), '-x264-params', 'open-gop=0:force-cfr=1']
    video_start = float(media.video.get('start_time') or 0)
    audio_start = float(media.audio[0].get('start_time') or 0) if media.audio else video_start
    offset = audio_start - video_start
    audio_filter = f'asetpts=PTS-STARTPTS+({offset:.6f})/TB,aresample=async=1:first_pts=0,apad'
    # TV output always normalizes audio; legacy Copy selections cannot bypass this policy.
    cmd += ['-c:a', 'aac', '-profile:a', 'aac_low', '-ar', '48000', '-ac', '2', '-b:a', '192k']
    if options.audio == 'Keep all' and media.audio:
        for index, stream in enumerate(media.audio):
            delta = float(stream.get('start_time') or 0) - video_start
            cmd += [f'-filter:a:{index}', f'asetpts=PTS-STARTPTS+({delta:.6f})/TB,aresample=async=1:first_pts=0,apad']
    else:
        cmd += ['-af', audio_filter]
    color = 'bt709' if target.height > 576 else 'smpte170m'
    cmd += ['-tag:v', {'h264':'avc1', 'hevc':'hvc1', 'av1':'av01'}[output_codec], '-colorspace', color, '-color_primaries', color,
            '-color_trc', color, '-color_range', 'tv', '-strict', 'normal',
            '-avoid_negative_ts', 'make_zero', '-video_track_timescale', str(timescale(target.fps)),
            '-movie_timescale', str(timescale(target.fps)), '-movflags', '+faststart', '-max_muxing_queue_size', '1024']
    if duration:
        cmd += ['-t', f'{duration:.9f}']
    else:
        cmd += ['-shortest']
    if output_codec == 'av1':
        # AV1 level indices are not AVC level strings. Let the encoder select its legal level.
        index = cmd.index('-level:v')
        del cmd[index:index+2]
    cmd += [str(output)]
    if gpu:
        status = f'Using QSV · {caps.get("qsv_device") or device_args(caps)[1]} · ' + (
            'GPU decode' if decode else 'CPU decode → NV12 upload')
        status += ' · ICQ' if icq else ' · ' + ('CBR' if options.rc == 'CBR' else 'VBR')
        if quality_mode and not icq:
            status += ' (ICQ unsupported; explicit VBR bitrate/maxrate/buffer)'
    else:
        reason = ('Compatibility retry requested' if software else
                  'Haswell supports H.264 QSV only' if is_haswell(caps) and codec != 'h264_qsv' else
                  supported.get('reason') or caps.get('error') or f'{codec} hardware self-test unavailable or failed')
        status = 'CPU fallback: ' + reason
    return Plan(cmd, target, 'GPU full' if full else 'Hybrid' if gpu else 'CPU fallback', duration, output_codec, status)


def output_path(source, options):
    source = Path(source)
    if options.output_mode == 'Custom':
        folder = Path(options.output_folder)
        if not options.output_folder:
            raise ValueError('Choose a custom output folder')
    elif options.output_mode == '_TV subfolder':
        folder = source.parent / '_TV'
    else:
        folder = source.parent
    if not options.suffix or any(c in options.suffix for c in '<>:"/\\|?*'):
        raise ValueError('Use a nonempty filename suffix without path characters')
    destination = folder / f'{source.stem}{options.suffix}.mp4'
    if destination.resolve() == source.resolve():
        raise ValueError('Output cannot replace the source')
    if options.overwrite == 'Rename' and destination.exists():
        index = 2
        while destination.with_stem(destination.stem + f'_{index}').exists():
            index += 1
        destination = destination.with_stem(destination.stem + f'_{index}')
    return destination
