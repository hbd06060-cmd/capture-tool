# -*- coding: utf-8 -*-
"""파일 저장 도우미."""
from datetime import datetime
from pathlib import Path

from PySide6.QtGui import QImage

from config import CONFIG

# Windows 의 time.strftime 은 한글이 섞인 형식 문자열에서 실패하므로
# 날짜 기호를 직접 바꿔 넣는다.
_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


WINDOW_TOKEN = "%W"
WINDOW_MAX = 40


def _window_part(window) -> str:
    """창 제목을 파일 이름에 쓸 수 있게 다듬는다. 모르면 빈 칸."""
    text = (window or "").strip()
    if not text:
        return ""
    cleaned = "".join(ch for ch in text
                      if ch not in '\\/:*?"<>|' and ord(ch) > 31)
    return cleaned.strip().strip(".")[:WINDOW_MAX].strip()


def _tidy(name: str) -> str:
    """창 제목이 비어서 생긴 '__' 나 앞뒤 구분기호를 정리한다."""
    for junk in ("___", "__", "--", "  "):
        while junk in name:
            name = name.replace(junk, junk[0])
    return name.strip(" _-")


def format_name(pattern: str, now=None, window="") -> str:
    """'캡처_%Y%m%d_%H%M%S' 같은 형식을 실제 날짜/시각 문자열로 바꾼다.

    %W 는 캡처한 창의 제목으로 바뀐다 (모를 때는 빈 칸).
    """
    now = now or datetime.now()
    hour12 = now.hour % 12 or 12
    table = {
        "%Y": "%04d" % now.year,
        "%y": "%02d" % (now.year % 100),
        "%m": "%02d" % now.month,
        "%d": "%02d" % now.day,
        "%H": "%02d" % now.hour,
        "%I": "%02d" % hour12,
        "%M": "%02d" % now.minute,
        "%S": "%02d" % now.second,
        "%p": "오전" if now.hour < 12 else "오후",
        "%A": _WEEKDAYS[now.weekday()] + "요일",
        "%a": _WEEKDAYS[now.weekday()],
        "%B": "%d월" % now.month,
        "%b": "%d월" % now.month,
        "%j": "%03d" % now.timetuple().tm_yday,
        "%f": "%03d" % (now.microsecond // 1000),
        "%W": _window_part(window),
    }
    had_window = WINDOW_TOKEN in pattern
    out = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "%" and index + 1 < length:
            token = pattern[index:index + 2]
            if token == "%%":
                out.append("%")
                index += 2
                continue
            if token in table:
                out.append(table[token])
                index += 2
                continue
        out.append(char)
        index += 1
    result = "".join(out)
    if had_window:
        result = _tidy(result)
    return result


def safe_name(name: str) -> str:
    cleaned = "".join(ch for ch in name if ch not in '\\/:*?"<>|' and ord(ch) > 31)
    cleaned = cleaned.strip().strip(".")
    return cleaned or "캡처"


def _extension():
    fmt = (CONFIG["image_format"] or "png").lower()
    return "jpg" if fmt in ("jpg", "jpeg") else "png"


def target_folder(now=None) -> Path:
    """설정에 따라 날짜별 하위 폴더까지 포함한 저장 폴더."""
    folder = CONFIG.save_dir()
    split = (CONFIG["folder_split"] or "").strip()
    if split:
        for part in format_name(split, now).replace("\\", "/").split("/"):
            part = safe_name(part)
            if part:
                folder = folder / part
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception:
            folder = CONFIG.save_dir()
    return folder


def suggest_path(window="") -> Path:
    from datetime import datetime
    now = datetime.now()
    folder = target_folder(now)
    pattern = CONFIG["file_pattern"] or "캡처_%Y%m%d_%H%M%S"
    # 이름 규칙 안에 폴더 구분이 들어 있어도 받아들인다
    raw = format_name(pattern, now, window).replace("\\", "/")
    parts = [safe_name(p) for p in raw.split("/") if p.strip()]
    if not parts:
        parts = ["캡처"]
    for part in parts[:-1]:
        folder = folder / part
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except Exception:
        folder = CONFIG.save_dir()
    name = parts[-1]
    ext = _extension()
    candidate = folder / ("%s.%s" % (name, ext))
    index = 2
    while candidate.exists():
        candidate = folder / ("%s (%d).%s" % (name, index, ext))
        index += 1
    return candidate


def write_image(image: QImage, path) -> bool:
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return False
    suffix = path.suffix.lower().lstrip(".")
    if suffix in ("jpg", "jpeg"):
        return bool(image.save(str(path), "JPG", int(CONFIG["jpeg_quality"])))
    if suffix == "bmp":
        return bool(image.save(str(path), "BMP"))
    return bool(image.save(str(path), "PNG"))


def save_image(image: QImage, window=""):
    """설정된 폴더에 자동으로 저장하고 경로를 돌려준다.

    window 는 캡처한 창의 제목 (이름 규칙에 %W 를 쓸 때만 의미가 있다).
    """
    path = suggest_path(window)
    if write_image(image, path):
        register(path, image)
        return str(path)
    return None


def register(path, image=None):
    """최근 캡처 목록에 남긴다 (실패해도 저장 자체에는 영향 없음)."""
    try:
        import recent
        recent.remember(path, image, temp=False)
    except Exception:
        pass
