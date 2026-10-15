"""Run with QT_QPA_PLATFORM=offscreen. Skip only if native Qt libraries are absent."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    pytest.skip('Native Qt GUI libraries are not installed',allow_module_level=True)
from app.core.settings import Settings
from app.ui.theme import apply_theme
from app.ui.main_window import MainWindow, PAGES
from PySide6.QtCore import QEventLoop, QTimer


def close_window(window):
    loop=QEventLoop()
    window.cleanup_done.connect(loop.quit)
    QTimer.singleShot(10000,loop.quit)
    window.close()
    loop.exec()


def test_pages_and_language(tmp_path,monkeypatch):
    monkeypatch.setenv('APPDATA',str(tmp_path))
    app=QApplication.instance() or QApplication([])
    apply_theme(app)
    monkeypatch.setattr(MainWindow,'probe_hardware',lambda *a:None)
    window=MainWindow(Settings())
    window.show()
    for page in PAGES:
        window.navigate(page)
        app.processEvents()
    window.change_language('fa')
    app.processEvents()
    window.change_language('en')
    app.processEvents()
    window.encoding.open()
    app.processEvents()
    close_window(window)


def test_queue_preflight_and_output_collisions(tmp_path,monkeypatch):
    from app.core.queue import QueueManager, Job
    from app.core.tv_rules import Options
    from app.core.worker import EncodeWorker
    from tests_support import sample_media
    app=QApplication.instance() or QApplication([])
    manager=QueueManager()
    manager.limit=2
    opts=Options(output_mode='Custom',output_folder=str(tmp_path/'out'))
    a=tmp_path/'a'/'same.mp4';a.parent.mkdir();a.touch()
    b=tmp_path/'b'/'same.mp4';b.parent.mkdir();b.touch()
    jobs=[Job(a,opts),Job(b,opts)]
    for job in jobs:
        job.media=sample_media(job.source)
    manager.jobs=jobs
    import threading
    gate=threading.Event()
    def fake_encode(self):
        gate.wait(timeout=1)
    monkeypatch.setattr(EncodeWorker,'run',fake_encode)
    manager.running=True
    manager.tick()
    assert jobs[0].data['reserved_output'] != jobs[1].data['reserved_output']
    gate.set()
    for job in jobs:
        job.thread.join(timeout=2)
    manager.stop_all()


def test_pills_and_keyboard_toggle():
    from app.ui.widgets.common import Pills, Toggle
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    app=QApplication.instance() or QApplication([])
    pills=Pills(['Auto','25','29.97'])
    pills.choose('29.97')
    assert pills.value=='29.97'
    assert pills.buttons['29.97'].isChecked()
    assert not pills.buttons['Auto'].isChecked()
    toggle=Toggle('Test')
    toggle.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress,Qt.Key_Space,Qt.NoModifier))
    assert toggle.isChecked()


def test_real_gui_queue_flow(tmp_path,monkeypatch):
    import shutil
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Real FFmpeg required')
    from app.core.process import capture
    from app.core.tv_rules import Options
    monkeypatch.setenv('APPDATA',str(tmp_path/'settings'))
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow,'probe_hardware',lambda *a:None)
    root=tmp_path/'media';root.mkdir()
    capture(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=size=320x180:rate=25:duration=1',
             '-c:v','libx264','-threads','2',str(root/'test.mp4')])
    w=MainWindow(Settings());w.show()
    loop=QEventLoop()
    w.manager.finished.connect(loop.quit)
    QTimer.singleShot(20000,loop.quit)
    w.scan_folder(str(root),Options(),True,w.fixer)
    loop.exec()
    assert len(w.manager.jobs)==1
    assert w.manager.jobs[0].status=='TV-Safe ✓',w.manager.jobs[0].data
    assert w.encoding.isVisible()
    assert w.encoding.current().status=='TV-Safe ✓'
    assert w.daily['files']==1
    assert (root/'_TV'/'test_TV.mp4').exists()
    close_window(w)
    app.processEvents()


def test_safety_defaults_modes_and_auto_pause(tmp_path,monkeypatch):
    from app.core.queue import QueueManager
    monkeypatch.setenv('APPDATA',str(tmp_path))
    app=QApplication.instance() or QApplication([])
    settings=Settings();settings.set('parallel','4')  # Migration must not retain unsafe old defaults.
    monkeypatch.setattr(MainWindow,'probe_hardware',lambda *a:None)
    window=MainWindow(settings)
    assert window.manager.limit==1 and window.manager.safe_mode
    assert not window.manager.high_priority
    page=window.settings_page
    assert page.safe.isChecked() and not page.performance.isChecked()
    page.performance.setChecked(True)
    assert not page.safe.isChecked() and not window.manager.safe_mode
    page.safe.setChecked(True)
    assert window.manager.safe_mode and not page.performance.isChecked()
    window.manager.running=True
    alerts=[];window.manager.safety_alert.connect(alerts.append)
    window.manager.observe_resources(91,30,now=0)
    window.manager.observe_resources(91,30,now=10)
    assert window.manager.paused and len(alerts)==1
    window.encoding.toggle_pause()
    assert not window.manager.paused
    close_window(window)


def test_low_ram_forces_single_job(tmp_path,monkeypatch):
    from app.core.queue import QueueManager,Job
    from app.core.worker import EncodeWorker
    from app.core.tv_rules import Options
    from tests_support import sample_media
    import psutil,threading
    from types import SimpleNamespace
    app=QApplication.instance() or QApplication([])
    manager=QueueManager();manager.limit=3;manager.running=True
    manager.jobs=[Job(tmp_path/f'{i}.mp4',Options()) for i in range(3)]
    for job in manager.jobs:job.media=sample_media(job.source)
    monkeypatch.setattr(psutil,'virtual_memory',lambda:SimpleNamespace(available=2*1024**3,percent=70))
    gate=threading.Event()
    monkeypatch.setattr(EncodeWorker,'run',lambda self:gate.wait(timeout=2))
    logs=[];manager.log.connect(lambda level,message,file,details:logs.append(message))
    manager.tick()
    assert sum(job.thread is not None for job in manager.jobs)==1
    assert any('below 3 GB' in message for message in logs)
    gate.set()
    for job in manager.jobs:
        if job.thread:job.thread.join(timeout=2)
    manager.stop_all()


def test_slow_temperature_sensor_does_not_delay_resource_guard(tmp_path,monkeypatch):
    import app.ui.main_window as module
    import threading
    monkeypatch.setenv('APPDATA',str(tmp_path))
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow,'probe_hardware',lambda *a:None)
    gate=threading.Event()
    def stalled_temperature():
        gate.wait(timeout=2)
        return None
    monkeypatch.setattr(module,'cpu_temperature',stalled_temperature)
    window=MainWindow(Settings());window.timer.stop()
    loop=QEventLoop()
    window.metrics_ready.connect(loop.quit)
    QTimer.singleShot(600,loop.quit)
    window.poll_metrics()
    try:
        loop.exec()
        assert window.last_metrics and not window.metric_busy
        assert window.temperature_busy  # CPU/RAM arrived while temperature was still blocked.
    finally:
        gate.set()
        close_window(window)


def test_hardware_page_matrix_controls_and_language_rebuild(tmp_path, monkeypatch):
    from hardware_support import matrix
    from app.ui.pages.fixer import selected
    monkeypatch.setenv('APPDATA', str(tmp_path))
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, 'probe_hardware', lambda *a: None)
    window = MainWindow(Settings())
    data = matrix('Intel', 'NVIDIA', 'CPU').to_dict()
    caps = dict(matrix=data, codecs={'h264_qsv':{'available':True, 'tested_options':[]}},
                qsv_device='qsv=hw,child_device_type=dxva2')
    try:
        window.set_capabilities(caps)
        window.navigate('Hardware')
        app.processEvents()
        assert window.hardware_page.matrix.encoders[0].available
        assert 'Build flags' in window.settings_page.capabilities.text()
        assert 'dxva2' in window.settings_page.capabilities.text()
        control = window.fixer.controls['hardware']
        assert not control.model().item(control.findData('AMD')).isEnabled()
        control.setCurrentIndex(control.findData('NVIDIA'))
        assert selected(control) == 'NVIDIA'
        window.change_language('fa')
        app.processEvents()
        window.navigate('Hardware')
        assert window.hardware_page.matrix
        assert window.manager.safe_mode and window.manager.limit == 1
    finally:
        close_window(window)


def test_hardware_scan_state_survives_language_rebuild(tmp_path, monkeypatch):
    from hardware_support import matrix
    monkeypatch.setenv('APPDATA', str(tmp_path))
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, 'probe_hardware', lambda *a: None)
    window = MainWindow(Settings())
    try:
        window.set_capabilities({'matrix': matrix('CPU').to_dict(), 'codecs':{}})
        window.cap_busy = window.manager.hardware_scanning = True
        window.change_language('fa')
        assert window.cap_busy and window.manager.hardware_scanning
        assert not window.hardware_page.scan_button.isEnabled()
        assert window.hardware_page.cancel_button.isEnabled()
    finally:
        close_window(window)
