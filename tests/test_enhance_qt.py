"""Native Qt acceptance for reusable quality controls, cancellation and queue isolation."""
import threading
from types import SimpleNamespace
import pytest
try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    pytest.skip('Native Qt GUI libraries are not installed',allow_module_level=True)
from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtGui import QImage
from app.ui.widgets.enhance_card import EnhanceCard
from app.core.queue import QueueManager
from app.core.tv_rules import Options
from app.core.process import ProbeSkipped
from test_enhance import caps


@pytest.fixture(autouse=True)
def keep_application(qt_application):
    return qt_application


def test_quality_controls_roundtrip_and_capability_disable_reasons():
    card=EnhanceCard()
    try:
        assert card.settings()['level']=='Off'
        card.set_capabilities(caps())
        assert card.pills.buttons['Balanced'].isEnabled()
        card.pills.choose('Custom');card.values['denoise'].setValue(2.3)
        card.loudnorm.setChecked(True)
        value=card.settings()
        assert value['level']=='Custom' and value['denoise']==2.3 and value['loudnorm']
        second=EnhanceCard(value)
        assert second.settings()==value
        second.dispose();second.deleteLater()
        proof=caps();proof['enhancement']['hqdn3d']={'available':False,'reason':'runtime failed'}
        proof['enhancement']['color_tags']={'available':False,'reason':'setparams failed'}
        card.set_capabilities(proof)
        assert not card.rows['denoise'].isEnabled() and 'runtime failed' in card.rows['denoise'].toolTip()
        assert not card.pills.buttons['Balanced'].isEnabled()
        assert 'setparams failed' in card.pills.buttons['Balanced'].toolTip()
    finally:
        card.dispose();card.deleteLater()


def test_preview_runs_off_ui_thread_cancel_unlocks_and_does_not_mutate_provider(tmp_path,monkeypatch):
    import app.ui.widgets.enhance_card as module
    from tests_support import sample_media
    path=tmp_path/'source.mp4';path.touch()
    provider=Options();manager=QueueManager()
    card=EnhanceCard({'level':'Balanced'},lambda:provider,manager)
    card.source.setText(str(path));card.set_capabilities(caps())
    entered=threading.Event();threads=[]
    monkeypatch.setattr(module,'probe',lambda *a,**kw:sample_media(path))
    def delayed(*args):
        threads.append(threading.get_ident());entered.set()
        args[4].wait(timeout=3)
        raise ProbeSkipped('cancelled')
    monkeypatch.setattr(module,'render_preview',delayed)
    loop=QEventLoop();card.preview_failed.connect(loop.quit)
    heartbeats=[]
    try:
        card.start_preview()
        assert entered.wait(timeout=1) and card.busy and manager.preview_lock.locked()
        assert provider.enhance=={'level':'Off'}
        manager.start();assert not manager.running
        QTimer.singleShot(20,lambda:heartbeats.append(True))
        QTimer.singleShot(50,card.dispose)
        QTimer.singleShot(3000,loop.quit)
        loop.exec()
        card.preview_thread.join(timeout=1)
        QApplication.instance().processEvents()
        assert heartbeats and threads[0]!=threading.get_ident()
        assert not card.busy and not manager.preview_lock.locked()
        assert 'cancelled' in card.notes.text()
    finally:
        card.dispose()
        if card.preview_thread:card.preview_thread.join(timeout=4)
        card.deleteLater()


def test_split_preview_paints_and_preserves_exact_result_details(tmp_path):
    card=EnhanceCard()
    image=QImage(32,18,QImage.Format_RGB32);image.fill(Qt.red)
    path=tmp_path/'preview.png';assert image.save(str(path),'PNG')
    data=path.read_bytes()
    result=SimpleNamespace(before=data,after=data,plan=SimpleNamespace(enhancement={'estimated_speed':None},encoding_status='CPU enhancement',command=['ffmpeg','output.mp4']),elapsed=.1,clip='',commands=[])
    try:
        card.finished(result);card.show()
        card.comparison.slider.setValue(75)
        QApplication.instance().processEvents()
        assert card.comparison.canvas.position==75
        assert not card.comparison.canvas.after.isNull()
        assert card.last_result is result
        assert 'unknown (not measured)' in card.notes.text()
        assert not card.grab().isNull()
    finally:
        card.dispose();card.close();card.deleteLater()


def test_fixer_hqx_default_off_and_enhancement_survives_language_rebuild(tmp_path,monkeypatch):
    from app.ui.main_window import MainWindow
    from app.core.settings import Settings
    from test_qt import close_window
    monkeypatch.setenv('APPDATA',str(tmp_path))
    monkeypatch.setattr(MainWindow,'probe_hardware',lambda *a:None)
    window=MainWindow(Settings())
    try:
        assert window.fixer.enhance_card.settings()['level']=='Off'
        assert window.hqx.enhance_card.settings()['level']=='Off'
        window.fixer.enhance_card.pills.choose('Balanced')
        window.change_language('fa')
        QApplication.instance().processEvents()
        assert window.fixer.enhance_card.settings()['level']=='Balanced'
        assert window.hqx.enhance_card.settings()['level']=='Off'
        assert window.manager.safe_mode and window.manager.limit==1
    finally:
        close_window(window)
