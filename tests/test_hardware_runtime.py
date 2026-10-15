import shutil
import threading
from pathlib import Path
import pytest
from app.core.process import capture, configure_tools, tool, TOOL_PATHS, ToolError
from app.core.queue import Job
from app.core.worker import EncodeWorker, EncodeFailure
from app.core.tv_rules import Options, verify
from app.core.ffprobe import probe
from hardware_support import matrix, encoder


@pytest.mark.parametrize('policy,expected,hardware_attempts', [('Use next','TV-Safe ✓',2),
    ('Use CPU','TV-Safe ✓',1),('Stop','Failed',1),('Ask','TV-Safe ✓',1)])
def test_worker_fallback_policy_real_cpu_result(tmp_path, monkeypatch, policy, expected, hardware_attempts):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg / FFprobe required')
    source=tmp_path/'ورودی فارسی.mp4'
    capture(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=25:duration=1',
             '-c:v','libx264','-threads','2',str(source)])
    opts=Options(output_mode='Same folder',failure_policy=policy)
    job=Job(source,opts);job.media=probe(source)
    m=matrix('Intel','NVIDIA','CPU')
    # The CPU fixture deliberately has only generic supported options for this real encode.
    cpu=m.encoders[-1]
    cpu.help_options=['preset','profile','level','crf','x264-params']
    events=[];attempts=[]
    def emit(identity,data):
        events.append(data)
        if 'fallback_request' in data:
            worker.answer_fallback('Use CPU')
    worker=EncodeWorker(job,opts,{'matrix':m.to_dict()},emit)
    def failing_hardware(plan, folder):
        attempts.append(plan.encoder_id)
        worker.emit(job.id,{'encoding_status':plan.encoding_status,'command':plan.command})
        raise EncodeFailure('MFX session -9' if 'qsv' in plan.encoder_id else 'Cannot load nvcuda.dll')
    monkeypatch.setattr(worker,'_preflight_hardware',failing_hardware)
    worker.run()
    assert len(attempts)==hardware_attempts
    assert events[-1]['status']==expected,events[-1]
    if expected=='TV-Safe ✓':
        output=Path(events[-1]['output'])
        assert output.exists() and source.exists()
        assert not verify(probe(output))
        assert 'CPU libx264' in events[-1]['encoding_status']
        assert 'Fallback reason:' in events[-1]['encoding_status']
    assert any(e.get('hardware_error')=='mfx_session' for e in events)
    assert not list(tmp_path.glob('*.part.mp4'))


def test_ask_cancellation_unblocks_worker():
    opts=Options(failure_policy='Ask')
    job=Job(Path('source.mp4'),opts)
    waiting=threading.Event()
    events=[]
    worker=EncodeWorker(job,opts,{},lambda _,data:(events.append(data),waiting.set() if 'fallback_request' in data else None))
    outcome=[]
    def run():
        try:
            worker._fallback_action('out of memory')
        except Exception as exc:
            outcome.append(type(exc).__name__)
    thread=threading.Thread(target=run)
    thread.start()
    assert waiting.wait(2)
    worker.cancel.set()
    thread.join(2)
    assert not thread.is_alive() and outcome==['Cancelled']


def test_custom_tool_paths_never_silently_ignore_missing_executable(tmp_path):
    original=dict(TOOL_PATHS)
    try:
        p=tmp_path/'custom ffmpeg.exe';p.touch()
        q=tmp_path/'custom ffprobe.exe';q.touch()
        configure_tools(str(p),str(q))
        assert tool('ffmpeg')==str(p.resolve()) and tool('ffprobe')==str(q.resolve())
        with pytest.raises(ToolError,match='does not exist'):
            configure_tools(str(tmp_path/'missing'),str(q))
        assert tool('ffmpeg')==str(p.resolve())  # failed change is atomic
        configure_tools()
        assert not TOOL_PATHS
    finally:
        TOOL_PATHS.clear();TOOL_PATHS.update(original)


def test_queue_use_all_assigns_separate_devices_without_changing_user_options(tmp_path, monkeypatch, qt_application):
    from app.core.queue import QueueManager
    from tests_support import sample_media
    import psutil
    app = qt_application
    manager=QueueManager();manager.timer.stop()
    manager.capabilities={'matrix':matrix('Intel','NVIDIA','CPU').to_dict()}
    manager.use_all_gpus=True;manager.limit=3;manager.running=True
    opts=Options(hardware='Auto',output_mode='Same folder')
    manager.jobs=[Job(tmp_path/f'{i}.mp4',opts) for i in range(3)]
    for job in manager.jobs:
        job.media=sample_media(job.source)
    monkeypatch.setattr(psutil,'virtual_memory',lambda:type('Mem',(),{'available':5*1024**3})())
    gate=threading.Event()
    monkeypatch.setattr(EncodeWorker,'run',lambda self:gate.wait(3))
    try:
        manager.tick()
        started=[j for j in manager.jobs if j.thread]
        assert len(started)==2
        assert {j.data['gpu_id'] for j in started}=={'intel','nvidia'}
        assert all(j.options.hardware=='Auto' for j in manager.jobs)
    finally:
        gate.set()
        for job in manager.jobs:
            if job.thread:
                job.thread.join(3)
        manager.stop_all()


def test_real_missing_hardware_preflight_recovers_to_cpu(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg / FFprobe required')
    src=tmp_path/'preflight source.mp4'
    capture(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=size=320x180:rate=25:duration=1',
             '-c:v','libx264','-threads','2',str(src)])
    opts=Options(output_mode='Same folder')
    job=Job(src,opts);job.media=probe(src)
    m=matrix('Intel','CPU')
    # An intentionally invalid runtime device MUST fail; never execute a claimed fake GPU encode.
    m.encoders[0].device_args=['-init_hw_device','not_a_hardware_backend=hw']
    m.encoders[-1].help_options=['preset','profile','level','crf','x264-params']
    events=[]
    EncodeWorker(job,opts,{'matrix':m.to_dict()},lambda _,e:events.append(e)).run()
    assert events[-1]['status']=='TV-Safe ✓',events[-1]
    assert any('Exact-job hardware preflight failed' in e.get('warning','') for e in events)
    assert events[-1]['encoding_status'].startswith('CPU libx264')
    assert not list(tmp_path.glob('*.part.mp4'))
