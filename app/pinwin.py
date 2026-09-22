# -*- coding: utf-8 -*-
"""핀 고정 창 — 캡처한 이미지를 화면 맨 위에 띄워 둔다.

v1.10 에서 더한 것
  * 클릭 통과 — 핀은 보이지만 마우스는 아래 창으로 (시안 겹쳐 놓고 작업)
  * 잠금 — 실수로 옮기거나 크기가 바뀌지 않게
  * 작게 접기 — 참고 이미지를 구석에 조그맣게
  * 회전·뒤집기, 가운데 버튼으로 원래대로
  * Ctrl+끌기 — 카톡·메일 창에 바로 놓기
  * 클립보드의 글자·색 코드도 핀으로 (text_card / color_card)
"""
import re

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QAction, QColor, QCursor, QFont, QGuiApplication,
                           QImage, QPainter, QPen, QPolygon, QTextDocument,
                           QTransform)
from PySide6.QtWidgets import QMenu, QWidget

import saver
import winutil

FOLD_SIDE = 120            # 접었을 때 긴 변 길이(논리 픽셀)


class PinWindow(QWidget):
    editRequested = Signal(QImage)
    ocrRequested = Signal(QImage)
    qrRequested = Signal(QImage)
    closed = Signal(object)
    passthroughChanged = Signal(object)

    def __init__(self, image: QImage, at=None, parent=None, scale=None,
                 opacity=None, locked=False):
        super().__init__(parent)
        self.image = image
        self.scale = 1.0
        self.locked = bool(locked)
        self.passthrough = False
        self.folded = False
        self._unfold_scale = 1.0
        self._drag_offset = None
        self._press_pos = None
        self._export_drag = False
        self.forget_on_close = False     # True 면 '닫은 핀 되살리기' 에 안 남김

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                            Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setWindowTitle("고정된 캡처")
        self.setCursor(Qt.OpenHandCursor)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self._refresh_tooltip()

        self._apply_scale(self._initial_scale() if scale is None else scale)
        if opacity is not None:
            self.setWindowOpacity(max(0.15, min(1.0, float(opacity))))
        self._place(at)

    # ------------------------------------------------------------ 배치
    def _initial_scale(self):
        screen = QGuiApplication.screenAt(QCursor.pos()) or \
            QGuiApplication.primaryScreen()
        if not screen:
            return 1.0
        available = screen.availableGeometry()
        limit_w = available.width() * 0.8
        limit_h = available.height() * 0.8
        scale = 1.0
        if self.image.width() > limit_w or self.image.height() > limit_h:
            scale = min(limit_w / max(1, self.image.width()),
                        limit_h / max(1, self.image.height()))
        return max(0.05, min(1.0, scale))

    def _place(self, at):
        screen = QGuiApplication.screenAt(QCursor.pos()) or \
            QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        if at is not None:
            pos = QPoint(at)
            here = QGuiApplication.screenAt(pos)
            if here is not None:
                available = here.availableGeometry()
        elif available is not None:
            pos = QPoint(available.right() - self.width() - 40,
                         available.top() + 60)
        else:
            pos = QPoint(80, 80)
        if available is not None:
            pos.setX(max(available.left(),
                         min(pos.x(), available.right() - self.width())))
            pos.setY(max(available.top(),
                         min(pos.y(), available.bottom() - self.height())))
        self.move(pos)

    def _apply_scale(self, scale):
        self.scale = max(0.05, min(8.0, scale))
        width = max(24, int(self.image.width() * self.scale))
        height = max(24, int(self.image.height() * self.scale))
        self.setFixedSize(QSize(width, height))
        self.update()

    def _refresh_tooltip(self):
        if self.locked:
            self.setToolTip("잠긴 핀 — L 로 풀기 · 오른쪽 클릭: 메뉴")
            return
        self.setToolTip(
            "드래그: 이동 · 휠: 크기 · Ctrl+휠: 투명도 · 가운데 버튼: 원래대로\n"
            "더블클릭: 복사 · Shift+더블클릭 / F: 작게 접기 · Ctrl+끌기: 다른 창에 놓기\n"
            "L 잠금 · 1/2 회전 · 3/4 뒤집기 · T 글자 추출 · Q QR 읽기\n"
            "오른쪽 클릭: 메뉴 · Esc: 닫기")

    # ---------------------------------------------------------- 그리기
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawImage(QRectF(0, 0, self.width(), self.height()),
                          self.image, QRectF(self.image.rect()))
        painter.setBrush(Qt.NoBrush)
        if self.passthrough:
            pen = QPen(QColor(60, 150, 255, 230), 2)
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.drawRect(1, 1, self.width() - 2, self.height() - 2)
        elif self.folded:
            painter.setPen(QPen(QColor(60, 150, 255, 220), 2))
            painter.drawRect(1, 1, self.width() - 2, self.height() - 2)
        else:
            painter.setPen(QPen(QColor(0, 0, 0, 120), 1))
            painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
            painter.setPen(QPen(QColor(255, 255, 255, 90), 1))
            painter.drawRect(1, 1, self.width() - 3, self.height() - 3)
        if self.locked:
            # 왼쪽 위 작은 주황 삼각형 — 잠겨 있다는 표시
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 152, 0, 230))
            painter.drawPolygon(QPolygon([QPoint(0, 0), QPoint(12, 0),
                                          QPoint(0, 12)]))
        painter.end()

    # -------------------------------------------------------------- 입력
    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self.reset_view()
            return
        if event.button() == Qt.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._export_drag = bool(event.modifiers() & Qt.ControlModifier)
            if self.locked or self._export_drag:
                return
            self._drag_offset = event.globalPosition().toPoint() - \
                self.frameGeometry().topLeft()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton):
            return
        if self._export_drag and self._press_pos is not None:
            moved = event.globalPosition().toPoint() - self._press_pos
            if moved.manhattanLength() > 8:
                self._export_drag = False
                self._press_pos = None
                import dragout
                dragout.start_drag(self, self.image)
            return
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        self._press_pos = None
        self._export_drag = False
        self.setCursor(Qt.OpenHandCursor)

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if event.modifiers() & Qt.ShiftModifier:
            self.toggle_fold()
        else:
            self.copy()

    def wheelEvent(self, event):
        event.accept()
        if self.locked:
            return
        delta = event.angleDelta().y()
        if not delta:
            return
        if event.modifiers() & Qt.ControlModifier:
            step = 0.06 if delta > 0 else -0.06
            self.setWindowOpacity(max(0.15, min(1.0,
                                                self.windowOpacity() + step)))
            return
        if self.folded:
            return
        center = self.frameGeometry().center()
        self._apply_scale(self.scale * (1.1 if delta > 0 else 1 / 1.1))
        new_pos = center - QPoint(self.width() // 2, self.height() // 2)
        self.move(new_pos)

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.ControlModifier)
        if key == Qt.Key_Escape:
            self.close()
        elif ctrl and key == Qt.Key_C:
            self.copy()
        elif ctrl and key == Qt.Key_S:
            self.save()
        elif key == Qt.Key_L:
            self.set_locked(not self.locked)
        elif self.locked:
            super().keyPressEvent(event)
        elif key in (Qt.Key_0, Qt.Key_Home):
            self.reset_view()
        elif key == Qt.Key_F:
            self.toggle_fold()
        elif key == Qt.Key_1:
            self.transform("left")
        elif key == Qt.Key_2:
            self.transform("right")
        elif key == Qt.Key_3:
            self.transform("flip")
        elif key == Qt.Key_4:
            self.transform("flipv")
        elif key == Qt.Key_T:
            self.ocrRequested.emit(self.image)
        elif key == Qt.Key_Q:
            self.qrRequested.emit(self.image)
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------- 메뉴
    def _show_menu(self, pos):
        menu = QMenu(self)

        def add(text, slot, checked=None, parent_menu=None):
            target = parent_menu or menu
            action = QAction(text, target)
            if checked is not None:
                action.setCheckable(True)
                action.setChecked(bool(checked))
            action.triggered.connect(slot)
            target.addAction(action)
            return action

        add("복사\tCtrl+C", self.copy)
        add("저장\tCtrl+S", self.save)
        add("글자 추출\tT", lambda: self.ocrRequested.emit(self.image))
        add("QR 코드 읽기\tQ", lambda: self.qrRequested.emit(self.image))
        add("편집창에서 열기", lambda: self.editRequested.emit(self.image))
        menu.addSeparator()
        add("잠금 (옮기기·크기 막기)\tL", lambda: self.set_locked(not self.locked),
            checked=self.locked)
        add("클릭 통과 (마우스가 아래 창으로)",
            lambda: self.set_passthrough(True), checked=self.passthrough)
        add("작게 접기\tF", self.toggle_fold, checked=self.folded)
        menu.addSeparator()

        view_menu = menu.addMenu("크기 · 방향")
        add("실제 크기 (100%)\t0", lambda: self._set_scale_keep_center(1.0),
            parent_menu=view_menu)
        add("절반 크기 (50%)", lambda: self._set_scale_keep_center(0.5),
            parent_menu=view_menu)
        view_menu.addSeparator()
        add("왼쪽으로 돌리기\t1", lambda: self.transform("left"),
            parent_menu=view_menu)
        add("오른쪽으로 돌리기\t2", lambda: self.transform("right"),
            parent_menu=view_menu)
        add("좌우 뒤집기\t3", lambda: self.transform("flip"),
            parent_menu=view_menu)
        add("위아래 뒤집기\t4", lambda: self.transform("flipv"),
            parent_menu=view_menu)

        opacity_menu = menu.addMenu("투명도")
        for percent in (100, 85, 70, 55, 40, 25):
            add("%d%%" % percent,
                lambda _=False, p=percent: self.setWindowOpacity(p / 100.0),
                checked=abs(self.windowOpacity() * 100 - percent) < 3,
                parent_menu=opacity_menu)

        for action in menu.actions():
            if self.locked and action.text().startswith(("작게", "크기")):
                action.setEnabled(False)

        menu.addSeparator()
        add("닫기\tEsc", self.close)
        menu.exec(self.mapToGlobal(pos))

    # ------------------------------------------------------------- 동작
    def copy(self):
        QGuiApplication.clipboard().setImage(self.image)

    def save(self):
        return saver.save_image(self.image)

    def reset_view(self):
        if self.locked:
            return
        if self.folded:
            self.folded = False
        self._set_scale_keep_center(1.0 if self._fits(1.0)
                                    else self._initial_scale())
        self.setWindowOpacity(1.0)

    def _fits(self, scale):
        screen = QGuiApplication.screenAt(self.frameGeometry().center()) or \
            QGuiApplication.primaryScreen()
        if not screen:
            return True
        area = screen.availableGeometry()
        return (self.image.width() * scale <= area.width() and
                self.image.height() * scale <= area.height())

    def _set_scale_keep_center(self, scale):
        if self.locked:
            return
        center = self.frameGeometry().center()
        self._apply_scale(scale)
        self.move(center - QPoint(self.width() // 2, self.height() // 2))

    def set_locked(self, locked):
        self.locked = bool(locked)
        self._refresh_tooltip()
        self.update()

    def set_passthrough(self, enabled):
        """클릭 통과. 켜면 이 창은 마우스를 받지 않는다.

        끄는 방법: 전역 단축키(기본 Ctrl+Alt+T) 또는 트레이 메뉴.
        """
        enabled = bool(enabled)
        if enabled == self.passthrough:
            return
        if not winutil.set_click_through(int(self.winId()), enabled):
            if enabled:
                return
        self.passthrough = enabled
        if enabled and self.windowOpacity() > 0.8:
            self.setWindowOpacity(0.6)       # 통과 중이라는 게 보이게
        self.update()
        self.passthroughChanged.emit(self)

    def toggle_fold(self):
        if self.locked:
            return
        top_left = self.frameGeometry().topLeft()
        if self.folded:
            self.folded = False
            self._apply_scale(self._unfold_scale)
        else:
            self._unfold_scale = self.scale
            longest = max(1, self.image.width(), self.image.height())
            self.folded = True
            self._apply_scale(min(self.scale, FOLD_SIDE / float(longest)))
        self.move(top_left)
        self.update()

    def transform(self, mode):
        if self.locked:
            return
        center = self.frameGeometry().center()
        if mode in ("left", "right"):
            angle = -90 if mode == "left" else 90
            self.image = self.image.transformed(QTransform().rotate(angle))
        elif mode == "flip":
            self.image = self.image.mirrored(True, False)
        elif mode == "flipv":
            self.image = self.image.mirrored(False, True)
        self._apply_scale(self.scale)
        self.move(center - QPoint(self.width() // 2, self.height() // 2))

    def state(self):
        """되살리기·배치 저장용 상태."""
        geo = self.frameGeometry()
        return {"image": self.image,
                "x": geo.x(), "y": geo.y(),
                "scale": self._unfold_scale if self.folded else self.scale,
                "opacity": self.windowOpacity(),
                "locked": self.locked}

    def closeEvent(self, event):
        self.closed.emit(self)
        super().closeEvent(event)


# ------------------------------------------------ 클립보드 글자·색 → 핀 이미지
_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_RGB = re.compile(r"^(?:rgba?\s*\()?\s*(\d{1,3})\s*[,\s]\s*(\d{1,3})\s*[,\s]\s*"
                  r"(\d{1,3})\s*(?:[,\s]\s*[\d.]+\s*)?\)?$", re.IGNORECASE)


def parse_color(text):
    """'#1e88e5', '1e88e5', 'rgb(30,136,229)', '30, 136, 229' → QColor."""
    value = (text or "").strip().rstrip(";")
    if not value or len(value) > 40:
        return None
    match = _HEX.match(value)
    if match:
        digits = match.group(1)
        if not value.startswith("#"):
            # '#' 없이는 영문 단어(add, facade)·숫자(123456)와 헷갈리므로
            # 글자와 숫자가 섞인 6자리일 때만 색으로 본다
            if len(digits) != 6 or digits.isdigit() or digits.isalpha():
                return None
        if len(digits) == 3:
            digits = "".join(ch * 2 for ch in digits)
        return QColor("#" + digits)
    match = _RGB.match(value)
    if match:
        parts = [int(match.group(i)) for i in (1, 2, 3)]
        if all(0 <= p <= 255 for p in parts):
            return QColor(*parts)
    return None


def color_card(color: QColor) -> QImage:
    """색 견본 카드 — 큰 색 네모 + HEX · RGB 글자."""
    width, swatch_h, text_h = 220, 120, 58
    image = QImage(width, swatch_h + text_h, QImage.Format_RGB32)
    image.fill(QColor(250, 250, 252))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.fillRect(QRect(0, 0, width, swatch_h), color)
    font = QFont()
    font.setPointSize(13)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor(30, 32, 36))
    painter.drawText(QRect(12, swatch_h + 4, width - 24, 26),
                     Qt.AlignLeft | Qt.AlignVCenter, color.name().upper())
    small = QFont()
    small.setPointSize(10)
    painter.setFont(small)
    painter.setPen(QColor(100, 104, 112))
    painter.drawText(QRect(12, swatch_h + 28, width - 24, 22),
                     Qt.AlignLeft | Qt.AlignVCenter,
                     "RGB %d, %d, %d" % (color.red(), color.green(),
                                          color.blue()))
    painter.end()
    return image


def text_card(text="", html=None, max_width=520) -> QImage:
    """글자(또는 서식 있는 글자)를 흰 카드 이미지로 만든다."""
    doc = QTextDocument()
    font = QFont()
    font.setPointSize(11)
    doc.setDefaultFont(font)
    doc.setDocumentMargin(14)
    if html:
        doc.setHtml(html)
    else:
        doc.setPlainText(text)
    # 짧은 글은 딱 맞게, 긴 글은 max_width 에서 줄바꿈
    doc.setTextWidth(-1)
    ideal = doc.idealWidth() + 28
    doc.setTextWidth(max(120, min(max_width, ideal)))
    size = doc.size().toSize()
    height = min(size.height(), 1600)
    image = QImage(max(60, size.width()), max(30, height), QImage.Format_RGB32)
    image.fill(QColor(255, 255, 254))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    doc.drawContents(painter, QRectF(0, 0, image.width(), height))
    painter.end()
    return image
