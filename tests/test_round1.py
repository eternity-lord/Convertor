"""Regression coverage for the user's verification, probe latency and PC-freeze report."""
import json
import threading
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.core.ffprobe import probe, verify_probe, probe_values, Media, Cadence, rate
from app.core.process import ToolError, ToolTimeout, ProbeSkipped, capture
from app.core.tv_rules import Options, choose_target, verify
from app.core.pipeline import build_command
from app.core.safety import encoding_threads, reserved_affinity, SustainedLoadGuard


def good_media(path=Path('input.mp4')):
    return Media(path, [dict(codec_type='video',codec_name='h264',profile='High',level=42,
        pix_fmt='yuv420p',codec_tag_string='avc1',width=1920,height=1080,sample_aspect_ratio='1:1',
        r_frame_rate='60000/1001',avg_frame_rate='60000/1001',duration='2.002',start_time='0',
        color_space='bt709',color_transfer='bt709',color_primaries='bt709',color_range='tv'),
        dict(codec_type='audio',codec_name='aac',profile='LC',channels=2,sample_rate='48000',
             duration='2.002',start_time='0')],{'duration':'2.002'},Cadence(rate('59.94'),samples=119))


def test_5994_preserved_with_default_preset_and_auto_level():
    target=choose_target(good_media(),Options())
    assert target.fps==Fraction(60000,1001)
    assert target.level=='4.2'
    assert choose_target(good_media(),Options(level='4.1')).level=='4.2'


@pytest.mark.parametrize('audio_mode',['TV-safe','Copy','Keep all'])
def test_unsafe_audio_never_copied(audio_mode):
    m=good_media();m.audio[0].update(codec_name='ac3',channels=6,sample_rate='96000')
    cmd=build_command(m,Options(audio=audio_mode),Path('out.mp4'),executable='ffmpeg').command
    assert cmd[cmd.index('-c:a')+1]=='aac'
    for flag,value in [('-profile:a','aac_low'),('-ar','48000'),('-ac','2'),('-b:a','192k')]:
        assert cmd[cmd.index(flag)+1]==value
    assert any('aresample=async=1:first_pts=0' in x for x in cmd)
    assert '-fflags' in cmd and '+genpts' in cmd and 'make_zero' in cmd


def test_negative_packet_timestamps_do_not_reject_legal_stream_start():
    m=good_media();m.cadence.negative=True
    m.video['start_time']='-0.02';m.audio[0]['start_time']='-0.01'
    assert not verify(m)
    m.audio[0]['start_time']='0.2'
    assert any('start offset' in x for x in verify(m))
    m.video['start_time']='-0.101'
    assert any('start_time' in x for x in verify(m))


def test_output_audio_fields_are_verified():
    m=good_media();m.audio[0].update(codec_name='ac3',channels=6,sample_rate='96000')
    assert any('Audio must be AAC' in x for x in verify(m))
    values=probe_values(m)
    assert values['audio'][0]['sample_rate']=='96000'
    assert values['video']['level']==42


def test_probe_uses_15_second_deadlines_and_bounded_pts_windows(monkeypatch):
    import app.core.ffprobe as module
    m=good_media();m.video['duration']='3600'
    calls=[]
    def fake(command,**kwargs):
        calls.append((command,kwargs))
        assert kwargs['timeout']==15
        if '-show_streams' in command:
            return json.dumps({'streams':m.streams,'format':m.format})
        assert command[command.index('-show_entries')+1]=='packet=pts_time'
        assert '-read_intervals' in command
        return '\n'.join(f'{i*1001/60000:.6f}' for i in range(180))
    monkeypatch.setattr(module,'tool',lambda name:name)
    monkeypatch.setattr(module,'capture',fake)
    steps=[]
    result=verify_probe('output.mp4',step=steps.append)
    assert result.cadence.verified
    assert len(calls)==5
    intervals=[c[c.index('-read_intervals')+1] for c,k in calls[1:]]
    assert intervals[0]=='0.000000%+20'
    assert all(i.endswith('%+3') for i in intervals[1:])
    assert steps==['Reading streams…','Checking frame timing…']


@pytest.mark.parametrize('failure',[ToolTimeout('15s deadline'),ProbeSkipped('Analysis skipped')])
def test_scan_timeout_or_skip_is_unverified_not_blocking(monkeypatch,failure):
    import app.core.ffprobe as module
    m=good_media();calls=[]
    def fake(command,**kwargs):
        calls.append(command)
        if '-show_streams' in command:
            return json.dumps({'streams':m.streams,'format':m.format})
        raise failure
    monkeypatch.setattr(module,'tool',lambda name:name)
    monkeypatch.setattr(module,'capture',fake)
    result=probe('source.mp4')
    assert result.metadata_verified
    assert not result.cadence.verified
    assert result.fps==rate('59.94')
    assert len(calls)==2  # No additional timeouts on the other windows.
    assert 'Unverified' in result.badge()


def test_stream_timeout_falls_back_explicitly_unverified(monkeypatch):
    import app.core.ffprobe as module
    monkeypatch.setattr(module,'tool',lambda name:name)
    def timeout(*args,**kwargs):raise ToolTimeout('Timed out after 15s')
    monkeypatch.setattr(module,'capture',timeout)
    result=probe('source.mp4',fallback_fps='59.94')
    assert not result.metadata_verified and not result.cadence.verified
    assert result.fps==rate('59.94')
    cmd=build_command(result,Options(fps='59.94'),Path('out.mp4'),executable='ffmpeg').command
    assert '0:a:0?' in cmd  # Optional source audio, not incorrectly replaced with silence.
    assert 'force_original_aspect_ratio=decrease' in cmd[cmd.index('-vf')+1]


def test_missing_ffprobe_is_clear(monkeypatch):
    import app.core.ffprobe as module
    def missing(name):raise ToolError('ffprobe is missing. Put ffprobe.exe beside the app.')
    monkeypatch.setattr(module,'tool',missing)
    with pytest.raises(ToolError,match='ffprobe.exe'):
        probe('source.mp4')


def test_skip_cancels_a_running_probe_promptly():
    import sys,time
    event=threading.Event()
    timer=threading.Timer(.1,event.set);timer.start()
    begin=time.monotonic()
    try:
        with pytest.raises(ProbeSkipped):
            capture([sys.executable,'-c','import time;time.sleep(30)'],timeout=15,cancel=event)
    finally:timer.cancel()
    assert time.monotonic()-begin < 3


def test_safe_threads_affinity_and_gpu_frames():
    assert encoding_threads(True,8)==6
    assert encoding_threads(True,2)==2
    assert encoding_threads(False,8)==0
    assert reserved_affinity(list(range(8)))==list(range(6))
    assert reserved_affinity([0,1]) is None
    caps={'codecs':{'h264_qsv':{'available':True}}}
    plan=build_command(good_media(),Options(),Path('out.mp4'),caps,executable='ffmpeg')
    assert '-filter_threads' in plan.command and 'hwupload=extra_hw_frames=16' in plan.command[plan.command.index('-vf')+1]
    assert plan.command[plan.command.index('-async_depth')+1]=='4'
    assert not Options().high_priority and Options().safe_mode


def test_spawn_windows_priority_is_normal_unless_opted_in(monkeypatch):
    import app.core.process as module
    calls=[]
    class Child:
        def poll(self):return 0
    monkeypatch.setattr(module,'os',SimpleNamespace(name='nt'))
    for name,value in [('CREATE_NO_WINDOW',8),('NORMAL_PRIORITY_CLASS',32),('HIGH_PRIORITY_CLASS',128)]:
        monkeypatch.setattr(module.subprocess,name,value,raising=False)
    def popen(*args,**kwargs):calls.append(kwargs);return Child()
    monkeypatch.setattr(module.subprocess,'Popen',popen)
    module.spawn(['ffmpeg'])
    module.spawn(['ffmpeg'],priority=True)
    assert calls[0]['creationflags']==8|32
    assert calls[1]['creationflags']==8|128


@pytest.mark.parametrize('ram,cpu',[(91,10),(40,96)])
def test_sustained_overload_guard(ram,cpu):
    guard=SustainedLoadGuard()
    assert guard.observe(ram,cpu,0) is None
    assert guard.observe(ram,cpu,9.9) is None
    assert 'paused the queue' in guard.observe(ram,cpu,10)
    assert guard.observe(ram,cpu,11) is None
    assert guard.observe(20,20,12) is None
    assert guard.observe(ram,cpu,13) is None
    assert guard.observe(ram,cpu,23) is not None


def test_thermal_and_priority_options_cannot_change_power_without_opt_in(tmp_path,monkeypatch):
    import app.core.system_tweaks as module
    calls=[]
    tweaks=object.__new__(module.SystemTweaks)
    tweaks.path=tmp_path/'restore.json';tweaks.state={};tweaks.lock=threading.RLock()
    tweaks.report=lambda *args:None
    monkeypatch.setattr(module,'os',SimpleNamespace(name='nt'))
    monkeypatch.setattr(module,'is_admin',lambda:True)
    monkeypatch.setattr(module,'capture',lambda command,**kwargs:calls.append(command) or '')
    tweaks.apply({'thermal':True,'priority':True,'power':False,'parking':False})
    assert not calls and not tweaks.path.exists()


def test_power_marker_restores_on_next_start(tmp_path,monkeypatch):
    import app.core.system_tweaks as module
    old='11111111-1111-1111-1111-111111111111'
    clone='22222222-2222-2222-2222-222222222222'
    calls=[]
    monkeypatch.setattr(module,'os',SimpleNamespace(name='nt'))
    monkeypatch.setattr(module,'is_admin',lambda:True)
    monkeypatch.setattr(module,'data_dir',lambda:tmp_path)
    def command(args,**kwargs):
        calls.append(args)
        if '/getactivescheme' in args:return old
        if '/duplicatescheme' in args:return clone
        if args==['powercfg','/setactive',clone]:
            assert json.loads((tmp_path/'restore.json').read_text())['power']==[old,clone]
        return ''
    monkeypatch.setattr(module,'capture',command)
    tweaks=object.__new__(module.SystemTweaks)
    tweaks.path=tmp_path/'restore.json';tweaks.state={};tweaks.lock=threading.RLock()
    tweaks.report=lambda *args:None
    tweaks.apply({'power':True})
    assert tweaks.path.exists()
    recovered=module.SystemTweaks()
    recovered.stop_event.set()
    assert ['powercfg','/setactive',old] in calls
    assert ['powercfg','/delete',clone] in calls
    assert not tweaks.path.exists()
