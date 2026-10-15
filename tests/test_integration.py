"""Real synthetic media tests; skipped only when FFmpeg/ffprobe are not installed."""
import shutil
from pathlib import Path
import pytest
from app.core.process import capture
from app.core.ffprobe import probe, rate
from app.core.tv_rules import Options, verify, faststart_ok
from app.core.pipeline import build_command
from app.core.queue import Job
from app.core.worker import EncodeWorker

pytestmark=pytest.mark.skipif(not(shutil.which('ffmpeg') and shutil.which('ffprobe')),
                              reason='FFmpeg and ffprobe required for integration tests')


def source(path, fps='25', pixel='yuv420p', audio=True, duration=2):
    cmd=['ffmpeg','-v','error','-y','-f','lavfi','-i',f'testsrc2=size=320x180:rate={fps}:duration={duration}']
    if audio:
        cmd+=['-f','lavfi','-i',f'sine=frequency=440:sample_rate=96000:duration={duration}']
    cmd+=['-c:v','libx264','-pix_fmt',pixel,'-threads','2']
    if audio:
        cmd+=['-c:a','aac']
    cmd+=[str(path)]
    capture(cmd,timeout=30)


@pytest.mark.parametrize('fps',['24000/1001','24','25','30000/1001','30','50','60000/1001','60'])
def test_actual_cpu_cfr(tmp_path,fps):
    src=tmp_path/'ورودی test.mp4'; source(src,fps)
    m=probe(src)
    out=tmp_path/'result.mp4'
    plan=build_command(m,Options(fps=fps),out,software=True)
    capture(plan.command,timeout=45)
    result=probe(out)
    assert rate(result.video['avg_frame_rate'])==rate(fps)==rate(result.video['r_frame_rate'])
    assert not result.cadence.unstable
    assert faststart_ok(out)
    assert not verify(result,plan.target)


def test_vfr_unification(tmp_path):
    src=tmp_path/'mixed.mp4'
    capture(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=50:duration=3',
             '-vf',"select='if(lt(t,1.5),not(mod(n,2)),not(mod(n,3)))'",'-fps_mode','vfr',
             '-c:v','libx264','-threads','2',str(src)],timeout=30)
    m=probe(src)
    assert m.cadence.unstable
    out=tmp_path/'fixed.mp4'
    plan=build_command(m,Options(fps='25'),out,software=True)
    capture(plan.command,timeout=45)
    result=probe(out)
    assert not verify(result,plan.target)
    assert result.fps==25


def test_worker_and_10bit_source(tmp_path):
    src=tmp_path/'422.mp4'; source(src,pixel='yuv422p10le')
    opts=Options(output_mode='Same folder')
    job=Job(src,opts)
    events=[]
    worker=EncodeWorker(job,opts,{},lambda identity,data: events.append(data))
    worker.run()
    final=[e for e in events if e.get('status') in ('TV-Safe ✓','Failed')][-1]
    assert final['status']=='TV-Safe ✓', events
    assert Path(final['output']).is_file()
    assert src.is_file()
    assert not list(tmp_path.glob('*.part.mp4'))


def test_one_retry_on_qsv_failure(tmp_path):
    src=tmp_path/'test.mp4'; source(src)
    opts=Options(output_mode='Same folder')
    job=Job(src,opts)
    events=[]
    # This simulated device has no real QSV; the software retry must complete.
    worker=EncodeWorker(job,opts,{'codecs':{'h264_qsv':{'available':True}}},
                        lambda identity,data: events.append(data))
    worker.run()
    assert any('CPU maximum compatibility' in e.get('warning','') for e in events)
    statuses = [e['encoding_status'] for e in events if 'encoding_status' in e]
    assert statuses[0].startswith('Using QSV')
    assert any(s.startswith('CPU fallback:') and 'encode failed' in s for s in statuses)
    assert any(e.get('status')=='TV-Safe ✓' for e in events),events


def test_audio_offset_preserved_and_synced(tmp_path):
    src=tmp_path/'offset.mp4'
    capture(['ffmpeg','-v','error','-y','-f','lavfi','-i',
             'testsrc2=size=320x180:rate=25:duration=3', '-itsoffset','0.25',
             '-f','lavfi','-i','sine=frequency=500:sample_rate=48000:duration=2.75',
             '-c:v','libx264','-threads','2','-c:a','aac',str(src)],timeout=30)
    m=probe(src)
    assert float(m.audio[0]['start_time']) > .20
    out=tmp_path/'synced.mp4'
    plan=build_command(m,Options(),out,software=True)
    capture(plan.command,timeout=45)
    result=probe(out)
    assert not verify(result,plan.target)


def test_sampled_output_verification(tmp_path):
    from app.core.ffprobe import verify_probe
    src=tmp_path/'source.mp4';source(src,'30000/1001',duration=5)
    out=tmp_path/'output.mp4'
    plan=build_command(probe(src),Options(),out,software=True)
    capture(plan.command,timeout=45)
    m=verify_probe(out)
    assert m.cadence.samples >= 148
    assert not m.cadence.negative
    assert not verify(m,plan.target)


def test_interlaced_keep_and_deinterlace(tmp_path):
    src=tmp_path/'interlaced.mp4'
    capture(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=50:duration=2',
             '-vf','tinterlace=mode=interleave_top','-c:v','libx264','-flags','+ilme+ildct',
             '-top','1','-threads','2',str(src)],timeout=30)
    m=probe(src)
    assert m.interlaced
    for deinterlace in (True,False):
        out=tmp_path/f'out_{deinterlace}.mp4'
        plan=build_command(m,Options(deinterlace=deinterlace),out,software=True)
        capture(plan.command,timeout=45)
        result=probe(out)
        assert result.interlaced == (not deinterlace)
        assert not verify(result,plan.target)


@pytest.mark.parametrize('action',['pause_resume','stop'])
def test_live_process_controls(tmp_path,monkeypatch,action):
    import threading
    import app.core.worker as module
    # Leave headroom beyond x264's lookahead so first progress is not an EOF race.
    src=tmp_path/'live.mp4';source(src,duration=6)
    opts=Options(output_mode='Same folder')
    job=Job(src,opts)
    progress=threading.Event()
    events=[]
    original=module.build_command
    def realtime_plan(*args,**kwargs):
        plan=original(*args,**kwargs)
        plan.command.insert(plan.command.index('-i'),'-re')
        return plan
    monkeypatch.setattr(module,'build_command',realtime_plan)
    def emit(identity,data):
        events.append(data)
        if 'frame' in data:
            progress.set()
    worker=EncodeWorker(job,opts,{},emit)
    thread=threading.Thread(target=worker.run,daemon=True)
    thread.start()
    try:
        assert progress.wait(timeout=8),events
        if action=='stop':
            worker.stop()
        else:
            worker.pause(True)
            assert worker.paused
            worker.pause(False)
            assert not worker.paused
        thread.join(timeout=15)
        assert not thread.is_alive()
        expected='Skipped' if action=='stop' else 'TV-Safe ✓'
        assert any(e.get('status')==expected for e in events),events
        assert src.exists()
        assert not list(tmp_path.glob('*.part.mp4'))
    finally:
        if thread.is_alive():
            worker.stop()
            thread.join(timeout=5)


@pytest.mark.parametrize('violation',['Audio must be AAC-LC, ≤2 channels, 48 kHz', 'Unexpected output dimensions'])
def test_verifier_warning_retains_output_and_original(tmp_path,monkeypatch,violation):
    src=tmp_path/'warning.mp4';source(src)
    opts=Options(output_mode='Same folder',delete_source=True)
    job=Job(src,opts);events=[]
    worker=EncodeWorker(job,opts,{},lambda identity,data:events.append(data))
    original=worker._inspect
    def warned(path,plan,options):
        media,violations,values=original(path,plan,options)
        return media,violations+[violation],values
    monkeypatch.setattr(worker,'_inspect',warned)
    worker.run()
    final=events[-1]
    assert final['status']=='Warning',events
    assert violation in final['reason']
    assert Path(final['output']).exists() and src.exists()
    assert not any(e.get('status')=='Failed' for e in events)
    assert 'exact FFmpeg command' in final['details'] and 'Stderr tail' in final['details']
    assert 'OUTPUT ffprobe values' in final['details'] and 'sample_rate' in final['details']
    assert not list(tmp_path.glob('*.part.mp4'))


def test_bf_zero_retry_failure_retains_first_successful_encode(tmp_path,monkeypatch):
    from app.core.worker import EncodeFailure
    src=tmp_path/'timing.mp4';source(src)
    opts=Options(output_mode='Same folder',delete_source=True)
    job=Job(src,opts);events=[]
    worker=EncodeWorker(job,opts,{},lambda identity,data:events.append(data))
    original_inspect=worker._inspect;original_encode=worker._encode;calls=[]
    def inspect(path,plan,options):
        media,violations,values=original_inspect(path,plan,options)
        return media,violations+['Audio/video start offset 0.100000s ≥40 ms'],values
    def encode(plan):
        calls.append(plan.command)
        if len(calls)==2:
            assert plan.command[plan.command.index('-bf')+1]=='0'
            raise EncodeFailure('FFmpeg exited with code 1: synthetic retry error')
        original_encode(plan)
    monkeypatch.setattr(worker,'_inspect',inspect)
    monkeypatch.setattr(worker,'_encode',encode)
    worker.run()
    assert len(calls)==2
    final=events[-1]
    assert final['status']=='Warning' and 'first encoded output retained' in final['reason']
    assert Path(final['output']).is_file() and src.is_file()
    assert not list(tmp_path.glob('*.part.mp4'))


def test_output_probe_timeout_is_warning_not_encoder_failure(tmp_path,monkeypatch):
    from app.core.process import ToolTimeout
    import app.core.worker as module
    src=tmp_path/'probe_timeout.mp4';source(src)
    opts=Options(output_mode='Same folder',delete_source=True)
    job=Job(src,opts);events=[]
    def timeout(*args,**kwargs):raise ToolTimeout('ffprobe timed out after 15s')
    monkeypatch.setattr(module,'verify_probe',timeout)
    EncodeWorker(job,opts,{},lambda identity,data:events.append(data)).run()
    assert events[-1]['status']=='Warning'
    assert '15s' in events[-1]['reason']
    assert Path(events[-1]['output']).exists() and src.exists()


def test_no_output_is_failed_with_real_reason(tmp_path,monkeypatch):
    src=tmp_path/'nooutput.mp4';source(src)
    opts=Options(output_mode='Same folder');job=Job(src,opts);events=[]
    worker=EncodeWorker(job,opts,{},lambda identity,data:events.append(data))
    monkeypatch.setattr(worker,'_encode',lambda plan:None)
    worker.run()
    assert events[-1]['status']=='Failed'
    assert 'no output file' in events[-1]['reason']
    assert src.exists()


@pytest.mark.parametrize('codec,sample_rate', [('aac','96000'),('ac3','48000')])
def test_actual_1080p5994_unsafe_audio_round1(tmp_path,codec,sample_rate):
    import time
    label='Khanum' if codec=='aac' else 'Agha'
    src=tmp_path/f'synthetic_{label}_1080p5994.mp4'
    capture(['ffmpeg','-v','error','-y','-f','lavfi','-i',
             'color=c=0x315445:size=1920x1080:rate=60000/1001:duration=2.002',
             '-f','lavfi','-i',f'anullsrc=r={sample_rate}:cl=5.1',
             '-t','2.002','-c:v','libx264','-preset','ultrafast','-threads','2',
             '-pix_fmt','yuv420p','-c:a',codec,'-b:a','384k',str(src)],timeout=30)
    begin=time.monotonic();media=probe(src);analysis_seconds=time.monotonic()-begin
    assert media.video['width']==1920 and media.fps==rate('59.94')
    assert media.audio[0]['channels']==6
    opts=Options(audio='Copy',output_mode='Same folder')
    job=Job(src,opts);job.media=media;events=[]
    EncodeWorker(job,opts,{},lambda identity,data:events.append(data)).run()
    final=events[-1]
    assert final['status']=='TV-Safe ✓',final
    output=probe(final['output'])
    assert output.video['level']==42
    assert rate(output.video['r_frame_rate'])==rate(output.video['avg_frame_rate'])==rate('59.94')
    assert output.audio[0]['codec_name']=='aac' and output.audio[0]['profile']=='LC'
    assert output.audio[0]['channels']==2 and output.audio[0]['sample_rate']=='48000'
    assert not verify(output)
    assert analysis_seconds < 15


def test_skipped_stream_analysis_still_encodes_and_keeps_warning(tmp_path):
    from app.core.ffprobe import unverified_media
    src=tmp_path/'unknown.mp4';source(src)
    opts=Options(fps='59.94',output_mode='Same folder',delete_source=True)
    job=Job(src,opts)
    job.media=unverified_media(src,'Streams timed out after 15s','59.94')
    events=[]
    EncodeWorker(job,opts,{},lambda identity,data:events.append(data)).run()
    final=events[-1]
    assert final['status']=='Warning',events
    assert Path(final['output']).exists() and src.exists()
    out=probe(final['output'])
    assert out.video['width']==320  # Unknown metadata must not trigger expensive upscaling.
    assert out.video['avg_frame_rate']=='60000/1001'
    assert out.audio[0]['codec_name']=='aac' and out.audio[0]['sample_rate']=='48000'
