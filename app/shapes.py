# -*- coding: utf-8 -*-
"""편집기에서 사용하는 도형(주석) 객체."""
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QPainter,
                           QPainterPath, QPen, QPolygonF)

KIND_LABELS = {
    "rect": "사각형",
    "ellipse": "타원",
    "line": "직선",
    "arrow": "화살표",
    "pen": "펜",
    "highlight": "형광펜",
    "text": "글자",
    "number": "번호",
}

BOX_KINDS = ("rect", "ellipse")
LINE_KINDS = ("line", "arrow")
FREE_KINDS = ("pen", "highlight")
FONT_KINDS = ("text", "number")

MIN_SIZE = 6.0

# 조절점 이름 → (가로 위치, 세로 위치). 0=왼쪽/위, 0.5=가운데, 1=오른쪽/아래
HANDLE_SPOTS = {
    "tl": (0.0, 0.0), "t": (0.5, 0.0), "tr": (1.0, 0.0),
    "l": (0.0, 0.5), "r": (1.0, 0.5),
    "bl": (0.0, 1.0), "b": (0.5, 1.0), "br": (1.0, 1.0),
}

CORNERS = ("tl", "tr", "bl", "br")

# 조절점을 잡았을 때 반대편에서 고정되는 점
OPPOSITE = {"tl": "br", "tr": "bl", "bl": "tr", "br": "tl",
            "t": "b", "b": "t", "l": "r", "r": "l"}


def box_handles(rect: QRectF) -> dict:
    """네모 하나에 대한 8개의 조절점 위치."""
    spots = {}
    for name, (fx, fy) in HANDLE_SPOTS.items():
        spots[name] = QPointF(rect.left() + rect.width() * fx,
                              rect.top() + rect.height() * fy)
    return spots


def resized_box(old: QRectF, name, point, keep_ratio=False):
    """조절점을 point 로 끌었을 때의 새 네모. 못 만들면 None."""
    if name not in HANDLE_SPOTS:
        return None
    left, top = old.left(), old.top()
    right, bottom = old.right(), old.bottom()
    fx, fy = HANDLE_SPOTS[name]

    if keep_ratio and name in CORNERS and old.width() > MIN_SIZE \
            and old.height() > MIN_SIZE:
        # 모서리를 Shift 와 함께 끌면 가로세로 비율을 지킨다
        ratio = old.height() / old.width()
        anchor_x = right if fx == 0.0 else left
        anchor_y = bottom if fy == 0.0 else top
        dx = abs(point.x() - anchor_x)
        dy = abs(point.y() - anchor_y)
        size = max(dx, dy / ratio if ratio else dx)
        new_x = anchor_x - size if fx == 0.0 else anchor_x + size
        new_y = anchor_y - size * ratio if fy == 0.0 else \
            anchor_y + size * ratio
        point = QPointF(new_x, new_y)

    # 끄는 변이 반대편 변을 넘어가지 못하게 막는다 (도형이 뒤집히거나
    # 엉뚱한 곳으로 튀지 않도록)
    if fx == 0.0:
        left = min(point.x(), right - MIN_SIZE)
    elif fx == 1.0:
        right = max(point.x(), left + MIN_SIZE)
    if fy == 0.0:
        top = min(point.y(), bottom - MIN_SIZE)
    elif fy == 1.0:
        bottom = max(point.y(), top + MIN_SIZE)

    return QRectF(QPointF(left, top), QPointF(right, bottom))


class Shape:
    def __init__(self, kind, color, width, fill=False, font_size=24,
                 text="", number=1, bold=True, italic=False, underline=False):
        self.kind = kind
        self.color = QColor(color)
        self.width = int(width)
        self.fill = bool(fill)
        self.font_size = int(font_size)
        self.text = text
        self.number = int(number)
        self.bold = bool(bold)
        self.italic = bool(italic)
        self.underline = bool(underline)
        self.points = []

    # ------------------------------------------------------------- 복제
    def clone(self):
        s = Shape(self.kind, self.color, self.width, self.fill,
                  self.font_size, self.text, self.number,
                  self.bold, self.italic, self.underline)
        s.points = [QPointF(p) for p in self.points]
        return s

    def text_font(self) -> QFont:
        font = QFont()
        font.setPointSizeF(self.font_size)
        font.setBold(self.bold)
        font.setItalic(self.italic)
        font.setUnderline(self.underline)
        return font

    # ------------------------------------------------------------- 조작
    def translate(self, dx, dy):
        self.points = [QPointF(p.x() + dx, p.y() + dy) for p in self.points]

    def map_points(self, func):
        """이미지를 돌리거나 뒤집을 때 주석도 같이 따라가게 한다.

        func(x, y) 는 새 좌표 (x, y) 를 돌려주는 함수다.
        """
        self.points = [QPointF(*func(p.x(), p.y())) for p in self.points]

    def is_valid(self):
        if self.kind == "text":
            return bool(self.text.strip()) and len(self.points) >= 1
        if self.kind == "number":
            return len(self.points) >= 1
        if self.kind in ("pen", "highlight"):
            return len(self.points) >= 2
        if len(self.points) < 2:
            return False
        rect = QRectF(self.points[0], self.points[-1]).normalized()
        if self.kind in ("line", "arrow"):
            return (abs(rect.width()) + abs(rect.height())) > 3
        return rect.width() > 2 and rect.height() > 2

    # ------------------------------------------------------------- 영역
    def bounds(self) -> QRectF:
        if not self.points:
            return QRectF()
        if self.kind == "text":
            font = self.text_font()
            metrics = QFontMetricsF(font)
            lines = (self.text or " ").split("\n")
            width = max(metrics.horizontalAdvance(line) for line in lines)
            height = metrics.height() * len(lines)
            return QRectF(self.points[0],
                          QPointF(self.points[0].x() + width + 8,
                                  self.points[0].y() + height + 6))
        if self.kind == "number":
            r = self._badge_radius()
            c = self.points[0]
            return QRectF(c.x() - r, c.y() - r, r * 2, r * 2)
        xs = [p.x() for p in self.points]
        ys = [p.y() for p in self.points]
        rect = QRectF(QPointF(min(xs), min(ys)), QPointF(max(xs), max(ys)))
        pad = self.width / 2.0 + 2
        return rect.adjusted(-pad, -pad, pad, pad)

    def contains(self, point) -> bool:
        return self.bounds().adjusted(-3, -3, 3, 3).contains(QPointF(point))

    def _badge_radius(self):
        return max(12.0, self.font_size * 0.85)

    # --------------------------------------------------- 크기 조절점
    def raw_bounds(self) -> QRectF:
        """선 굵기 여백을 빼고, 실제로 찍은 점만으로 만든 네모."""
        if not self.points:
            return QRectF()
        xs = [p.x() for p in self.points]
        ys = [p.y() for p in self.points]
        return QRectF(QPointF(min(xs), min(ys)), QPointF(max(xs), max(ys)))

    def handles(self) -> dict:
        """끌어서 크기를 바꿀 수 있는 점들. {이름: 위치}"""
        if len(self.points) < 2:
            return {}
        if self.kind in LINE_KINDS:
            return {"p0": QPointF(self.points[0]),
                    "p1": QPointF(self.points[-1])}
        if self.kind in BOX_KINDS:
            rect = self._rect()
        elif self.kind in FREE_KINDS:
            rect = self.raw_bounds()
        else:
            return {}
        if rect.width() <= 0 and rect.height() <= 0:
            return {}
        return box_handles(rect)

    def resize(self, name, point, keep_ratio=False):
        """조절점 하나를 point 자리로 끌었을 때 도형을 다시 맞춘다."""
        point = QPointF(point)
        if self.kind in LINE_KINDS:
            if name == "p0":
                self.points[0] = point
            elif name == "p1":
                self.points[-1] = point
            return
        if self.kind in BOX_KINDS:
            old = self._rect()
        elif self.kind in FREE_KINDS:
            old = self.raw_bounds()
        else:
            return
        new = resized_box(old, name, point, keep_ratio)
        if new is None:
            return
        if self.kind in BOX_KINDS:
            self.points = [QPointF(new.topLeft()), QPointF(new.bottomRight())]
            return
        # 펜·형광펜은 찍은 점 전체를 새 네모에 맞춰 늘린다
        sx = new.width() / old.width() if old.width() > MIN_SIZE else 1.0
        sy = new.height() / old.height() if old.height() > MIN_SIZE else 1.0
        ox, oy = old.left(), old.top()
        nx, ny = new.left(), new.top()
        self.points = [QPointF(nx + (p.x() - ox) * sx,
                               ny + (p.y() - oy) * sy) for p in self.points]

    def scale_font(self, factor):
        """글자·번호처럼 크기가 글꼴로 정해지는 도형용."""
        self.font_size = max(8, min(200, int(round(self.font_size * factor))))

    # ------------------------------------------------------------- 그리기
    def draw(self, painter: QPainter):
        if not self.points:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            getattr(self, "_draw_" + self.kind)(painter)
        except AttributeError:
            pass
        painter.restore()

    def _pen(self, width=None, alpha=None):
        color = QColor(self.color)
        if alpha is not None:
            color.setAlpha(alpha)
        pen = QPen(color, width if width is not None else self.width)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        return pen

    def _rect(self):
        return QRectF(self.points[0], self.points[-1]).normalized()

    def _draw_rect(self, painter):
        painter.setPen(self._pen())
        if self.fill:
            fill = QColor(self.color)
            fill.setAlpha(60)
            painter.setBrush(QBrush(fill))
        else:
            painter.setBrush(Qt.NoBrush)
        painter.drawRect(self._rect())

    def _draw_ellipse(self, painter):
        painter.setPen(self._pen())
        if self.fill:
            fill = QColor(self.color)
            fill.setAlpha(60)
            painter.setBrush(QBrush(fill))
        else:
            painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(self._rect())

    def _draw_line(self, painter):
        painter.setPen(self._pen())
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(self.points[0], self.points[-1])

    def _draw_arrow(self, painter):
        start, end = self.points[0], self.points[-1]
        dx, dy = end.x() - start.x(), end.y() - start.y()
        length = math.hypot(dx, dy)
        if length < 1:
            return
        head = max(10.0, self.width * 3.4)
        head = min(head, length * 0.55)
        angle = math.atan2(dy, dx)
        spread = math.radians(24)
        base = QPointF(end.x() - head * math.cos(angle),
                       end.y() - head * math.sin(angle))
        painter.setPen(self._pen())
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(start, base)
        left = QPointF(end.x() - head * math.cos(angle - spread),
                       end.y() - head * math.sin(angle - spread))
        right = QPointF(end.x() - head * math.cos(angle + spread),
                        end.y() - head * math.sin(angle + spread))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(self.color))
        painter.drawPolygon(QPolygonF([end, left, right]))

    def _polyline_path(self):
        path = QPainterPath(self.points[0])
        if len(self.points) < 3:
            for p in self.points[1:]:
                path.lineTo(p)
            return path
        for i in range(1, len(self.points) - 1):
            mid = QPointF((self.points[i].x() + self.points[i + 1].x()) / 2.0,
                          (self.points[i].y() + self.points[i + 1].y()) / 2.0)
            path.quadTo(self.points[i], mid)
        path.lineTo(self.points[-1])
        return path

    def _draw_pen(self, painter):
        painter.setPen(self._pen())
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(self._polyline_path())

    def _draw_highlight(self, painter):
        painter.setCompositionMode(QPainter.CompositionMode_Multiply)
        painter.setPen(self._pen(width=max(self.width * 4, 14), alpha=150))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(self._polyline_path())

    def _draw_text(self, painter):
        font = self.text_font()
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        left = self.points[0].x() + 3
        y = self.points[0].y() + 2
        for line in self.text.split("\n"):
            if line:
                # 배경 위에서도 읽히도록 흰 테두리를 두른 뒤 색을 채운다
                line_path = QPainterPath()
                line_path.addText(left, y + metrics.ascent(), font, line)
                painter.setPen(QPen(QColor(255, 255, 255, 190), 3))
                painter.setBrush(Qt.NoBrush)
                painter.drawPath(line_path)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(self.color))
                painter.drawPath(line_path)
                if self.underline:
                    pen = QPen(self.color, max(1.0, self.font_size / 14.0))
                    painter.setPen(pen)
                    painter.setBrush(Qt.NoBrush)
                    under = y + metrics.ascent() + metrics.descent() * 0.45
                    painter.drawLine(QPointF(left, under),
                                     QPointF(left + metrics.horizontalAdvance(line),
                                             under))
            y += metrics.height()

    def _draw_number(self, painter):
        r = self._badge_radius()
        center = self.points[0]
        painter.setPen(QPen(QColor(255, 255, 255), max(2.0, self.width * 0.6)))
        painter.setBrush(QBrush(self.color))
        painter.drawEllipse(center, r, r)
        font = QFont()
        font.setPointSizeF(r * 0.95)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(QColor(255, 255, 255)))
        painter.setBrush(Qt.NoBrush)
        painter.drawText(QRectF(center.x() - r, center.y() - r, r * 2, r * 2),
                         Qt.AlignCenter, str(self.number))
