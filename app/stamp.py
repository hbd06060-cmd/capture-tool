# -*- coding: utf-8 -*-
"""날짜 도장 · 워터마크 — 이미지 구석에 작은 글자를 새긴다.

문서에 붙였을 때 "언제 찍은 화면인지" 가 남도록 하기 위한 것이라,
가운데를 가리지 않고 오른쪽 아래에 작게 넣는다.
"""
import datetime

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter

MARGIN = 10          # 가장자리에서 띄우는 거리
PADDING_X = 9        # 글자 좌우 여백
PADDING_Y = 5        # 글자 위아래 여백
MIN_POINT = 9
MAX_POINT = 22


def today_text(when=None) -> str:
    """2026-08-25 14:30 형태. Windows 한국어 환경에서 strftime 이
    깨지는 일이 있어 직접 조립한다."""
    now = when or datetime.datetime.now()
    return "%04d-%02d-%02d %02d:%02d" % (now.year, now.month, now.day,
                                         now.hour, now.minute)


def font_point_for(image: QImage) -> int:
    """이미지 크기에 맞춰 읽을 만한 글자 크기를 고른다."""
    if image.isNull():
        return MIN_POINT
    base = min(image.width(), image.height())
    point = int(round(base / 45.0))
    return max(MIN_POINT, min(MAX_POINT, point))


def apply(image: QImage, text, corner="br", point=None,
          text_color="#ffffff", box_color=(0, 0, 0, 150)) -> QImage:
    """이미지 위에 반투명 상자와 글자를 새겨서 새 이미지를 돌려준다.

    corner 는 br(오른쪽 아래) · bl · tr · tl 중 하나.
    """
    text = (text or "").strip()
    if image.isNull() or not text:
        return image

    out = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    font = QFont()
    font.setPointSize(point or font_point_for(image))
    font.setBold(True)

    metrics = QFontMetricsF(font)
    text_width = metrics.horizontalAdvance(text)
    text_height = metrics.height()
    box_width = text_width + PADDING_X * 2
    box_height = text_height + PADDING_Y * 2

    # 상자가 이미지보다 크면 넣지 않는다
    if box_width > out.width() or box_height > out.height():
        return image

    right = out.width() - MARGIN - box_width
    bottom = out.height() - MARGIN - box_height
    spots = {"br": (right, bottom), "bl": (MARGIN, bottom),
             "tr": (right, MARGIN), "tl": (MARGIN, MARGIN)}
    x, y = spots.get(corner, spots["br"])
    box = QRectF(max(0.0, x), max(0.0, y), box_width, box_height)

    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    painter.setPen(Qt.NoPen)
    if isinstance(box_color, (tuple, list)):
        painter.setBrush(QColor(*box_color))
    else:
        painter.setBrush(QColor(box_color))
    painter.drawRoundedRect(box, 4, 4)
    painter.setFont(font)
    painter.setPen(QColor(text_color))
    painter.drawText(box, Qt.AlignCenter, text)
    painter.end()
    return out
