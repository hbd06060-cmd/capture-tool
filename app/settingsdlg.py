# -*- coding: utf-8 -*-
"""설정 창."""
import copy
import os
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton,
                               QSpinBox, QTabWidget, QVBoxLayout, QWidget)

import winutil
from config import CONFIG, DEFAULTS, HOTKEY_LABELS, default_save_dir

NAME_PRESETS = [
    ("캡처_%Y%m%d_%H%M%S", "캡처_20260825_143012"),
    ("%Y-%m-%d_%H%M%S", "2026-08-25_143012"),
    ("screenshot_%Y%m%d_%H%M%S", "screenshot_20260825_143012"),
    ("캡처 %m월%d일 %H시%M분%S초", "캡처 08월25일 14시30분12초"),
    ("%W_%Y%m%d_%H%M%S", "카카오톡_20260825_143012"),
    ("캡처_%W_%Y%m%d", "캡처_카카오톡_20260825"),
]

# 파일 이름 미리보기에 쓸 가짜 창 제목
PREVIEW_WINDOW = "카카오톡"

_MOD_KEYS = (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta,
             Qt.Key_unknown)


class HotkeyEdit(QLineEdit):
    """단축키를 눌러서 지정하는 입력칸."""

    def __init__(self, value="", parent=None):
        super().__init__(value, parent)
        self.setReadOnly(True)
        self.setPlaceholderText("여기를 클릭하고 원하는 키를 누르세요")
        self.setCursor(Qt.PointingHandCursor)
        self.setClearButtonEnabled(True)

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Escape,):
            self.clearFocus()
            return
        if key in (Qt.Key_Backspace, Qt.Key_Delete):
            self.setText("")
            return
        if key in _MOD_KEYS:
            return
        modifiers = event.modifiers()
        sequence = QKeySequence(int(modifiers.value) | int(key))
        text = sequence.toString(QKeySequence.PortableText)
        if text:
            self.setText(text)

    def keyReleaseEvent(self, event):
        pass


class SettingsDialog(QDialog):
    applied = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("캡처도구 설정")
        self.setMinimumWidth(560)
        self._build()
        self._load()

    # ---------------------------------------------------------- 화면 구성
    def _build(self):
        layout = QVBoxLayout(self)
        tabs = QTabWidget(self)
        tabs.addTab(self._tab_general(), "저장 · 동작")
        tabs.addTab(self._tab_recent(), "목록 · 정리")
        tabs.addTab(self._tab_hotkeys(), "단축키")
        tabs.addTab(self._tab_etc(), "기타")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel |
            QDialogButtonBox.RestoreDefaults, self)
        buttons.button(QDialogButtonBox.Ok).setText("확인")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.button(QDialogButtonBox.RestoreDefaults).setText("기본값으로")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(
            self._restore_defaults)
        layout.addWidget(buttons)

    def _tab_general(self):
        page = QWidget(self)
        outer = QVBoxLayout(page)

        save_box = QGroupBox("저장", page)
        form = QFormLayout(save_box)

        row = QHBoxLayout()
        self.dir_edit = QLineEdit(save_box)
        self.dir_edit.setPlaceholderText(str(default_save_dir()))
        browse = QPushButton("찾아보기", save_box)
        browse.clicked.connect(self._browse_dir)
        open_btn = QPushButton("폴더 열기", save_box)
        open_btn.clicked.connect(lambda: open_folder(CONFIG.save_dir()))
        row.addWidget(self.dir_edit, 1)
        row.addWidget(browse)
        row.addWidget(open_btn)
        form.addRow("저장 폴더", row)

        self.pattern_combo = QComboBox(save_box)
        self.pattern_combo.setEditable(True)
        for pattern, _example in NAME_PRESETS:
            self.pattern_combo.addItem(pattern)
        self.pattern_combo.currentTextChanged.connect(self._update_preview)
        form.addRow("파일 이름", self.pattern_combo)

        self.preview_label = QLabel("", save_box)
        self.preview_label.setStyleSheet("color: #666;")
        form.addRow("", self.preview_label)

        pattern_note = QLabel(
            "%Y 년 · %m 월 · %d 일 · %H 시 · %M 분 · %S 초 · "
            "%W 캡처한 창 이름", save_box)
        pattern_note.setStyleSheet("color: #888; font-size: 12px;")
        pattern_note.setWordWrap(True)
        form.addRow("", pattern_note)

        self.split_combo = QComboBox(save_box)
        self.split_combo.addItem("안 함 — 한 폴더에 모두 저장", "")
        self.split_combo.addItem("월별 폴더 (2026-08)", "%Y-%m")
        self.split_combo.addItem("일별 폴더 (2026-08-25)", "%Y-%m-%d")
        form.addRow("폴더 분류", self.split_combo)

        fmt_row = QHBoxLayout()
        self.format_combo = QComboBox(save_box)
        self.format_combo.addItem("PNG — 화질 그대로, 글자가 선명함", "png")
        self.format_combo.addItem("JPG — 용량이 작음", "jpg")
        self.format_combo.currentIndexChanged.connect(self._update_preview)
        self.quality_spin = QSpinBox(save_box)
        self.quality_spin.setRange(40, 100)
        self.quality_spin.setSuffix(" 품질")
        fmt_row.addWidget(self.format_combo, 1)
        fmt_row.addWidget(self.quality_spin)
        form.addRow("이미지 형식", fmt_row)
        outer.addWidget(save_box)

        after_box = QGroupBox("캡처한 뒤 할 일", page)
        after_layout = QVBoxLayout(after_box)
        self.copy_check = QCheckBox("클립보드에 복사", after_box)
        self.save_check = QCheckBox("저장 폴더에 자동 저장", after_box)
        self.edit_check = QCheckBox("편집창 열기", after_box)
        self.bar_check = QCheckBox(
            "영역을 정하면 [편집·핀·글자추출·QR·복사·저장] 버튼 보여주기 "
            "(그때그때 골라서 쓰기)", after_box)
        for widget in (self.copy_check, self.save_check, self.edit_check,
                       self.bar_check):
            after_layout.addWidget(widget)
        note = QLabel("※ 위 세 가지는 버튼을 누르지 않고 Enter 로 넘어갔을 때의 "
                      "기본 동작입니다.", after_box)
        note.setWordWrap(True)
        note.setStyleSheet("color: #666;")
        after_layout.addWidget(note)

        quick_row = QHBoxLayout()
        self.quick_check = QCheckBox(
            "편집창을 열지 않을 때 구석에 작은 미리보기 띄우기 "
            "(끌어서 카톡·메일에 놓기)", after_box)
        self.quick_spin = QSpinBox(after_box)
        self.quick_spin.setRange(2, 60)
        self.quick_spin.setSuffix(" 초")
        self.quick_check.toggled.connect(self.quick_spin.setEnabled)
        quick_row.addWidget(self.quick_check)
        quick_row.addWidget(self.quick_spin)
        quick_row.addStretch(1)
        after_layout.addLayout(quick_row)

        delay_row = QHBoxLayout()
        self.delay_spin = QSpinBox(after_box)
        self.delay_spin.setRange(1, 60)
        self.delay_spin.setSuffix(" 초")
        delay_row.addWidget(QLabel("지연 캡처 대기 시간"))
        delay_row.addWidget(self.delay_spin)
        delay_row.addStretch(1)
        after_layout.addLayout(delay_row)
        outer.addWidget(after_box)

        hint_box = QGroupBox("화면 단축키 안내", page)
        hint_layout = QFormLayout(hint_box)
        self.hint_combo = QComboBox(hint_box)
        self.hint_combo.addItem("잠시 뒤 흐려지게 (권장)", "fade")
        self.hint_combo.addItem("항상 진하게 표시", "always")
        self.hint_combo.addItem("처음 몇 번만 표시", "count")
        self.hint_combo.addItem("표시하지 않음", "off")
        hint_layout.addRow("표시 방식", self.hint_combo)
        self.hint_count_spin = QSpinBox(hint_box)
        self.hint_count_spin.setRange(1, 200)
        self.hint_count_spin.setSuffix(" 번")
        hint_layout.addRow("표시 횟수", self.hint_count_spin)
        hint_note = QLabel("캡처할 때 화면 구석에 단축키 목록을 보여줍니다. "
                           "캡처 중 F1 을 누르면 켜고 끌 수 있습니다.", hint_box)
        hint_note.setWordWrap(True)
        hint_note.setStyleSheet("color: #666;")
        hint_layout.addRow("", hint_note)
        outer.addWidget(hint_box)
        outer.addStretch(1)
        return page

    def _tab_recent(self):
        page = QWidget(self)
        outer = QVBoxLayout(page)

        list_box = QGroupBox("최근 캡처 목록", page)
        form = QFormLayout(list_box)
        self.recent_spin = QSpinBox(list_box)
        self.recent_spin.setRange(20, 1000)
        self.recent_spin.setSuffix(" 개")
        form.addRow("목록에 남길 개수", self.recent_spin)
        self.keep_unsaved_check = QCheckBox(
            "저장하지 않고 복사만 한 캡처도 목록에 남기기", list_box)
        self.keep_unsaved_check.setToolTip(
            "다른 것을 복사해서 클립보드가 덮여도 되찾을 수 있습니다")
        form.addRow("", self.keep_unsaved_check)
        self.unsaved_spin = QSpinBox(list_box)
        self.unsaved_spin.setRange(5, 100)
        self.unsaved_spin.setSuffix(" 장")
        form.addRow("임시 보관 개수", self.unsaved_spin)
        outer.addWidget(list_box)

        clean_box = QGroupBox("오래된 캡처 자동 정리", page)
        clean_layout = QVBoxLayout(clean_box)
        self.cleanup_check = QCheckBox("자동 정리 사용", clean_box)
        clean_layout.addWidget(self.cleanup_check)

        mode_row = QHBoxLayout()
        self.cleanup_mode = QComboBox(clean_box)
        self.cleanup_mode.addItem("일정 기간이 지나면", "days")
        self.cleanup_mode.addItem("폴더 용량이 넘으면", "size")
        self.cleanup_days = QSpinBox(clean_box)
        self.cleanup_days.setRange(1, 3650)
        self.cleanup_days.setSuffix(" 일")
        self.cleanup_size = QSpinBox(clean_box)
        self.cleanup_size.setRange(1, 500)
        self.cleanup_size.setSuffix(" GB")
        mode_row.addWidget(self.cleanup_mode)
        mode_row.addWidget(self.cleanup_days)
        mode_row.addWidget(self.cleanup_size)
        mode_row.addStretch(1)
        clean_layout.addLayout(mode_row)

        warn = QLabel(
            "· 파일을 지우지 않고 <b>휴지통으로</b> 보냅니다. 되살릴 수 있습니다.<br>"
            "· 캡처도구가 만든 날짜 폴더와 목록에 있는 파일만 건드립니다. "
            "저장 폴더에 직접 넣어 두신 자료는 손대지 않습니다.<br>"
            "· 처음 한 번은 몇 개가 대상인지 알려 드리고 확인을 받습니다.", clean_box)
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #666;")
        clean_layout.addWidget(warn)
        self.cleanup_preview = QPushButton("지금 대상 확인해 보기", clean_box)
        self.cleanup_preview.clicked.connect(self._preview_cleanup)
        clean_layout.addWidget(self.cleanup_preview, 0, Qt.AlignLeft)
        outer.addWidget(clean_box)

        self.cleanup_mode.currentIndexChanged.connect(self._sync_cleanup)
        self.cleanup_check.toggled.connect(self._sync_cleanup)
        self.keep_unsaved_check.toggled.connect(
            lambda on: self.unsaved_spin.setEnabled(on))
        outer.addStretch(1)
        return page

    def _sync_cleanup(self):
        on = self.cleanup_check.isChecked()
        by_days = self.cleanup_mode.currentData() == "days"
        for widget in (self.cleanup_mode, self.cleanup_preview):
            widget.setEnabled(on)
        self.cleanup_days.setEnabled(on and by_days)
        self.cleanup_size.setEnabled(on and not by_days)

    def _preview_cleanup(self):
        import cleanup
        saved = (CONFIG["cleanup_enabled"], CONFIG["cleanup_mode"],
                 CONFIG["cleanup_days"], CONFIG["cleanup_size_gb"])
        CONFIG["cleanup_enabled"] = True
        CONFIG["cleanup_mode"] = self.cleanup_mode.currentData()
        CONFIG["cleanup_days"] = self.cleanup_days.value()
        CONFIG["cleanup_size_gb"] = self.cleanup_size.value()
        try:
            targets, _ = cleanup.find_targets()
        except Exception:
            targets = []
        (CONFIG["cleanup_enabled"], CONFIG["cleanup_mode"],
         CONFIG["cleanup_days"], CONFIG["cleanup_size_gb"]) = saved
        if not targets:
            QMessageBox.information(self, "자동 정리",
                                    "지금 조건에 해당하는 파일이 없습니다.")
            return
        sample = "\n".join(os.path.basename(str(p)) for p in targets[:8])
        more = "\n… 외 %d개" % (len(targets) - 8) if len(targets) > 8 else ""
        QMessageBox.information(
            self, "자동 정리",
            "지금 조건이면 %d개가 휴지통으로 갑니다.\n\n%s%s"
            % (len(targets), sample, more))

    def _tab_hotkeys(self):
        page = QWidget(self)
        layout = QVBoxLayout(page)
        info = QLabel("칸을 클릭한 뒤 원하는 키 조합을 누르세요. "
                      "Delete 키를 누르면 해제됩니다.", page)
        info.setWordWrap(True)
        layout.addWidget(info)

        grid = QGridLayout()
        self.hotkey_edits = {}
        for row, (action, label) in enumerate(HOTKEY_LABELS.items()):
            grid.addWidget(QLabel(label, page), row, 0)
            edit = HotkeyEdit("", page)
            self.hotkey_edits[action] = edit
            grid.addWidget(edit, row, 1)
        layout.addLayout(grid)

        self.hotkey_warning = QLabel("", page)
        self.hotkey_warning.setWordWrap(True)
        self.hotkey_warning.setStyleSheet("color: #b25b00;")
        layout.addWidget(self.hotkey_warning)
        layout.addStretch(1)
        return page

    def _tab_etc(self):
        page = QWidget(self)
        layout = QVBoxLayout(page)

        helper_box = QGroupBox("영역 잡기 도우미", page)
        helper_layout = QVBoxLayout(helper_box)
        self.snap_check = QCheckBox(
            "드래그할 때 화면의 경계선에 착 붙기 (Alt 를 누르면 잠깐 해제)",
            helper_box)
        self.auto_region_check = QCheckBox(
            "마우스를 올린 자리의 칸을 자동으로 잡아 주기 "
            "(Ctrl 로 칸 → 창 → 모니터)", helper_box)
        helper_layout.addWidget(self.snap_check)
        helper_layout.addWidget(self.auto_region_check)
        helper_note = QLabel(
            "표나 서류처럼 테두리가 뚜렷한 화면에서 잘 동작합니다. "
            "사진처럼 경계가 흐릿하면 아무 일도 일어나지 않습니다.", helper_box)
        helper_note.setWordWrap(True)
        helper_note.setStyleSheet("color: #666;")
        helper_layout.addWidget(helper_note)
        layout.addWidget(helper_box)

        preset_box = QGroupBox("크기지정 캡처에 쓸 크기", page)
        preset_layout = QVBoxLayout(preset_box)
        self.preset_list = QListWidget(preset_box)
        self.preset_list.setMaximumHeight(120)
        preset_layout.addWidget(self.preset_list)
        row = QHBoxLayout()
        self.preset_w = QSpinBox(preset_box)
        self.preset_w.setRange(10, 40000)
        self.preset_w.setValue(1280)
        self.preset_h = QSpinBox(preset_box)
        self.preset_h.setRange(10, 40000)
        self.preset_h.setValue(720)
        add_btn = QPushButton("추가", preset_box)
        add_btn.clicked.connect(self._add_preset)
        del_btn = QPushButton("선택 삭제", preset_box)
        del_btn.clicked.connect(self._remove_preset)
        row.addWidget(self.preset_w)
        row.addWidget(QLabel("×", preset_box))
        row.addWidget(self.preset_h)
        row.addWidget(add_btn)
        row.addWidget(del_btn)
        row.addStretch(1)
        preset_layout.addLayout(row)
        layout.addWidget(preset_box)

        self.autostart_check = QCheckBox("Windows 시작할 때 자동 실행", page)
        layout.addWidget(self.autostart_check)
        self.autostart_note = QLabel("", page)
        self.autostart_note.setStyleSheet("color: #777; font-size: 12px;")
        self.autostart_note.setWordWrap(True)
        self.autostart_note.setIndent(22)
        layout.addWidget(self.autostart_note)
        self.autostart_check.toggled.connect(
            lambda _=False: self._show_autostart_note())
        self.tray_msg_check = QCheckBox("저장했을 때 알림 풍선 보여주기", page)
        layout.addWidget(self.tray_msg_check)

        ocr_row = QHBoxLayout()
        ocr_row.addWidget(QLabel("글자 추출 언어"))
        self.ocr_combo = QComboBox(page)
        self.ocr_combo.addItem("시스템 기본", "")
        ocr_row.addWidget(self.ocr_combo, 1)
        layout.addLayout(ocr_row)

        self.ocr_note = QLabel("", page)
        self.ocr_note.setWordWrap(True)
        self.ocr_note.setStyleSheet("color: #666;")
        layout.addWidget(self.ocr_note)

        path_note = QLabel("설정 파일 위치: %s" % CONFIG.path, page)
        path_note.setWordWrap(True)
        path_note.setStyleSheet("color: #888; font-size: 11px;")
        layout.addStretch(1)
        layout.addWidget(path_note)
        return page

    def _add_preset(self):
        width, height = self.preset_w.value(), self.preset_h.value()
        label = "%d × %d" % (width, height)
        for index in range(self.preset_list.count()):
            if self.preset_list.item(index).text() == label:
                return
        item = QListWidgetItem(label)
        item.setData(Qt.UserRole, [width, height])
        self.preset_list.addItem(item)

    def _remove_preset(self):
        for item in self.preset_list.selectedItems():
            self.preset_list.takeItem(self.preset_list.row(item))

    # ------------------------------------------------------------- 값
    def _load(self):
        self.dir_edit.setText(CONFIG["save_dir"] or "")
        self.pattern_combo.setCurrentText(CONFIG["file_pattern"])
        index = self.format_combo.findData(
            "jpg" if str(CONFIG["image_format"]).lower() in ("jpg", "jpeg")
            else "png")
        self.format_combo.setCurrentIndex(max(0, index))
        self.quality_spin.setValue(int(CONFIG["jpeg_quality"]))
        self.copy_check.setChecked(bool(CONFIG["after_copy"]))
        self.save_check.setChecked(bool(CONFIG["after_save"]))
        self.edit_check.setChecked(bool(CONFIG["after_edit"]))
        self.bar_check.setChecked(bool(CONFIG["show_action_bar"]))
        self.quick_check.setChecked(bool(CONFIG["quick_preview"]))
        self.quick_spin.setValue(int(CONFIG["quick_preview_seconds"] or 6))
        self.quick_spin.setEnabled(bool(CONFIG["quick_preview"]))
        self.delay_spin.setValue(int(CONFIG["delay_seconds"]))
        index = self.split_combo.findData(CONFIG["folder_split"] or "")
        self.split_combo.setCurrentIndex(max(0, index))
        index = self.hint_combo.findData(CONFIG["hint_mode"] or "fade")
        self.hint_combo.setCurrentIndex(max(0, index))
        self.hint_count_spin.setValue(int(CONFIG["hint_count_limit"]))
        self.recent_spin.setValue(int(CONFIG["recent_limit"]))
        self.keep_unsaved_check.setChecked(bool(CONFIG["keep_unsaved"]))
        self.unsaved_spin.setValue(int(CONFIG["unsaved_limit"]))
        self.unsaved_spin.setEnabled(bool(CONFIG["keep_unsaved"]))
        self.cleanup_check.setChecked(bool(CONFIG["cleanup_enabled"]))
        index = self.cleanup_mode.findData(CONFIG["cleanup_mode"] or "days")
        self.cleanup_mode.setCurrentIndex(max(0, index))
        self.cleanup_days.setValue(int(CONFIG["cleanup_days"]))
        self.cleanup_size.setValue(int(CONFIG["cleanup_size_gb"]))
        self._sync_cleanup()
        for action, edit in self.hotkey_edits.items():
            edit.setText(CONFIG.hotkey(action))
        self.autostart_check.setChecked(
            winutil.get_autostart("캡처도구") if winutil.IS_WIN
            else bool(CONFIG["autostart"]))
        self._show_autostart_note()
        self.tray_msg_check.setChecked(bool(CONFIG["show_tray_message"]))
        self.snap_check.setChecked(bool(CONFIG["snap_edges"]))
        self.auto_region_check.setChecked(bool(CONFIG["auto_region"]))
        self.preset_list.clear()
        for entry in (CONFIG["size_presets"] or []):
            try:
                width, height = int(entry[0]), int(entry[1])
            except Exception:
                continue
            item = QListWidgetItem("%d × %d" % (width, height))
            item.setData(Qt.UserRole, [width, height])
            self.preset_list.addItem(item)
        self._load_ocr_languages()
        self._update_preview()

    def _show_autostart_note(self):
        """자동 실행이 어떤 방식으로 등록돼 있는지 알려 준다."""
        if not winutil.IS_WIN:
            self.autostart_note.setText("(Windows 에서만 동작합니다)")
            return
        if not self.autostart_check.isChecked():
            self.autostart_note.setText(
                "켜 두면 컴퓨터를 켤 때 창 없이 조용히 시작됩니다.")
            return
        problem = winutil.autostart_problem("캡처도구")
        if problem == "console":
            self.autostart_note.setText(
                "지금은 옛 방식으로 등록돼 있어 컴퓨터를 켤 때 검은 창이 "
                "뜹니다. [적용]을 누르면 창이 뜨지 않는 방식으로 고쳐집니다.")
        elif problem == "moved":
            self.autostart_note.setText(
                "등록된 위치에 프로그램이 없습니다 (폴더를 옮기신 것 같습니다). "
                "[적용]을 누르면 지금 위치로 다시 맞춥니다.")
        else:
            self.autostart_note.setText(
                "창 없이 조용히 시작하도록 올바르게 등록돼 있습니다.")

    def _load_ocr_languages(self):
        """인식 가능한 언어를 별도 스레드에서 알아본다.

        본 스레드에서 바로 물어보면 프로그램이 멈출 수 있어서
        (v1.3 까지 글자 추출이 멈추던 것과 같은 이유) 반드시 떼어 놓는다.
        """
        self.ocr_note.setText("인식 가능한 언어를 확인하는 중입니다…")
        try:
            import ocr
        except Exception:
            self.ocr_note.setText("글자 인식 기능을 쓸 수 없습니다.")
            return
        task = ocr.CheckTask()
        task.finished.connect(self._ocr_languages_ready)
        self._ocr_check = task           # 끝날 때까지 살려 둔다
        task.start()

    def _ocr_languages_ready(self, report):
        languages = list(report.get("languages") or [])
        for tag in languages:
            self.ocr_combo.addItem(tag, tag)
        current = CONFIG["ocr_language"] or ""
        index = self.ocr_combo.findData(current)
        self.ocr_combo.setCurrentIndex(max(0, index))
        if report.get("korean"):
            self.ocr_note.setText("한국어 인식 준비됨 — 가능한 언어: %s"
                                  % ", ".join(languages))
        elif languages:
            self.ocr_note.setText(
                "한국어만 빠져 있습니다 (가능한 언어: %s).\n"
                "트레이 메뉴 → [글자 추출 진단] 에서 설치 방법을 안내합니다."
                % ", ".join(languages))
        else:
            self.ocr_note.setText(
                "인식 가능한 언어를 찾지 못했습니다.\n"
                "트레이 메뉴 → [글자 추출 진단] 을 눌러 확인해 주세요.")

    def _update_preview(self):
        import saver
        pattern = self.pattern_combo.currentText() or "캡처_%Y%m%d_%H%M%S"
        try:
            name = saver.safe_name(saver.format_name(pattern, None,
                                                    PREVIEW_WINDOW))
        except Exception:
            name = "(이름 규칙이 올바르지 않습니다)"
        ext = self.format_combo.currentData() or "png"
        extra = ""
        if "%W" in pattern:
            extra = "   (창 이름을 모를 때는 그 자리가 빠집니다)"
        self.preview_label.setText("예시:  %s.%s%s" % (name, ext, extra))
        self.quality_spin.setEnabled(ext == "jpg")

    def _browse_dir(self):
        start = self.dir_edit.text() or str(default_save_dir())
        folder = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", start)
        if folder:
            self.dir_edit.setText(folder)

    def _restore_defaults(self):
        answer = QMessageBox.question(
            self, "기본값으로", "모든 설정을 처음 상태로 되돌릴까요?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        CONFIG.data = copy.deepcopy(DEFAULTS)
        self._load()

    # ------------------------------------------------------------ 저장
    def _accept(self):
        values = {edit.text().strip() for edit in self.hotkey_edits.values()
                  if edit.text().strip()}
        entered = [edit.text().strip() for edit in self.hotkey_edits.values()
                   if edit.text().strip()]
        if len(values) != len(entered):
            QMessageBox.warning(self, "단축키 중복",
                                "같은 단축키를 두 곳에 지정할 수 없습니다.")
            return

        folder = self.dir_edit.text().strip()
        if folder:
            try:
                Path(folder).mkdir(parents=True, exist_ok=True)
            except Exception:
                QMessageBox.warning(
                    self, "폴더 오류",
                    "지정한 폴더를 사용할 수 없습니다.\n다른 폴더를 골라 주세요.")
                return

        CONFIG["save_dir"] = folder
        CONFIG["file_pattern"] = self.pattern_combo.currentText().strip() or \
            DEFAULTS["file_pattern"]
        CONFIG["image_format"] = self.format_combo.currentData() or "png"
        CONFIG["jpeg_quality"] = self.quality_spin.value()
        CONFIG["after_copy"] = self.copy_check.isChecked()
        CONFIG["after_save"] = self.save_check.isChecked()
        CONFIG["after_edit"] = self.edit_check.isChecked()
        CONFIG["show_action_bar"] = self.bar_check.isChecked()
        CONFIG["quick_preview"] = self.quick_check.isChecked()
        CONFIG["quick_preview_seconds"] = self.quick_spin.value()
        CONFIG["delay_seconds"] = self.delay_spin.value()
        CONFIG["folder_split"] = self.split_combo.currentData() or ""
        CONFIG["hint_mode"] = self.hint_combo.currentData() or "fade"
        CONFIG["hint_count_limit"] = self.hint_count_spin.value()
        CONFIG["recent_limit"] = self.recent_spin.value()
        CONFIG["keep_unsaved"] = self.keep_unsaved_check.isChecked()
        CONFIG["unsaved_limit"] = self.unsaved_spin.value()
        was_on = bool(CONFIG["cleanup_enabled"])
        CONFIG["cleanup_enabled"] = self.cleanup_check.isChecked()
        CONFIG["cleanup_mode"] = self.cleanup_mode.currentData() or "days"
        CONFIG["cleanup_days"] = self.cleanup_days.value()
        CONFIG["cleanup_size_gb"] = self.cleanup_size.value()
        if not was_on and CONFIG["cleanup_enabled"]:
            CONFIG["cleanup_confirmed"] = False   # 처음 한 번은 다시 확인
        CONFIG["show_tray_message"] = self.tray_msg_check.isChecked()
        CONFIG["snap_edges"] = self.snap_check.isChecked()
        CONFIG["auto_region"] = self.auto_region_check.isChecked()
        CONFIG["size_presets"] = [
            self.preset_list.item(i).data(Qt.UserRole)
            for i in range(self.preset_list.count())]
        CONFIG["ocr_language"] = self.ocr_combo.currentData() or ""
        CONFIG["hotkeys"] = {action: edit.text().strip()
                             for action, edit in self.hotkey_edits.items()}
        CONFIG["autostart"] = self.autostart_check.isChecked()
        if winutil.IS_WIN:
            winutil.set_autostart(self.autostart_check.isChecked(), "캡처도구",
                                  winutil.startup_command())
        CONFIG.save()
        self.applied.emit()
        self.accept()


def open_folder(path):
    path = str(path)
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass
