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


def test_full_file_verification_bounded_heap(tmp_path):
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
    src=tmp_path/'live.mp4';source(src)
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
