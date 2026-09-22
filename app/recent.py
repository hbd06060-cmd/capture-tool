# -*- coding: utf-8 -*-
"""최근 캡처 기록 — 저장한 파일과 '복사만 한' 임시 보관본을 함께 관리한다."""
import json
from datetime import datetime
from pathlib import Path

from PySide6.QtGui import QImage

from config import CONFIG

_MAX_TEMP_AGE_DAYS = 14


def _index_path() -> Path:
    return CONFIG.data_dir() / "최근기록.json"


def _temp_dir() -> Path:
    folder = CONFIG.data_dir() / "임시보관"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _load():
    try:
        raw = _index_path().read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict) and d.get("path")]
    except Exception:
        pass
    return []


def _write(items):
    try:
        _index_path().write_text(
            json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def remember(path, image=None, temp=False):
    """캡처 하나를 기록에 추가한다."""
    path = str(path)
    items = [d for d in _load() if d.get("path") != path]
    entry = {
        "path": path,
        "time": datetime.now().isoformat(timespec="seconds"),
        "temp": bool(temp),
    }
    if image is not None and not image.isNull():
        entry["w"] = image.width()
        entry["h"] = image.height()
    items.insert(0, entry)
    limit = max(20, int(CONFIG["recent_limit"]))
    _write(items[:limit])
    return entry


def store_unsaved(image: QImage):
    """저장하지 않은 캡처를 임시 보관하고 기록에 남긴다."""
    if not CONFIG["keep_unsaved"] or image is None or image.isNull():
        return None
    folder = _temp_dir()
    # Windows 에서는 strftime 서식 문자열에 한글이 들어가면 실패하므로 직접 만든다
    now = datetime.now()
    name = "임시_%04d%02d%02d_%02d%02d%02d_%03d.png" % (
        now.year, now.month, now.day, now.hour, now.minute, now.second,
        now.microsecond // 1000)
    path = folder / name
    if not image.save(str(path), "PNG"):
        return None
    remember(path, image, temp=True)
    _prune_temp()
    return str(path)


def _prune_temp():
    """임시 보관본은 개수와 보관 기간을 넘기면 지운다."""
    folder = _temp_dir()
    try:
        files = sorted((p for p in folder.glob("*.png") if p.is_file()),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        return
    limit = max(5, int(CONFIG["unsaved_limit"]))
    now = datetime.now().timestamp()
    for index, path in enumerate(files):
        too_many = index >= limit
        try:
            too_old = (now - path.stat().st_mtime) > _MAX_TEMP_AGE_DAYS * 86400
        except Exception:
            too_old = False
        if too_many or too_old:
            try:
                path.unlink()
            except Exception:
                pass


def entries(limit=None):
    """실제로 남아 있는 파일만 최신순으로 돌려준다."""
    items = _load()
    alive = []
    changed = False
    for item in items:
        try:
            if Path(item["path"]).is_file():
                alive.append(item)
            else:
                changed = True
        except Exception:
            changed = True
    if changed:
        _write(alive)
    return alive[:limit] if limit else alive


def forget(path):
    path = str(path)
    items = [d for d in _load() if d.get("path") != path]
    _write(items)


def label_for(item):
    """'14:30 · 672×340' 형태의 짧은 설명."""
    try:
        stamp = datetime.fromisoformat(item.get("time", ""))
        when = stamp.strftime("%H:%M")
    except Exception:
        when = "??:??"
    size = ""
    if item.get("w") and item.get("h"):
        size = "  ·  %d×%d" % (item["w"], item["h"])
    mark = "  (임시)" if item.get("temp") else ""
    return "%s%s%s" % (when, size, mark)


def day_label(item):
    try:
        stamp = datetime.fromisoformat(item.get("time", ""))
    except Exception:
        return "날짜 모름"
    today = datetime.now().date()
    delta = (today - stamp.date()).days
    if delta == 0:
        return "오늘"
    if delta == 1:
        return "어제"
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    return "%d월 %d일 (%s)" % (stamp.month, stamp.day,
                              weekdays[stamp.weekday()])
