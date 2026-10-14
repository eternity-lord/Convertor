from collections import deque
from PySide6.QtCore import Qt, Signal, Property, QPropertyAnimation, QEasingCurve, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QLinearGradient, QRadialGradient, QPen
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QGraphicsDropShadowEffect, QScrollArea, QFrame, QSizeGrip)
from app.i18n import tr


def label(text, role=None):
    widget = QLabel(tr(text))
    widget.setWordWrap(True)
    if role:
        widget.setProperty('role', role)
    return widget


def button(text, callback=None, primary=False):
    widget = QPushButton(tr(text).replace('&', '&&'))
    widget.setCursor(Qt.PointingHandCursor)
    if primary:
        widget.setProperty('primary', True)
        shadow = QGraphicsDropShadowEffect(widget)
        shadow.setColor(QColor('#886581'))
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 6)
        widget.setGraphicsEffect(shadow)
    if callback:
        widget.clicked.connect(callback)
    return widget


def row(*widgets):
    layout = QHBoxLayout()
    layout.setSpacing(10)
    for widget in widgets:
        if isinstance(widget, QWidget):
            layout.addWidget(widget)
        else:
            layout.addStretch()
    return layout


class Card(QWidget):
    def __init__(self, title='', glow=False):
        super().__init__()
        self.glow = glow
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(20, 18, 20, 18)
        self.box.setSpacing(12)
        if title:
            self.box.addWidget(label(title, 'title'))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 21, 21)
        painter.fillPath(path, QColor('#0b0a0d'))
        if self.glow:
            gradient = QRadialGradient(QPointF(self.width() * .75, self.height()), max(self.width(), self.height()) * .85)
            gradient.setColorAt(0, QColor('#594052'))
            gradient.setColorAt(.45, QColor('#221922'))
            gradient.setColorAt(1, QColor('#0b0a0d'))
            painter.fillPath(path, gradient)
        painter.setPen(QPen(QColor(255, 255, 255, 20), 1))
        painter.drawPath(path)


class Toggle(QWidget):
    toggled = Signal(bool)

    def __init__(self, text='', checked=False):
        super().__init__()
        self.checked = checked
        self._position = float(checked)
        self.animation = QPropertyAnimation(self, b'position', self)
        self.animation.setDuration(150)
        self.animation.setEasingCurve(QEasingCurve.InOutCubic)
        self.setMinimumHeight(32)
        self.setMinimumWidth(70)
        self.setCursor(Qt.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 58, 0)
        self.caption = label(text)
        layout.addWidget(self.caption)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(tr(text))

    def isChecked(self):
        return self.checked

    def setChecked(self, value):
        if self.checked == bool(value):
            return
        self.checked = bool(value)
        self.animation.stop()
        self.animation.setStartValue(self._position)
        self.animation.setEndValue(float(value))
        self.animation.start()
        self.toggled.emit(self.checked)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setChecked(not self.checked)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Space, Qt.Key_Return):
            self.setChecked(not self.checked)
        else:
            super().keyPressEvent(event)

    def getPosition(self):
        return self._position

    def setPosition(self, value):
        self._position = value
        self.update()

    position = Property(float, getPosition, setPosition)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        x, y = self.width() - 46, (self.height() - 23) / 2
        p.setPen(QPen(QColor('#80627c' if self.checked else '#3a303c'), 1))
        p.setBrush(QColor('#71586e' if self.checked else '#1b161e'))
        p.drawRoundedRect(QRectF(x, y, 43, 23), 11.5, 11.5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor('#e6cde2' if self.checked else '#8b7d8f'))
        p.drawEllipse(QRectF(x + 4 + self._position * 20, y + 4, 15, 15))


class Pills(QWidget):
    selected = Signal(str)

    def __init__(self, values, selected=None):
        super().__init__()
        self.value = selected or values[0]
        self.buttons = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        for value in values:
            btn = button(value)
            btn.setCheckable(True)
            btn.setChecked(value == self.value)
            btn.setStyleSheet('padding: 6px 10px; border-radius: 14px; font-size: 11px;')
            btn.clicked.connect(lambda checked=False, v=value: self.choose(v))
            layout.addWidget(btn)
            self.buttons[value] = btn
        layout.addStretch()

    def choose(self, value):
        self.value = value
        for key, btn in self.buttons.items():
            btn.setChecked(key == value)
        self.selected.emit(value)


class Chart(QWidget):
    def __init__(self, capacity=180):
        super().__init__()
        self.values = deque(maxlen=capacity)
        self.setMinimumHeight(150)

    def append(self, value):
        self.values.append(float(value))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(35, 16, max(1, self.width() - 55), max(1, self.height() - 44))
        vals = list(self.values)
        maximum = max(1, max(vals, default=1)) * 1.15
        p.setPen(QPen(QColor('#241a25'), 1))
        for i in range(5):
            y = rect.top() + rect.height() * i / 4
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            p.setPen(QColor('#817383'))
            p.drawText(QRectF(0, y - 6, 30, 14), Qt.AlignRight, f'{maximum * (1-i/4):.1f}')
            p.setPen(QColor('#241a25'))
        if len(vals) < 2:
            p.setPen(QColor('#766977'))
            p.drawText(rect, Qt.AlignCenter, tr('Ready') + ' · live telemetry')
            return
        points = [QPointF(rect.left() + i / (len(vals) - 1) * rect.width(),
                          rect.bottom() - v / maximum * rect.height()) for i, v in enumerate(vals)]
        line = QPainterPath(points[0])
        for point in points[1:]:
            line.lineTo(point)
        area = QPainterPath(line)
        area.lineTo(rect.bottomRight())
        area.lineTo(rect.bottomLeft())
        area.closeSubpath()
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0, QColor(203, 168, 198, 120))
        gradient.setColorAt(1, QColor(113, 88, 110, 0))
        p.fillPath(area, gradient)
        p.setPen(QPen(QColor('#dabbd4'), 1.6))
        p.drawPath(line)
        point = points[-1]
        p.setPen(QPen(QColor('#a78b9f'), 1, Qt.DashLine))
        p.drawLine(point, QPointF(point.x(), rect.bottom()))
        glow = QRadialGradient(point, 15)
        glow.setColorAt(0, QColor('#ffd3f4'))
        glow.setColorAt(1, QColor(255, 211, 244, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(point, 15, 15)
        p.setBrush(QColor('#ffd3f4'))
        p.drawEllipse(point, 3.5, 3.5)
        p.setPen(QColor('#ad98ac'))
        p.drawText(QRectF(rect.left(), rect.bottom() + 10, rect.width(), 20), Qt.AlignLeft, 'Earlier')
        p.drawText(QRectF(rect.left(), rect.bottom() + 10, rect.width(), 20), Qt.AlignRight, f'{vals[-1]:.2f}  now')


class GlassWindow(QWidget):
    def __init__(self, title, minimum=(1100, 720)):
        super().__init__()
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(*minimum)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(13, 7, 13, 12)
        self.root.setSpacing(0)
        self.titlebar = QWidget()
        self.titlebar.setFixedHeight(29)
        bar = QHBoxLayout(self.titlebar)
        bar.setContentsMargins(12, 0, 7, 0)
        title_label = label(title, 'muted')
        title_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        bar.addWidget(title_label)
        bar.addStretch()
        for symbol, action in [('−', self.showMinimized), ('□', self.toggle_max), ('×', self.close)]:
            btn = button(symbol, action)
            btn.setFixedSize(28, 25)
            btn.setStyleSheet('padding:0; border:0; border-radius:10px;')
            bar.addWidget(btn)
        self.titlebar.mousePressEvent = self.drag_window
        self.titlebar.mouseDoubleClickEvent = lambda e: self.toggle_max()
        self.root.addWidget(self.titlebar)
        self.grip = QSizeGrip(self)
        self.grip.setFixedSize(17, 17)

    def toggle_max(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def drag_window(self, event):
        if event.button() == Qt.LeftButton and self.windowHandle():
            self.windowHandle().startSystemMove()

    def resizeEvent(self, event):
        self.grip.move(self.width() - 22, self.height() - 22)
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        pos = event.position()
        edges = Qt.Edges()
        if pos.x() < 10:
            edges |= Qt.LeftEdge
        if pos.x() > self.width() - 10:
            edges |= Qt.RightEdge
        if pos.y() < 10:
            edges |= Qt.TopEdge
        if pos.y() > self.height() - 10:
            edges |= Qt.BottomEdge
        if edges and self.windowHandle():
            self.windowHandle().startSystemResize(edges)
        else:
            super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        gradient = QLinearGradient(rect.topLeft(), QPointF(self.width() * .65, self.height() * .85))
        gradient.setColorAt(0, QColor('#5d535b'))
        gradient.setColorAt(.55, QColor('#302830'))
        gradient.setColorAt(1, QColor('#0f0d10'))
        p.setBrush(gradient)
        p.setPen(QPen(QColor(255, 255, 255, 26), 1))
        p.drawRoundedRect(rect, 28, 28)


def scroll(widget):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    return area
