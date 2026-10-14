import sys
import pytest
from app.core.process import capture, ToolError


def test_pipes_drained_without_deadlock():
    text=capture([sys.executable,'-c',"import sys;sys.stderr.write('x'*2000000);print('ok')"],timeout=10)
    assert text.strip()=='ok'


def test_capture_cap():
    with pytest.raises(ToolError,match='bounded'):
        capture([sys.executable,'-c',"print('x'*1000000)"],limit=4096)


def test_probe_timeout():
    with pytest.raises(ToolError,match='timed out'):
        capture([sys.executable,'-c','import time; time.sleep(10)'],timeout=.1)
