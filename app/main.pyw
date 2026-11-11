# -*- coding: utf-8 -*-
"""캡처도구 시작 지점."""
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SERVER_NAME = "CaptureTool-udcenter-single-instance"

# 로그가 한글이어도 깨지거나 예외가 나지 않도록
for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _log_path():
    try:
        base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) \
            else Path(__file__).resolve().parent.parent
        probe = base / "오류기록.txt"
        with open(probe, "a", encoding="utf-8"):
            pass
        return probe
    except Exception:
        return Path(os.environ.get("TEMP", ".")) / "capturetool_error.txt"


def _install_excepthook():
    def hook(exc_type, exc_value, exc_tb):
        message = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            with open(_log_path(), "a", encoding="utf-8") as handle:
                handle.write(message + "\n" + "-" * 60 + "\n")
        except Exception:
            pass
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None, "캡처도구 오류",
                    "예상치 못한 문제가 발생했습니다.\n\n%s\n\n"
                    "자세한 내용은 '오류기록.txt' 에 저장했습니다."
                    % str(exc_value)[:400])
        except Exception:
            pass
    sys.excepthook = hook


def main():
    _install_excepthook()

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:
        pass

    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    # 이미 실행 중이면 그쪽에서 영역 캡처를 시작하고 종료
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if socket.waitForConnected(300):
        socket.write(b"capture")
        socket.flush()
        socket.waitForBytesWritten(300)
        socket.disconnectFromServer()
        return 0

    QLocalServer.removeServer(SERVER_NAME)

    # 먼저 자리를 잡아 둔다. CaptureApp 을 만드는 동안(단축키 등록 포함)
    # 두 번째 복사본이 끼어들어 같은 단축키를 두고 다투지 않게 하기 위해서다.
    guard = QLocalServer()
    guard.listen(SERVER_NAME)

    from app import CaptureApp
    application = CaptureApp(sys.argv)

    # Qt 기본 대화상자를 한국어로
    try:
        from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
        for name in ("qtbase", "qt"):
            translator = QTranslator(application.qt_app)
            if translator.load(QLocale("ko_KR"), name, "_",
                               QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
                application.qt_app.installTranslator(translator)
                application.translators = getattr(
                    application, "translators", []) + [translator]
    except Exception:
        pass

    server = guard
    if not server.isListening():
        server.listen(SERVER_NAME)

    def on_new_connection():
        conn = server.nextPendingConnection()
        if conn is None:
            return

        def on_ready():
            data = bytes(conn.readAll())
            if b"capture" in data:
                application.capture_region()
            conn.disconnectFromServer()

        conn.readyRead.connect(on_ready)

    server.newConnection.connect(on_new_connection)

    if "--selftest" in sys.argv:
        # 배포본이 실제로 뜨는지 확인하기 위한 점검 모드
        from PySide6.QtCore import QTimer
        from config import CONFIG

        def selftest():
            print("[selftest] 트레이 아이콘:",
                  application.tray.isSystemTrayAvailable())
            print("[selftest] 설정 파일:", CONFIG.path)
            print("[selftest] 저장 폴더:", CONFIG.save_dir())
            application.capture_fullscreen()

        def overlay_test():
            application.capture_region()

        def check_overlay():
            # v1.9 부터 캡처 화면은 모니터마다 창을 띄우는 묶음(OverlayGroup)이다
            group = application.overlay
            overlays = getattr(group, "overlays", None)
            ov = overlays[0] if overlays else group
            print("[selftest] 오버레이 표시:", ov is not None and ov.isVisible(),
                  "geometry =", None if ov is None else ov.geometry())
            if ov is not None:
                from PySide6.QtCore import QRect
                ov.selection = QRect(30, 30, 240, 180)
                ov._finish_selection()
                ov._emit("copy")

        def finish():
            from PySide6.QtGui import QGuiApplication as _G
            print("[selftest] 클립보드 이미지:",
                  _G.clipboard().image().size())
            print("[selftest] 마지막 캡처:",
                  None if application.last_image is None
                  else application.last_image.size())
            print("[selftest] 편집창 수:", len(application.editors))
            print("[selftest] 정상 종료")
            application.quit()

        QTimer.singleShot(1500, selftest)
        QTimer.singleShot(4000, overlay_test)
        QTimer.singleShot(5500, check_overlay)
        QTimer.singleShot(7000, finish)

    code = application.run()
    server.close()
    QLocalServer.removeServer(SERVER_NAME)

    # 업데이트를 적용하고 '다시 시작'을 고른 경우. 자리(중복 실행 방지)를
    # 완전히 놓은 지금에야 새로 띄울 수 있다. 먼저 띄우면 새로 뜬 쪽이
    # '이미 실행 중'으로 보고 그냥 꺼져 버린다.
    if getattr(application, "_relaunch_after_quit", False):
        try:
            import updater
            updater.relaunch()
        except Exception:
            traceback.print_exc()
    return code


if __name__ == "__main__":
    sys.exit(main())
