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
    assert w.daily['files']==1
    assert (root/'_TV'/'test_TV.mp4').exists()
    close_window(w)
    app.processEvents()
