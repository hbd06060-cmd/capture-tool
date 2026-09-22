# -*- coding: utf-8 -*-
"""프로그램 아이콘을 코드로 그린다 (외부 파일 불필요)."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap


def _draw(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor("#3b82f6"))
    gradient.setColorAt(1.0, QColor("#1d4ed8"))
    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)
    radius = size * 0.22
    painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

    # 캡처 프레임 모서리
    margin = size * 0.24
    arm = size * 0.15
    pen = QPen(QColor(255, 255, 255), max(1.6, size * 0.075))
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    left, top = margin, margin
    right, bottom = size - margin, size - margin
    painter.drawLine(left, top + arm, left, top)
    painter.drawLine(left, top, left + arm, top)
    painter.drawLine(right - arm, top, right, top)
    painter.drawLine(right, top, right, top + arm)
    painter.drawLine(left, bottom - arm, left, bottom)
    painter.drawLine(left, bottom, left + arm, bottom)
    painter.drawLine(right - arm, bottom, right, bottom)
    painter.drawLine(right, bottom, right, bottom - arm)

    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#ef4444"))
    dot = size * 0.13
    painter.drawEllipse(QRectF(size / 2 - dot / 2, size / 2 - dot / 2, dot, dot))
    painter.end()
    return pixmap


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_draw(size))
    return icon


def icon_pixmap(size=256) -> QPixmap:
    return _draw(size)
