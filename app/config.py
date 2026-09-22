# -*- coding: utf-8 -*-
"""설정 저장/불러오기 (포터블 우선, 실패 시 사용자 폴더)."""
import copy
import json
import os
import sys
from pathlib import Path

APP_NAME = "캡처도구"
ORG_NAME = "CaptureTool"

# 프로그램 버전 — 여기 한 곳만 고치면 트레이·도움말·진단에 모두 반영된다
VERSION = "1.10.0"


DEFAULTS = {
    # 저장
    "save_dir": "",                       # 비우면 그림/캡처도구
    "file_pattern": "캡처_%Y%m%d_%H%M%S",
    "folder_split": "",                   # "" | "%Y-%m" | "%Y-%m-%d"
    "image_format": "png",                # png | jpg
    "jpeg_quality": 92,
    # 캡처 후 동작
    "after_copy": True,
    "after_save": True,
    "after_edit": True,
    "show_action_bar": True,              # 영역 선택 후 버튼 막대 표시
    "delay_seconds": 3,
    "quick_preview": True,                # 캡처 직후 구석에 작은 미리보기
    "quick_preview_seconds": 6,
    # 영역 기록 (쉼표/마침표로 이전 영역 불러오기)
    "region_history": [],                 # [[x, y, w, h], ...] 최신이 앞
    "region_history_limit": 10,
    # 편집 기본값
    "pen_color": "#e53935",
    "pen_width": 4,
    "font_size": 24,
    "highlight_color": "#ffeb3b",
    "frame_margin": 12,                   # 여백·테두리 두르기 기본값
    "frame_line": 1,
    "watermark_text": "",                 # 비우면 워터마크 안 넣음
    "stamp_on_save": False,               # 저장할 때 날짜 도장 자동으로
    "name_from_window": False,            # 파일 이름에 창 제목 넣기
    "hotkey_fallback": True,              # 막힌 단축키를 빈 키로 자동 대체
    "hotkey_warned": "",                  # 같은 문제로 반복해서 알리지 않기
    # 핀
    "pin_opacity": 100,
    "pin_restore_limit": 10,              # 닫은 핀을 되살릴 수 있는 개수
    # 크기지정 캡처
    "size_presets": [[1920, 1080], [1280, 720], [1024, 768],
                     [800, 600], [640, 480]],
    # 영역 잡기 도우미
    "snap_edges": True,                   # 드래그 중 경계선에 착 붙기
    "auto_region": True,                  # 마우스 올린 자리의 칸 자동 인식
    # 화면 안내 (단축키 도움말)
    "hint_mode": "fade",                  # fade | always | count | off
    "hint_count_limit": 20,
    "hint_shown": 0,
    # 최근 캡처
    "recent_limit": 200,
    "keep_unsaved": True,                 # 복사만 한 캡처도 임시 보관
    "unsaved_limit": 20,
    # 자동 정리 (기본 꺼짐)
    "cleanup_enabled": False,
    "cleanup_mode": "days",               # days | size
    "cleanup_days": 30,
    "cleanup_size_gb": 2,
    "cleanup_confirmed": False,
    "cleanup_last_run": "",
    # 스크롤 캡처
    "scroll_step": 3,                     # 한 번에 굴릴 휠 칸 수
    "scroll_delay_ms": 260,
    "scroll_max_frames": 30,              # (예전 값 — 더 이상 쓰지 않음)
    "scroll_frame_limit": 60,             # 최대 몇 장까지 찍을지
    # 기타
    "autostart": False,
    "show_tray_message": True,
    "ocr_language": "",                   # 비우면 시스템 기본
    # 단축키
    "hotkeys": {
        "region": "Print",
        "fullscreen": "Ctrl+Print",
        "window": "Alt+Print",
        "window_pick": "Ctrl+Alt+W",
        "delay": "Shift+Print",
        "scroll": "Ctrl+Alt+Print",
        "pin_clipboard": "F4",
        "recent": "Ctrl+Alt+L",
        "open_folder": "Ctrl+Alt+O",
        "last_region": "Ctrl+Shift+Print",
        "pins_hide": "Ctrl+Alt+H",
        "pin_restore": "Ctrl+Alt+Z",
        "pins_passthrough": "Ctrl+Alt+T",
    },
}

HOTKEY_LABELS = {
    "region": "영역 캡처",
    "fullscreen": "전체 화면 캡처",
    "window": "활성 창 캡처",
    "window_pick": "창 골라서 캡처",
    "delay": "지연 캡처",
    "scroll": "스크롤 캡처",
    "last_region": "직전 영역 다시 캡처",
    "pin_clipboard": "클립보드 핀 고정 (이미지·글자·색)",
    "pins_hide": "핀 모두 숨기기 / 보이기",
    "pin_restore": "닫은 핀 되살리기",
    "pins_passthrough": "핀 클릭 통과 켜기 / 끄기",
    "recent": "최근 캡처 목록",
    "open_folder": "저장 폴더 열기",
}


def base_dir() -> Path:
    """실행 파일(또는 소스)이 있는 폴더."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("1", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def config_path() -> Path:
    portable = base_dir() / "설정.json"
    if _writable(portable.parent):
        return portable
    appdata = os.environ.get("APPDATA") or str(Path.home())
    folder = Path(appdata) / ORG_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "settings.json"


def default_save_dir() -> Path:
    home = Path.home()
    for candidate in ("Pictures", "그림", "사진"):
        p = home / candidate
        if p.exists():
            return p / APP_NAME
    return home / APP_NAME


class Config:
    def __init__(self):
        self.data = copy.deepcopy(DEFAULTS)
        self.path = config_path()
        self.load()

    def load(self):
        try:
            if self.path.exists():
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    hk = dict(DEFAULTS["hotkeys"])
                    hk.update(loaded.get("hotkeys") or {})
                    self.data.update(loaded)
                    self.data["hotkeys"] = hk
        except Exception:
            pass

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8")
            return True
        except Exception:
            return False

    # 사전처럼 사용
    def __getitem__(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key, value):
        self.data[key] = value

    def get(self, key, fallback=None):
        return self.data.get(key, DEFAULTS.get(key, fallback))

    def hotkey(self, action):
        return (self.data.get("hotkeys") or {}).get(
            action, DEFAULTS["hotkeys"].get(action, ""))

    # ------------------------------------------------ 이전에 잡은 영역 기록
    def remember_region(self, x, y, w, h):
        """확정한 선택 영역을 기록 맨 앞에 넣는다 (같은 영역은 하나만)."""
        if w < 4 or h < 4:
            return
        entry = [int(x), int(y), int(w), int(h)]
        history = [list(r) for r in (self.data.get("region_history") or [])
                   if isinstance(r, (list, tuple)) and len(r) == 4]
        history = [r for r in history if r != entry]
        history.insert(0, entry)
        limit = max(1, int(self.get("region_history_limit", 10) or 10))
        self.data["region_history"] = history[:limit]
        self.save()

    def region_history(self):
        out = []
        for r in (self.data.get("region_history") or []):
            try:
                if len(r) == 4 and int(r[2]) >= 4 and int(r[3]) >= 4:
                    out.append([int(v) for v in r])
            except Exception:
                continue
        return out

    def data_dir(self) -> Path:
        """임시 보관·목록 등 프로그램 자료를 두는 폴더."""
        folder = self.path.parent / "자료"
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception:
            import tempfile
            folder = Path(tempfile.gettempdir()) / "캡처도구자료"
            folder.mkdir(parents=True, exist_ok=True)
        return folder

    def save_dir(self) -> Path:
        raw = (self.data.get("save_dir") or "").strip()
        p = Path(raw) if raw else default_save_dir()
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception:
            p = default_save_dir()
            p.mkdir(parents=True, exist_ok=True)
        return p


CONFIG = Config()
