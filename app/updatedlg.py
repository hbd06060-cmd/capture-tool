# -*- coding: utf-8 -*-
"""업데이트 화면 — 확인 중 표시, 새 버전 안내, 내려받기 진행률, 바꿔 끼우기.

무거운 일(인터넷 연결, 파일 내려받기)은 모두 별도 스레드에서 한다.
확인하는 동안에도 캡처는 평소처럼 쓸 수 있어야 하기 때문이다.
"""
from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox,
                               QProgressBar, QPushButton, QVBoxLayout)

import updater
from config import CONFIG, VERSION


# ------------------------------------------------------- 뒤에서 도는 일꾼
class CheckWorker(QThread):
    """새 버전이 있는지 확인만 한다."""
    done = Signal(object)          # updater.Result

    def __init__(self, force=False, parent=None):
        super().__init__(parent)
        self.force = force

    def run(self):
        try:
            self.done.emit(updater.check(force=self.force))
        except Exception as exc:                      # 혹시 모를 예외까지
            self.done.emit(updater.Result("error", message=str(exc)))


class DownloadWorker(QThread):
    """새 버전을 내려받아 압축을 풀고 확인까지 한다 (아직 바꾸지는 않는다)."""
    progress = Signal(int, int)    # 받은 바이트, 전체 바이트
    done = Signal(bool, str, object)   # 성공?, 메시지, 풀어 놓은 폴더

    def run(self):
        try:
            zip_path = updater.download(
                lambda got, total: self.progress.emit(got, total))
            folder, found = updater.extract(zip_path)
            self.done.emit(True, found, folder)
        except updater.UpdateError as exc:
            self.done.emit(False, str(exc), None)
        except Exception as exc:
            self.done.emit(False, "내려받는 중 문제가 생겼습니다.\n(%s)" % exc,
                           None)


# ------------------------------------------------------------- 안내 창
class UpdateDialog(QDialog):
    """새 버전 안내 창. 받아 둔 게 있으면 바로 '지금 적용'으로 시작한다."""

    def __init__(self, result, ready_folder=None, parent=None):
        super().__init__(parent)
        self.result = result
        self.folder = ready_folder        # 이미 받아 둔 폴더 (없으면 None)
        self.worker = None
        self.installed = False

        self.setWindowTitle("캡처도구 업데이트")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)

        head = QLabel("새 버전 v%s 가 나왔습니다." % result.version, self)
        head.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(head)

        sub = QLabel("지금 쓰시는 버전은 v%s 입니다." % VERSION, self)
        sub.setStyleSheet("color: #666;")
        layout.addWidget(sub)

        if result.title():
            title = QLabel(result.title(), self)
            title.setWordWrap(True)
            title.setStyleSheet("margin-top: 8px; font-weight: bold;")
            layout.addWidget(title)

        notes = result.notes()
        if notes:
            body = QLabel("\n".join("· " + n for n in notes), self)
            body.setWordWrap(True)
            body.setStyleSheet("color: #333; margin-top: 4px;")
            layout.addWidget(body)

        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #555; margin-top: 10px;")
        layout.addWidget(self.status)

        self.bar = QProgressBar(self)
        self.bar.setVisible(False)
        self.bar.setTextVisible(True)
        layout.addWidget(self.bar)

        row = QHBoxLayout()
        self.skip_btn = QPushButton("이 버전 건너뛰기", self)
        self.skip_btn.clicked.connect(self._skip)
        row.addWidget(self.skip_btn)
        row.addStretch(1)
        self.later_btn = QPushButton("나중에", self)
        self.later_btn.clicked.connect(self.reject)
        row.addWidget(self.later_btn)
        self.go_btn = QPushButton(self)
        self.go_btn.setDefault(True)
        self.go_btn.clicked.connect(self._go)
        row.addWidget(self.go_btn)
        layout.addLayout(row)

        if self.folder is not None:
            self.status.setText("내려받기가 끝났습니다. 바로 적용할 수 있습니다.")
            self.go_btn.setText("지금 적용")
        else:
            self.go_btn.setText("내려받고 적용")

    # ---------------------------------------------------------- 단추 동작
    def _skip(self):
        CONFIG["update_skip_version"] = self.result.version
        CONFIG.save()
        self.reject()

    def _go(self):
        if self.folder is not None:
            self._install()
            return
        self._busy(True)
        self.status.setText("새 버전을 내려받는 중입니다…")
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)           # 전체 크기를 모를 때는 흐르는 막대
        self.worker = DownloadWorker(self)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._on_downloaded)
        self.worker.start()

    def _busy(self, busy):
        for button in (self.go_btn, self.skip_btn, self.later_btn):
            button.setEnabled(not busy)

    def _on_progress(self, got, total):
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(got)
            self.bar.setFormat("%.1f / %.1f MB"
                               % (got / 1048576.0, total / 1048576.0))
        else:
            self.bar.setFormat("%.1f MB 받는 중" % (got / 1048576.0))

    def _on_downloaded(self, ok, message, folder):
        self.worker = None
        if not ok:
            self.bar.setVisible(False)
            self._busy(False)
            self.status.setText(message)
            self.status.setStyleSheet("color: #c62828; margin-top: 10px;")
            return
        self.folder = folder
        self._install()

    def _install(self):
        self._busy(True)
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)
        self.status.setStyleSheet("color: #555; margin-top: 10px;")
        self.status.setText("파일을 바꿔 끼우는 중입니다…")
        # 화면이 멈춘 것처럼 보이지 않도록 한 박자 뒤에 실행한다
        QTimer.singleShot(60, self._do_install)

    def _do_install(self):
        try:
            count, backup, skipped = updater.install(self.folder)
        except updater.UpdateError as exc:
            self.bar.setVisible(False)
            self._busy(False)
            self.status.setStyleSheet("color: #c62828; margin-top: 10px;")
            self.status.setText(str(exc))
            return
        except Exception as exc:
            self.bar.setVisible(False)
            self._busy(False)
            self.status.setStyleSheet("color: #c62828; margin-top: 10px;")
            self.status.setText("바꾸는 중 문제가 생겼습니다.\n(%s)" % exc)
            return

        self.installed = True
        CONFIG["update_skip_version"] = ""
        CONFIG.save()
        updater.cleanup_work()

        note = "파일 %d개를 새 것으로 바꿨습니다." % count
        if skipped:
            note += ("\n다만 %s 는 지금 쓰는 중이라 바꾸지 못했습니다. "
                     "프로그램 동작에는 지장이 없습니다." % ", ".join(skipped))
        note += "\n예전 파일은 여기에 백업해 두었습니다.\n%s" % backup

        answer = QMessageBox.question(
            self, "업데이트 완료",
            note + "\n\n새 버전으로 쓰려면 프로그램을 다시 시작해야 합니다."
                   "\n지금 다시 시작할까요?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        self.restart = answer == QMessageBox.Yes
        self.accept()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(3000)
        super().closeEvent(event)


# ------------------------------------------------------------ 지휘자
class UpdateManager(QObject):
    """캡처도구 본체에 붙어 업데이트 흐름 전체를 맡는다."""

    def __init__(self, app):
        super().__init__()
        self.app = app                 # CaptureApp
        self.checker = None
        self.downloader = None
        self.quiet = True              # 조용히 확인하는 중인지
        self.pending = None            # 새 버전이 있을 때의 updater.Result
        self.ready_folder = None       # 받아서 풀어 둔 폴더
        self.dialog = None

    # -------------------------------------------------- 시작할 때 조용히
    def check_quietly(self):
        """프로그램을 켠 뒤 주기가 됐으면 조용히 확인한다."""
        if not updater.due_for_check():
            return
        self._start_check(force=False, quiet=True)

    # ---------------------------------------------- 메뉴에서 직접 눌렀을 때
    def check_now(self):
        """'업데이트 확인' 메뉴. 결과를 반드시 보여 준다."""
        if self.pending is not None:
            self.open_dialog()
            return
        if self.checker is not None and self.checker.isRunning():
            self.app.notify("업데이트", "이미 확인하고 있습니다. 잠시만요.")
            return
        self.app.notify("업데이트", "새 버전이 있는지 확인하고 있습니다…")
        self._start_check(force=True, quiet=False)

    def _start_check(self, force, quiet):
        self.quiet = quiet
        self.checker = CheckWorker(force, self)
        self.checker.done.connect(self._on_checked)
        self.checker.start()

    def _on_checked(self, result):
        self.checker = None
        if result.status != "error":
            updater.record_check_time()       # 주 스레드에서 기록

        if result.status == "error":
            if not self.quiet:
                QMessageBox.warning(None, "업데이트 확인", result.message)
            return

        if result.status == "none":
            if not self.quiet:
                QMessageBox.information(
                    None, "업데이트 확인",
                    result.message or "이미 최신 버전입니다.")
            return

        self.pending = result
        self.app.refresh_update_menu()

        if CONFIG.get("update_auto_download", True):
            self._start_download()
        elif self.quiet:
            self.app.notify("새 버전 v%s 가 나왔습니다" % result.version,
                            "트레이 아이콘 메뉴에서 설치할 수 있습니다.")
        else:
            self.open_dialog()

    # -------------------------------------------------- 자동으로 받아 두기
    def _start_download(self):
        if self.downloader is not None and self.downloader.isRunning():
            return
        self.downloader = DownloadWorker(self)
        self.downloader.done.connect(self._on_downloaded)
        self.downloader.start()

    def _on_downloaded(self, ok, message, folder):
        self.downloader = None
        if not ok:
            # 자동으로 받다 실패해도 방해하지 않는다. 창에서 다시 받으면 된다.
            if not self.quiet:
                QMessageBox.warning(None, "업데이트", message)
            return
        self.ready_folder = folder
        self.app.refresh_update_menu()
        if self.quiet:
            self.app.notify(
                "새 버전 v%s 를 받아 두었습니다" % self.pending.version,
                "트레이 아이콘 메뉴에서 '새 버전 설치'를 누르면 적용됩니다.")
        else:
            self.open_dialog()

    # ------------------------------------------------------------ 창 열기
    def open_dialog(self):
        if self.pending is None:
            self.check_now()
            return
        if self.dialog is not None and self.dialog.isVisible():
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        if self.downloader is not None and self.downloader.isRunning():
            # 뒤에서 이미 받고 있다. 창에서 또 받게 두면 같은 자리에 두 번
            # 쓰게 되므로, 끝날 때까지 기다리게 한다.
            self.app.notify(
                "새 버전 v%s 를 받는 중입니다" % self.pending.version,
                "다 받으면 알려 드립니다. 잠시만 기다려 주세요.")
            return
        dialog = UpdateDialog(self.pending, self.ready_folder)
        dialog.setWindowIcon(self.app.icon)
        dialog.setAttribute(Qt.WA_DeleteOnClose, False)
        self.dialog = dialog
        dialog.exec()
        self.dialog = None

        if dialog.installed:
            self.pending = None
            self.ready_folder = None
            self.app.refresh_update_menu()
            if getattr(dialog, "restart", False):
                self.app.restart_for_update()
        else:
            # '건너뛰기'를 눌렀으면 메뉴에서도 치운다
            if self.pending is not None and \
                    CONFIG.get("update_skip_version") == self.pending.version:
                self.pending = None
                self.ready_folder = None
                self.app.refresh_update_menu()

    def stop(self):
        """프로그램을 끌 때 돌고 있는 일꾼을 정리한다."""
        for worker in (self.checker, self.downloader):
            if worker is not None and worker.isRunning():
                worker.wait(2000)
