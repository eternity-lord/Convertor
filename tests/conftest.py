import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


import os
import pytest


@pytest.fixture(scope='session')
def qt_application():
    """One strong reference across core/GUI tests: never destroy/recreate Qt's singleton."""
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    try:
        from PySide6.QtWidgets import QApplication
        application_type = QApplication
    except ImportError:
        from PySide6.QtCore import QCoreApplication
        application_type = QCoreApplication
    application = application_type.instance() or application_type([])
    yield application
