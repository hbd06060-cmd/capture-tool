# -*- coding: utf-8 -*-
"""창 하나를 통째로 캡처한다 — 다른 창에 가려져 있어도.

두 가지 방법을 순서대로 시도한다.
  1. 창에게 직접 그려달라고 요청 (PrintWindow) — 깜빡임 없음
  2. 잠깐 앞으로 꺼내 찍고 원래 자리로 돌려놓기 — 어떤 프로그램이든 됨
"""
import ctypes
import time

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

import capture
import winutil

PW_RENDERFULLCONTENT = 0x00000002
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0

HWND_TOP = 0
HWND_BOTTOM = 1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
GW_HWNDPREV = 3
SW_RESTORE = 9
SW_MINIMIZE = 6

# 목록에서 빼야 하는 창 (바탕화면·작업표시줄 등)
_SKIP_CLASSES = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow", "ApplicationFrameWindow_Hidden",
    "TaskListThumbnailWnd", "Xaml_WindowedPopupClass", "tooltips_class32",
}
_SKIP_PROCESSES = {"textinputhost.exe", "applicationframehost.exe"}

if winutil.IS_WIN:
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32 = winutil.user32

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16),
                    ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32),
                    ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32),
                    ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                    ("bmiColors", ctypes.c_uint32 * 3)]

    gdi32.CreateDIBSection.restype = ctypes.c_void_p
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.SelectObject.restype = ctypes.c_void_p
    user32.GetDC.restype = ctypes.c_void_p


# ------------------------------------------------------------- 창 목록

def list_windows(exclude_hwnds=()):
    """고를 수 있는 창 목록.

    반환: [{hwnd, title, process, rect(물리), logical(QRect), minimized}]
    """
    if not winutil.IS_WIN:
        return []
    mapping = capture.build_monitor_map()
    out = []
    for hwnd, prect, title in winutil.enumerate_windows(exclude_hwnds):
        cls = winutil.window_class(hwnd)
        if cls in _SKIP_CLASSES:
            continue
        exstyle = winutil.window_exstyle(hwnd)
        if exstyle & winutil.WS_EX_TOOLWINDOW:
            continue
        if exstyle & winutil.WS_EX_TRANSPARENT:
            continue                      # 클릭이 통과하는 덮개 창
        process = winutil.window_process(hwnd)
        if process.lower() in _SKIP_PROCESSES:
            continue
        if not title.strip():
            continue
        out.append({
            "hwnd": hwnd,
            "title": title,
            "process": process,
            "rect": prect,
            "logical": capture.phys_to_logical_rect(prect, mapping),
            "minimized": winutil.is_minimized(hwnd),
            "class": cls,
        })
    return out


# ------------------------------------------- 1. 창에게 직접 그려달라 하기

def _blank(image: QImage) -> bool:
    """그려달라고 했는데 빈 화면이 온 경우인지 판정."""
    if image is None or image.isNull():
        return True
    small = image.scaled(48, 32, Qt.IgnoreAspectRatio, Qt.FastTransformation)
    colors = {}
    total = 0
    for y in range(small.height()):
        for x in range(small.width()):
            value = small.pixel(x, y) & 0xFFFFFF
            colors[value] = colors.get(value, 0) + 1
            total += 1
    if not total:
        return True
    top = max(colors.values())
    return top / float(total) > 0.985      # 거의 한 가지 색 = 빈 화면


def print_window(hwnd) -> QImage:
    """PrintWindow 로 창 내용을 받아온다. 실패하면 빈 QImage."""
    if not winutil.IS_WIN or not hwnd:
        return QImage()
    rect = winutil.RECT()
    if not user32.GetWindowRect(ctypes.c_void_p(int(hwnd)),
                                ctypes.byref(rect)):
        return QImage()
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width < 4 or height < 4 or width > 20000 or height > 20000:
        return QImage()

    screen_dc = user32.GetDC(None)
    mem_dc = gdi32.CreateCompatibleDC(ctypes.c_void_p(screen_dc))
    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height          # 위에서 아래로
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0
    bits = ctypes.c_void_p()
    bitmap = gdi32.CreateDIBSection(ctypes.c_void_p(screen_dc),
                                    ctypes.byref(info), DIB_RGB_COLORS,
                                    ctypes.byref(bits), None, 0)
    image = QImage()
    try:
        if not bitmap or not bits:
            return QImage()
        old = gdi32.SelectObject(ctypes.c_void_p(mem_dc),
                                 ctypes.c_void_p(bitmap))
        ok = user32.PrintWindow(ctypes.c_void_p(int(hwnd)),
                                ctypes.c_void_p(mem_dc),
                                PW_RENDERFULLCONTENT)
        if ok:
            raw = ctypes.string_at(bits, width * height * 4)
            image = QImage(raw, width, height, width * 4,
                           QImage.Format_RGB32).copy()
        gdi32.SelectObject(ctypes.c_void_p(mem_dc), ctypes.c_void_p(old))
    finally:
        if bitmap:
            gdi32.DeleteObject(ctypes.c_void_p(bitmap))
        if mem_dc:
            gdi32.DeleteDC(ctypes.c_void_p(mem_dc))
        if screen_dc:
            user32.ReleaseDC(None, ctypes.c_void_p(screen_dc))
    if image.isNull():
        return image
    return _trim_invisible_border(hwnd, image, rect)


def _trim_invisible_border(hwnd, image, window_rect):
    """Windows 가 창 주변에 두는 보이지 않는 여백을 잘라낸다."""
    bounds = winutil._window_rect(int(hwnd))     # DWM 확장 프레임
    if not bounds:
        return image
    left = bounds[0] - window_rect.left
    top = bounds[1] - window_rect.top
    if left <= 0 and top <= 0:
        return image
    crop = QRect(max(0, left), max(0, top),
                 min(bounds[2], image.width() - max(0, left)),
                 min(bounds[3], image.height() - max(0, top)))
    if crop.width() < 4 or crop.height() < 4:
        return image
    return image.copy(crop)


# --------------------------------- 2. 잠깐 앞으로 꺼냈다 원래대로 돌리기

def _wait(ms):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.01)


def capture_by_raising(hwnd) -> QImage:
    """창을 잠깐 맨 앞으로 올려 찍고 원래 순서로 되돌린다."""
    if not winutil.IS_WIN or not hwnd:
        return QImage()
    handle = ctypes.c_void_p(int(hwnd))
    previous_top = user32.GetWindow(handle, GW_HWNDPREV)
    previous_focus = user32.GetForegroundWindow()
    minimized = winutil.is_minimized(hwnd)
    try:
        if minimized:
            user32.ShowWindow(handle, SW_RESTORE)
            _wait(260)
        user32.SetWindowPos(handle, ctypes.c_void_p(HWND_TOP), 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        user32.SetForegroundWindow(handle)
        _wait(220)
        prect = winutil._window_rect(int(hwnd))
        if not prect:
            return QImage()
        logical = capture.phys_to_logical_rect(prect)
        image = capture.grab_region(logical)
    finally:
        try:
            if previous_top:
                user32.SetWindowPos(handle, ctypes.c_void_p(previous_top),
                                    0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            if minimized:
                user32.ShowWindow(handle, SW_MINIMIZE)
            if previous_focus and int(previous_focus) != int(hwnd):
                user32.SetForegroundWindow(ctypes.c_void_p(previous_focus))
        except Exception:
            pass
    return image


# ---------------------------------------------------------------- 대표

def capture_window(hwnd, allow_raise=True):
    """창 하나를 캡처한다. (이미지, 사용한 방법) 을 돌려준다."""
    image = QImage()
    try:
        if not winutil.is_minimized(hwnd):
            image = print_window(hwnd)
    except Exception:
        image = QImage()
    if not image.isNull() and not _blank(image):
        return image, "direct"
    if not allow_raise:
        return image, "direct-blank"
    try:
        raised = capture_by_raising(hwnd)
    except Exception:
        raised = QImage()
    if not raised.isNull():
        return raised, "raise"
    return image, "direct-blank"


def thumbnail(entry, size=200):
    """목록에 보여줄 작은 그림.

    (이미지, 정확한지 여부) 를 돌려준다. 창이 직접 그려주지 못해
    화면에서 오려온 경우에는 다른 창에 가려진 모습일 수 있으므로
    False 를 함께 돌려준다. (실제 캡처는 온전하게 나온다)
    """
    image = QImage()
    exact = True
    try:
        if not entry.get("minimized"):
            image = print_window(entry["hwnd"])
    except Exception:
        image = QImage()
    if image.isNull() or _blank(image):
        exact = False
        try:
            logical = entry.get("logical")
            if logical is not None and not logical.isEmpty():
                image = capture.grab_region(logical)
        except Exception:
            image = QImage()
    if image.isNull():
        return QImage(), False
    return image.scaled(size, size, Qt.KeepAspectRatio,
                        Qt.SmoothTransformation), exact
