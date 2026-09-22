# -*- coding: utf-8 -*-
"""Windows 전용 유틸리티 (DPI, 모니터/창 정보, 전역 단축키, 시작프로그램 등록).

Linux 등 다른 OS에서는 안전하게 비활성화되어 개발/검증용으로 import 가능하다.
"""
import sys
import ctypes

IS_WIN = sys.platform == "win32"

if IS_WIN:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shcore = None
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
    except OSError:
        pass
    dwmapi = None
    try:
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    except OSError:
        pass
else:  # pragma: no cover - 개발 환경용 더미
    user32 = shcore = dwmapi = None

# ---------------------------------------------------------------- DPI

def set_dpi_aware():
    """프로세스를 Per-Monitor V2 DPI 인식으로 설정한다. Qt 초기화 전에 호출."""
    if not IS_WIN:
        return
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        if shcore:
            shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
            return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


# ---------------------------------------------------------- 모니터 정보

if IS_WIN:

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                    ("rcWork", RECT), ("dwFlags", ctypes.c_ulong),
                    ("szDevice", ctypes.c_wchar * 32)]

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(RECT), ctypes.c_double)


def physical_monitors():
    """[(device_name, (x, y, w, h))] 물리 픽셀 기준 모니터 목록."""
    if not IS_WIN:
        return []
    out = []

    def cb(hmon, hdc, lprc, data):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            out.append((mi.szDevice, (r.left, r.top,
                                      r.right - r.left, r.bottom - r.top)))
        return 1

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    return out


# ------------------------------------------------------------- 창 정보

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14


def _window_rect(hwnd):
    """물리 픽셀 기준 창 사각형. DWM 확장 프레임 우선(그림자 제외)."""
    if dwmapi is not None:
        r = RECT()
        res = dwmapi.DwmGetWindowAttribute(
            ctypes.c_void_p(hwnd), ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(r), ctypes.sizeof(r))
        if res == 0 and r.right > r.left and r.bottom > r.top:
            return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    r = RECT()
    if user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r)):
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    return None


def _is_cloaked(hwnd):
    if dwmapi is None:
        return False
    val = ctypes.c_int(0)
    res = dwmapi.DwmGetWindowAttribute(
        ctypes.c_void_p(hwnd), ctypes.c_uint(DWMWA_CLOAKED),
        ctypes.byref(val), ctypes.sizeof(val))
    return res == 0 and val.value != 0


def enumerate_windows(exclude_hwnds=()):
    """화면에 보이는 최상위 창 목록을 Z-순서(앞→뒤)로 반환.

    반환: [(hwnd, (x, y, w, h), title)]  — 물리 픽셀 좌표
    """
    if not IS_WIN:
        return []
    results = []
    exclude = set(int(h) for h in exclude_hwnds if h)

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p,
                                     ctypes.c_void_p)

    def cb(hwnd, lparam):
        h = int(hwnd)
        if h in exclude:
            return 1
        if not user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
            return 1
        if user32.IsIconic(ctypes.c_void_p(hwnd)):
            return 1
        if _is_cloaked(hwnd):
            return 1
        rect = _window_rect(h)
        if not rect or rect[2] < 8 or rect[3] < 8:
            return 1
        n = user32.GetWindowTextLengthW(ctypes.c_void_p(hwnd))
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(ctypes.c_void_p(hwnd), buf, n + 1)
        results.append((h, rect, buf.value))
        return 1

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return results


def client_rect(hwnd):
    """창의 '내용이 그려지는 영역' — 제목표시줄과 테두리를 뺀 부분.

    물리 픽셀 화면 좌표 (x, y, w, h). 실패하면 None.
    """
    if not IS_WIN or not hwnd:
        return None
    try:
        class _RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class _POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        rect = _RECT()
        if not user32.GetClientRect(ctypes.c_void_p(int(hwnd)),
                                    ctypes.byref(rect)):
            return None
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return None
        origin = _POINT(rect.left, rect.top)
        if not user32.ClientToScreen(ctypes.c_void_p(int(hwnd)),
                                     ctypes.byref(origin)):
            return None
        return (origin.x, origin.y, width, height)
    except Exception:
        return None


# 내용 영역 후보에서 걸러낼 자식 창들 (스크롤바·그림자 따위)
_SKIP_CHILD_CLASSES = {"ScrollBar", "msctls_progress32", "tooltips_class32",
                       "SysHeader32", "Shell_TrayWnd"}
CHILD_MIN_SIZE = 60          # 이보다 작은 자식 창은 내용 영역으로 치지 않는다
CHILD_MAX = 400              # 너무 많이 뒤지지 않게


def child_rects(hwnd, min_size=CHILD_MIN_SIZE):
    """창 안쪽에서 '내용 영역' 후보가 될 만한 자식 창들.

    크롬의 웹 페이지 영역처럼, 창 안에서 실제로 내용이 그려지는 부분을
    집어내기 위한 것이다. 물리 픽셀 좌표 목록으로 돌려준다.
    """
    if not IS_WIN or not hwnd:
        return []
    outer = _window_rect(int(hwnd))
    if not outer:
        return []
    found = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p,
                                     ctypes.c_void_p)

    def cb(child, _lparam):
        if len(found) >= CHILD_MAX:
            return 0
        handle = int(child)
        try:
            if not user32.IsWindowVisible(ctypes.c_void_p(handle)):
                return 1
            if window_class(handle) in _SKIP_CHILD_CLASSES:
                return 1
            rect = _window_rect(handle)
            if not rect or rect[2] < min_size or rect[3] < min_size:
                return 1
            found.append(rect)
        except Exception:
            pass
        return 1

    try:
        user32.EnumChildWindows(ctypes.c_void_p(int(hwnd)), WNDENUMPROC(cb), 0)
    except Exception:
        return []
    return found


def foreground_window():
    """지금 맨 앞에 있는 창의 손잡이(hwnd). 없으면 0."""
    if not IS_WIN:
        return 0
    try:
        return int(user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def foreground_window_rect():
    """활성 창의 물리 픽셀 사각형."""
    if not IS_WIN:
        return None
    hwnd = foreground_window()
    if not hwnd:
        return None
    return _window_rect(hwnd)


def window_class(hwnd):
    if not IS_WIN or not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(ctypes.c_void_p(int(hwnd)), buf, 256)
    return buf.value


def window_title(hwnd):
    if not IS_WIN or not hwnd:
        return ""
    n = user32.GetWindowTextLengthW(ctypes.c_void_p(int(hwnd)))
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(ctypes.c_void_p(int(hwnd)), buf, n + 1)
    return buf.value


GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOREDIRECTIONBITMAP = 0x00200000


def window_exstyle(hwnd):
    if not IS_WIN or not hwnd:
        return 0
    try:
        getter = getattr(user32, "GetWindowLongPtrW", None) or \
            user32.GetWindowLongW
        getter.restype = ctypes.c_ssize_t
        return int(getter(ctypes.c_void_p(int(hwnd)), GWL_EXSTYLE)) & 0xFFFFFFFF
    except Exception:
        return 0


WS_EX_LAYERED = 0x00080000


def set_click_through(hwnd, enabled):
    """창이 마우스를 받지 않고 아래 창으로 흘려보내게 한다 (핀 클릭 통과).

    Qt 의 창 속성을 바꾸면 창이 새로 만들어지며 깜빡이므로
    Windows 확장 스타일을 직접 고친다. 성공하면 True.
    """
    if not IS_WIN or not hwnd:
        return False
    try:
        long_ptr = ctypes.c_ssize_t
        getter = getattr(user32, "GetWindowLongPtrW", None) or \
            user32.GetWindowLongW
        setter = getattr(user32, "SetWindowLongPtrW", None) or \
            user32.SetWindowLongW
        getter.restype = long_ptr
        getter.argtypes = [ctypes.c_void_p, ctypes.c_int]
        setter.restype = long_ptr
        setter.argtypes = [ctypes.c_void_p, ctypes.c_int, long_ptr]
        style = int(getter(ctypes.c_void_p(int(hwnd)), GWL_EXSTYLE))
        if enabled:
            style |= WS_EX_TRANSPARENT | WS_EX_LAYERED
        else:
            style &= ~WS_EX_TRANSPARENT
        ctypes.set_last_error(0)
        setter(ctypes.c_void_p(int(hwnd)), GWL_EXSTYLE, long_ptr(style))
        return ctypes.get_last_error() == 0
    except Exception:
        return False


def window_pid(hwnd):
    """창을 띄운 프로그램의 번호(PID). 모르면 0."""
    if not IS_WIN or not hwnd:
        return 0
    try:
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(ctypes.c_void_p(int(hwnd)),
                                        ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def is_own_window(hwnd):
    """우리 프로그램이 띄운 창인지 (캡처 대상에서 빼기 위해)."""
    import os
    return bool(hwnd) and window_pid(hwnd) == os.getpid()


def window_process(hwnd):
    """창을 띄운 프로그램의 실행 파일 이름."""
    if not IS_WIN or not hwnd:
        return ""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(ctypes.c_void_p(int(hwnd)),
                                        ctypes.byref(pid))
        if not pid.value:
            return ""
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        try:
            size = ctypes.c_ulong(512)
            buf = ctypes.create_unicode_buffer(512)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf,
                                                   ctypes.byref(size)):
                import os
                return os.path.basename(buf.value)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        pass
    return ""


def is_minimized(hwnd):
    if not IS_WIN or not hwnd:
        return False
    return bool(user32.IsIconic(ctypes.c_void_p(int(hwnd))))


def windows_build():
    """Windows 빌드 번호 (기능 지원 여부 판단용)."""
    if not IS_WIN:
        return 0
    try:
        import sys as _sys
        return int(_sys.getwindowsversion().build)
    except Exception:
        return 0


# ---------------------------------------------------------- 전역 단축키

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312

_VK_NAMES = {
    "PRINT": 0x2C, "PRINTSCREEN": 0x2C, "PRTSC": 0x2C, "SNAPSHOT": 0x2C,
    "INSERT": 0x2D, "DELETE": 0x2E, "HOME": 0x24, "END": 0x23,
    "PAGEUP": 0x21, "PAGEDOWN": 0x22, "SPACE": 0x20, "ESC": 0x1B,
    "TAB": 0x09, "ENTER": 0x0D, "RETURN": 0x0D, "BACKSPACE": 0x08,
    "PAUSE": 0x13, "SCROLLLOCK": 0x91, "NUMLOCK": 0x90,
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD,
    "\\": 0xDC, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}
for _i in range(1, 25):
    _VK_NAMES["F%d" % _i] = 0x6F + _i


def parse_hotkey(text):
    """'Ctrl+Shift+A' -> (modifiers, vk). 실패 시 None."""
    if not text:
        return None
    mods = 0
    vk = None
    parts = [p.strip() for p in text.replace(" ", "").split("+") if p.strip()]
    if not parts:
        return None
    for p in parts:
        u = p.upper()
        if u in ("CTRL", "CONTROL"):
            mods |= MOD_CONTROL
        elif u == "ALT":
            mods |= MOD_ALT
        elif u == "SHIFT":
            mods |= MOD_SHIFT
        elif u in ("WIN", "META", "SUPER"):
            mods |= MOD_WIN
        elif u in _VK_NAMES:
            vk = _VK_NAMES[u]
        elif len(u) == 1 and (u.isalpha() or u.isdigit()):
            vk = ord(u)
        else:
            return None
    if vk is None:
        return None
    return (mods | MOD_NOREPEAT, vk)


# RegisterHotKey 가 실패했을 때 Windows 가 알려 주는 이유
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
ERROR_INVALID_PARAMETER = 87

HOTKEY_REASONS = {
    ERROR_HOTKEY_ALREADY_REGISTERED: "다른 프로그램이 이미 쓰고 있습니다",
    ERROR_INVALID_PARAMETER: "Windows 가 받아들이지 않는 조합입니다",
    0: "등록됨",
}


def hotkey_reason(code):
    """오류 번호를 사람이 읽을 말로."""
    if code in HOTKEY_REASONS:
        return HOTKEY_REASONS[code]
    return "알 수 없는 이유 (오류 %d)" % code


def register_hotkey_ex(hwnd, hotkey_id, text):
    """단축키를 등록하고 (성공여부, 오류번호) 를 돌려준다.

    오류번호 1409 는 '다른 프로그램이 이미 쓰는 중' 이라는 뜻이다.
    이걸 알아야 사용자에게 정확한 이유를 알려 줄 수 있다.
    """
    combo = parse_hotkey(text)
    if not IS_WIN:
        return False, -1
    if combo is None:
        return False, ERROR_INVALID_PARAMETER
    mods, vk = combo
    try:
        ctypes.set_last_error(0)
        result = user32.RegisterHotKey(ctypes.c_void_p(int(hwnd)),
                                       int(hotkey_id), mods, vk)
        if result:
            return True, 0
        return False, ctypes.get_last_error()
    except Exception:
        return False, -1


def register_hotkey(hwnd, hotkey_id, text):
    ok, _code = register_hotkey_ex(hwnd, hotkey_id, text)
    return ok


def unregister_hotkey(hwnd, hotkey_id):
    if not IS_WIN:
        return
    try:
        user32.UnregisterHotKey(ctypes.c_void_p(int(hwnd)), int(hotkey_id))
    except Exception:
        pass


PROBE_ID = 0x4321          # 시험용으로만 쓰는 번호 (실제 기능과 겹치지 않게)

# 막힌 단축키를 대신할 후보. 앞에서부터 비어 있는 것을 고른다.
_MODIFIER_SWAPS = ["Ctrl+Shift+%s", "Ctrl+Alt+Shift+%s", "Alt+Shift+%s",
                   "Ctrl+Alt+%s"]


def _last_key(text):
    """'Ctrl+Alt+O' 에서 'O' 만 떼어낸다."""
    parts = [p.strip() for p in (text or "").split("+") if p.strip()]
    return parts[-1] if parts else ""


def alternatives(text, extra_keys=()):
    """이 단축키가 막혔을 때 대신 써 볼 조합들."""
    key = _last_key(text)
    out = []
    if key:
        for shape in _MODIFIER_SWAPS:
            candidate = shape % key
            if candidate.lower() != (text or "").lower():
                out.append(candidate)
    for other in extra_keys:
        if other and other.lower() != (text or "").lower() and other not in out:
            out.append(other)
    return out


def find_free_hotkey(hwnd, text, extra_keys=()):
    """막힌 단축키를 대신할 빈 조합을 찾는다. 없으면 빈 칸."""
    for candidate in alternatives(text, extra_keys):
        ok, _code = hotkey_available(hwnd, candidate)
        if ok:
            return candidate
    return ""


def hotkey_available(hwnd, text):
    """이 단축키를 지금 쓸 수 있는지 살짝 등록해 보고 바로 되돌린다.

    돌려주는 값: (쓸 수 있으면 True, 오류번호)
    """
    if not IS_WIN:
        return False, -1
    unregister_hotkey(hwnd, PROBE_ID)
    ok, code = register_hotkey_ex(hwnd, PROBE_ID, text)
    if ok:
        unregister_hotkey(hwnd, PROBE_ID)
    return ok, code


# --------------------------------------------- 화면 캡처에서 창 제외하기

WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11


def exclude_from_capture(hwnd, enabled=True):
    """이 창을 화면 캡처에 찍히지 않게 한다 (사람 눈에는 그대로 보인다).

    Windows 10 2004 이상에서만 동작한다. 성공하면 True.
    """
    if not IS_WIN or not hwnd:
        return False
    try:
        affinity = WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE
        return bool(user32.SetWindowDisplayAffinity(
            ctypes.c_void_p(int(hwnd)), ctypes.c_uint(affinity)))
    except Exception:
        return False


# ------------------------------------------------------- 마우스 휠 보내기

INPUT_MOUSE = 0
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120

if IS_WIN:

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                    ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                    ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


def get_cursor_pos():
    if not IS_WIN:
        return None
    point = POINT()
    if user32.GetCursorPos(ctypes.byref(point)):
        return (point.x, point.y)
    return None


def set_cursor_pos(x, y):
    if not IS_WIN:
        return False
    return bool(user32.SetCursorPos(int(x), int(y)))


def window_from_point(x, y):
    if not IS_WIN:
        return 0
    point = POINT(int(x), int(y))
    try:
        return int(user32.WindowFromPoint(point) or 0)
    except Exception:
        return 0


def activate_window(hwnd):
    if not IS_WIN or not hwnd:
        return False
    try:
        root = user32.GetAncestor(ctypes.c_void_p(int(hwnd)), 2)  # GA_ROOT
        target = root or hwnd
        return bool(user32.SetForegroundWindow(ctypes.c_void_p(int(target))))
    except Exception:
        return False


def send_wheel(x, y, clicks):
    """(x, y) 지점에 마우스 휠 신호를 보낸다. clicks>0 이면 아래로."""
    if not IS_WIN:
        return False
    set_cursor_pos(x, y)
    data = ctypes.c_ulong(int(-WHEEL_DELTA * clicks) & 0xFFFFFFFF)
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = MOUSEINPUT(0, 0, data.value, MOUSEEVENTF_WHEEL, 0, None)
    sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    return sent == 1


KEYEVENTF_KEYUP = 0x0002
VK_DOWN = 0x28
VK_UP = 0x26
VK_ESCAPE = 0x1B


def key_down(vk):
    """지금 그 키가 눌려 있는지 (어느 창에 입력이 가 있든 상관없이)."""
    if not IS_WIN:
        return False
    try:
        return bool(user32.GetAsyncKeyState(int(vk)) & 0x8000)
    except Exception:
        return False


def send_key(vk, count=1):
    """키를 count 번 눌렀다 뗀다 (휠이 안 먹는 창의 스크롤 대체 수단)."""
    if not IS_WIN or count <= 0:
        return False
    sent = 0
    for _ in range(int(count)):
        for flags in (0, KEYEVENTF_KEYUP):
            inp = INPUT()
            inp.type = 1                 # INPUT_KEYBOARD
            inp.ki = KEYBDINPUT(int(vk), 0, flags, 0, None)
            sent += user32.SendInput(1, ctypes.byref(inp),
                                     ctypes.sizeof(INPUT))
    return sent == 2 * int(count)


# ---------------------------------------------------------- 휴지통 보내기

FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400

if IS_WIN:

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("hwnd", ctypes.c_void_p),
                    ("wFunc", ctypes.c_uint),
                    ("pFrom", ctypes.c_wchar_p),
                    ("pTo", ctypes.c_wchar_p),
                    ("fFlags", ctypes.c_ushort),
                    ("fAnyOperationsAborted", ctypes.c_int),
                    ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", ctypes.c_wchar_p)]


def move_to_recycle_bin(paths):
    """파일들을 휴지통으로 보낸다. 성공한 개수를 돌려준다."""
    paths = [str(p) for p in paths if p]
    if not paths:
        return 0
    if not IS_WIN:
        return 0
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        buffer = "\0".join(paths) + "\0\0"
        op = SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = FO_DELETE
        op.pFrom = buffer
        op.pTo = None
        op.fFlags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT |
                     FOF_NOERRORUI)
        op.fAnyOperationsAborted = 0
        op.hNameMappings = None
        op.lpszProgressTitle = None
        result = shell32.SHFileOperationW(ctypes.byref(op))
        if result != 0 or op.fAnyOperationsAborted:
            return 0
        return len(paths)
    except Exception:
        return 0


# ------------------------------------------------------- 시작프로그램 등록

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


LAUNCHER_NAME = "캡처도구.exe"


def startup_command():
    """Windows 시작할 때 실행할 명령.

    반드시 실행기(캡처도구.exe)를 쓴다. python.exe 를 바로 등록하면
    로그인할 때마다 검은 명령 프롬프트 창이 하나 뜨고, 그 창을 닫으면
    프로그램이 같이 꺼져 버린다. 실행기는 창 없이 띄운다.
    """
    import os
    from pathlib import Path

    base = Path(__file__).resolve().parent.parent      # app 의 상위 폴더
    launcher = base / LAUNCHER_NAME
    if launcher.exists():
        return '"%s"' % launcher

    # 실행기를 못 찾으면 콘솔 없는 pythonw.exe 로 대신한다
    script = Path(__file__).resolve().parent / "main.pyw"
    exe = Path(sys.executable)
    quiet = exe.with_name("pythonw.exe")
    if quiet.exists():
        return '"%s" "%s"' % (quiet, script)
    return '"%s" "%s"' % (exe, os.path.abspath(sys.argv[0]))


def _console_command(command):
    """검은 창이 뜨는 옛 등록 방식인지 (python.exe 를 바로 부르는 경우)."""
    lowered = (command or "").lower()
    return "python.exe" in lowered and "pythonw.exe" not in lowered


def get_autostart_command(name):
    """지금 등록돼 있는 시작 명령. 없으면 빈 칸."""
    if not IS_WIN:
        return ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, name)
        return value or ""
    except Exception:
        return ""


def _first_path(command):
    """'"C:\\...\\x.exe" "y"' 에서 맨 앞 실행 파일 경로만 꺼낸다."""
    text = (command or "").strip()
    if not text:
        return ""
    if text.startswith('"'):
        end = text.find('"', 1)
        return text[1:end] if end > 0 else text[1:]
    return text.split(" ")[0]


def _missing_target(command):
    """등록된 실행 파일이 이제 그 자리에 없는지 (폴더를 옮긴 경우)."""
    import os
    target = _first_path(command)
    return bool(target) and not os.path.exists(target)


def autostart_problem(name="캡처도구"):
    """자동 실행 등록에 문제가 있으면 그 종류를 알려 준다.

    "console" — python.exe 를 바로 등록해 검은 창이 뜨는 옛 방식
    "moved"   — 등록된 자리에 프로그램이 더 이상 없음 (폴더를 옮김)
    ""        — 문제 없음 (등록 안 된 경우 포함)
    """
    if not IS_WIN:
        return ""
    current = get_autostart_command(name)
    if not current:
        return ""                       # 자동 실행을 안 쓰는 경우 — 건드리지 않음
    if _console_command(current):
        return "console"
    if _missing_target(current):
        return "moved"
    return ""


def repair_autostart(name="캡처도구"):
    """등록이 잘못돼 있으면 조용히 고친다.

    v1.5 까지는 python.exe 를 그대로 등록해서, 컴퓨터를 켤 때마다
    검은 창이 하나 떴고 그 창을 닫으면 프로그램도 같이 꺼졌다.
    이미 그렇게 등록된 컴퓨터를 자동으로 되돌린다. 폴더를 옮겨서
    등록이 헛돌고 있는 경우도 지금 위치로 다시 맞춘다.

    돌려주는 값: 고쳤으면 문제의 종류("console"/"moved"), 아니면 "".
    """
    problem = autostart_problem(name)
    if not problem:
        return ""
    good = startup_command()
    if not good or good == get_autostart_command(name):
        return ""
    return problem if set_autostart(True, name, good) else ""


def set_autostart(enabled, name, command):
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_ALL_ACCESS) as key:
            if enabled:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False


def get_autostart(name):
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, name)
        return True
    except Exception:
        return False
