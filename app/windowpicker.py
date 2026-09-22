# -*- coding: utf-8 -*-
"""열려 있는 창을 골라서 캡처하는 창."""
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout, QWidget)

import windowcap

THUMB = QSize(196, 124)
CELL = QSize(216, 186)
CHUNK = 3


def _placeholder(text="미리보기 없음"):
    pixmap = QPixmap(THUMB)
    pixmap.fill(QColor("#e9edf3"))
    painter = QPainter(pixmap)
    painter.setPen(QPen(QColor("#aab3c0"), 1))
    painter.drawRect(0, 0, THUMB.width() - 1, THUMB.height() - 1)
    painter.setPen(QColor("#8b95a5"))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, text)
    painter.end()
    return QIcon(pixmap)


def _framed(image, exact=True):
    pixmap = QPixmap(THUMB)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    x = (THUMB.width() - image.width()) // 2
    y = (THUMB.height() - image.height()) // 2
    painter.fillRect(x, y, image.width(), image.height(), QColor("#ffffff"))
    painter.drawImage(x, y, image)
    painter.setPen(QPen(QColor(0, 0, 0, 70), 1))
    painter.setBrush(Qt.NoBrush)
    painter.drawRect(x, y, image.width() - 1, image.height() - 1)
    if not exact:
        # 화면에서 오려온 미리보기 — 가려진 모습일 수 있다
        text = "가려짐"
        font = painter.font()
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        chip_w = metrics.horizontalAdvance(text) + 12
        chip_h = metrics.height() + 4
        chip_x = x + image.width() - chip_w - 4
        chip_y = y + 4
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(200, 60, 40, 230))
        painter.drawRoundedRect(chip_x, chip_y, chip_w, chip_h, 3, 3)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(chip_x, chip_y, chip_w, chip_h, Qt.AlignCenter, text)
    painter.end()
    return QIcon(pixmap)


class WindowPicker(QWidget):
    """겹쳐 있는 창도 골라서 통째로 캡처할 수 있게 목록으로 보여 준다."""

    picked = Signal(object)          # 고른 창 정보(dict)

    def __init__(self, exclude_hwnds=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("창 골라서 캡처")
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.resize(760, 580)
        self._exclude = list(exclude_hwnds)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        top = QHBoxLayout()
        self.count_label = QLabel("", self)
        self.count_label.setStyleSheet("color: #555;")
        top.addWidget(self.count_label, 1)
        refresh = QPushButton("새로 고침", self)
        refresh.clicked.connect(self.reload)
        top.addWidget(refresh)
        layout.addLayout(top)

        self.list = QListWidget(self)
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(THUMB)
        self.list.setGridSize(CELL)
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setSpacing(6)
        self.list.setWordWrap(True)
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.itemActivated.connect(self._choose)
        self.list.itemDoubleClicked.connect(self._choose)
        layout.addWidget(self.list, 1)

        bottom = QHBoxLayout()
        hint = QLabel("창을 두 번 누르면 그 창만 통째로 캡처됩니다. "
                      "다른 창에 가려져 있어도 됩니다.  "
                      "('가려짐' 표시는 미리보기에만 해당합니다)", self)
        hint.setStyleSheet("color: #777; font-size: 12px;")
        hint.setWordWrap(True)
        bottom.addWidget(hint, 1)
        self.capture_button = QPushButton("이 창 캡처", self)
        self.capture_button.setDefault(True)
        self.capture_button.clicked.connect(
            lambda: self._choose(self.list.currentItem()))
        close_button = QPushButton("닫기", self)
        close_button.clicked.connect(self.close)
        bottom.addWidget(self.capture_button)
        bottom.addWidget(close_button)
        layout.addLayout(bottom)

        self._queue = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._load_chunk)
        self.reload()

    # ------------------------------------------------------------ 목록
    def reload(self):
        self.list.clear()
        self._timer.stop()
        try:
            entries = windowcap.list_windows(self._exclude)
        except Exception:
            entries = []
        self.count_label.setText("열려 있는 창 %d개" % len(entries))
        if not entries:
            self.count_label.setText(
                "고를 수 있는 창을 찾지 못했습니다 (Windows 에서만 동작합니다)")
        self._queue = []
        for entry in entries:
            name = entry["process"].replace(".exe", "") or "프로그램"
            title = entry["title"]
            if len(title) > 40:
                title = title[:39] + "…"
            label = "%s\n%s" % (name, title)
            if entry["minimized"]:
                label += "  (최소화됨)"
            item = QListWidgetItem(_placeholder("불러오는 중"), label)
            item.setData(Qt.UserRole, entry)
            item.setToolTip("%s\n%s" % (entry["process"], entry["title"]))
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            item.setSizeHint(CELL)
            self.list.addItem(item)
            self._queue.append(item)
        if self._queue:
            self.list.setCurrentRow(0)
            self._timer.start(10)

    def _load_chunk(self):
        for _ in range(CHUNK):
            if not self._queue:
                self._timer.stop()
                return
            item = self._queue.pop(0)
            entry = item.data(Qt.UserRole)
            exact = True
            try:
                result = windowcap.thumbnail(entry, max(THUMB.width(),
                                                        THUMB.height()))
                if isinstance(result, tuple):
                    image, exact = result
                else:
                    image = result
            except Exception:
                image = None
            if image is not None and not image.isNull():
                item.setIcon(_framed(image, exact))
                if not exact:
                    item.setToolTip(item.toolTip() +
                                    "\n\n미리보기는 가려진 모습이지만, "
                                    "캡처하면 온전하게 나옵니다.")
            else:
                item.setIcon(_placeholder())

    # ------------------------------------------------------------ 선택
    def _choose(self, item):
        if item is None:
            return
        entry = item.data(Qt.UserRole)
        if not entry:
            return
        self.hide()
        self.picked.emit(entry)
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
