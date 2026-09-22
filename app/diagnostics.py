# -*- coding: utf-8 -*-
"""진단 정보 저장 — 문제가 생겼을 때 상황을 파일로 남긴다."""
import platform
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import qVersion
from PySide6.QtGui import QGuiApplication

import capture
import winutil
from config import CONFIG, VERSION


def _line(title):
    return "\n" + title + "\n" + "-" * 60


def collect(app=None) -> str:
    now = datetime.now()
    out = []
    out.append("캡처도구 진단 정보")
    out.append("작성 시각: %04d-%02d-%02d %02d:%02d:%02d"
               % (now.year, now.month, now.day, now.hour, now.minute,
                  now.second))

    out.append(_line("1. 실행 환경"))
    out.append("  캡처도구 버전 : v%s" % VERSION)
    out.append("  운영체제      : %s %s (빌드 %s)"
               % (platform.system(), platform.release(),
                  winutil.windows_build() or platform.version()))
    out.append("  파이썬        : %s" % sys.version.split()[0])
    out.append("  Qt            : %s" % qVersion())
    out.append("  캡처 제외 지원: %s"
               % ("예 (Windows 10 2004 이상)"
                  if winutil.windows_build() >= 19041 else
                  "아니오 — 스크롤 캡처 중 안내창이 잠깐 사라집니다"))

    out.append(_line("2. 모니터"))
    try:
        for index, screen in enumerate(QGuiApplication.screens(), 1):
            geo = screen.geometry()
            out.append("  [%d] %s  논리 %d,%d %dx%d  배율 %.2f"
                       % (index, screen.name(), geo.x(), geo.y(),
                          geo.width(), geo.height(),
                          screen.devicePixelRatio()))
    except Exception as exc:
        out.append("  (읽기 실패: %s)" % exc)
    try:
        for device, prect in winutil.physical_monitors():
            out.append("  Win32 %s  물리 %d,%d %dx%d"
                       % (device, prect[0], prect[1], prect[2], prect[3]))
    except Exception as exc:
        out.append("  (Win32 모니터 읽기 실패: %s)" % exc)
    out.append("  좌표 매핑표:")
    scales = []
    try:
        for prect, geo, dpr in capture.build_monitor_map():
            out.append("    물리 %s  ->  논리 %d,%d %dx%d  (배율 %.2f = %d%%)"
                       % (prect, geo.x(), geo.y(), geo.width(), geo.height(),
                          dpr, round(dpr * 100)))
            scales.append(round(dpr, 3))
    except Exception as exc:
        out.append("    (실패: %s)" % exc)
    if len(set(scales)) > 1:
        out.append("  ※ 모니터마다 배율이 다릅니다 (%s)."
                   % ", ".join("%d%%" % round(s * 100) for s in scales))
        out.append("    v1.9 부터는 캡처 화면을 모니터마다 따로 띄워서"
                   " 처리합니다.")
    elif scales:
        out.append("  ※ 모든 모니터가 같은 배율(%d%%)입니다."
                   % round(scales[0] * 100))

    out.append(_line("3. 감지된 창 (앞에 있는 것부터)"))
    out.append("  ※ '덮개'로 표시된 창이 화면 전체를 덮고 있으면"
               " 창 자동 감지가 방해받습니다.")
    try:
        windows = winutil.enumerate_windows()
        if not windows:
            out.append("  (감지된 창 없음)")
        for index, (hwnd, prect, title) in enumerate(windows[:60], 1):
            exstyle = winutil.window_exstyle(hwnd)
            marks = []
            if exstyle & winutil.WS_EX_TRANSPARENT:
                marks.append("덮개(클릭통과)")
            if exstyle & winutil.WS_EX_TOOLWINDOW:
                marks.append("도구창")
            if exstyle & winutil.WS_EX_NOREDIRECTIONBITMAP:
                marks.append("직접그리기")
            out.append("  %2d. %-22s %-28s %d,%d %dx%d %s"
                       % (index, (winutil.window_process(hwnd) or "?")[:22],
                          (title or "(제목 없음)")[:28],
                          prect[0], prect[1], prect[2], prect[3],
                          " ".join(marks)))
            out.append("      클래스: %s" % winutil.window_class(hwnd))
    except Exception as exc:
        out.append("  (창 목록 실패: %s)" % exc)

    out.append(_line("4. 설정"))
    try:
        for key in sorted(CONFIG.data):
            if key.startswith("_"):
                continue
            out.append("  %-22s %s" % (key, CONFIG.data[key]))
    except Exception as exc:
        out.append("  (설정 읽기 실패: %s)" % exc)

    out.append(_line("5. 최근 오류 기록"))
    try:
        from config import base_dir
        log = base_dir() / "오류기록.txt"
        if log.exists():
            text = log.read_text(encoding="utf-8", errors="replace")
            out.append(text[-4000:] if len(text) > 4000 else text)
        else:
            out.append("  (오류 기록 없음 — 정상)")
    except Exception as exc:
        out.append("  (읽기 실패: %s)" % exc)

    return "\n".join(out)


def save(app=None):
    """진단 정보를 저장 폴더에 파일로 남기고 경로를 돌려준다."""
    now = datetime.now()
    name = "캡처도구_진단_%04d%02d%02d_%02d%02d%02d.txt" % (
        now.year, now.month, now.day, now.hour, now.minute, now.second)
    folder = CONFIG.save_dir()
    path = Path(folder) / name
    try:
        path.write_text(collect(app), encoding="utf-8")
    except Exception:
        import tempfile
        path = Path(tempfile.gettempdir()) / name
        path.write_text(collect(app), encoding="utf-8")
    return str(path)
