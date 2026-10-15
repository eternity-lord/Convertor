"""Glass-style, keyboard accessible before/after wipe. No rendering work in paintEvent."""
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QPainter, QColor, QImage, QPen
from PySide6.QtWidgets import QWidget, QVBoxLayout, QSlider
from .common import label


class SplitCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.before, self.after = QImage(), QImage()
        self.position = 50
        self.setMinimumSize(280, 180)
        self.setAccessibleName('Before / after enhancement comparison')

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#09080d'))
        if self.after.isNull():
            painter.setPen(QColor('#ac93aa'))
            painter.drawText(self.rect(), Qt.AlignCenter, 'Before / After · Preview')
            return
        size = self.after.size().scaled(self.size(), Qt.KeepAspectRatio)
        target = QRect((self.width()-size.width())//2, (self.height()-size.height())//2, size.width(), size.height())
        # Both images occupy the same display canvas even for explicit upscaling.
        painter.drawImage(target, self.before)
        wipe = target.left() + round(target.width()*self.position/100)
        painter.save()
        painter.setClipRect(QRect(wipe, target.top(), target.right()-wipe+1, target.height()))
        painter.drawImage(target, self.after)
        painter.restore()
        painter.setPen(QPen(QColor('#cc9dc5'), 2))
        painter.drawLine(wipe, target.top(), wipe, target.bottom())
        painter.setPen(QColor('#f1deed'))
        painter.drawText(target.adjusted(10, 8, -10, -8), Qt.AlignTop | Qt.AlignLeft, 'Before')
        painter.drawText(target.adjusted(10, 8, -10, -8), Qt.AlignTop | Qt.AlignRight, 'After')


class SplitPreview(QWidget):
    def __init__(self):
        super().__init__()
        box = QVBoxLayout(self); box.setContentsMargins(0,0,0,0)
        self.canvas = SplitCanvas()
        box.addWidget(self.canvas)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0,100); self.slider.setValue(50)
        self.slider.setAccessibleName('Before / after split position')
        self.slider.setToolTip('Move the split. Arrow keys also work. Images are resized only for display.')
        self.slider.setStyleSheet('QSlider::groove:horizontal{height:5px;background:#2e2432;border-radius:2px;}'
                                 'QSlider::handle:horizontal{width:15px;margin:-5px 0;background:#b28bac;border-radius:7px;}')
        self.slider.valueChanged.connect(self.move_split)
        box.addWidget(self.slider)
        self.caption = label('Preview uses the same filter chain; display is limited to 960 px. '
                             'Temporal filters start at the chosen seek point, not the beginning of the entire movie.', 'muted')
        box.addWidget(self.caption)

    def move_split(self, value):
        self.canvas.position = value
        self.canvas.update()

    def set_images(self, before, after):
        a, b = QImage.fromData(before, 'PNG'), QImage.fromData(after, 'PNG')
        if a.isNull() or b.isNull():
            raise ValueError('Preview frames could not be decoded')
        self.canvas.before, self.canvas.after = a, b
        self.canvas.update()
