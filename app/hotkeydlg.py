# -*- coding: utf-8 -*-
"""단축키 진단 — 어떤 키가 왜 막혔는지 보여 주고, 빈 키를 찾아 준다.

RegisterHotKey 가 실패한 이유(오류 1409 = 다른 프로그램이 이미 사용 중)를
그대로 보여 주고, 눌러서 바로 대체 키를 잡을 수 있게 한다.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

import winutil
from config import CONFIG, HOTKEY_LABELS

OK_STYLE = "color: #2e7d32;"
WARN_STYLE = "color: #c62828; font-weight: 600;"
MOVED_STYLE = "color: #ef6c00;"

# 사용자가 바로 골라 볼 만한 여벌 조합
SPARE_KEYS = ["Ctrl+Shift+F1", "Ctrl+Shift+F2", "Ctrl+Shift+F3",
              "Ctrl+Shift+F4", "Ctrl+Alt+F5", "Ctrl+Alt+F6"]


class HotkeyDoctor(QDialog):
    """지금 단축키가 실제로 잡혔는지 하나씩 확인해서 보여 준다."""

    changed = Signal()

    def __init__(self, hwnd, status=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("단축키 진단")
        self.resize(660, 560)
        self._hwnd = int(hwnd)
        self._status = dict(status or {})
        self._rows = {}

        layout = QVBoxLayout(self)
        intro = QLabel(
            "지금 각 단축키가 실제로 잡혀 있는지 Windows 에 하나씩 물어본 "
            "결과입니다.\n빨간 항목은 <b>다른 프로그램이 먼저 그 키를 "
            "쓰고 있다</b>는 뜻입니다. [빈 키로 바꾸기] 를 누르면 지금 "
            "비어 있는 조합을 찾아서 바로 적용합니다.", self)
        intro.setTextFormat(Qt.RichText)
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #444;")
        layout.addWidget(intro)

        area = QScrollArea(self)
        area.setWidgetResizable(True)
        holder = QWidget(area)
        self._rows_layout = QVBoxLayout(holder)
        self._rows_layout.setSpacing(6)
        area.setWidget(holder)
        layout.addWidget(area, 1)

        self.summary = QLabel("", self)
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("color: #555;")
        layout.addWidget(self.summary)

        row = QHBoxLayout()
        recheck = QPushButton("다시 확인", self)
        recheck.clicked.connect(self.refresh)
        fix_all = QPushButton("막힌 것 모두 빈 키로 바꾸기", self)
        fix_all.clicked.connect(self._fix_all)
        row.addWidget(recheck)
        row.addWidget(fix_all)
        row.addStretch(1)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        buttons.button(QDialogButtonBox.Close).setText("닫기")
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self._build_rows()
        self.refresh()

    # ------------------------------------------------------------ 화면
    def _build_rows(self):
        for action in HOTKEY_LABELS:
            line = QWidget(self)
            row = QHBoxLayout(line)
            row.setContentsMargins(0, 0, 0, 0)

            name = QLabel(HOTKEY_LABELS[action], line)
            name.setMinimumWidth(150)
            key = QLabel("", line)
            key.setMinimumWidth(130)
            key.setStyleSheet("font-family: Consolas, monospace;")
            state = QLabel("", line)
            state.setMinimumWidth(190)
            button = QPushButton("빈 키로 바꾸기", line)
            button.clicked.connect(
                lambda _=False, a=action: self._fix_one(a))

            row.addWidget(name)
            row.addWidget(key)
            row.addWidget(state, 1)
            row.addWidget(button)
            self._rows_layout.addWidget(line)
            self._rows[action] = (key, state, button)
        self._rows_layout.addStretch(1)

    # ------------------------------------------------------------ 확인
    def _check(self, action):
        """이 단축키를 지금 쓸 수 있는지 실제로 물어본다.

        이미 우리가 잡고 있는 키는 '등록됨' 이므로 시험 등록이 실패한다.
        그래서 먼저 지금 상태를 보고, 우리 것이면 정상으로 친다.
        """
        text = CONFIG.hotkey(action)
        if not text:
            return "none", 0
        registered, code, replaced = self._status.get(action, ("", 0, False))
        if registered:
            return ("moved" if replaced else "ok"), code
        if not winutil.IS_WIN:
            return "unknown", -1
        ok, probe_code = winutil.hotkey_available(self._hwnd, text)
        return ("ok" if ok else "blocked"), (code or probe_code)

    def refresh(self):
        blocked = 0
        moved = 0
        for action, (key_label, state_label, button) in self._rows.items():
            text = CONFIG.hotkey(action)
            key_label.setText(text or "(없음)")
            kind, code = self._check(action)
            if kind == "none":
                state_label.setText("단축키가 정해져 있지 않습니다")
                state_label.setStyleSheet("color: #888;")
                button.setEnabled(True)
            elif kind == "ok":
                state_label.setText("정상 — 잘 잡혀 있습니다")
                state_label.setStyleSheet(OK_STYLE)
                button.setEnabled(False)
            elif kind == "moved":
                state_label.setText("겹쳐서 이 키로 바꿔 잡았습니다")
                state_label.setStyleSheet(MOVED_STYLE)
                button.setEnabled(True)
                moved += 1
            elif kind == "blocked":
                state_label.setText(winutil.hotkey_reason(code))
                state_label.setStyleSheet(WARN_STYLE)
                button.setEnabled(True)
                blocked += 1
            else:
                state_label.setText("확인할 수 없습니다 (Windows 전용)")
                state_label.setStyleSheet("color: #888;")
                button.setEnabled(False)

        if blocked:
            self.summary.setText(
                "<b>%d개가 막혀 있습니다.</b> 다른 프로그램을 먼저 켰기 "
                "때문이며, 캡처도구 잘못이 아닙니다. 막힌 기능도 "
                "<b>트레이 아이콘 메뉴에서는 그대로 쓸 수 있습니다.</b>"
                % blocked)
            self.summary.setTextFormat(Qt.RichText)
        elif moved:
            self.summary.setText(
                "%d개가 겹쳐서 다른 키로 바꿔 잡았습니다. 위 목록의 키를 "
                "쓰시면 됩니다." % moved)
        else:
            self.summary.setText("모든 단축키가 정상으로 잡혀 있습니다.")

    # ------------------------------------------------------------ 고치기
    def _fix_one(self, action):
        text = CONFIG.hotkey(action)
        used = {CONFIG.hotkey(a) for a in HOTKEY_LABELS if a != action}
        candidate = ""
        for option in winutil.alternatives(text, SPARE_KEYS):
            if option in used:
                continue
            ok, _code = winutil.hotkey_available(self._hwnd, option)
            if ok:
                candidate = option
                break
        if not candidate:
            self.summary.setText(
                "비어 있는 조합을 찾지 못했습니다. 설정 → 단축키 에서 "
                "직접 원하는 키를 정해 주세요.")
            return
        CONFIG["hotkeys"][action] = candidate
        CONFIG.save()
        self._status.pop(action, None)
        self.changed.emit()              # 본체가 다시 등록한다
        self.refresh()
        self.summary.setText("%s 를 %s 로 바꿨습니다." %
                             (HOTKEY_LABELS.get(action, action), candidate))

    def _fix_all(self):
        fixed = 0
        for action in list(self._rows):
            kind, _code = self._check(action)
            if kind in ("blocked", "none"):
                before = CONFIG.hotkey(action)
                self._fix_one(action)
                if CONFIG.hotkey(action) != before:
                    fixed += 1
        self.refresh()
        if fixed:
            self.summary.setText("%d개를 빈 키로 바꿨습니다." % fixed)
        else:
            self.summary.setText("바꿀 것이 없습니다.")

    def set_status(self, status):
        self._status = dict(status or {})
        self.refresh()
