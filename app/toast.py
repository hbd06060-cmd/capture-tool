# -*- coding: utf-8 -*-
"""마우스 옆에 잠깐 떴다 사라지는 작은 알림 ("#1E88E5 복사했습니다").

트레이 알림 풍선은 느리고 알림 센터에 쌓이므로, 짧은 확인 메시지는
이것으로 보여 준다. 클릭을 가로채지 않는다.
"""
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

_current = None


class Toast(QWidget):
    def __init__(self, text, color=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.Tool | Qt.WindowTransparentForInput |
                         Qt.NoDropShadowWindowHint)
        self.text = text
        self.swatch = color
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.font = QFont()
        self.font.setPointSize(10)
        self.font.setBold(True)
        metrics = QFontMetrics(self.font)
        extra = 22 if color is not None else 0
        self.setFixedSize(metrics.horizontalAdvance(text) + 28 + extra,
                          metrics.height() + 16)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.close)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor(255, 255, 255, 50), 1))
        painter.setBrush(QColor(20, 22, 27, 235))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)
        x = 14
        if self.swatch is not None:
            painter.setPen(QPen(QColor(255, 255, 255, 120), 1))
            painter.setBrush(self.swatch)
            side = self.height() - 16
            painter.drawRoundedRect(QRect(x, 8, side, side), 3, 3)
            x += side + 8
        painter.setFont(self.font)
        painter.setPen(QColor(240, 244, 250))
        painter.drawText(QRect(x, 0, self.width() - x - 10, self.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, self.text)
        painter.end()


def show(text, ms=1500, color=None, at=None):
    """알림을 띄운다. 이전 알림이 있으면 바꿔치기한다."""
    global _current
    try:
        if _current is not None:
            _current.close()
    except RuntimeError:
        pass
    toast = Toast(text, color)
    point = QPoint(at) if at is not None else QCursor.pos()
    toast.move(point + QPoint(16, 20))
    toast.show()
    toast.timer.start(max(400, int(ms)))
    toast.destroyed.connect(lambda *_: _forget(toast))
    _current = toast
    return toast


def _forget(toast):
    global _current
    if _current is toast:
        _current = None
