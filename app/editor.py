# -*- coding: utf-8 -*-
"""캡처 이미지 편집 창."""
import os

from PySide6.QtCore import (QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt,
                            Signal)
from PySide6.QtGui import (QAction, QActionGroup, QColor, QCursor, QGuiApplication,
                           QIcon, QImage, QKeySequence, QPainter, QPen, QPixmap)
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox,
                               QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit,
                               QMainWindow, QMenu,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QScrollArea, QSizePolicy, QSpinBox, QStatusBar,
                               QToolBar, QToolButton, QVBoxLayout, QWidget)

import imageops
import saver
from config import CONFIG
from shapes import Shape

TOOLS = [
    ("select", "선택", "V", "도형을 클릭해 옮기거나 Delete로 지웁니다"),
    ("rect", "사각형", "R", "사각형 테두리"),
    ("ellipse", "타원", "E", "원/타원 테두리"),
    ("arrow", "화살표", "A", "가리키는 화살표"),
    ("line", "직선", "L", "직선"),
    ("pen", "펜", "P", "자유롭게 그리기"),
    ("highlight", "형광펜", "H", "반투명 강조"),
    ("text", "글자", "T", "클릭한 자리에 글자 입력"),
    ("number", "번호", "N", "1, 2, 3 순서 표시"),
    ("mosaic", "모자이크", "M", "개인정보를 네모나게 가립니다"),
    ("blur", "흐리게", "B", "선택 영역을 뿌옇게 만듭니다"),
    ("crop", "자르기", "C", "필요한 부분만 남깁니다"),
]

PALETTE = ["#e53935", "#fb8c00", "#fdd835", "#43a047", "#1e88e5",
           "#5e35b1", "#000000", "#ffffff"]


def _color_icon(color, size=18):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(QPen(QColor("#7a7a7a"), 1))
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), 3, 3)
    painter.end()
    return QIcon(pixmap)


class TextInput(QPlainTextEdit):
    """이미지 위에 띄우는 여러 줄 글자 입력 상자."""

    committed = Signal()
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QPlainTextEdit.NoFrame)
        self.textChanged.connect(self._grow)

    def _grow(self):
        lines = max(1, self.toPlainText().count("\n") + 1)
        line_height = self.fontMetrics().height()
        self.setFixedHeight(int(line_height * lines + 14))

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Escape:
            self.cancelled.emit()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter) and \
                (event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)):
            self.committed.emit()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.committed.emit()


class Canvas(QWidget):
    """이미지 + 주석을 그리고 마우스 입력을 처리하는 영역."""

    changed = Signal()
    statusMessage = Signal(str)
    cropChanged = Signal(object)
    selectionChanged = Signal(object)      # 고른 도형(없으면 None)

    def __init__(self, image: QImage, parent=None):
        super().__init__(parent)
        self.base = image
        self.original = image.copy()
        self.shapes = []
        self.zoom = 1.0
        self.tool = "rect"
        self.color = QColor(CONFIG["pen_color"])
        self.pen_width = int(CONFIG["pen_width"])
        self.font_size = int(CONFIG["font_size"])
        self.fill_shape = False
        self.next_number = 1
        self.mosaic_level = 1          # 0 약함 · 1 보통 · 2 강함
        self.text_bold = True
        self.text_italic = False
        self.text_underline = False
        self.crop_rect = None
        self._crop_handle = None
        self._crop_move_from = None
        self.scroll_area = None
        self._space_held = False
        self._panning = False
        self._pan_from = None
        self._pan_start = (0, 0)

        self._undo = []
        self._redo = []
        self._current = None
        self._drag_rect = None
        self._selected = None
        self._move_last = None
        self._moving_pushed = False
        self._resize_handle = None
        self._editor_box = None

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.CrossCursor)
        self._sync_size()

    # ------------------------------------------------------------ 상태
    def _sync_size(self):
        self.setFixedSize(QSize(max(1, int(self.base.width() * self.zoom)),
                                max(1, int(self.base.height() * self.zoom))))
        self.update()

    def set_zoom(self, zoom):
        self.zoom = max(0.1, min(8.0, zoom))
        self._sync_size()

    def zoom_at(self, factor, view_point=None):
        """마우스가 가리키던 지점이 제자리에 남도록 확대·축소한다."""
        area = self.scroll_area
        old = self.zoom
        new = max(0.1, min(8.0, old * factor))
        if abs(new - old) < 1e-6:
            return
        if area is None:
            self.set_zoom(new)
            return
        bar_h = area.horizontalScrollBar()
        bar_v = area.verticalScrollBar()
        if view_point is None:
            view_point = area.viewport().rect().center()
        image_x = (bar_h.value() + view_point.x()) / old
        image_y = (bar_v.value() + view_point.y()) / old
        self.set_zoom(new)
        bar_h.setValue(int(round(image_x * new - view_point.x())))
        bar_v.setValue(int(round(image_y * new - view_point.y())))

    MOSAIC_BLOCKS = (8, 14, 24)
    BLUR_STRENGTH = (5, 8, 14)

    def set_tool(self, tool):
        self._commit_text_box()
        self.tool = tool
        self._selected = None
        self._resize_handle = None
        self.selectionChanged.emit(None)
        self.crop_rect = None
        self._crop_handle = None
        self.cropChanged.emit(self.crop_rect)
        if tool == "select":
            self.setCursor(Qt.ArrowCursor)
        elif tool == "text":
            self.setCursor(Qt.IBeamCursor)
        else:
            self.setCursor(Qt.CrossCursor)
        self.update()

    def push_undo(self):
        self._undo.append((self.base, [s.clone() for s in self.shapes],
                           self.next_number))
        if len(self._undo) > 40:
            self._undo.pop(0)
        self._redo.clear()
        self.changed.emit()

    def undo(self):
        if not self._undo:
            return
        self._redo.append((self.base, [s.clone() for s in self.shapes],
                           self.next_number))
        self.base, self.shapes, self.next_number = self._undo.pop()
        self._selected = None
        self._resize_handle = None
        self.selectionChanged.emit(None)
        self._sync_size()
        self.changed.emit()

    def redo(self):
        if not self._redo:
            return
        self._undo.append((self.base, [s.clone() for s in self.shapes],
                           self.next_number))
        self.base, self.shapes, self.next_number = self._redo.pop()
        self._selected = None
        self._resize_handle = None
        self.selectionChanged.emit(None)
        self._sync_size()
        self.changed.emit()

    def reset_edits(self):
        """편집을 모두 지우고 원본으로 되돌린다."""
        if self.base is self.original and not self.shapes:
            return False
        self.push_undo()
        self.base = self.original.copy()
        self.shapes = []
        self.next_number = 1
        self._selected = None
        self._resize_handle = None
        self.selectionChanged.emit(None)
        if self.tool == "crop":
            self.crop_rect = None
            self.cropChanged.emit(None)
        self._sync_size()
        return True

    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def is_modified(self):
        return bool(self._undo)

    # ------------------------------------------------------------ 렌더링
    def render_image(self) -> QImage:
        out = self.base.copy()
        painter = QPainter(out)
        painter.setRenderHint(QPainter.Antialiasing, True)
        for shape in self.shapes:
            shape.draw(painter)
        painter.end()
        return out

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, self.zoom < 1.0)
        painter.drawImage(QRectF(0, 0, self.width_px(), self.height_px()),
                          self.base, QRectF(self.base.rect()))
        painter.scale(self.zoom, self.zoom)
        painter.setRenderHint(QPainter.Antialiasing, True)
        for shape in self.shapes:
            shape.draw(painter)
        if self._current is not None:
            self._current.draw(painter)
        if self._drag_rect is not None:
            pen = QPen(QColor("#1e88e5"), max(1.0, 1.5 / self.zoom), Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self._drag_rect)
        if self.tool == "crop" and self.crop_rect is not None:
            self._paint_crop(painter)
        if self._selected is not None:
            pen = QPen(QColor("#1e88e5"), max(1.0, 1.5 / self.zoom), Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self._selected.bounds())
            self._paint_handles(painter)
        painter.end()

    HANDLE_PIXELS = 8          # 화면에서 보이는 조절점 크기(픽셀)

    def _handle_size(self):
        """확대해도 조절점은 화면상 같은 크기로 보이게."""
        return self.HANDLE_PIXELS / max(0.1, self.zoom)

    def _paint_handles(self, painter):
        shape = self._selected
        if shape is None:
            return
        spots = shape.handles()
        if not spots:
            return
        size = self._handle_size()
        half = size / 2.0
        painter.setPen(QPen(QColor("#1e88e5"), max(1.0, 1.2 / self.zoom)))
        painter.setBrush(QColor("#ffffff"))
        for point in spots.values():
            painter.drawRect(QRectF(point.x() - half, point.y() - half,
                                    size, size))

    CROP_HANDLE = 9

    def _paint_crop(self, painter):
        rect = self.crop_rect
        full = QRectF(self.base.rect())
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 110))
        for area in (QRectF(full.left(), full.top(), full.width(),
                            rect.top() - full.top()),
                     QRectF(full.left(), rect.bottom(), full.width(),
                            full.bottom() - rect.bottom()),
                     QRectF(full.left(), rect.top(), rect.left() - full.left(),
                            rect.height()),
                     QRectF(rect.right(), rect.top(),
                            full.right() - rect.right(), rect.height())):
            if area.width() > 0 and area.height() > 0:
                painter.drawRect(area)
        painter.setPen(QPen(QColor("#ffffff"), max(1.0, 1.5 / self.zoom)))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect)
        size = self.CROP_HANDLE / max(0.2, self.zoom)
        painter.setPen(QPen(QColor("#1e88e5"), max(1.0, 1.0 / self.zoom)))
        painter.setBrush(QColor("#ffffff"))
        for point in self._crop_points(rect).values():
            painter.drawRect(QRectF(point.x() - size / 2, point.y() - size / 2,
                                    size, size))

    @staticmethod
    def _crop_points(rect):
        cx, cy = rect.center().x(), rect.center().y()
        return {"tl": QPointF(rect.left(), rect.top()),
                "tr": QPointF(rect.right(), rect.top()),
                "bl": QPointF(rect.left(), rect.bottom()),
                "br": QPointF(rect.right(), rect.bottom()),
                "t": QPointF(cx, rect.top()), "b": QPointF(cx, rect.bottom()),
                "l": QPointF(rect.left(), cy), "r": QPointF(rect.right(), cy)}

    def _crop_handle_at(self, point):
        if self.crop_rect is None:
            return None
        reach = self.CROP_HANDLE / max(0.2, self.zoom)
        for name, hp in self._crop_points(self.crop_rect).items():
            if abs(hp.x() - point.x()) <= reach and \
                    abs(hp.y() - point.y()) <= reach:
                return name
        return None

    def set_crop_size(self, width, height):
        if self.crop_rect is None:
            return
        rect = QRectF(self.crop_rect)
        rect.setWidth(max(2, min(width, self.base.width() - rect.left())))
        rect.setHeight(max(2, min(height, self.base.height() - rect.top())))
        self.crop_rect = rect
        self.update()

    def width_px(self):
        return self.base.width() * self.zoom

    def height_px(self):
        return self.base.height() * self.zoom

    # ------------------------------------------------------- 좌표 변환
    def to_image(self, pos) -> QPointF:
        return QPointF(pos.x() / self.zoom, pos.y() / self.zoom)

    def to_view(self, point) -> QPoint:
        return QPoint(int(point.x() * self.zoom), int(point.y() * self.zoom))

    # ------------------------------------------------- 화면 끌기 (손 도구)
    def set_space_held(self, held):
        """Space 를 누르고 있는 동안 손 모양으로 바꾼다."""
        if self._space_held == held:
            return
        self._space_held = held
        if held:
            self.setCursor(Qt.OpenHandCursor)
        else:
            self._panning = False
            self._pan_from = None
            self._restore_cursor()        # 원래 도구의 커서로 되돌린다

    def _restore_cursor(self):
        """그리던 내용은 그대로 두고 커서 모양만 도구에 맞게 되돌린다."""
        if self.tool == "select":
            self.setCursor(Qt.ArrowCursor)
        elif self.tool == "text":
            self.setCursor(Qt.IBeamCursor)
        else:
            self.setCursor(Qt.CrossCursor)

    def _can_pan(self):
        area = self.scroll_area
        if area is None:
            return False
        return (area.horizontalScrollBar().maximum() > 0 or
                area.verticalScrollBar().maximum() > 0)

    def _begin_pan(self, global_pos):
        area = self.scroll_area
        if area is None:
            return False
        self._panning = True
        self._pan_from = global_pos
        self._pan_start = (area.horizontalScrollBar().value(),
                           area.verticalScrollBar().value())
        self.setCursor(Qt.ClosedHandCursor)
        return True

    def _do_pan(self, global_pos):
        area = self.scroll_area
        if area is None or self._pan_from is None:
            return
        dx = global_pos.x() - self._pan_from.x()
        dy = global_pos.y() - self._pan_from.y()
        area.horizontalScrollBar().setValue(self._pan_start[0] - dx)
        area.verticalScrollBar().setValue(self._pan_start[1] - dy)

    def _end_pan(self):
        self._panning = False
        self._pan_from = None
        if self._space_held:
            self.setCursor(Qt.OpenHandCursor)
        else:
            self._restore_cursor()

    # -------------------------------------------------------------- 입력
    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton or \
                (self._space_held and event.button() == Qt.LeftButton):
            self._begin_pan(event.globalPosition().toPoint())
            return
        if event.button() != Qt.LeftButton:
            return
        self._commit_text_box()
        point = self.to_image(event.position())

        if self.tool == "select":
            # 이미 고른 도형의 조절점을 먼저 살펴본다
            handle = self._shape_handle_at(point)
            if handle:
                self.push_undo()
                self._resize_handle = handle
                self._move_last = None
                return
            self._selected = None
            for shape in reversed(self.shapes):
                if shape.contains(point):
                    self._selected = shape
                    break
            self._move_last = point
            self.selectionChanged.emit(self._selected)
            self.update()
            return

        if self.tool == "crop":
            handle = self._crop_handle_at(point)
            if handle:
                self._crop_handle = handle
            elif self.crop_rect is not None and self.crop_rect.contains(point):
                self._crop_move_from = point
            else:
                self.crop_rect = QRectF(point, point)
                self._crop_handle = "br"
            self.update()
            return

        if self.tool in ("mosaic", "blur"):
            self._drag_rect = QRectF(point, point)
            self.update()
            return

        if self.tool == "text":
            self._open_text_box(point)
            return

        if self.tool == "number":
            self.push_undo()
            shape = Shape("number", self.color, self.pen_width, False,
                          self.font_size, "", self.next_number)
            shape.points = [point]
            self.shapes.append(shape)
            self.next_number += 1
            self.update()
            return

        shape = Shape(self.tool, self.color, self.pen_width,
                      self.fill_shape, self.font_size)
        shape.points = [point, point]
        self._current = shape
        self.update()

    def mouseMoveEvent(self, event):
        if self._panning:
            self._do_pan(event.globalPosition().toPoint())
            return
        point = self.to_image(event.position())
        self.statusMessage.emit("%d, %d" % (int(point.x()), int(point.y())))
        if not (event.buttons() & Qt.LeftButton):
            if self.tool == "crop":
                handle = self._crop_handle_at(point)
                self.setCursor(self._CROP_CURSORS.get(handle, Qt.CrossCursor)
                               if handle else Qt.CrossCursor)
            elif self.tool == "select":
                handle = self._shape_handle_at(point)
                self.setCursor(self._HANDLE_CURSORS.get(handle, Qt.ArrowCursor)
                               if handle else Qt.ArrowCursor)
            return
        if self.tool == "crop":
            self._drag_crop(point)
            return
        if self._resize_handle is not None and self._selected is not None:
            self._selected.resize(self._resize_handle, point,
                                  bool(event.modifiers() & Qt.ShiftModifier))
            self.update()
            self.changed.emit()
            return
        if self.tool == "select" and self._selected is not None:
            if self._move_last is not None:
                dx = point.x() - self._move_last.x()
                dy = point.y() - self._move_last.y()
                if dx or dy:
                    if not self._moving_pushed:
                        # push_undo 는 복제본을 저장하므로 원본을 그대로 옮겨도 안전
                        self.push_undo()
                        self._moving_pushed = True
                    self._selected.translate(dx, dy)
                self._move_last = point
                self.update()
            return
        if self._drag_rect is not None:
            self._drag_rect = QRectF(self._drag_rect.topLeft(), point)
            self.update()
            return
        if self._current is not None:
            if self._current.kind in ("pen", "highlight"):
                self._current.points.append(point)
            else:
                self._current.points[-1] = self._constrain(
                    self._current, point, event.modifiers())
            self.update()

    _CROP_CURSORS = {"tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
                     "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
                     "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
                     "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor}

    def _drag_crop(self, point):
        if self.crop_rect is None:
            return
        full = QRectF(self.base.rect())
        point = QPointF(max(full.left(), min(point.x(), full.right())),
                        max(full.top(), min(point.y(), full.bottom())))
        rect = (QRectF(self.crop_rect) if self.crop_rect is not None
                else QRectF(full))
        if self._crop_move_from is not None:
            dx = point.x() - self._crop_move_from.x()
            dy = point.y() - self._crop_move_from.y()
            rect.translate(dx, dy)
            if rect.left() < full.left():
                rect.moveLeft(full.left())
            if rect.top() < full.top():
                rect.moveTop(full.top())
            if rect.right() > full.right():
                rect.moveRight(full.right())
            if rect.bottom() > full.bottom():
                rect.moveBottom(full.bottom())
            self._crop_move_from = point
        elif self._crop_handle:
            h = self._crop_handle
            left, top = rect.left(), rect.top()
            right, bottom = rect.right(), rect.bottom()
            if "l" in h:
                left = point.x()
            if "r" in h:
                right = point.x()
            if h.startswith("t"):
                top = point.y()
            if h.startswith("b"):
                bottom = point.y()
            rect = QRectF(QPointF(min(left, right), min(top, bottom)),
                          QPointF(max(left, right), max(top, bottom)))
        self.crop_rect = rect
        self.cropChanged.emit(rect)
        self.update()

    def apply_crop_now(self):
        """현재 자르기 영역을 적용한다."""
        if self.crop_rect is None:
            return False
        rect = self.crop_rect.toRect()
        if rect.width() < 2 or rect.height() < 2:
            return False
        self.apply_crop(rect)
        self.crop_rect = None
        self.cropChanged.emit(None)
        return True

    def mouseReleaseEvent(self, event):
        if self._panning:
            self._end_pan()
            return
        if event.button() != Qt.LeftButton:
            return
        self._moving_pushed = False
        self._move_last = None
        if self._resize_handle is not None:
            self._resize_handle = None
            self.update()
            return
        if self.tool == "crop":
            self._crop_handle = None
            self._crop_move_from = None
            return
        if self._drag_rect is not None:
            rect = self._drag_rect.normalized().toRect()
            tool = self.tool
            self._drag_rect = None
            if rect.width() > 2 and rect.height() > 2:
                level = max(0, min(2, int(self.mosaic_level)))
                if tool == "mosaic":
                    self.push_undo()
                    self.base = imageops.mosaic(self.base, rect,
                                                self.MOSAIC_BLOCKS[level])
                elif tool == "blur":
                    self.push_undo()
                    self.base = imageops.blur(self.base, rect,
                                              self.BLUR_STRENGTH[level])
            self.update()
            return
        if self._current is not None:
            shape = self._current
            self._current = None
            if shape.is_valid():
                self.push_undo()
                self.shapes.append(shape)
            self.update()

    @staticmethod
    def _constrain(shape, point, modifiers):
        """Shift 를 누르면 정사각형 / 정원 / 45도 단위 직선."""
        if not (modifiers & Qt.ShiftModifier) or not shape.points:
            return point
        start = shape.points[0]
        dx = point.x() - start.x()
        dy = point.y() - start.y()
        if shape.kind in ("rect", "ellipse"):
            side = max(abs(dx), abs(dy))
            return QPointF(start.x() + (side if dx >= 0 else -side),
                           start.y() + (side if dy >= 0 else -side))
        if shape.kind in ("line", "arrow"):
            import math
            angle = math.atan2(dy, dx)
            step = math.pi / 4
            angle = round(angle / step) * step
            length = math.hypot(dx, dy)
            return QPointF(start.x() + length * math.cos(angle),
                           start.y() + length * math.sin(angle))
        return point

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            if not self._text_box_open():
                self.set_space_held(True)
                event.accept()
                return
        if self.tool == "crop":
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.apply_crop_now()
                return
            if event.key() == Qt.Key_Escape:
                self.crop_rect = None
                self.cropChanged.emit(None)
                self.update()
                return
        arrows = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0),
                  Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}
        if event.key() in arrows and self._selected is not None:
            step = 10 if event.modifiers() & Qt.ShiftModifier else 1
            dx, dy = arrows[event.key()]
            self.nudge_selected(dx * step, dy * step)
            return
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and \
                self._selected is not None:
            self.push_undo()
            try:
                self.shapes.remove(self._selected)
            except ValueError:
                pass
            self._selected = None
            self._resize_handle = None
            self.selectionChanged.emit(None)
            self.update()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            if self._space_held:
                self.set_space_held(False)
                event.accept()
                return
        super().keyReleaseEvent(event)

    def _text_box_open(self):
        return bool(self._editor_box)

    # ------------------------------------------------- 도형 크기 조절
    _HANDLE_CURSORS = {
        "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
        "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
        "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
        "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
        "p0": Qt.SizeAllCursor, "p1": Qt.SizeAllCursor,
    }

    def _shape_handle_at(self, point):
        """고른 도형의 조절점 위에 마우스가 있으면 그 이름을 돌려준다."""
        if self.tool != "select" or self._selected is None:
            return None
        spots = self._selected.handles()
        if not spots:
            return None
        reach = max(4.0, self._handle_size() * 0.8)
        best = None
        best_distance = reach
        for name, spot in spots.items():
            distance = max(abs(spot.x() - point.x()), abs(spot.y() - point.y()))
            if distance <= best_distance:
                best_distance = distance
                best = name
        return best

    # ------------------------------------------------- 이미지 자체 변형
    def _after_image_change(self):
        self._selected = None
        self._resize_handle = None
        self.crop_rect = None
        self._crop_handle = None
        self.selectionChanged.emit(None)
        self.cropChanged.emit(None)
        self._sync_size()
        self.changed.emit()

    def transform_image(self, mode):
        """이미지를 돌리거나 뒤집는다. 그려 놓은 주석도 같이 따라간다."""
        if self.base.isNull():
            return False
        width = float(self.base.width())
        height = float(self.base.height())
        if mode == "right":                       # 시계 방향 90도
            image = imageops.rotate(self.base, 90)
            move = lambda x, y: (height - y, x)          # noqa: E731
        elif mode == "left":                      # 반시계 방향 90도
            image = imageops.rotate(self.base, 270)
            move = lambda x, y: (y, width - x)           # noqa: E731
        elif mode == "flip":                      # 좌우 뒤집기
            image = imageops.flip(self.base, True)
            move = lambda x, y: (width - x, y)           # noqa: E731
        elif mode == "flipv":                     # 위아래 뒤집기
            image = imageops.flip(self.base, False)
            move = lambda x, y: (x, height - y)          # noqa: E731
        else:
            return False
        self.push_undo()
        self.base = image
        for shape in self.shapes:
            shape.map_points(move)
        self._after_image_change()
        return True

    def add_frame(self, margin, line, margin_color="#ffffff",
                  line_color="#c8ced8"):
        """바깥에 여백과 테두리를 두른다."""
        margin = max(0, int(margin))
        line = max(0, int(line))
        if margin == 0 and line == 0:
            return False
        self.push_undo()
        self.base = imageops.add_border(self.base, margin, line,
                                        margin_color, line_color)
        pad = margin + line
        for shape in self.shapes:
            shape.translate(pad, pad)
        self._after_image_change()
        return True

    def stamp_text(self, text, corner="br"):
        """오른쪽 아래에 날짜나 기관명을 새긴다."""
        import stamp
        if not (text or "").strip():
            return False
        self.push_undo()
        self.base = stamp.apply(self.base, text, corner)
        self._after_image_change()
        return True

    def scale_image(self, width, height):
        """이미지 크기를 바꾼다. 주석의 위치와 굵기도 같은 비율로 맞춘다."""
        old_w = max(1, self.base.width())
        old_h = max(1, self.base.height())
        width = max(1, int(width))
        height = max(1, int(height))
        if width == old_w and height == old_h:
            return False
        self.push_undo()
        self.base = imageops.resized(self.base, width, height)
        sx = self.base.width() / float(old_w)
        sy = self.base.height() / float(old_h)
        average = (sx + sy) / 2.0
        for shape in self.shapes:
            shape.map_points(lambda x, y: (x * sx, y * sy))   # noqa: B023
            shape.width = max(1, int(round(shape.width * average)))
            shape.font_size = max(8, int(round(shape.font_size * average)))
        self._after_image_change()
        return True

    # --------------------------------------------- 고른 도형 손보기
    def restyle_selected(self, **changes):
        """이미 그려 놓은 도형의 색·굵기 등을 지우지 않고 바꾼다."""
        shape = self._selected
        if shape is None:
            return False
        real = {}
        for key, value in changes.items():
            if value is None:
                continue
            if key == "color":
                value = QColor(value)
            if getattr(shape, key, None) != value:
                real[key] = value
        if not real:
            return False
        self.push_undo()
        for key, value in real.items():
            setattr(shape, key, value)
        self.update()
        self.changed.emit()
        return True

    def duplicate_selected(self, offset=14):
        """고른 도형을 살짝 옮겨서 하나 더 만든다."""
        shape = self._selected
        if shape is None:
            return False
        self.push_undo()
        copy = shape.clone()
        copy.translate(offset, offset)
        if copy.kind == "number":
            copy.number = self.next_number
            self.next_number += 1
        self.shapes.append(copy)
        self._selected = copy
        self._resize_handle = None
        self.selectionChanged.emit(copy)
        self.update()
        self.changed.emit()
        return True

    def reorder_selected(self, to_front=True):
        """고른 도형을 맨 앞으로 보내거나 맨 뒤로 보낸다."""
        shape = self._selected
        if shape is None or len(self.shapes) < 2:
            return False
        try:
            index = self.shapes.index(shape)
        except ValueError:
            return False
        if to_front and index == len(self.shapes) - 1:
            return False
        if not to_front and index == 0:
            return False
        self.push_undo()
        self.shapes.pop(index)
        if to_front:
            self.shapes.append(shape)
        else:
            self.shapes.insert(0, shape)
        self.update()
        self.changed.emit()
        return True

    def nudge_selected(self, dx, dy):
        """방향키로 고른 도형을 조금씩 옮긴다."""
        if self._selected is None:
            return False
        self.push_undo()
        self._selected.translate(dx, dy)
        self.update()
        self.changed.emit()
        return True

    def select_shape(self, shape):
        self._selected = shape
        self._resize_handle = None
        self.selectionChanged.emit(shape)
        self.update()

    def selected_shape(self):
        return self._selected

    # ---------------------------------------------------------- 글자 입력
    def _open_text_box(self, point):
        box = TextInput(self)
        pixels = max(10, int(self.font_size * self.zoom * 1.33))
        weight = "700" if self.text_bold else "400"
        style = "italic" if self.text_italic else "normal"
        decoration = "underline" if self.text_underline else "none"
        box.setStyleSheet(
            "QPlainTextEdit { background: rgba(255,255,255,238);"
            " border: 1px dashed #1e88e5; padding: 3px; color: %s;"
            " font-size: %dpx; font-weight: %s; font-style: %s;"
            " text-decoration: %s; }"
            % (self.color.name(), pixels, weight, style, decoration))
        box.move(self.to_view(point))
        box.setFixedWidth(max(180, int(260 * self.zoom)))
        box._grow()
        box.show()
        box.setFocus()
        box.committed.connect(self._commit_text_box)
        box.cancelled.connect(self._cancel_text_box)
        self._editor_box = (box, point)
        self.statusMessage.emit("Enter 줄바꿈 · Ctrl+Enter 완료 · Esc 취소")

    def _close_text_box(self):
        if not self._editor_box:
            return None
        box, point = self._editor_box
        self._editor_box = None
        text = box.toPlainText()
        box.hide()
        box.deleteLater()
        return text, point

    def _cancel_text_box(self):
        self._close_text_box()
        self.update()

    def _commit_text_box(self):
        closed = self._close_text_box()
        if closed is None:
            return
        text, point = closed
        if text.strip():
            self.push_undo()
            shape = Shape("text", self.color, self.pen_width, False,
                          self.font_size, text.rstrip("\n"), 1,
                          self.text_bold, self.text_italic,
                          self.text_underline)
            shape.points = [QPointF(point)]
            self.shapes.append(shape)
        self.update()

    # -------------------------------------------------------------- 자르기
    def apply_crop(self, rect: QRect):
        rect = rect.intersected(self.base.rect())
        if rect.width() < 2 or rect.height() < 2:
            return
        self.push_undo()
        flattened = self.render_image()
        self.base = flattened.copy(rect)
        self.shapes = []
        self._selected = None
        self._resize_handle = None
        self.selectionChanged.emit(None)
        self._sync_size()

    def select_all_crop(self):
        self._drag_rect = None
        self.update()


class EditorWindow(QMainWindow):
    """편집 창."""

    pinRequested = Signal(QImage)
    ocrRequested = Signal(QImage)
    closed = Signal(object)

    # 높이가 폭의 이 배수 이상이면 '긴 그림' 으로 보고 폭에만 맞춰 연다
    TALL_RATIO = 1.5

    def __init__(self, image: QImage, saved_path=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("캡처 편집")
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.saved_path = saved_path
        self._saved_clean = saved_path is not None
        self._syncing = False          # 도구 모음 값을 맞추는 중인지

        self.canvas = Canvas(image, self)
        self.canvas.changed.connect(self._refresh_actions)
        self.canvas.statusMessage.connect(self._show_position)

        self.canvas.cropChanged.connect(self._crop_changed)

        self.scroll = QScrollArea(self)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setWidget(self.canvas)
        self.canvas.scroll_area = self.scroll      # 손 도구·확대 기준점용
        self.scroll.setStyleSheet(
            "QScrollArea { background: #2b2d31; border: none; }")

        central = QWidget(self)
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.scroll, 1)
        self.crop_bar = self._build_crop_bar()
        column.addWidget(self.crop_bar)
        self.crop_bar.hide()
        self.setCentralWidget(central)

        self._build_toolbars()
        # 도구 모음이 만들어진 뒤에 연결해야 값을 맞출 수 있다
        self.canvas.selectionChanged.connect(self._selection_changed)
        self._sync_shape_actions(None)
        self._build_status()
        self._resize_to_image(image)
        self._refresh_actions()
        if saved_path:
            self.info_label.setText("저장됨 → %s" % saved_path)
            self.setWindowTitle("캡처 편집 — %s" % os.path.basename(saved_path))

    # ---------------------------------------------------------- 화면 구성
    def _resize_to_image(self, image):
        screen = QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QRect(0, 0, 1280, 800)
        width = min(image.width() + 60, int(available.width() * 0.9))
        height = min(image.height() + 190, int(available.height() * 0.9))
        self.resize(max(760, width), max(520, height))
        if image.width() + 60 > available.width() * 0.9 or \
                image.height() + 190 > available.height() * 0.9:
            fit_w = (available.width() * 0.85) / max(1, image.width())
            fit_h = (available.height() * 0.72) / max(1, image.height())
            # 스크롤 캡처처럼 세로로 긴 그림은 폭에만 맞춘다.
            # 전체를 한 화면에 넣으면 10% 안팎까지 줄어 글자가 뭉개져 보인다.
            if image.height() >= image.width() * self.TALL_RATIO:
                fit = fit_w
            else:
                fit = min(fit_w, fit_h)
            self.canvas.set_zoom(min(1.0, fit))
        self.move(available.center() - self.rect().center())

    def _build_toolbars(self):
        bar = QToolBar("도구", self)
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(Qt.TopToolBarArea, bar)
        self._tool_group = QActionGroup(self)
        self._tool_group.setExclusive(True)
        self._tool_actions = {}
        for key, label, shortcut, tip in TOOLS:
            action = QAction(label, self)
            action.setCheckable(True)
            action.setToolTip("%s  (%s)" % (tip, shortcut))
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(lambda _=False, k=key: self._set_tool(k))
            self._tool_group.addAction(action)
            bar.addAction(action)
            self._tool_actions[key] = action
        self._tool_actions["rect"].setChecked(True)

        bar.addSeparator()
        self.color_button = QToolButton(self)
        self.color_button.setText("색")
        self.color_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.color_button.setIcon(_color_icon(self.canvas.color))
        self.color_button.clicked.connect(self._pick_color)
        bar.addWidget(self.color_button)
        for color in PALETTE:
            btn = QToolButton(self)
            btn.setIcon(_color_icon(color))
            btn.setAutoRaise(True)
            btn.setToolTip(color)
            btn.clicked.connect(lambda _=False, c=color: self._apply_color(QColor(c)))
            bar.addWidget(btn)

        bar.addSeparator()
        bar.addWidget(QLabel(" 굵기 "))
        self.width_spin = QSpinBox(self)
        self.width_spin.setRange(1, 40)
        self.width_spin.setValue(self.canvas.pen_width)
        self.width_spin.setFixedWidth(56)
        self.width_spin.valueChanged.connect(self._set_width)
        bar.addWidget(self.width_spin)

        self._font_only = [bar.addWidget(QLabel(" 글자크기 "))]
        self.font_spin = QSpinBox(self)
        self.font_spin.setRange(8, 160)
        self.font_spin.setValue(self.canvas.font_size)
        self.font_spin.setFixedWidth(60)
        self.font_spin.valueChanged.connect(self._set_font_size)
        self._font_only.append(bar.addWidget(self.font_spin))

        self.fill_check = QCheckBox("채우기", self)
        self.fill_check.setToolTip("사각형/타원 안쪽을 반투명하게 채웁니다")
        self.fill_check.toggled.connect(self._set_fill)
        self.fill_action = bar.addWidget(self.fill_check)

        # 글자 도구를 골랐을 때만 보이는 항목
        self.style_buttons = {}
        self._text_only = []
        for key, label, tip, default in (
                ("bold", "굵게", "글자를 굵게", True),
                ("italic", "기울임", "글자를 기울여서", False),
                ("underline", "밑줄", "글자에 밑줄", False)):
            button = QToolButton(self)
            button.setText(label)
            button.setCheckable(True)
            button.setChecked(default)
            button.setToolTip(tip)
            button.toggled.connect(
                lambda on, k=key: self._set_text_style(k, on))
            self._text_only.append(bar.addWidget(button))
            self.style_buttons[key] = button

        # 모자이크·흐리게를 골랐을 때만 보이는 항목
        self._level_only = [bar.addWidget(QLabel(" 가림 강도 "))]
        self.level_combo = QComboBox(self)
        self.level_combo.addItem("약하게", 0)
        self.level_combo.addItem("보통", 1)
        self.level_combo.addItem("강하게", 2)
        self.level_combo.setCurrentIndex(1)
        self.level_combo.setToolTip("모자이크와 흐리게의 세기")
        self.level_combo.currentIndexChanged.connect(
            lambda: setattr(self.canvas, "mosaic_level",
                            self.level_combo.currentData()))
        self._level_only.append(bar.addWidget(self.level_combo))

        # 도형 도구를 골랐을 때만 보이는 항목
        self._shape_only = [self.fill_action]
        self._sync_tool_widgets("rect")

        # ---- 두 번째 줄: 동작
        self.addToolBarBreak(Qt.TopToolBarArea)
        actions = QToolBar("동작", self)
        actions.setMovable(False)
        actions.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(Qt.TopToolBarArea, actions)

        self.undo_action = QAction("실행 취소", self)
        self.undo_action.setShortcut(QKeySequence.Undo)
        self.undo_action.triggered.connect(self.canvas.undo)
        actions.addAction(self.undo_action)

        self.redo_action = QAction("다시 실행", self)
        self.redo_action.setShortcuts([QKeySequence.Redo,
                                       QKeySequence("Ctrl+Y")])
        self.redo_action.triggered.connect(self.canvas.redo)
        actions.addAction(self.redo_action)

        self.reset_action = QAction("편집 초기화", self)
        self.reset_action.setToolTip("그린 것과 가린 것을 모두 지우고 원본으로")
        self.reset_action.triggered.connect(self._reset_edits)
        actions.addAction(self.reset_action)
        actions.addSeparator()

        def add(text, shortcut, slot, tip=""):
            act = QAction(text, self)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
            if tip:
                act.setToolTip(tip)
            act.triggered.connect(slot)
            actions.addAction(act)
            return act

        add("저장", "Ctrl+S", self.save, "설정된 폴더에 저장합니다")
        add("다른 이름으로", "Ctrl+Shift+S", self.save_as, "위치를 골라 저장합니다")
        add("복사", "Ctrl+C", self.copy_to_clipboard, "클립보드로 복사합니다")
        add("핀 고정", "Ctrl+T", self.pin, "화면 위에 항상 띄워 둡니다")
        add("글자 추출", "Ctrl+E", self.run_ocr, "이미지 속 글자를 텍스트로 뽑습니다")
        add("인쇄", "Ctrl+P", self.print_image, "미리보기로 확인한 뒤 인쇄합니다")
        actions.addSeparator()

        add("확대", "Ctrl++", lambda: self._zoom_by(1.25))
        add("축소", "Ctrl+-", lambda: self._zoom_by(0.8))
        add("100%", "Ctrl+0", lambda: self._set_zoom(1.0))
        add("창에 맞춤", "Ctrl+9", self.fit_to_window)
        actions.addSeparator()

        # ---- 고른 도형에만 쓰는 것들 (묶어서 메뉴로)
        self._shape_actions = []
        shape_menu = QMenu(self)

        def shape_item(text, shortcut, slot, tip=""):
            act = QAction(text, self)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
                act.setShortcutContext(Qt.WindowShortcut)
            if tip:
                act.setToolTip(tip)
            act.triggered.connect(slot)
            shape_menu.addAction(act)
            self.addAction(act)          # 메뉴를 열지 않아도 단축키가 듣게
            self._shape_actions.append(act)
            return act

        shape_item("복제", "Ctrl+D", self._duplicate_shape,
                   "고른 도형을 하나 더 만듭니다")
        shape_item("맨 앞으로", "Ctrl+Shift+Up",
                   lambda: self._order_shape(True))
        shape_item("맨 뒤로", "Ctrl+Shift+Down",
                   lambda: self._order_shape(False))
        shape_button = QToolButton(self)
        shape_button.setText("고른 도형 ▾")
        shape_button.setToolTip(
            "선택 도구(V)로 도형을 고른 뒤 쓸 수 있습니다.\n"
            "모서리의 점을 끌면 크기가 바뀌고, 색·굵기는 위 줄에서 바꿉니다.")
        shape_button.setMenu(shape_menu)
        shape_button.setPopupMode(QToolButton.InstantPopup)
        actions.addWidget(shape_button)

        # ---- 이미지 자체를 다루는 것들
        image_menu = QMenu(self)

        def image_item(text, slot, shortcut=None, tip=""):
            act = QAction(text, self)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
            if tip:
                act.setToolTip(tip)
            act.triggered.connect(slot)
            image_menu.addAction(act)
            if shortcut:
                self.addAction(act)
            return act

        image_item("왼쪽으로 90도 돌리기",
                   lambda: self._transform("left"), "Ctrl+Shift+L")
        image_item("오른쪽으로 90도 돌리기",
                   lambda: self._transform("right"), "Ctrl+Shift+R")
        image_menu.addSeparator()
        image_item("좌우 뒤집기", lambda: self._transform("flip"))
        image_item("위아래 뒤집기", lambda: self._transform("flipv"))
        image_menu.addSeparator()
        image_item("여백·테두리 두르기…", self._ask_frame,
                   tip="흰 배경 캡처의 경계가 보이도록 얇은 선을 두릅니다")
        image_item("이미지 크기 바꾸기…", self._ask_resize,
                   tip="메일 첨부용으로 줄일 때 씁니다")
        image_menu.addSeparator()
        image_item("날짜 도장 찍기", lambda: self._stamp(date_only=True),
                   tip="오른쪽 아래에 오늘 날짜를 넣습니다")
        image_item("워터마크 넣기…", lambda: self._stamp(date_only=False))
        image_menu.addSeparator()
        image_item("개인정보 자동 가리기", self.auto_redact, "Ctrl+Shift+M",
                   tip="이메일·전화번호·주민등록번호·카드번호를 찾아 모자이크합니다")
        image_button = QToolButton(self)
        image_button.setText("이미지 ▾")
        image_button.setMenu(image_menu)
        image_button.setPopupMode(QToolButton.InstantPopup)
        actions.addWidget(image_button)

        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        actions.addWidget(spacer)
        add("닫기", "Ctrl+W", self.close)

    def _build_crop_bar(self):
        bar = QWidget(self)
        bar.setStyleSheet(
            "QWidget { background: #f2f4f8; border-top: 1px solid #d6dbe4; }"
            "QSpinBox { background: #ffffff; }")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(8)
        layout.addWidget(QLabel("남길 크기", bar))
        self.crop_w = QSpinBox(bar)
        self.crop_w.setRange(2, 40000)
        self.crop_w.setSuffix(" px")
        self.crop_h = QSpinBox(bar)
        self.crop_h.setRange(2, 40000)
        self.crop_h.setSuffix(" px")
        self.crop_w.valueChanged.connect(self._crop_size_typed)
        self.crop_h.valueChanged.connect(self._crop_size_typed)
        layout.addWidget(self.crop_w)
        layout.addWidget(QLabel("×", bar))
        layout.addWidget(self.crop_h)
        self.crop_pos = QLabel("", bar)
        self.crop_pos.setStyleSheet("color: #667; border: none;")
        layout.addWidget(self.crop_pos)
        layout.addStretch(1)
        self.crop_note = QLabel("남길 부분을 드래그하세요", bar)
        self.crop_note.setStyleSheet("color: #778; border: none;")
        layout.addWidget(self.crop_note)
        apply_btn = QPushButton("적용", bar)
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(self._apply_crop)
        cancel_btn = QPushButton("취소", bar)
        cancel_btn.clicked.connect(lambda: self._set_tool("select"))
        layout.addWidget(apply_btn)
        layout.addWidget(cancel_btn)
        return bar

    def _crop_changed(self, rect):
        active = rect is not None
        self.crop_bar.setVisible(self.canvas.tool == "crop")
        for widget in (self.crop_w, self.crop_h):
            widget.setEnabled(active)
        if not active:
            self.crop_pos.setText("")
            self.crop_note.setText("남길 부분을 드래그하세요")
            return
        self.crop_note.setText("모서리를 끌어 조절하고 Enter 로 적용")
        self._syncing_crop = True
        self.crop_w.setValue(max(2, int(round(rect.width()))))
        self.crop_h.setValue(max(2, int(round(rect.height()))))
        self.crop_pos.setText("  (위치 %d, %d)"
                              % (int(rect.left()), int(rect.top())))
        self._syncing_crop = False

    def _crop_size_typed(self):
        if getattr(self, "_syncing_crop", False):
            return
        self.canvas.set_crop_size(self.crop_w.value(), self.crop_h.value())

    def _apply_crop(self):
        if self.canvas.apply_crop_now():
            self.info_label.setText("잘랐습니다 — %d × %d"
                                    % (self.canvas.base.width(),
                                       self.canvas.base.height()))
            self._set_tool("select")

    def _build_status(self):
        status = QStatusBar(self)
        self.setStatusBar(status)
        self.info_label = QLabel(
            "Space 를 누른 채 끌면 화면을 손으로 옮길 수 있습니다 "
            "(Ctrl+휠 = 확대·축소)", self)
        self.pos_label = QLabel("", self)
        self.zoom_label = QLabel("", self)
        status.addWidget(self.info_label, 1)
        status.addPermanentWidget(self.pos_label)
        status.addPermanentWidget(self.zoom_label)

    # ------------------------------------------------------------- 도구
    def _sync_tool_widgets(self, key):
        """고른 도구에 필요한 항목만 도구 모음에 보여 준다."""
        for action in self._text_only:
            action.setVisible(key == "text")
        for action in self._font_only:
            action.setVisible(key in ("text", "number"))
        for action in self._level_only:
            action.setVisible(key in ("mosaic", "blur"))
        for action in self._shape_only:
            action.setVisible(key in ("rect", "ellipse"))

    def _set_tool(self, key):
        self.canvas.set_tool(key)
        self.crop_bar.setVisible(key == "crop")
        self._sync_tool_widgets(key)
        action = self._tool_actions.get(key)
        if action is not None and not action.isChecked():
            action.setChecked(True)
        hints = {
            "crop": "남길 부분을 드래그하면 나머지가 잘립니다",
            "mosaic": "가릴 부분을 드래그하세요",
            "blur": "흐리게 할 부분을 드래그하세요",
            "text": "글자를 넣을 자리를 클릭한 뒤 입력하고 Enter",
            "select": "도형을 클릭해 옮기고, Delete 로 지웁니다",
        }
        self.info_label.setText(hints.get(key, ""))

    def _pick_color(self):
        color = QColorDialog.getColor(self.canvas.color, self, "색 선택")
        if color.isValid():
            self._apply_color(color)

    def _apply_color(self, color):
        self.canvas.color = QColor(color)
        self.color_button.setIcon(_color_icon(color))
        CONFIG["pen_color"] = color.name()
        CONFIG.save()
        # 고른 도형이 있으면 그 도형 색도 함께 바꾼다
        if self.canvas.restyle_selected(color=QColor(color)):
            self.info_label.setText("고른 도형의 색을 바꿨습니다")

    def _set_width(self, value):
        self.canvas.pen_width = value
        CONFIG["pen_width"] = value
        if self._syncing:
            return
        if self.canvas.restyle_selected(width=value):
            self.info_label.setText("고른 도형의 굵기를 바꿨습니다")

    def _set_font_size(self, value):
        self.canvas.font_size = value
        CONFIG["font_size"] = value
        if self._syncing:
            return
        if self.canvas.restyle_selected(font_size=value):
            self.info_label.setText("고른 글자의 크기를 바꿨습니다")

    def _set_fill(self, checked):
        self.canvas.fill_shape = checked
        if self._syncing:
            return
        self.canvas.restyle_selected(fill=checked)

    def _set_text_style(self, key, on):
        setattr(self.canvas, "text_" + key, on)
        if self._syncing:
            return
        self.canvas.restyle_selected(**{key: on})

    # ------------------------------------------------ 고른 도형 다루기
    def _selection_changed(self, shape):
        """도형을 고르면 도구 모음이 그 도형의 값을 보여 주게 한다."""
        self._syncing = True
        try:
            if shape is not None:
                self.width_spin.setValue(max(1, min(40, shape.width)))
                self.font_spin.setValue(max(8, min(160, shape.font_size)))
                self.fill_check.setChecked(bool(shape.fill))
                for key, button in self.style_buttons.items():
                    button.setChecked(bool(getattr(shape, key, False)))
                self.color_button.setIcon(_color_icon(shape.color))
        finally:
            self._syncing = False
        self._sync_shape_actions(shape)

    def _sync_shape_actions(self, shape):
        has = shape is not None
        for action in getattr(self, "_shape_actions", []):
            action.setEnabled(has)
        if has:
            from shapes import KIND_LABELS
            self.info_label.setText(
                "%s 를 골랐습니다 — 모서리 점을 끌어 크기 조절, "
                "방향키로 이동, Ctrl+D 복제" % KIND_LABELS.get(shape.kind, "도형"))

    # ------------------------------------------------- 이미지 변형 조작
    def _transform(self, mode):
        names = {"left": "왼쪽으로 돌렸습니다",
                 "right": "오른쪽으로 돌렸습니다",
                 "flip": "좌우로 뒤집었습니다",
                 "flipv": "위아래로 뒤집었습니다"}
        if self.canvas.transform_image(mode):
            self.info_label.setText("%s (%d × %d)" % (
                names.get(mode, "바꿨습니다"),
                self.canvas.base.width(), self.canvas.base.height()))
            self._refresh_actions()

    def _ask_frame(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("여백 · 테두리")
        form = QFormLayout(dialog)
        margin = QSpinBox(dialog)
        margin.setRange(0, 200)
        margin.setValue(int(CONFIG.get("frame_margin", 12)))
        margin.setSuffix(" 픽셀")
        line = QSpinBox(dialog)
        line.setRange(0, 20)
        line.setValue(int(CONFIG.get("frame_line", 1)))
        line.setSuffix(" 픽셀")
        form.addRow("바깥 여백", margin)
        form.addRow("테두리 선 굵기", line)
        note = QLabel("흰 배경을 캡처했을 때 문서에 붙이면\n"
                      "어디까지가 그림인지 또렷하게 보입니다.", dialog)
        note.setStyleSheet("color: #666; font-size: 12px;")
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
                                   parent=dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        CONFIG["frame_margin"] = margin.value()
        CONFIG["frame_line"] = line.value()
        CONFIG.save()
        if self.canvas.add_frame(margin.value(), line.value()):
            self.info_label.setText("여백을 둘렀습니다 (%d × %d)" % (
                self.canvas.base.width(), self.canvas.base.height()))
            self._refresh_actions()

    def _ask_resize(self):
        image = self.canvas.base
        start_w, start_h = image.width(), image.height()
        dialog = QDialog(self)
        dialog.setWindowTitle("이미지 크기 바꾸기")
        form = QFormLayout(dialog)
        width = QSpinBox(dialog)
        width.setRange(1, 20000)
        width.setValue(start_w)
        height = QSpinBox(dialog)
        height.setRange(1, 20000)
        height.setValue(start_h)
        keep = QCheckBox("가로세로 비율 유지", dialog)
        keep.setChecked(True)
        guard = {"busy": False}

        def sync(source):
            if not keep.isChecked() or guard["busy"] or not start_w or not start_h:
                return
            guard["busy"] = True
            try:
                if source == "w":
                    height.setValue(max(1, int(round(
                        width.value() * start_h / float(start_w)))))
                else:
                    width.setValue(max(1, int(round(
                        height.value() * start_w / float(start_h)))))
            finally:
                guard["busy"] = False

        width.valueChanged.connect(lambda: sync("w"))
        height.valueChanged.connect(lambda: sync("h"))
        form.addRow("가로", width)
        form.addRow("세로", height)
        form.addRow(keep)

        quick = QHBoxLayout()
        for label, factor in (("50%", 0.5), ("75%", 0.75), ("100%", 1.0)):
            button = QPushButton(label, dialog)
            button.clicked.connect(
                lambda _=False, f=factor: width.setValue(
                    max(1, int(round(start_w * f)))))
            quick.addWidget(button)
        form.addRow("빠른 선택", quick)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
                                   parent=dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        if self.canvas.scale_image(width.value(), height.value()):
            self.info_label.setText("크기를 바꿨습니다 (%d × %d)" % (
                self.canvas.base.width(), self.canvas.base.height()))
            self._refresh_actions()

    def _stamp(self, date_only=True):
        import stamp
        text = stamp.today_text()
        if not date_only:
            current = CONFIG.get("watermark_text", "") or ""
            typed, ok = QInputDialog.getText(
                self, "워터마크", "이미지 오른쪽 아래에 넣을 글자:",
                QLineEdit.Normal, current or "유디센터")
            if not ok:
                return
            typed = typed.strip()
            if not typed:
                return
            CONFIG["watermark_text"] = typed
            CONFIG.save()
            text = typed
        self.canvas.stamp_text(text)
        self.info_label.setText("'%s' 를 오른쪽 아래에 넣었습니다" % text)
        self._refresh_actions()

    def auto_redact(self):
        """글자 인식으로 개인정보 자리를 찾아 한 번에 모자이크한다."""
        if getattr(self, "_redact_task", None) is not None:
            return
        try:
            import ocr
            task = ocr.WordsTask(self.canvas.base, CONFIG["ocr_language"] or "")
        except Exception as exc:
            QMessageBox.information(self, "개인정보 자동 가리기",
                                    "글자 인식을 시작하지 못했습니다.\n%s" % exc)
            return
        self.info_label.setText("개인정보를 찾는 중입니다…")
        QApplication.setOverrideCursor(Qt.BusyCursor)

        def done(lines):
            self._redact_task = None
            QApplication.restoreOverrideCursor()
            self._apply_redact(lines)

        def failed(message):
            self._redact_task = None
            QApplication.restoreOverrideCursor()
            self.info_label.setText("")
            QMessageBox.information(self, "개인정보 자동 가리기", message)

        task.finished.connect(done)
        task.failed.connect(failed)
        self._redact_task = task
        task.start()

    def _apply_redact(self, lines):
        import redact
        base = self.canvas.base
        found = redact.find(lines, (base.width(), base.height()))
        found = [(kind, rect) for kind, rect in found
                 if rect.width() > 2 and rect.height() > 2]
        if not found:
            self.info_label.setText("가릴 개인정보를 찾지 못했습니다")
            QMessageBox.information(
                self, "개인정보 자동 가리기",
                "이메일·전화번호·주민등록번호·카드번호를 찾지 못했습니다.\n"
                "글자가 작으면 인식이 안 될 수 있으니 모자이크 도구(M)로 "
                "직접 가려 주세요.")
            return
        answer = QMessageBox.question(
            self, "개인정보 자동 가리기",
            "%d곳을 찾았습니다 (%s).\n모자이크로 가릴까요?\n\n"
            "Ctrl+Z 로 되돌릴 수 있습니다." % (len(found), redact.summary(found)),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if answer != QMessageBox.Yes:
            self.info_label.setText("")
            return
        self.canvas.push_undo()
        image = self.canvas.base
        for _kind, rect in found:
            block = max(4, min(Canvas.MOSAIC_BLOCKS[1], rect.height() // 2))
            image = imageops.mosaic(image, rect, block)
        self.canvas.base = image
        self.canvas.update()
        self.canvas.changed.emit()
        self._refresh_actions()
        self.info_label.setText("%d곳을 가렸습니다 (%s)"
                                % (len(found), redact.summary(found)))

    def _duplicate_shape(self):
        if not self.canvas.duplicate_selected():
            self.info_label.setText("먼저 선택 도구(V)로 도형을 골라 주세요")

    def _order_shape(self, to_front):
        if self.canvas.reorder_selected(to_front):
            self.info_label.setText(
                "맨 앞으로 보냈습니다" if to_front else "맨 뒤로 보냈습니다")
        else:
            self.info_label.setText("순서를 바꿀 도형이 없습니다")

    def _reset_edits(self):
        if not self.canvas.is_modified():
            self.info_label.setText("되돌릴 편집 내용이 없습니다")
            return
        answer = QMessageBox.question(
            self, "편집 초기화",
            "그린 것과 가린 것을 모두 지우고 처음 캡처 상태로 되돌릴까요?\n"
            "(Ctrl+Z 로 다시 되살릴 수 있습니다)",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        if self.canvas.reset_edits():
            self.info_label.setText("원본으로 되돌렸습니다")

    # ------------------------------------------------------------- 확대
    def _zoom_by(self, factor):
        self.canvas.zoom_at(factor)          # 화면 가운데를 기준으로
        self._refresh_actions()

    def _set_zoom(self, zoom):
        self.canvas.set_zoom(zoom)
        self._refresh_actions()

    def fit_to_window(self):
        area = self.scroll.viewport().size()
        image = self.canvas.base
        if image.width() and image.height():
            self._set_zoom(min(area.width() / image.width(),
                               area.height() / image.height()))

    def wheelEvent(self, event):
        modifiers = event.modifiers()
        if modifiers & Qt.ControlModifier:
            delta = event.angleDelta().y()
            factor = 1.15 if delta > 0 else 1 / 1.15
            # 마우스가 가리키던 지점이 제자리에 남도록
            point = self.scroll.viewport().mapFromGlobal(QCursor.pos())
            if not self.scroll.viewport().rect().contains(point):
                point = None
            self.canvas.zoom_at(factor, point)
            self._refresh_actions()
            event.accept()
            return
        if modifiers & Qt.ShiftModifier:
            delta = event.angleDelta().y() or event.angleDelta().x()
            if delta:
                bar = self.scroll.horizontalScrollBar()
                bar.setValue(bar.value() - delta)
                event.accept()
                return
        super().wheelEvent(event)

    # ------------------------------------------------------------- 손 도구
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space and not event.isAutoRepeat() \
                and not self.canvas._text_box_open():
            self.canvas.set_space_held(True)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self.canvas.set_space_held(False)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def changeEvent(self, event):
        # 창이 비활성화되면 손 모양이 남아 있지 않도록 되돌린다
        if event.type() == QEvent.ActivationChange and not self.isActiveWindow():
            self.canvas.set_space_held(False)
        super().changeEvent(event)

    # ------------------------------------------------------------- 동작
    def image(self) -> QImage:
        return self.canvas.render_image()

    def save(self):
        path = saver.save_image(self.image())
        if path:
            self.saved_path = path
            self._saved_clean = True
            self.info_label.setText("저장했습니다 → %s" % path)
            self.setWindowTitle("캡처 편집 — %s" % os.path.basename(path))
        else:
            QMessageBox.warning(self, "저장 실패",
                                "파일을 저장하지 못했습니다.\n"
                                "설정에서 저장 폴더를 확인해 주세요.")
        return path

    def save_as(self):
        suggested = saver.suggest_path()
        path, _selected = QFileDialog.getSaveFileName(
            self, "다른 이름으로 저장", str(suggested),
            "PNG 이미지 (*.png);;JPEG 이미지 (*.jpg);;비트맵 (*.bmp)")
        if not path:
            return None
        if saver.write_image(self.image(), path):
            saver.register(path, self.image())
            self.saved_path = path
            self._saved_clean = True
            self.info_label.setText("저장했습니다 → %s" % path)
            return path
        QMessageBox.warning(self, "저장 실패", "파일을 저장하지 못했습니다.")
        return None

    def copy_to_clipboard(self):
        QGuiApplication.clipboard().setImage(self.image())
        self.info_label.setText("클립보드에 복사했습니다")

    def pin(self):
        self.pinRequested.emit(self.image())

    def run_ocr(self):
        self.ocrRequested.emit(self.image())

    def print_image(self):
        """미리보기 창을 먼저 보여 주고, 거기서 인쇄한다."""
        try:
            from PySide6.QtGui import QPageLayout
            from PySide6.QtPrintSupport import QPrinter, QPrintPreviewDialog
        except Exception:
            QMessageBox.information(self, "인쇄", "이 환경에서는 인쇄를 지원하지 않습니다.")
            return
        image = self.image()
        if image.isNull():
            return
        printer = QPrinter(QPrinter.HighResolution)
        printer.setDocName(self.windowTitle() or "캡처")
        # 가로로 긴 캡처는 용지도 가로로 (미리보기에서 바꿀 수 있음)
        if image.width() > image.height():
            printer.setPageOrientation(QPageLayout.Landscape)
        else:
            printer.setPageOrientation(QPageLayout.Portrait)
        # 캡처 픽셀은 실제 화면 픽셀 → 화면 배율을 알아야 원래 크기로 찍힌다
        screen_dpi = 96.0 * (self.devicePixelRatioF() or 1.0)

        dialog = QPrintPreviewDialog(printer, self)
        dialog.setWindowTitle("인쇄 미리보기")
        dialog.resize(900, 760)
        dialog.paintRequested.connect(
            lambda p: self._paint_print_page(p, image, screen_dpi))
        dialog.exec()

    def _paint_print_page(self, printer, image, screen_dpi):
        """이미지를 화면에서 보던 크기로, 용지보다 크면 줄여서 가운데에 그린다."""
        painter = QPainter()
        if not painter.begin(printer):
            QMessageBox.warning(self, "인쇄 실패", "프린터를 시작하지 못했습니다.")
            return
        page_w, page_h = printer.width(), printer.height()
        ratio = printer.resolution() / screen_dpi
        w, h = image.width() * ratio, image.height() * ratio
        if w > page_w or h > page_h:
            shrink = min(page_w / w, page_h / h)
            w, h = w * shrink, h * shrink
        target = QRectF((page_w - w) / 2, (page_h - h) / 2, w, h)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawImage(target, image)
        painter.end()

    # ------------------------------------------------------------- 갱신
    def _refresh_actions(self):
        self.undo_action.setEnabled(self.canvas.can_undo())
        self.redo_action.setEnabled(self.canvas.can_redo())
        self.zoom_label.setText(" %d%%  ·  %d × %d " % (
            round(self.canvas.zoom * 100),
            self.canvas.base.width(), self.canvas.base.height()))
        if self.canvas.is_modified():
            self._saved_clean = False

    def _show_position(self, text):
        self.pos_label.setText(" " + text + " ")

    # ------------------------------------------------------------- 종료
    def closeEvent(self, event):
        if self.canvas.is_modified() and not self._saved_clean:
            answer = QMessageBox.question(
                self, "편집 내용",
                "편집한 내용을 저장하지 않고 닫을까요?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save)
            if answer == QMessageBox.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.Save:
                if not self.save():
                    event.ignore()
                    return
        self.closed.emit(self)
        super().closeEvent(event)
