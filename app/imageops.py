# -*- coding: utf-8 -*-
"""이미지 픽셀 가공 — 모자이크 / 흐리게."""
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QTransform


def _clamped(image: QImage, rect: QRect) -> QRect:
    return rect.normalized().intersected(image.rect())


def mosaic(image: QImage, rect: QRect, block: int = 12) -> QImage:
    """지정 영역을 모자이크 처리한 새 이미지를 반환."""
    area = _clamped(image, rect)
    if area.width() < 2 or area.height() < 2:
        return image
    block = max(2, int(block))
    patch = image.copy(area)
    small_w = max(1, patch.width() // block)
    small_h = max(1, patch.height() // block)
    small = patch.scaled(small_w, small_h, Qt.IgnoreAspectRatio,
                         Qt.FastTransformation)
    blocky = small.scaled(patch.width(), patch.height(), Qt.IgnoreAspectRatio,
                          Qt.FastTransformation)
    out = image.copy()
    painter = QPainter(out)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
    painter.drawImage(area.topLeft(), blocky)
    painter.end()
    return out


def blur(image: QImage, rect: QRect, strength: int = 8) -> QImage:
    """지정 영역을 흐리게 처리한 새 이미지를 반환.

    축소 후 부드럽게 확대하는 방식을 반복해 가우시안에 가까운 결과를 낸다.
    """
    area = _clamped(image, rect)
    if area.width() < 2 or area.height() < 2:
        return image
    strength = max(2, int(strength))
    patch = image.copy(area)
    work = patch
    for _ in range(3):
        w = max(1, work.width() // strength)
        h = max(1, work.height() // strength)
        work = work.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        work = work.scaled(patch.width(), patch.height(), Qt.IgnoreAspectRatio,
                           Qt.SmoothTransformation)
        strength = max(2, strength // 2)
    out = image.copy()
    painter = QPainter(out)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    painter.drawImage(area.topLeft(), work)
    painter.end()
    return out


# ------------------------------------------------------ 이미지 자체 변형
def rotate(image: QImage, degrees: int) -> QImage:
    """90도 단위로 돌린다. degrees 는 90 / 180 / 270 / -90."""
    if image.isNull():
        return image
    turn = int(degrees) % 360
    if turn == 0:
        return image.copy()
    transform = QTransform().rotate(turn)
    return image.transformed(transform, Qt.SmoothTransformation)


def flip(image: QImage, horizontal=True) -> QImage:
    """좌우(또는 위아래)로 뒤집는다."""
    if image.isNull():
        return image
    return image.mirrored(bool(horizontal), not bool(horizontal))


def add_border(image: QImage, margin: int = 0, line: int = 0,
               margin_color="#ffffff", line_color="#c8ced8") -> QImage:
    """바깥에 여백을 두르고, 원하면 그 안쪽에 테두리 선을 그린다.

    흰 배경 화면을 캡처하면 어디까지가 그림인지 안 보이는데,
    얇은 테두리를 두르면 문서에 붙였을 때 훨씬 또렷해진다.
    """
    if image.isNull():
        return image
    margin = max(0, int(margin))
    line = max(0, int(line))
    if margin == 0 and line == 0:
        return image.copy()
    pad = margin + line
    out = QImage(image.width() + pad * 2, image.height() + pad * 2,
                 QImage.Format_ARGB32_Premultiplied)
    out.fill(QColor(margin_color))
    painter = QPainter(out)
    if line > 0:
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(line_color))
        painter.drawRect(margin, margin,
                         out.width() - margin * 2, out.height() - margin * 2)
    painter.drawImage(pad, pad, image)
    painter.end()
    return out


def resized(image: QImage, width: int, height: int) -> QImage:
    """지정한 크기로 매끄럽게 줄이거나 늘린다."""
    if image.isNull():
        return image
    width = max(1, int(width))
    height = max(1, int(height))
    if width == image.width() and height == image.height():
        return image.copy()
    return image.scaled(width, height, Qt.IgnoreAspectRatio,
                        Qt.SmoothTransformation)
