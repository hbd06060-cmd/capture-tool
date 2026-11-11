# -*- coding: utf-8 -*-
"""캡처도구 본체 — 트레이 상주, 전역 단축키, 캡처 흐름 관리."""
import ctypes
import sys
import traceback

from PySide6.QtCore import (QAbstractNativeEventFilter, QObject, QSize, QTimer,
                            Qt)
from PySide6.QtGui import QAction, QGuiApplication, QImage, QKeySequence
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox,
                               QHBoxLayout, QLabel, QMenu, QMessageBox,
                               QPlainTextEdit, QProgressDialog, QPushButton,
                               QSystemTrayIcon, QVBoxLayout, QWidget)

import appicon
import cleanup
import recent
import saver
import winutil
from capture import ScreenShot
from config import CONFIG, HOTKEY_LABELS, VERSION
from editor import EditorWindow
from gallery import RecentGallery
from overlay import OverlayGroup
from pinwin import PinWindow
from settingsdlg import SettingsDialog, open_folder

HOTKEY_IDS = {"region": 1, "fullscreen": 2, "window": 3, "delay": 4,
              "pin_clipboard": 5, "open_folder": 6, "scroll": 7, "recent": 8,
              "window_pick": 9, "last_region": 10, "pins_hide": 11,
              "pin_restore": 12, "pins_passthrough": 13}


# --------------------------------------------------------------- 글자 결과 창
class OcrResultDialog(QDialog):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setWindowTitle("추출한 글자")
        self.resize(620, 460)
        layout = QVBoxLayout(self)
        info = QLabel("인식된 글자입니다. 필요한 부분을 고쳐서 사용하세요.", self)
        info.setStyleSheet("color: #555;")
        layout.addWidget(info)
        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setPlainText(text)
        layout.addWidget(self.text_edit, 1)

        row = QHBoxLayout()
        copy_btn = QPushButton("전체 복사", self)
        copy_btn.clicked.connect(self._copy)
        save_btn = QPushButton("텍스트 파일로 저장", self)
        save_btn.clicked.connect(self._save)
        row.addWidget(copy_btn)
        row.addWidget(save_btn)
        row.addStretch(1)
        close_btn = QPushButton("닫기", self)
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        layout.addLayout(row)
        self.status = QLabel("", self)
        self.status.setStyleSheet("color: #2e7d32;")
        layout.addWidget(self.status)

    def _copy(self):
        QGuiApplication.clipboard().setText(self.text_edit.toPlainText())
        self.status.setText("클립보드에 복사했습니다.")

    def _save(self):
        from PySide6.QtWidgets import QFileDialog
        path = CONFIG.save_dir() / "추출한글자.txt"
        target, _ = QFileDialog.getSaveFileName(
            self, "텍스트 저장", str(path), "텍스트 파일 (*.txt)")
        if not target:
            return
        try:
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(self.text_edit.toPlainText())
            self.status.setText("저장했습니다 → %s" % target)
        except Exception as exc:
            QMessageBox.warning(self, "저장 실패", str(exc))


# ------------------------------------------------------------- 단축키 수신창
class HotkeyHost(QWidget):
    """전역 단축키를 받기 위한 보이지 않는 창."""

    def __init__(self):
        super().__init__(None, Qt.Tool)
        self.setFixedSize(1, 1)
        self.setWindowOpacity(0.0)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint |
                            Qt.WindowStaysOnBottomHint)
        self.move(-32000, -32000)
        self.show()
        self.hide_softly()

    def hide_softly(self):
        # 창을 실제로 유지해야 HWND 가 살아 있으므로 화면 밖으로만 보낸다
        self.setWindowOpacity(0.0)


if winutil.IS_WIN:
    from ctypes import wintypes

    class _MSG(ctypes.Structure):
        _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint),
                    ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                    ("time", ctypes.c_uint), ("pt_x", ctypes.c_long),
                    ("pt_y", ctypes.c_long)]


class HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def nativeEventFilter(self, event_type, message):
        if not winutil.IS_WIN:
            return False, 0
        try:
            if event_type != b"windows_generic_MSG":
                return False, 0
            pointer = int(message)
            msg = ctypes.cast(pointer, ctypes.POINTER(_MSG)).contents
            if msg.message == winutil.WM_HOTKEY:
                self.callback(int(msg.wParam))
                return True, 0
        except Exception:
            pass
        return False, 0


# ------------------------------------------------------------------- 본체
class CaptureApp(QObject):
    def __init__(self, argv):
        super().__init__()
        winutil.set_dpi_aware()
        self.qt_app = QApplication(argv)
        self.qt_app.setApplicationName("캡처도구")
        self._load_korean_translation()
        self.qt_app.setQuitOnLastWindowClosed(False)
        self.icon = appicon.app_icon()
        self.qt_app.setWindowIcon(self.icon)

        self.overlay = None
        self.pins = []
        self.editors = []
        self.last_image = None
        self._busy = False
        self.gallery = None
        self.scroller = None
        self._pending_scroll = None
        self._last_scroll_target = None
        self._fixed_size = None
        self._last_scroll_frames = []
        self.picker = None
        self._ocr_task = None
        self._ocr_check = None
        self._last_target_hwnd = 0     # 캡처 직전 앞에 있던 남의 창
        self.hotkey_status = {}
        self.hotkey_doctor = None
        self.closed_pins = []          # 닫은 핀 (되살리기용, 최근 것이 끝)
        self._pins_hidden = False
        self.quick = None              # 캡처 직후 미리보기
        self._quitting = False
        self.updates = None            # 업데이트 지휘자 (필요할 때 만든다)
        self._relaunch_after_quit = False   # 업데이트 뒤 다시 띄울지

        self.host = HotkeyHost()
        self.filter = HotkeyFilter(self._on_hotkey)
        self.qt_app.installNativeEventFilter(self.filter)
        self._id_to_action = {v: k for k, v in HOTKEY_IDS.items()}

        self._build_tray()
        self.reload_hotkeys()
        self._fix_autostart()
        self._greet()
        QTimer.singleShot(2500, self._run_cleanup)
        # 켜자마자 인터넷을 붙잡으면 시작이 느려 보인다. 한숨 돌린 뒤에 확인한다.
        QTimer.singleShot(8000, self._check_updates_quietly)

    def _load_korean_translation(self):
        """Qt 기본 창(인쇄 미리보기, 색 고르기 등)의 영어 문구를 한국어로."""
        from PySide6.QtCore import QLibraryInfo, QTranslator
        self._translator = QTranslator(self.qt_app)
        folder = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
        if self._translator.load("qtbase_ko", folder):
            self.qt_app.installTranslator(self._translator)

    def _fix_autostart(self):
        """v1.5 까지의 잘못된 자동 실행 등록을 조용히 바로잡는다.

        예전에는 python.exe 를 그대로 등록해서, 컴퓨터를 켤 때마다
        검은 명령 프롬프트 창이 하나 떴고 그 창을 닫으면 프로그램이
        같이 꺼졌다. 이미 그렇게 등록된 컴퓨터를 알아서 되돌린다.
        """
        try:
            fixed = winutil.repair_autostart("캡처도구")
        except Exception:
            return
        if fixed == "console":
            QTimer.singleShot(1500, lambda: self.notify(
                "자동 실행 방식을 고쳤습니다",
                "이제 컴퓨터를 켤 때 검은 창이 뜨지 않습니다."))
        elif fixed == "moved":
            QTimer.singleShot(1500, lambda: self.notify(
                "자동 실행 위치를 다시 맞췄습니다",
                "프로그램 폴더를 옮기신 것 같아 지금 위치로 고쳤습니다."))

    # ------------------------------------------------------------- 트레이
    def _build_tray(self):
        self.tray = QSystemTrayIcon(self.icon)
        self.tray.setToolTip("캡처도구 v%s" % VERSION)
        menu = QMenu()

        self._labelled_actions = []    # (QAction, 글자 만드는 함수, 단축키 이름)

        def add(text, action_key, slot, target=None):
            target = target or menu
            action = QAction("", target)
            action.triggered.connect(slot)
            target.addAction(action)
            maker = text if callable(text) else (lambda t=text: t)
            self._labelled_actions.append((action, maker, action_key))
            self._label_action(action, maker, action_key)
            return action

        version_item = QAction("캡처도구 v%s" % VERSION, menu)
        version_item.setEnabled(False)        # 눌리지 않는 안내줄
        menu.addAction(version_item)

        # 새 버전이 있을 때만 맨 위에 나타나는 줄. 평소에는 숨겨 둔다.
        self.update_action = QAction("", menu)
        self.update_action.triggered.connect(self.open_update_dialog)
        self.update_action.setVisible(False)
        menu.addAction(self.update_action)
        menu.addSeparator()

        add("영역 캡처", "region", self.capture_region)
        add("전체 화면 캡처", "fullscreen", self.capture_fullscreen)
        add("활성 창 캡처", "window", self.capture_window)
        add("창 골라서 캡처 (가려져 있어도)", "window_pick", self.pick_window)
        add(lambda: "지연 캡처 (%d초 뒤)" % int(CONFIG["delay_seconds"]),
            "delay", self.capture_delayed)
        add("스크롤 캡처", "scroll", self.capture_scroll)
        add("직전 영역 다시 캡처", "last_region", self.capture_last_region)
        self.size_menu = menu.addMenu("크기지정 캡처")
        self.size_menu.aboutToShow.connect(self._fill_size_menu)
        menu.addSeparator()
        add("클립보드 핀 고정 (이미지·글자·색)", "pin_clipboard",
            self.pin_clipboard)
        pin_menu = menu.addMenu("핀 관리")
        add("핀 모두 숨기기 / 보이기", "pins_hide", self.toggle_pins_hidden,
            pin_menu)
        add("닫은 핀 되살리기", "pin_restore", self.restore_pin, pin_menu)
        add("핀 클릭 통과 켜기 / 끄기", "pins_passthrough",
            self.toggle_passthrough, pin_menu)
        pin_menu.addSeparator()
        add("지금 핀 배치 저장", None, self.save_pin_layout, pin_menu)
        add("저장한 핀 배치 불러오기", None, self.load_pin_layout, pin_menu)
        pin_menu.addSeparator()
        add("모든 핀 닫기", None, self.close_all_pins, pin_menu)
        self.recent_menu = menu.addMenu("최근 캡처")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        add("최근 캡처 목록 창", "recent", self.open_gallery)
        menu.addSeparator()
        add("저장 폴더 열기", "open_folder",
            lambda: open_folder(CONFIG.save_dir()))
        add("설정", None, self.open_settings)
        add("도움말 · 단축키", None, self.show_help)
        add("단축키 진단", None, self.check_hotkeys)
        add("글자 추출 진단", None, self.check_ocr)
        add("진단 정보 저장", None, self.save_diagnostics)
        add("업데이트 확인", None, self.check_updates)
        menu.addSeparator()
        add("종료", None, self.quit)

        self.tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self.capture_region()

    @staticmethod
    def _label_action(action, maker, action_key):
        text = maker()
        hotkey = CONFIG.hotkey(action_key) if action_key else ""
        action.setText(text if not hotkey else "%s\t%s" % (text, hotkey))

    def _refresh_tray_labels(self):
        for action, maker, action_key in self._labelled_actions:
            try:
                self._label_action(action, maker, action_key)
            except RuntimeError:
                pass

    # ---------------------------------------------------- 창 골라서 캡처
    def pick_window(self):
        if self.picker is not None:
            self.picker.reload()
            self.picker.show()
            self.picker.raise_()
            self.picker.activateWindow()
            return
        from windowpicker import WindowPicker
        picker = WindowPicker(self._own_hwnds())
        picker.setWindowIcon(self.icon)
        picker.picked.connect(self._capture_picked_window)
        picker.destroyed.connect(lambda *_: setattr(self, "picker", None))
        self.picker = picker
        picker.show()
        picker.raise_()
        picker.activateWindow()

    def _capture_picked_window(self, entry):
        import windowcap
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            image, how = windowcap.capture_window(entry["hwnd"])
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(None, "창 캡처", "캡처하지 못했습니다.\n%s" % exc)
            return
        QApplication.restoreOverrideCursor()
        if image.isNull():
            QMessageBox.information(
                None, "창 캡처",
                "이 창은 캡처하지 못했습니다.\n"
                "창을 화면에 띄운 뒤 [활성 창 캡처]를 써 보세요.")
            return
        # 고른 창의 제목을 파일 이름에 쓸 수 있게 넘긴다
        self._finish(image, "default", entry.get("title", ""))
        if how == "raise":
            self.notify("창 캡처",
                        "이 프로그램은 직접 그려주지 않아 잠깐 앞으로 꺼내 찍었습니다.")

    # -------------------------------------------------------- 진단 정보
    def save_diagnostics(self):
        try:
            import diagnostics
            path = diagnostics.save(self.qt_app)
        except Exception as exc:
            QMessageBox.warning(None, "진단 정보", "저장하지 못했습니다.\n%s" % exc)
            return
        box = QMessageBox()
        box.setWindowIcon(self.icon)
        box.setWindowTitle("진단 정보 저장")
        box.setText("진단 정보를 저장했습니다.")
        box.setInformativeText("이 파일을 보내주시면 문제 원인을 찾는 데 "
                               "도움이 됩니다.\n\n%s" % path)
        open_btn = box.addButton("폴더 열기", QMessageBox.AcceptRole)
        box.addButton("닫기", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            import os
            open_folder(os.path.dirname(path))

    # ------------------------------------------------------ 크기지정 캡처
    def _fill_size_menu(self):
        menu = self.size_menu
        menu.clear()
        presets = CONFIG["size_presets"] or []
        if not presets:
            empty = QAction("설정 → 기타 에서 크기를 등록하세요", menu)
            empty.setEnabled(False)
            menu.addAction(empty)
            return
        for entry in presets:
            try:
                width, height = int(entry[0]), int(entry[1])
            except Exception:
                continue
            action = QAction("%d × %d" % (width, height), menu)
            action.triggered.connect(
                lambda _=False, w=width, h=height: self.capture_fixed(w, h))
            menu.addAction(action)

    def capture_fixed(self, width, height):
        if self._busy or self.overlay is not None:
            return
        self._fixed_size = QSize(int(width), int(height))
        self._busy = True
        QTimer.singleShot(80, self._start_region)

    # -------------------------------------------------------- 최근 캡처
    def _fill_recent_menu(self):
        menu = self.recent_menu
        menu.clear()
        items = recent.entries(10)
        if not items:
            empty = QAction("아직 캡처한 것이 없습니다", menu)
            empty.setEnabled(False)
            menu.addAction(empty)
            return
        import gallery
        for entry in items:
            text = "%s  %s" % (recent.day_label(entry),
                              recent.label_for(entry))
            action = QAction(text, menu)
            try:
                action.setIcon(gallery._thumbnail(entry["path"]))
            except Exception:
                pass
            action.triggered.connect(
                lambda _=False, p=entry["path"]: self.open_path(p))
            menu.addAction(action)
        menu.addSeparator()
        more = QAction("전체 목록 보기…", menu)
        more.triggered.connect(self.open_gallery)
        menu.addAction(more)

    def open_path(self, path):
        image = QImage(str(path))
        if image.isNull():
            QMessageBox.information(None, "최근 캡처",
                                    "파일을 열 수 없습니다.\n%s" % path)
            recent.forget(path)
            return
        self.open_editor(image, str(path))

    def open_gallery(self):
        if self.gallery is not None:
            self.gallery.reload()
            self.gallery.show()
            self.gallery.raise_()
            self.gallery.activateWindow()
            return
        window = RecentGallery()
        window.setWindowIcon(self.icon)
        window.openRequested.connect(self.open_path)
        window.pinRequested.connect(self._pin_path)
        window.ocrRequested.connect(self._ocr_path)
        window.destroyed.connect(lambda *_: setattr(self, "gallery", None))
        self.gallery = window
        window.show()
        window.raise_()
        window.activateWindow()

    def _pin_path(self, path):
        image = QImage(str(path))
        if not image.isNull():
            self.add_pin(image)

    def _ocr_path(self, path):
        image = QImage(str(path))
        if not image.isNull():
            self.run_ocr(image)

    # ----------------------------------------------------------- 업데이트
    def _update_manager(self):
        """업데이트 지휘자를 처음 쓸 때 만든다 (시작을 가볍게 하려고)."""
        if self.updates is None:
            from updatedlg import UpdateManager
            self.updates = UpdateManager(self)
        return self.updates

    def _check_updates_quietly(self):
        """켠 뒤 자동 확인. 새 버전이 없으면 아무것도 보여 주지 않는다."""
        if not CONFIG.get("update_check_enabled", True):
            return
        try:
            self._update_manager().check_quietly()
        except Exception:
            pass        # 업데이트 확인이 실패해도 캡처는 계속 써야 한다

    def check_updates(self):
        """'업데이트 확인' 메뉴 — 결과를 반드시 알려 준다."""
        self._update_manager().check_now()

    def open_update_dialog(self):
        self._update_manager().open_dialog()

    def refresh_update_menu(self):
        """새 버전이 있으면 트레이 메뉴 맨 위에 안내 줄을 띄운다."""
        manager = self.updates
        pending = None if manager is None else manager.pending
        if pending is None:
            self.update_action.setVisible(False)
            self.tray.setToolTip("캡처도구 v%s" % VERSION)
            return
        ready = manager.ready_folder is not None
        self.update_action.setText(
            "새 버전 v%s %s" % (pending.version,
                               "설치하기" if ready else "보기"))
        self.update_action.setVisible(True)
        self.tray.setToolTip("캡처도구 v%s — 새 버전 v%s 있음"
                             % (VERSION, pending.version))

    def restart_for_update(self):
        """업데이트를 끝내고 새 코드로 다시 띄운다.

        여기서 바로 새 프로그램을 띄우면 안 된다. 캡처도구는 두 번 실행되지
        않도록 자리를 하나 잡아 두는데, 지금 그 자리를 이쪽이 쥐고 있어서
        새로 뜬 쪽이 '이미 실행 중'으로 보고 그냥 꺼져 버린다. 그래서
        표시만 남기고, 완전히 끝난 뒤 main.pyw 가 대신 띄워 준다.
        """
        self._relaunch_after_quit = True
        self.quit()

    # ---------------------------------------------------------- 자동 정리
    def _run_cleanup(self):
        if not CONFIG["cleanup_enabled"]:
            return
        try:
            if not CONFIG["cleanup_confirmed"]:
                targets, _ = cleanup.find_targets()
                if not targets:
                    return
                answer = QMessageBox.question(
                    None, "오래된 캡처 정리",
                    "%s\n\n지금 %d개가 대상입니다.\n"
                    "지우지 않고 휴지통으로 보내며, 언제든 되살릴 수 있습니다.\n\n"
                    "정리를 시작할까요?" % (cleanup.describe(), len(targets)),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                if answer != QMessageBox.Yes:
                    CONFIG["cleanup_enabled"] = False
                    CONFIG.save()
                    return
                CONFIG["cleanup_confirmed"] = True
                CONFIG.save()
            moved, targets = cleanup.run()
            if moved:
                self.notify("정리 완료",
                            "오래된 캡처 %d개를 휴지통으로 보냈습니다." % moved)
            elif targets:
                # 휴지통 이동이 막힌 환경 — 임의로 지우지 않고 기능을 끈다
                CONFIG["cleanup_enabled"] = False
                CONFIG.save()
                QMessageBox.information(
                    None, "자동 정리",
                    "파일을 휴지통으로 보내지 못했습니다.\n"
                    "안전을 위해 아무 파일도 지우지 않고 자동 정리를 껐습니다.\n\n"
                    "최근 캡처 목록 창에서 직접 골라 삭제하실 수 있습니다.")
        except Exception:
            pass

    def notify(self, title, message):
        if CONFIG["show_tray_message"]:
            try:
                self.tray.showMessage(title, message, self.icon, 2500)
            except Exception:
                pass

    def _greet(self):
        if CONFIG.get("_greeted"):
            return
        CONFIG["_greeted"] = True
        CONFIG.save()
        QTimer.singleShot(800, lambda: self.notify(
            "캡처도구가 실행되었습니다",
            "%s 키로 영역 캡처를 시작하세요.\n작업표시줄 오른쪽 아이콘에서 "
            "모든 기능을 쓸 수 있습니다." % CONFIG.hotkey("region")))

    # ------------------------------------------------------------- 단축키
    def reload_hotkeys(self, announce=True):
        """단축키를 등록한다. 막힌 것은 빈 조합으로 대신 잡아 준다."""
        if not winutil.IS_WIN:
            self.hotkey_status = {}
            return
        hwnd = int(self.host.winId())
        for hotkey_id in HOTKEY_IDS.values():
            winutil.unregister_hotkey(hwnd, hotkey_id)

        status = {}                      # action -> (등록된 키, 오류번호, 대체여부)
        blocked = []
        moved = []
        taken = {CONFIG.hotkey(a) for a in HOTKEY_IDS if CONFIG.hotkey(a)}

        for action, hotkey_id in HOTKEY_IDS.items():
            text = CONFIG.hotkey(action)
            if not text:
                status[action] = ("", 0, False)
                continue
            ok, code = winutil.register_hotkey_ex(hwnd, hotkey_id, text)
            if ok:
                status[action] = (text, 0, False)
                continue
            label = HOTKEY_LABELS.get(action, action)
            # 막혔으면 비어 있는 다른 조합을 찾아 대신 잡는다
            replacement = ""
            if CONFIG.get("hotkey_fallback", True):
                for candidate in winutil.alternatives(text):
                    if candidate in taken:
                        continue
                    got, _c = winutil.register_hotkey_ex(hwnd, hotkey_id,
                                                         candidate)
                    if got:
                        replacement = candidate
                        break
            if replacement:
                CONFIG["hotkeys"][action] = replacement
                taken.discard(text)
                taken.add(replacement)
                status[action] = (replacement, code, True)
                moved.append("%s: %s → %s" % (label, text, replacement))
            else:
                status[action] = ("", code, False)
                blocked.append("%s (%s) — %s" %
                               (label, text, winutil.hotkey_reason(code)))

        self.hotkey_status = status
        if moved:
            CONFIG.save()
            self._refresh_tray_labels()
        if announce:
            self._announce_hotkeys(moved, blocked)

    def _announce_hotkeys(self, moved, blocked):
        """같은 내용을 켤 때마다 반복해서 알리지 않는다."""
        if not moved and not blocked:
            CONFIG["hotkey_warned"] = ""
            return
        signature = "|".join(sorted(moved + blocked))
        if signature == CONFIG.get("hotkey_warned", ""):
            return                       # 지난번과 같은 상황 — 조용히 넘어간다
        CONFIG["hotkey_warned"] = signature
        CONFIG.save()
        if moved and not blocked:
            self.notify(
                "단축키가 겹쳐서 다른 키로 바꿨습니다",
                "\n".join(moved) +
                "\n\n(트레이 메뉴 → 단축키 진단 에서 다시 정할 수 있습니다)")
            return
        lines = []
        if moved:
            lines.append("바꾼 것: " + ", ".join(moved))
        if blocked:
            lines.append("못 쓰는 것: " + ", ".join(blocked))
        lines.append("")
        lines.append("이 기능들은 트레이 아이콘 메뉴에서 그대로 쓸 수 있습니다.")
        lines.append("트레이 메뉴 → [단축키 진단] 에서 빈 키를 찾아 드립니다.")
        self.notify("일부 단축키를 등록하지 못했습니다", "\n".join(lines))

    def _on_hotkey(self, hotkey_id):
        action = self._id_to_action.get(hotkey_id)
        handlers = {
            "region": self.capture_region,
            "fullscreen": self.capture_fullscreen,
            "window": self.capture_window,
            "delay": self.capture_delayed,
            "scroll": self.capture_scroll,          # v1.9.1 까지 빠져 있었음
            "recent": self.open_gallery,            # v1.9.1 까지 빠져 있었음
            "pin_clipboard": self.pin_clipboard,
            "window_pick": self.pick_window,
            "open_folder": lambda: open_folder(CONFIG.save_dir()),
            "last_region": self.capture_last_region,
            "pins_hide": self.toggle_pins_hidden,
            "pin_restore": self.restore_pin,
            "pins_passthrough": self.toggle_passthrough,
        }
        handler = handlers.get(action)
        if handler:
            handler()

    # ------------------------------------------------------------- 캡처
    def _own_hwnds(self):
        handles = []
        for widget in [self.host, self.quick] + self.pins + self.editors:
            try:
                if widget is not None and widget.isVisible():
                    handles.append(int(widget.winId()))
            except Exception:
                pass
        if self.overlay is not None:
            try:
                handles.extend(self.overlay.hwnds())
            except Exception:
                pass
        return handles

    def capture_region(self):
        if self._busy or self.overlay is not None:
            return
        self._busy = True
        self._close_quick()
        QTimer.singleShot(80, self._start_region)

    def capture_last_region(self):
        """직전에 잡았던 영역을 그대로 다시 찍는다 (같은 자리 반복 캡처)."""
        if self._busy or self.overlay is not None:
            return
        history = CONFIG.region_history()
        if not history:
            self.notify("직전 영역 다시 캡처",
                        "아직 잡았던 영역이 없습니다. 먼저 영역 캡처를 한 번 해 주세요.")
            return
        self._busy = True
        self._close_quick()
        QTimer.singleShot(80, lambda: self._do_last_region(history[0]))

    def _do_last_region(self, entry):
        self._remember_target()
        try:
            from PySide6.QtCore import QRect
            shot = ScreenShot()
            rect = QRect(*entry).intersected(shot.virtual_rect)
            if rect.width() < 4 or rect.height() < 4:
                raise RuntimeError("예전 영역이 지금 화면 밖에 있습니다.")
            self._finish(shot.image_for(rect), "default")
        except Exception as exc:
            QMessageBox.warning(None, "캡처 실패", str(exc))
        finally:
            self._busy = False

    def _remember_target(self):
        """캡처 직전에 앞에 있던 (우리 것이 아닌) 창을 기억해 둔다."""
        try:
            hwnd = winutil.foreground_window()
            if hwnd and not winutil.is_own_window(hwnd):
                self._last_target_hwnd = hwnd
        except Exception:
            pass

    def _start_region(self):
        self._remember_target()
        try:
            shot = ScreenShot()
            if not shot.shots:
                raise RuntimeError("화면을 읽지 못했습니다.")
            fixed = self._fixed_size
            self._fixed_size = None
            # 모니터마다 창을 하나씩 띄운다 (배율이 달라도 제대로 그려지게)
            overlay = OverlayGroup(shot, bool(CONFIG["show_action_bar"]),
                                   fixed)
            overlay.prepare(self._own_hwnds())
            overlay.completed.connect(self._on_selection)
            overlay.cancelled.connect(self._on_cancel)
            self.overlay = overlay
            overlay.show()
        except Exception as exc:
            self._busy = False
            self.overlay = None
            QMessageBox.warning(None, "캡처 실패", str(exc))

    def capture_fullscreen(self):
        if self._busy:
            return
        self._busy = True
        self._close_quick()
        QTimer.singleShot(80, self._do_fullscreen)

    def _do_fullscreen(self):
        self._remember_target()
        try:
            shot = ScreenShot()
            screen = QGuiApplication.screenAt(
                self.host.mapToGlobal(self.host.rect().center()))
            target = None
            if screen is not None:
                target = screen.geometry()
            image = shot.image_for(target) if target else shot.full_image()
            if image.isNull():
                image = shot.full_image()
            self._finish(image, "default")
        except Exception as exc:
            QMessageBox.warning(None, "캡처 실패", str(exc))
        finally:
            self._busy = False

    def capture_window(self):
        if self._busy:
            return
        self._busy = True
        self._close_quick()
        QTimer.singleShot(80, self._do_window)

    def _do_window(self):
        self._remember_target()
        try:
            shot = ScreenShot()
            rect = shot.foreground_rect()
            if rect is None or rect.isEmpty():
                image = shot.full_image()
            else:
                rect = rect.intersected(shot.virtual_rect)
                image = shot.image_for(rect)
            self._finish(image, "default")
        except Exception as exc:
            QMessageBox.warning(None, "캡처 실패", str(exc))
        finally:
            self._busy = False

    def capture_scroll(self):
        """스크롤 캡처 — 영역을 고른 뒤 자동으로 굴리며 이어붙인다."""
        if self._busy or self.overlay is not None:
            return
        self._pending_scroll = True
        self.capture_region()

    def _start_scroll(self, logical_rect, shot, manual=False):
        import scrollcap
        self.scroller = scrollcap.ScrollCapture(logical_rect, shot, manual)
        self.scroller.finished.connect(self._scroll_done)
        self.scroller.failed.connect(self._scroll_failed)
        self.scroller.start()

    def _scroll_done(self, image, info):
        runner = self.scroller
        self.scroller = None
        self._last_scroll_frames = list(runner.frames) if runner else []
        if image.isNull():
            return
        note = info.get("note") or ""
        frames = info.get("frames", 1)
        captured = info.get("captured", frames)
        if frames <= 1 and captured > 1:
            self._scroll_stitch_failed(captured)
            return
        self.last_image = image
        window = self.open_editor(image, None)
        summary = "%d장을 이어붙였습니다 (%d × %d)" % (
            frames, image.width(), image.height())
        if note:
            summary += "  ·  " + note
        window.info_label.setText(summary)

    def _scroll_stitch_failed(self, captured):
        """여러 장 찍었지만 이어붙이지 못했을 때."""
        box = QMessageBox()
        box.setWindowIcon(self.icon)
        box.setWindowTitle("스크롤 캡처")
        box.setText("%d장을 찍었지만 서로 이어붙이지 못했습니다." % captured)
        box.setInformativeText(
            "화면이 계속 움직이거나(광고·동영상), 스크롤되는 내용이 거의 없는 "
            "경우에 이럴 수 있습니다.\n\n"
            "본문 영역만 다시 잡아 보시거나, 직접 스크롤하며 찍기를 "
            "사용해 보세요.")
        manual = box.addButton("직접 스크롤하며 찍기", QMessageBox.AcceptRole)
        save = box.addButton("찍은 화면 저장", QMessageBox.ActionRole)
        box.addButton("닫기", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is manual and self._last_scroll_target:
            rect, shot = self._last_scroll_target
            self._start_scroll(rect, shot, manual=True)
        elif clicked is save:
            self._save_scroll_frames()

    def _save_scroll_frames(self):
        """진단용으로 찍은 낱장을 폴더에 저장한다."""
        frames = getattr(self, "_last_scroll_frames", [])
        if not frames:
            return
        from datetime import datetime
        now = datetime.now()
        folder = CONFIG.save_dir() / ("스크롤원본_%04d%02d%02d_%02d%02d%02d" % (
            now.year, now.month, now.day, now.hour, now.minute, now.second))
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for index, frame in enumerate(frames, 1):
                frame.save(str(folder / ("%02d.png" % index)), "PNG")
        except Exception as exc:
            QMessageBox.warning(None, "저장 실패", str(exc))
            return
        open_folder(folder)

    def _scroll_failed(self, message):
        self.scroller = None
        if not message:
            return
        box = QMessageBox()
        box.setWindowIcon(self.icon)
        box.setWindowTitle("스크롤 캡처")
        box.setText(message)
        manual = box.addButton("직접 스크롤하며 찍기",
                               QMessageBox.AcceptRole)
        box.addButton("닫기", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is manual and self._last_scroll_target:
            rect, shot = self._last_scroll_target
            self._start_scroll(rect, shot, manual=True)

    def capture_delayed(self):
        if self._busy or self.overlay is not None:
            return
        seconds = max(1, int(CONFIG["delay_seconds"]))
        self.notify("지연 캡처", "%d초 뒤에 화면을 붙잡습니다." % seconds)
        QTimer.singleShot(seconds * 1000, self.capture_region)

    # --------------------------------------------------------- 결과 처리
    def _on_cancel(self):
        self.overlay = None
        self._busy = False
        self._pending_scroll = None

    def _on_selection(self, image, action):
        overlay = self.overlay
        self.overlay = None
        self._busy = False
        pending = self._pending_scroll
        self._pending_scroll = None
        if overlay is not None:
            try:
                rect = overlay.last_rect
                self._last_scroll_target = ((rect, overlay.shot)
                                            if rect is not None else None)
                if rect is not None:
                    # 쉼표·마침표로 다시 불러오고, 직전 영역 캡처에 쓴다
                    CONFIG.remember_region(rect.x(), rect.y(),
                                           rect.width(), rect.height())
            except Exception:
                self._last_scroll_target = None
        self._fixed_size = None
        if action == "scroll" or (pending and action == "default"):
            if self._last_scroll_target:
                rect, shot = self._last_scroll_target
                QTimer.singleShot(120, lambda: self._start_scroll(rect, shot))
                return
        if image.isNull():
            return
        self._finish(image, action)

    def _foreground_title(self):
        """지금 앞에 있는 창의 제목 (파일 이름 규칙 %W 용). 실패해도 조용히."""
        if "%W" not in (CONFIG["file_pattern"] or ""):
            return ""
        try:
            hwnd = winutil.foreground_window()
            if not hwnd or winutil.is_own_window(hwnd):
                # 우리 선택 화면이 앞에 있을 때는 그 아래 창을 찾는다
                hwnd = self._last_target_hwnd or 0
            if not hwnd or winutil.is_own_window(hwnd):
                return ""
            return winutil.window_title(hwnd)
        except Exception:
            return ""

    def _finish(self, image: QImage, action: str, window_title=""):
        if image.isNull():
            return
        self.last_image = image
        # 파일 이름 규칙에 %W 를 쓸 때만 의미가 있다
        title = window_title or self._foreground_title()
        if action == "copy":
            QGuiApplication.clipboard().setImage(image)
            temp_path = recent.store_unsaved(image)
            if not self._show_quick(image, temp_path, "복사함", saved=False):
                self.notify("복사 완료", "클립보드에 복사했습니다.")
            return
        if action == "save":
            path = saver.save_image(image, title)
            if path:
                if not self._show_quick(image, path, "저장함"):
                    self.notify("저장 완료", path)
            else:
                QMessageBox.warning(None, "저장 실패",
                                    "파일을 저장하지 못했습니다.\n"
                                    "설정에서 저장 폴더를 확인해 주세요.")
            return
        if action == "pin":
            self.add_pin(image)
            return
        if action == "ocr":
            self.run_ocr(image)
            return
        if action == "qr":
            self.run_qr(image)
            return
        if action == "edit":
            self.open_editor(image, None)
            return

        # 기본 동작 (설정에 따름)
        saved_path = None
        temp_path = None
        if CONFIG["after_copy"]:
            QGuiApplication.clipboard().setImage(image)
        if CONFIG["after_save"]:
            saved_path = saver.save_image(image, title)
        if not saved_path:
            temp_path = recent.store_unsaved(image)
        if CONFIG["after_edit"]:
            self.open_editor(image, saved_path)
        elif saved_path:
            note = "저장·복사함" if CONFIG["after_copy"] else "저장함"
            if not self._show_quick(image, saved_path, note):
                self.notify("저장 완료", saved_path)
        elif CONFIG["after_copy"]:
            if not self._show_quick(image, temp_path, "복사함", saved=False):
                self.notify("복사 완료", "클립보드에 복사했습니다.")
        else:
            self._show_quick(image, temp_path, "캡처함", saved=False)

    # ------------------------------------------------ 캡처 직후 미리보기
    def _show_quick(self, image, path=None, note="", saved=True):
        """구석에 작은 미리보기를 띄운다. 설정에서 껐으면 False."""
        if not CONFIG["quick_preview"]:
            return False
        self._close_quick()
        try:
            from quickview import QuickView
            view = QuickView(image, path if saved else None, note,
                             CONFIG["quick_preview_seconds"])
        except Exception:
            return False
        if not saved and path:
            view.drag_path = path
        view.editRequested.connect(
            lambda img, p: self.open_editor(img, p))
        view.pinRequested.connect(self.add_pin)
        view.destroyed.connect(lambda *_: self._forget_quick(view))
        self.quick = view
        view.popup()
        return True

    def _forget_quick(self, view):
        if self.quick is view:
            self.quick = None

    def _close_quick(self):
        if self.quick is not None:
            try:
                self.quick.close()
            except RuntimeError:
                pass
            self.quick = None

    # ------------------------------------------------------------- 창 관리
    def open_editor(self, image, saved_path=None):
        window = EditorWindow(image, saved_path)
        window.setWindowIcon(self.icon)
        window.pinRequested.connect(self.add_pin)
        window.ocrRequested.connect(self.run_ocr)
        window.closed.connect(self._editor_closed)
        self.editors.append(window)
        window.show()
        window.raise_()
        window.activateWindow()
        return window

    def _editor_closed(self, window):
        if window in self.editors:
            self.editors.remove(window)

    def add_pin(self, image, at=None, scale=None, opacity=None, locked=False):
        pin = PinWindow(image, at=at, scale=scale, opacity=opacity,
                        locked=locked)
        pin.setWindowIcon(self.icon)
        pin.editRequested.connect(lambda img: self.open_editor(img))
        pin.ocrRequested.connect(self.run_ocr)
        pin.qrRequested.connect(self.run_qr)
        pin.closed.connect(self._pin_closed)
        self.pins.append(pin)
        if self._pins_hidden:
            self._pins_hidden = False          # 새 핀을 띄우면 숨긴 핀도 다시
            for other in self.pins:
                other.show()
        pin.show()
        pin.raise_()
        return pin

    def _pin_closed(self, pin):
        if pin in self.pins:
            self.pins.remove(pin)
        if self._quitting or getattr(pin, "forget_on_close", False):
            return
        try:
            self.closed_pins.append(pin.state())
        except Exception:
            return
        limit = max(1, int(CONFIG["pin_restore_limit"] or 10))
        del self.closed_pins[:-limit]

    def pin_clipboard(self):
        """클립보드 내용을 핀으로 — 이미지, 이미지 파일, 색 코드, 글자."""
        import pinwin
        clipboard = QGuiApplication.clipboard()
        mime = clipboard.mimeData()
        image = clipboard.image()
        if not image.isNull():
            self.add_pin(image)
            return
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    loaded = QImage(url.toLocalFile())
                    if not loaded.isNull():
                        self.add_pin(loaded)
                        return
        text = clipboard.text() or ""
        if text.strip():
            color = pinwin.parse_color(text)
            if color is not None and color.isValid():
                self.add_pin(pinwin.color_card(color))
                return
            html = mime.html() if (mime is not None and mime.hasHtml()) else None
            self.add_pin(pinwin.text_card(text, html))
            return
        self.notify("핀 고정", "클립보드에 이미지나 글자가 없습니다.")

    # ------------------------------------------------------------ 핀 관리
    def _toast(self, text, ms=1600):
        try:
            import toast
            toast.show(text, ms)
        except Exception:
            self.notify("캡처도구", text)

    def toggle_pins_hidden(self):
        if not self.pins:
            self._pins_hidden = False
            self._toast("고정된 핀이 없습니다")
            return
        self._pins_hidden = not self._pins_hidden
        for pin in list(self.pins):
            try:
                if self._pins_hidden:
                    pin.hide()
                else:
                    pin.show()
                    pin.raise_()
            except RuntimeError:
                pass
        self._toast("핀 %d개를 숨겼습니다 — 같은 키로 다시 보이기" % len(self.pins)
                    if self._pins_hidden else "핀 %d개를 다시 띄웠습니다" % len(self.pins))

    def restore_pin(self):
        if not self.closed_pins:
            self._toast("되살릴 핀이 없습니다")
            return
        state = self.closed_pins.pop()
        from PySide6.QtCore import QPoint
        self.add_pin(state["image"], at=QPoint(state["x"], state["y"]),
                     scale=state["scale"], opacity=state["opacity"],
                     locked=state["locked"])
        left = len(self.closed_pins)
        self._toast("핀을 되살렸습니다" + (" (%d개 더 있음)" % left if left else ""))

    def toggle_passthrough(self):
        """핀 클릭 통과 — 켜져 있으면 모두 끄고, 아니면 마우스 아래 핀을 켠다."""
        if not self.pins:
            self._toast("고정된 핀이 없습니다")
            return
        active = [p for p in self.pins if p.passthrough]
        if active:
            for pin in active:
                pin.set_passthrough(False)
            self._toast("핀 클릭 통과를 껐습니다")
            return
        from PySide6.QtGui import QCursor
        here = QCursor.pos()
        target = None
        for pin in reversed(self.pins):
            if pin.isVisible() and pin.frameGeometry().contains(here):
                target = pin
                break
        target = target or self.pins[-1]
        target.set_passthrough(True)
        if target.passthrough:
            self._toast("클릭 통과 켬 — 끄려면 %s"
                        % (CONFIG.hotkey("pins_passthrough") or "트레이 메뉴 → 핀 관리"),
                        2600)
        else:
            self._toast("클릭 통과를 켜지 못했습니다")

    def close_all_pins(self):
        for pin in list(self.pins):
            try:
                pin.close()
            except RuntimeError:
                pass

    def _layout_folder(self):
        folder = CONFIG.data_dir() / "핀배치"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def save_pin_layout(self):
        import json
        if not self.pins:
            self._toast("저장할 핀이 없습니다")
            return
        folder = self._layout_folder()
        for old in folder.glob("핀_*.png"):
            try:
                old.unlink()
            except Exception:
                pass
        entries = []
        for index, pin in enumerate(self.pins, 1):
            state = pin.state()
            name = "핀_%02d.png" % index
            if not state["image"].save(str(folder / name), "PNG"):
                continue
            entries.append({"file": name, "x": state["x"], "y": state["y"],
                            "scale": state["scale"],
                            "opacity": state["opacity"],
                            "locked": state["locked"]})
        try:
            (folder / "배치.json").write_text(
                json.dumps(entries, ensure_ascii=False, indent=1),
                encoding="utf-8")
        except Exception as exc:
            QMessageBox.warning(None, "핀 배치 저장", "저장하지 못했습니다.\n%s" % exc)
            return
        self._toast("핀 %d개의 배치를 저장했습니다" % len(entries))

    def load_pin_layout(self):
        import json
        from PySide6.QtCore import QPoint
        folder = self._layout_folder()
        try:
            entries = json.loads((folder / "배치.json").read_text(encoding="utf-8"))
        except Exception:
            self._toast("저장한 핀 배치가 없습니다")
            return
        count = 0
        for entry in entries:
            image = QImage(str(folder / entry.get("file", "")))
            if image.isNull():
                continue
            self.add_pin(image, at=QPoint(int(entry.get("x", 80)),
                                          int(entry.get("y", 80))),
                         scale=float(entry.get("scale", 1.0)),
                         opacity=float(entry.get("opacity", 1.0)),
                         locked=bool(entry.get("locked", False)))
            count += 1
        self._toast("핀 %d개를 불러왔습니다" % count if count
                    else "불러올 핀이 없습니다")

    # ---------------------------------------------------------- QR 코드
    def run_qr(self, image):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            import qrdecode
            text = qrdecode.decode(image)
        except Exception as exc:
            text = None
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(None, "QR 코드 읽기", "읽지 못했습니다.\n%s" % exc)
            return
        QApplication.restoreOverrideCursor()
        if not text:
            QMessageBox.information(
                None, "QR 코드 읽기",
                "QR 코드를 찾지 못했습니다.\n\n"
                "QR 코드 전체(네 모서리의 흰 여백까지)가 들어오게 "
                "조금 넓게 잡아 보세요.")
            return
        self._show_qr_result(text)

    def _show_qr_result(self, text):
        QGuiApplication.clipboard().setText(text)
        box = QMessageBox()
        box.setWindowIcon(self.icon)
        box.setWindowTitle("QR 코드 읽기")
        box.setText("QR 코드 내용을 클립보드에 복사했습니다.")
        box.setInformativeText(text if len(text) <= 600 else text[:600] + " …")
        box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lowered = text.strip().lower()
        is_link = lowered.startswith(("http://", "https://", "www."))
        open_button = box.addButton("링크 열기", QMessageBox.AcceptRole) \
            if is_link else None
        box.addButton("닫기", QMessageBox.RejectRole)
        box.exec()
        if open_button is not None and box.clickedButton() is open_button:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            url = text.strip()
            if url.lower().startswith("www."):
                url = "https://" + url
            QDesktopServices.openUrl(QUrl(url))

    def reopen_last(self):
        if self.last_image is None or self.last_image.isNull():
            self.notify("캡처도구", "아직 캡처한 이미지가 없습니다.")
            return
        self.open_editor(self.last_image)

    # ---------------------------------------------------------------- OCR
    def run_ocr(self, image):
        """글자 추출 — 별도 스레드에서 돌려 프로그램이 멈추지 않게 한다."""
        try:
            import ocr as ocr_module
        except Exception as exc:
            QMessageBox.warning(None, "글자 추출",
                                "모듈을 불러오지 못했습니다.\n%s" % exc)
            return
        try:
            task = ocr_module.OcrTask(image, CONFIG["ocr_language"] or "")
        except ocr_module.OcrError as exc:
            QMessageBox.information(None, "글자 추출", str(exc))
            return
        except Exception as exc:
            QMessageBox.warning(None, "글자 추출", "실패했습니다.\n%s" % exc)
            return

        progress = QProgressDialog("글자를 읽는 중입니다…", "취소", 0, 0)
        progress.setWindowTitle("글자 추출")
        progress.setWindowIcon(self.icon)
        progress.setWindowModality(Qt.NonModal)
        progress.setMinimumDuration(200)
        progress.setAutoClose(False)
        progress.setAutoReset(False)

        state = {"done": False}

        def close_progress():
            state["done"] = True
            progress.reset()
            progress.close()

        def on_finished(text):
            if state["done"]:
                return
            close_progress()
            # 캡처 속에 QR 코드가 있으면 그 내용도 함께 보여 준다
            if image.width() * image.height() <= 6000000:
                try:
                    import qrdecode
                    qr_text = qrdecode.decode(image)
                except Exception:
                    qr_text = None
                if qr_text:
                    text = "[QR 코드]\n%s\n\n%s" % (qr_text, text or "")
            self._show_ocr_result(text)

        def on_failed(message):
            if state["done"]:
                return
            close_progress()
            QMessageBox.information(None, "글자 추출", message)

        task.finished.connect(on_finished)
        task.failed.connect(on_failed)
        progress.canceled.connect(lambda: (task.cancel(), close_progress()))
        self._ocr_task = task            # 끝날 때까지 살려 둔다
        task.start()
        progress.show()

    # --------------------------------------------------- 단축키 진단
    def check_hotkeys(self):
        """어떤 단축키가 왜 막혔는지 보여 주고 빈 키를 찾아 준다."""
        from hotkeydlg import HotkeyDoctor
        if self.hotkey_doctor is not None:
            try:
                self.hotkey_doctor.set_status(self.hotkey_status)
                self.hotkey_doctor.raise_()
                self.hotkey_doctor.activateWindow()
                return
            except RuntimeError:
                self.hotkey_doctor = None
        dialog = HotkeyDoctor(int(self.host.winId()), self.hotkey_status)
        dialog.setWindowIcon(self.icon)
        dialog.changed.connect(self._hotkeys_edited)
        self.hotkey_doctor = dialog
        dialog.exec()
        self.hotkey_doctor = None

    def _hotkeys_edited(self):
        """진단 창에서 키를 바꿨을 때 곧바로 다시 잡는다."""
        self.reload_hotkeys(announce=False)
        self._refresh_tray_labels()
        if self.hotkey_doctor is not None:
            try:
                self.hotkey_doctor.set_status(self.hotkey_status)
            except RuntimeError:
                self.hotkey_doctor = None

    # ------------------------------------------------- 글자 추출 진단
    def check_ocr(self):
        """이 컴퓨터가 글자를 읽을 준비가 됐는지 실제로 확인해서 알려 준다."""
        try:
            import ocr as ocr_module
        except Exception as exc:
            QMessageBox.warning(None, "글자 추출 진단",
                                "확인하지 못했습니다.\n%s" % exc)
            return

        progress = QProgressDialog("확인하는 중입니다…", None, 0, 0)
        progress.setWindowTitle("글자 추출 진단")
        progress.setWindowIcon(self.icon)
        progress.setWindowModality(Qt.NonModal)
        progress.setMinimumDuration(150)
        progress.setAutoClose(False)
        progress.setAutoReset(False)

        task = ocr_module.CheckTask()

        def done(report):
            progress.reset()
            progress.close()
            self._show_ocr_report(report, ocr_module.INSTALL_COMMAND)

        task.finished.connect(done)
        self._ocr_check = task           # 끝날 때까지 살려 둔다
        task.start()
        progress.show()

    def _show_ocr_report(self, report, install_command):
        box = QMessageBox()
        box.setWindowIcon(self.icon)
        box.setWindowTitle("글자 추출 진단")
        box.setIcon(QMessageBox.Information if report.get("ok")
                    else QMessageBox.Warning)
        box.setText(report.get("summary", ""))
        box.setInformativeText(report.get("detail", ""))
        box.addButton(QMessageBox.Ok)
        if not report.get("korean"):
            copy_button = box.addButton("설치 명령 복사",
                                        QMessageBox.ActionRole)
        else:
            copy_button = None
        box.exec()
        if copy_button is not None and box.clickedButton() is copy_button:
            QGuiApplication.clipboard().setText(install_command)
            self.notify("복사했습니다",
                        "PowerShell 에 붙여넣고 Enter 를 누르세요.")

    def _show_ocr_result(self, text):
        if not text.strip():
            QMessageBox.information(None, "글자 추출",
                                    "글자를 찾지 못했습니다.\n"
                                    "글자가 작으면 더 크게 캡처해 보세요.")
            return
        QGuiApplication.clipboard().setText(text)
        dialog = OcrResultDialog(text)
        dialog.setWindowIcon(self.icon)
        dialog.exec()

    # ------------------------------------------------------------- 설정
    def open_settings(self):
        dialog = SettingsDialog()
        dialog.setWindowIcon(self.icon)
        dialog.applied.connect(self._settings_applied)
        dialog.check_update_requested.connect(self.check_updates)
        dialog.exec()

    def _settings_applied(self):
        self.reload_hotkeys()
        self._refresh_tray_labels()

    def show_help(self):
        rows = []
        for action, label in HOTKEY_LABELS.items():
            rows.append("  %-22s %s" % (label, CONFIG.hotkey(action) or "(없음)"))
        text = (
            "전역 단축키\n" + "\n".join(rows) +
            "\n\n영역 선택 중\n"
            "  드래그               캡처할 범위 지정\n"
            "  창 위에서 클릭        그 창만 캡처\n"
            "  Ctrl+A               현재 모니터 전체\n"
            "  화살표키              1픽셀씩 이동 (Alt: 크기 조절)\n"
            "  Enter                기본 동작 실행\n"
            "  E / P / T / Q        편집 / 핀 고정 / 글자 추출 / QR 읽기\n"
            "  S                    스크롤 캡처 (긴 화면 이어붙이기)\n"
            "  C                    마우스 아래 색 코드 복사 (Shift+C 는 RGB)\n"
            "  , / .                예전에 잡았던 영역 불러오기 (더 예전 / 최근)\n"
            "  R                    화면 자 — 빈 공간의 가로·세로 길이 재기\n"
            "  Tab                  안내판을 핀·전역 단축키로 넘기기\n"
            "  휠                    잡는 단위 바꾸기 (칸 ↔ 내용 영역 ↔ 창)\n"
            "  제목표시줄 위          창 전체 · 창 안쪽이면 내용 영역\n"
            "  Ctrl (누른 채)        창 전체 잡기 (Ctrl+Shift 모니터 전체)\n"
            "  Alt (누른 채)         경계선에 붙지 않게\n"
            "  Shift (누른 채)       정사각형으로\n"
            "  방향키                마우스를 1픽셀씩 (Shift 10픽셀)\n"
            "  Space                마우스 없이 시작점·끝점 찍기\n"
            "  F1                   단축키 안내판 켜기 / 끄기\n"
            "  Esc                  취소\n\n"
            "편집창\n"
            "  V R E A L P H T N M B C   도구 바꾸기\n"
            "  Ctrl+Z / Ctrl+Y      실행 취소 / 다시 실행\n"
            "  Ctrl+S / Ctrl+C      저장 / 복사\n"
            "  Ctrl+T / Ctrl+E      핀 고정 / 글자 추출\n"
            "  Space (누른 채)+드래그  손 모양으로 화면 옮기기\n"
            "  마우스 가운데 버튼     Space 없이도 화면 옮기기\n"
            "  Ctrl+휠              가리키는 곳 기준으로 확대·축소\n"
            "  Shift+휠             가로로 이동\n\n"
            "편집창 — 그려 놓은 도형 손보기 (V 선택 도구)\n"
            "  모서리의 흰 점        끌어서 크기 조절 (Shift 비율 유지)\n"
            "  방향키                1픽셀씩 이동 (Shift 10픽셀)\n"
            "  위쪽 줄 색·굵기       고른 도형에 바로 적용\n"
            "  Ctrl+D                복제\n"
            "  Ctrl+Shift+↑ / ↓      맨 앞으로 / 맨 뒤로\n"
            "  Delete                지우기\n\n"
            "편집창 — [이미지 ▾] 메뉴\n"
            "  Ctrl+Shift+L / R      왼쪽 / 오른쪽 90도 회전\n"
            "  좌우·위아래 뒤집기\n"
            "  여백·테두리 두르기 · 크기 바꾸기\n"
            "  날짜 도장 · 워터마크\n"
            "  Ctrl+Shift+M          개인정보 자동 가리기 (이메일·전화·주민번호·카드)\n\n"
            "핀 고정 창\n"
            "  드래그               위치 옮기기\n"
            "  Ctrl+끌기             카톡·메일 창에 이미지로 바로 놓기\n"
            "  휠                   크기 조절\n"
            "  Ctrl+휠              투명하게\n"
            "  가운데 버튼 / 0       크기·투명도 원래대로\n"
            "  더블클릭              클립보드로 복사\n"
            "  Shift+더블클릭 / F    작게 접기 ↔ 펼치기\n"
            "  L                    잠금 (옮기기·크기 막기)\n"
            "  1 / 2 / 3 / 4        왼쪽·오른쪽 회전 / 좌우·위아래 뒤집기\n"
            "  T / Q                글자 추출 / QR 읽기\n"
            "  오른쪽 클릭           메뉴 (클릭 통과 켜기 포함)\n"
            "  Esc                  닫기 (닫은 핀은 되살릴 수 있습니다)\n\n"
            "핀 — 어디서나 쓰는 키 (트레이 → 핀 관리 에도 있음)\n"
            "  %-20s 클립보드 핀 (이미지·이미지 파일·색 코드·글자)\n"
            "  %-20s 핀 모두 숨기기 / 보이기\n"
            "  %-20s 닫은 핀 되살리기 (최근 %d개)\n"
            "  %-20s 핀 클릭 통과 켜기 / 끄기\n"
            "                       (켜면 핀은 보이고 마우스는 아래 창으로)\n\n"
            "캡처 직후 미리보기 (편집창 자동 열기를 끈 경우)\n"
            "  썸네일 끌기           카톡·메일·탐색기에 바로 놓기\n"
            "  썸네일 클릭           편집창에서 열기\n"
            "  마우스 올려 두기       사라지지 않고 기다림\n\n"
            "스크롤 캡처\n"
            "  자동 진행 중          Esc 로 중단 (여기까지 이어붙여 줍니다)\n"
            "                       휠 칸 수를 알아서 맞추고, 안 맞으면 덜 굴려 다시 찍습니다\n"
            "  직접 스크롤 모드      천천히 스크롤하면 멈출 때마다 자동으로 붙입니다\n"
            "                       Space 한 장 추가 · Enter 완료 · Esc 취소"
            % (CONFIG.hotkey("pin_clipboard") or "(없음)",
               CONFIG.hotkey("pins_hide") or "(없음)",
               CONFIG.hotkey("pin_restore") or "(없음)",
               int(CONFIG["pin_restore_limit"] or 10),
               CONFIG.hotkey("pins_passthrough") or "(없음)"))
        box = QMessageBox()
        box.setWindowIcon(self.icon)
        box.setWindowTitle("캡처도구 도움말")
        box.setText("캡처도구 v%s — 단축키와 사용법" % VERSION)
        box.setDetailedText(text)
        box.setInformativeText(
            "자세한 목록은 아래 '자세히 보기'를 눌러 확인하세요.")
        box.exec()

    # ------------------------------------------------------------- 종료
    def quit(self):
        self._quitting = True
        if self.updates is not None:
            try:
                self.updates.stop()
            except Exception:
                pass
        if winutil.IS_WIN:
            hwnd = int(self.host.winId())
            for hotkey_id in HOTKEY_IDS.values():
                winutil.unregister_hotkey(hwnd, hotkey_id)
        CONFIG.save()
        self.tray.hide()
        self.qt_app.quit()

    def run(self):
        return self.qt_app.exec()
