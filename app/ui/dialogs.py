from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QDialog, QVBoxLayout, QPlainTextEdit
from .widgets.common import button, label


def folder(parent, initial=''):
    return QFileDialog.getExistingDirectory(parent, 'Select folder', initial,
                                             QFileDialog.DontUseNativeDialog)


def save_file(parent, name, filters):
    return QFileDialog.getSaveFileName(parent, 'Export', name, filters,
                                      options=QFileDialog.DontUseNativeDialog)[0]


def details(parent, title, text):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(760, 430)
    box = QVBoxLayout(dialog)
    box.addWidget(label(title, 'title'))
    edit = QPlainTextEdit(text)
    edit.setReadOnly(True)
    box.addWidget(edit)
    from PySide6.QtWidgets import QApplication
    box.addWidget(button('Copy', lambda: QApplication.clipboard().setText(text)))
    box.addWidget(button('Close', dialog.accept))
    dialog.exec()


def open_folder(path):
    from pathlib import Path
    target = Path(path)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(target if target.is_dir() else target.parent)))
