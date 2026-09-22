# -*- coding: utf-8 -*-
"""오래된 캡처 파일 자동 정리.

안전을 위해 세 가지 원칙을 지킨다.
  1. 지우지 않고 휴지통으로 보낸다 (되살릴 수 있다)
  2. 캡처도구가 만든 날짜 폴더와 기록에 남은 파일만 건드린다
  3. 처음 한 번은 사용자에게 확인을 받는다
"""
import re
from datetime import date, datetime
from pathlib import Path

import recent
import winutil
from config import CONFIG

IMAGE_SUFFIX = {".png", ".jpg", ".jpeg", ".bmp"}
DATE_FOLDER = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


def _known_files():
    """정리 대상이 될 수 있는 파일 목록 (캡처도구가 만든 것만)."""
    found = {}
    root = CONFIG.save_dir()

    # (1) 날짜별 하위 폴더 안의 이미지
    try:
        for child in root.iterdir():
            if child.is_dir() and DATE_FOLDER.match(child.name):
                for path in child.rglob("*"):
                    if path.is_file() and path.suffix.lower() in IMAGE_SUFFIX:
                        found[str(path)] = path
    except Exception:
        pass

    # (2) 최근 기록에 남아 있는 저장본
    for item in recent.entries():
        if item.get("temp"):
            continue
        try:
            path = Path(item["path"])
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIX:
                found[str(path)] = path
        except Exception:
            pass
    return list(found.values())


def _age_days(path: Path):
    try:
        return (datetime.now().timestamp() - path.stat().st_mtime) / 86400.0
    except Exception:
        return 0.0


def find_targets():
    """정리 대상 (파일 목록, 전체 용량 바이트)."""
    files = _known_files()
    mode = CONFIG["cleanup_mode"]
    if mode == "size":
        limit = max(1, int(CONFIG["cleanup_size_gb"])) * 1024 ** 3
        sized = []
        total = 0
        for path in files:
            try:
                size = path.stat().st_size
            except Exception:
                continue
            sized.append((path.stat().st_mtime, size, path))
            total += size
        if total <= limit:
            return [], total
        sized.sort()                       # 오래된 것부터
        targets = []
        for _mtime, size, path in sized:
            if total <= limit:
                break
            targets.append(path)
            total -= size
        return targets, total
    days = max(1, int(CONFIG["cleanup_days"]))
    targets = [p for p in files if _age_days(p) > days]
    return targets, 0


def describe():
    """설정 화면에 보여줄 한 줄 설명."""
    if not CONFIG["cleanup_enabled"]:
        return "자동 정리를 사용하지 않습니다."
    if CONFIG["cleanup_mode"] == "size":
        return ("캡처 폴더가 %dGB를 넘으면 오래된 것부터 휴지통으로 보냅니다."
                % int(CONFIG["cleanup_size_gb"]))
    return ("%d일이 지난 캡처를 휴지통으로 보냅니다."
            % int(CONFIG["cleanup_days"]))


def run(force=False):
    """정리를 실행하고 (보낸 개수, 대상 목록)을 돌려준다.

    force=False 이면 하루에 한 번만 실제로 동작한다.
    """
    if not CONFIG["cleanup_enabled"]:
        return 0, []
    today = date.today().isoformat()
    if not force and CONFIG["cleanup_last_run"] == today:
        return 0, []
    targets, _total = find_targets()
    if not targets:
        CONFIG["cleanup_last_run"] = today
        CONFIG.save()
        return 0, []
    moved = winutil.move_to_recycle_bin(targets)
    if moved:
        for path in targets:
            recent.forget(path)
        CONFIG["cleanup_last_run"] = today
        CONFIG.save()
    # 휴지통으로 보내지 못하면 파일을 그대로 두고 알린다 (임의로 지우지 않는다)
    return moved, targets
