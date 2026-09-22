# -*- coding: utf-8 -*-
"""캡처 직후 미니 미리보기 — 화면 구석에 잠깐 떠서 바로 끌어다 쓰게 한다.

  * 썸네일을 끌어서 카톡·메일·탐색기에 놓기
  * 썸네일 클릭 → 편집창
  * [핀] [저장] [복사] 버튼
  * 마우스를 올려 두면 사라지지 않는다
"""
import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QCursor, QFont, QGuiApplication, QImage,
                           QPainter, QPen)
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

import saver

THUMB_W = 250
THUMB_H = 150
PAD = 10

_STYLE = """
QPushButton {
    color: #eef2f8; background: rgba(255,255,255,22);
    border: 1px solid rgba(255,255,255,40); border-radius: 5px;
    padding: 4px 0px; font-size: 12px;
}
QPushButton:hover { background: rgba(255,255,255,50); }
QPushButton#close { border: none; background: transparent; font-size: 14px;
                    color: #aab2c0; padding: 0px; }
QPushButton#close:hover { color: #ffffff; }
"""


class QuickView(QWidget):
    editRequested = Signal(QImage, object)     # 이미지, 저장된 경로(없으면 None)
    pinRequested = Signal(QImage)

    def __init__(self, image: QImage, saved_path=None, note="", seconds=6,
                 parent=None):
        super().__init__(parent)
        self.image = image
        self.saved_path = saved_path
        self.note = note
        self.seconds = max(2, int(seconds or 6))
        self._press = None
        self._hovered = False

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                            Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setStyleSheet(_STYLE)

        self.thumb = image.scaled(THUMB_W, THUMB_H, Qt.KeepAspectRatio,
                                  Qt.SmoothTransformation)
        width = THUMB_W + PAD * 2
        self.thumb_rect = QRect(PAD + (THUMB_W - self.thumb.width()) // 2,
                                PAD + 20 + (THUMB_H - self.thumb.height()) // 2,
                                self.thumb.width(), self.thumb.height())
        height = PAD + 20 + THUMB_H + 8 + 30 + PAD
        self.setFixedSize(width, height)
        self._build_buttons()

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._timeout)

    def _build_buttons(self):
        close = QPushButton("✕", self)
        close.setObjectName("close")
        close.setGeometry(self.width() - 30, 6, 22, 20)
        close.setCursor(Qt.PointingHandCursor)
        close.setToolTip("닫기")
        close.clicked.connect(self.close)

        holder = QWidget(self)
        holder.setGeometry(PAD, self.height() - PAD - 30, THUMB_W, 30)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        buttons = [
            ("편집", self._edit, "편집창에서 엽니다"),
            ("핀", self._pin, "화면 위에 고정합니다"),
            ("폴더" if self.saved_path else "저장", self._save_or_folder,
             "저장된 폴더를 엽니다" if self.saved_path else "파일로 저장합니다"),
            ("복사", self._copy, "클립보드에 복사합니다"),
        ]
        self.buttons = {}
        for text, slot, tip in buttons:
            button = QPushButton(text, holder)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(tip)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(slot)
            row.addWidget(button)
            self.buttons[text] = button

    # ------------------------------------------------------------ 표시
    def popup(self):
        screen = QGuiApplication.screenAt(QCursor.pos()) or \
            QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 16,
                      area.bottom() - self.height() - 16)
        self.show()
        self.raise_()
        self.timer.start(self.seconds * 1000)

    def _timeout(self):
        if self._hovered:
            return
        self.close()

    def enterEvent(self, event):
        self._hovered = True
        self.timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.timer.start(2500)
        super().leaveEvent(event)

    # ------------------------------------------------------------ 그리기
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
        painter.setBrush(QColor(22, 24, 29, 240))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                                10, 10)
        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)
        painter.setPen(QColor(170, 180, 196))
        head = self.note or ("저장됨" if self.saved_path else "캡처됨")
        painter.drawText(QRect(PAD + 2, 6, self.width() - 50, 20),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         "%s · 끌어서 다른 창에 놓기" % head)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 70))
        painter.drawRect(QRect(PAD, PAD + 20, THUMB_W, THUMB_H))
        painter.drawImage(self.thumb_rect, self.thumb)
        painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.thumb_rect.adjusted(0, 0, -1, -1))
        painter.end()

    # ------------------------------------------------------------ 입력
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and \
                self.thumb_rect.contains(event.position().toPoint()):
            self._press = event.position().toPoint()

    def mouseMoveEvent(self, event):
        inside = self.thumb_rect.contains(event.position().toPoint())
        self.setCursor(Qt.OpenHandCursor if inside else Qt.ArrowCursor)
        if self._press is None or not (event.buttons() & Qt.LeftButton):
            return
        if (event.position().toPoint() - self._press).manhattanLength() < 8:
            return
        self._press = None
        import dragout
        path = self.saved_path or getattr(self, "drag_path", None)
        if dragout.start_drag(self, self.image, path):
            self.close()

    def mouseReleaseEvent(self, event):
        if self._press is not None and event.button() == Qt.LeftButton:
            self._press = None
            self._edit()

    # ------------------------------------------------------------ 동작
    def _edit(self):
        self.editRequested.emit(self.image, self.saved_path)
        self.close()

    def _pin(self):
        self.pinRequested.emit(self.image)
        self.close()

    def _copy(self):
        QGuiApplication.clipboard().setImage(self.image)
        self.note = "복사함"
        self.update()
        self.timer.start(1800)

    def _save_or_folder(self):
        if self.saved_path:
            _reveal(self.saved_path)
            self.close()
            return
        path = saver.save_image(self.image)
        if path:
            self.saved_path = path
            self.note = "저장함"
            button = self.buttons.get("저장")
            if button is not None:
                button.setText("폴더")
                button.setToolTip(path)
            self.update()
            self.timer.start(2500)


def _reveal(path):
    """탐색기에서 파일을 골라 둔 채로 연다."""
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(Path(path))])
            return
    except Exception:
        pass
    try:
        from settingsdlg import open_folder
        open_folder(Path(path).parent)
    except Exception:
        pass
