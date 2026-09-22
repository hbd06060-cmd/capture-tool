# -*- coding: utf-8 -*-
"""최근 캡처 목록 창."""
import os

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMenu,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)

import recent
import winutil
from config import CONFIG
from settingsdlg import open_folder

THUMB = QSize(168, 108)
CELL = QSize(190, 168)
CHUNK = 8               # 한 번에 읽어들일 썸네일 수


def _thumbnail(path) -> QIcon:
    image = QImage(str(path))
    if image.isNull():
        return QIcon()
    scaled = image.scaled(THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    canvas = QPixmap(THUMB)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    x = (THUMB.width() - scaled.width()) // 2
    y = (THUMB.height() - scaled.height()) // 2
    painter.fillRect(x, y, scaled.width(), scaled.height(), QColor("#ffffff"))
    painter.drawImage(x, y, scaled)
    painter.setPen(QPen(QColor(0, 0, 0, 70), 1))
    painter.setBrush(Qt.NoBrush)
    painter.drawRect(x, y, scaled.width() - 1, scaled.height() - 1)
    painter.end()
    return QIcon(canvas)


class RecentGallery(QWidget):
    """최근에 찍은 캡처를 격자로 보여 준다."""

    openRequested = Signal(str)
    pinRequested = Signal(str)
    ocrRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("최근 캡처")
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.resize(880, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        top = QHBoxLayout()
        self.count_label = QLabel("", self)
        self.count_label.setStyleSheet("color: #555;")
        top.addWidget(self.count_label, 1)
        refresh = QPushButton("새로 고침", self)
        refresh.clicked.connect(self.reload)
        folder = QPushButton("저장 폴더 열기", self)
        folder.clicked.connect(lambda: open_folder(CONFIG.save_dir()))
        top.addWidget(refresh)
        top.addWidget(folder)
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
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemActivated.connect(self._activate)
        self.list.itemDoubleClicked.connect(self._activate)
        layout.addWidget(self.list, 1)

        self.hint = QLabel(
            "더블클릭하면 편집창이 열립니다  ·  오른쪽 클릭으로 복사·핀 고정·삭제  ·  "
            "'(임시)' 는 저장하지 않고 복사만 한 캡처입니다", self)
        self.hint.setStyleSheet("color: #777; font-size: 12px;")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        self._queue = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._load_chunk)
        self.reload()

    # ------------------------------------------------------------ 목록
    def reload(self):
        self.list.clear()
        self._timer.stop()
        items = recent.entries()
        self.count_label.setText("최근 캡처 %d개" % len(items))
        self._queue = []
        for entry in items:
            label = "%s %s" % (recent.day_label(entry),
                               recent.label_for(entry))
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, entry["path"])
            item.setToolTip(entry["path"])
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            item.setSizeHint(CELL)
            self.list.addItem(item)
            self._queue.append(item)
        if self._queue:
            self._timer.start(10)
        else:
            self.count_label.setText("아직 캡처한 것이 없습니다")

    def _load_chunk(self):
        for _ in range(CHUNK):
            if not self._queue:
                self._timer.stop()
                return
            item = self._queue.pop(0)
            try:
                item.setIcon(_thumbnail(item.data(Qt.UserRole)))
            except Exception:
                pass

    # ------------------------------------------------------------ 동작
    def _selected_paths(self):
        return [i.data(Qt.UserRole) for i in self.list.selectedItems()]

    def _activate(self, item):
        path = item.data(Qt.UserRole)
        if path:
            self.openRequested.emit(path)

    def _menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        path = item.data(Qt.UserRole)
        menu = QMenu(self)
        entries = [
            ("편집창에서 열기", lambda: self.openRequested.emit(path)),
            ("클립보드로 복사", lambda: self._copy(path)),
            ("화면에 핀 고정", lambda: self.pinRequested.emit(path)),
            ("글자 추출", lambda: self.ocrRequested.emit(path)),
            (None, None),
            ("탐색기에서 보기", lambda: self._reveal(path)),
            ("휴지통으로 보내기", self._delete_selected),
        ]
        for text, slot in entries:
            if text is None:
                menu.addSeparator()
                continue
            action = QAction(text, menu)
            action.triggered.connect(slot)
            menu.addAction(action)
        menu.exec(self.list.mapToGlobal(pos))

    def _copy(self, path):
        from PySide6.QtGui import QGuiApplication
        image = QImage(str(path))
        if not image.isNull():
            QGuiApplication.clipboard().setImage(image)

    def _reveal(self, path):
        try:
            if winutil.IS_WIN:
                import subprocess
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                open_folder(os.path.dirname(path))
        except Exception:
            open_folder(os.path.dirname(path))

    def _delete_selected(self):
        paths = self._selected_paths()
        if not paths:
            return
        answer = QMessageBox.question(
            self, "휴지통으로 보내기",
            "%d개를 휴지통으로 보낼까요?\n휴지통에서 되살릴 수 있습니다." % len(paths),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        moved = winutil.move_to_recycle_bin(paths)
        if not moved:
            # 휴지통이 안 되면 직접 지운다
            for path in paths:
                try:
                    os.remove(path)
                    moved += 1
                except Exception:
                    pass
        for path in paths:
            recent.forget(path)
        self.reload()
