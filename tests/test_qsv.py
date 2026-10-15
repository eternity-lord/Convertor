"""QSV command policy regression tests; no Intel GPU required."""
import pytest
from app.core import capabilities as probing
from app.core.pipeline import build_command
from app.core.process import ToolError
from app.core.qsv import DEVICE_CANDIDATES, is_haswell, preview_command
from app.core.tv_rules import Options
from test_rules import media


def caps(**extra):
    return dict(adapter='Intel(R) HD Graphics 4600', haswell=True,
                qsv_device=DEVICE_CANDIDATES[0], decode=True,
                vpp_qsv=True, vpp_framerate=True,
                codecs={'h264_qsv': {'available': True, 'icq': True,
                                     'tested_options': ['look_ahead', 'low_power']}}, **extra)


def value(command, flag):
    return command[command.index(flag) + 1]


@pytest.mark.parametrize('name', ['Intel(R) HD Graphics 4600', 'Intel HD 4600',
                                  'Intel Iris Pro Graphics 5200'])
def test_haswell_identity(name):
    assert is_haswell({'adapter': name})
    assert not is_haswell({'adapter': 'Intel UHD Graphics 630'})


def test_haswell_h264_gpu_decode_and_nv12_vpp():
    plan = build_command(media(), Options(advanced={'low_power': 1}), 'out.mp4', caps(), executable='ffmpeg')
    assert value(plan.command, '-init_hw_device') == DEVICE_CANDIDATES[0]
    assert value(plan.command, '-hwaccel_device') == 'hw'
    assert value(plan.command, '-hwaccel') == 'qsv'
    assert 'format=nv12' in value(plan.command, '-vf')
    assert '-low_power' not in plan.command
    assert plan.encoding_status.startswith('Using QSV')


@pytest.mark.parametrize('codec,pix_fmt', [('hevc', 'yuv420p'), ('av1', 'yuv420p'),
                                          ('h264', 'yuv420p10le'), ('hqx', 'yuv422p10le')])
def test_haswell_other_sources_cpu_decode_gpu_encode(codec, pix_fmt):
    source = media()
    source.video.update(codec_name=codec, pix_fmt=pix_fmt)
    plan = build_command(source, Options(safe_mode=False), 'out.mp4', caps(), executable='ffmpeg')
    assert '-hwaccel' not in plan.command
    assert value(plan.command, '-c:v') == 'h264_qsv'
    assert 'format=nv12,hwupload=extra_hw_frames=16' in value(plan.command, '-vf')
    assert 'CPU decode' in plan.encoding_status


def test_h264_decode_kept_when_cpu_filters_required():
    plan = build_command(media(), Options(method='Motion interpolation'), 'out.mp4', caps(), executable='ffmpeg')
    assert value(plan.command, '-hwaccel') == 'qsv'
    filters = value(plan.command, '-vf')
    assert filters.startswith('hwdownload,format=nv12,')
    assert filters.endswith('format=nv12,hwupload=extra_hw_frames=16')


@pytest.mark.parametrize('codec', ['hevc_qsv', 'av1_qsv'])
def test_haswell_never_modern_gpu_encode_even_with_stale_caps(codec):
    c = caps()
    c['codecs'][codec] = {'available': True}
    plan = build_command(media(), Options(codec=codec), 'out.mp4', c, executable='ffmpeg')
    assert value(plan.command, '-c:v') == 'libx264'
    assert '-init_hw_device' not in plan.command
    assert 'Haswell' in plan.encoding_status


@pytest.mark.parametrize('rc', ['Quality', 'ICQ', 'CQP'])
def test_quality_explicit_icq_and_vbr_fallback(rc):
    c = caps()
    opts = Options(rc=rc, quality=27, advanced={'look_ahead': 1})
    plan = build_command(media(), opts, 'out.mp4', c, executable='ffmpeg')
    assert value(plan.command, '-global_quality') == '27'
    assert value(plan.command, '-look_ahead') == '0'
    assert '-b:v' not in plan.command  # ICQ must not accidentally become VBR/CQP.
    c['codecs']['h264_qsv']['icq'] = False
    plan = build_command(media(), opts, 'out.mp4', c, executable='ffmpeg')
    assert '-global_quality' not in plan.command
    assert all(flag in plan.command for flag in ['-b:v', '-maxrate', '-bufsize'])
    assert 'ICQ unsupported' in plan.encoding_status


def test_cpu_fallback_reason():
    c = {'codecs': {'h264_qsv': {'available': False, 'reason': 'MFX session -9'}}}
    plan = build_command(media(), Options(), 'out.mp4', c, executable='ffmpeg')
    assert plan.encoding_status == 'CPU fallback: MFX session -9'


def test_preview_uses_cached_device_and_source_policy():
    c = caps()
    c['qsv_device'] = DEVICE_CANDIDATES[1]
    source = media()
    command, gpu = preview_command(source, c, 2, 'preview.jpg', 'ffmpeg')
    assert gpu and value(command, '-init_hw_device') == DEVICE_CANDIDATES[1]
    assert value(command, '-vf') == 'hwdownload,format=nv12,scale=480:-2'
    source.video['pix_fmt'] = 'yuv420p10le'
    command, gpu = preview_command(source, c, 2, 'preview.jpg', 'ffmpeg')
    assert not gpu and '-hwaccel' not in command


@pytest.mark.parametrize('working', [0, 1, 2, None])
def test_device_probe_order_cache_and_all_qsv_commands(tmp_path, monkeypatch, working):
    binary = tmp_path / 'ffmpeg'
    binary.touch()
    monkeypatch.setattr(probing, 'tool', lambda _: str(binary))
    monkeypatch.setattr(probing, 'data_dir', lambda: tmp_path)
    monkeypatch.setattr(probing, 'is_haswell', lambda _: True)
    calls = []

    def capture(command, **kwargs):
        calls.append(command)
        if '-encoders' in command:
            return 'h264_qsv hevc_qsv av1_qsv'
        if '-h' in command:
            return 'VPP framerate' if 'filter=vpp_qsv' in command else ' -extbrc control'
        if '-init_hw_device' in command:
            selected = value(command, '-init_hw_device')
            assert '-low_power' not in command
            assert ('format=nv12' in value(command, '-vf'))
            if working is None or selected != DEVICE_CANDIDATES[working]:
                raise ToolError('MFX session -9: ' + selected)
            if '-c:v' in command:
                assert value(command, '-c:v') == 'h264_qsv'
                assert '-b:v' in command or '-global_quality' in command
        return ''

    monkeypatch.setattr(probing, 'capture', capture)
    result = probing.probe_capabilities()
    selected = result['qsv_device']
    assert selected == (DEVICE_CANDIDATES[working] if working is not None else None)
    first = [value(c, '-init_hw_device') for c in calls if '-init_hw_device' in c]
    length = working + 1 if working is not None else 3
    assert first[:length] == list(DEVICE_CANDIDATES[:length])
    assert not result['codecs']['hevc_qsv']['available']
    assert not result['codecs']['av1_qsv']['available']
    if working is not None:
        assert result['decode'] and result['vpp_framerate']
    before = len(calls)
    assert probing.probe_capabilities()['qsv_device'] == selected
    assert len(calls) == before
    # Old cache entries are invalidated by the versioned key.
    import json
    cache = tmp_path / 'capabilities.json'
    old = json.loads(cache.read_text())
    old['key'] = old['key'].split(':', 1)[1]
    cache.write_text(json.dumps(old))
    probing.probe_capabilities()
    assert len(calls) > before


@pytest.mark.parametrize('failed_stage', ['icq', 'vpp'])
def test_optional_probe_failure_does_not_disable_encode_or_decode(tmp_path, monkeypatch, failed_stage):
    binary = tmp_path / 'ffmpeg'
    binary.touch()
    monkeypatch.setattr(probing, 'tool', lambda _: str(binary))
    monkeypatch.setattr(probing, 'data_dir', lambda: tmp_path)

    def capture(command, **kwargs):
        if '-encoders' in command:
            return 'h264_qsv'
        if '-h' in command:
            return 'VPP framerate' if 'filter=vpp_qsv' in command else ''
        if failed_stage == 'icq' and '-global_quality' in command:
            raise ToolError('ICQ unsupported')
        if failed_stage == 'vpp' and '-vf' in command and 'vpp_qsv' in value(command, '-vf'):
            raise ToolError('VPP framerate unsupported')
        return ''

    monkeypatch.setattr(probing, 'capture', capture)
    result = probing.probe_capabilities()
    assert result['codecs']['h264_qsv']['available']
    assert result['decode']
    assert result['codecs']['h264_qsv']['icq'] == (failed_stage != 'icq')
    assert result['vpp_framerate'] == (failed_stage != 'vpp')
