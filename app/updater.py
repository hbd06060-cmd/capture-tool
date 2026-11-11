# -*- coding: utf-8 -*-
"""새 버전 확인 · 내려받기 · 바꿔 끼우기 (화면 코드 없음).

이 파일은 PySide6 를 쓰지 않는다. 순수 파이썬만 쓰므로 윈도우가 아닌
곳에서도 그대로 돌려 보며 시험할 수 있다. 화면에 보이는 부분은
updatedlg.py 가 맡는다.

안전을 위해 지키는 원칙
  1. 사용자가 만든 것은 절대 건드리지 않는다
     (설정.json, 자료/, runtime/, 오류기록.txt 는 손대지 않는다)
  2. 바꾸기 전에 지금 파일을 통째로 백업한다
  3. 중간에 실패하면 백업에서 되돌린다
  4. 내려받은 꾸러미가 캡처도구가 맞는지 확인한 뒤에만 바꾼다
"""
import json
import os
import re
import shutil
import ssl
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from config import CONFIG, VERSION, base_dir

# 받은 파일을 풀어 놓는 작업 폴더 이름 (자료/ 아래)
WORK_DIR_NAME = "업데이트"
TOKEN_FILE_NAME = "업데이트토큰.txt"
TOKEN_ENV = "CAPTURE_TOOL_GITHUB_TOKEN"

# 새 버전으로 바꿔 끼울 대상. 이 목록에 없는 것은 건드리지 않는다.
UPDATE_FILES = ("버전정보.json", "사용설명서.html", "먼저 읽어보세요.txt")
UPDATE_DIRS = ("app",)
# app 폴더 안에서 바꿀 확장자 (파이썬 코드만)
CODE_SUFFIX = (".py", ".pyw")

TIMEOUT = 20          # 한 번 요청에 기다릴 최대 시간(초)
MAX_ZIP_BYTES = 80 * 1024 * 1024      # 내려받을 꾸러미 크기 상한 (80MB)


# ------------------------------------------------------------ 버전 비교
def parse_version(text):
    """'1.10.0' → (1, 10, 0). 숫자가 아닌 꼬리표는 무시한다."""
    numbers = re.findall(r"\d+", str(text or ""))
    if not numbers:
        return ()
    return tuple(int(n) for n in numbers[:4])


def is_newer(candidate, current=VERSION):
    """candidate 가 current 보다 높은 버전이면 True."""
    left, right = parse_version(candidate), parse_version(current)
    if not left:
        return False
    # 자리 수가 다르면 짧은 쪽을 0 으로 채워서 비교한다 (1.10 < 1.10.1)
    length = max(len(left), len(right))
    left += (0,) * (length - len(left))
    right += (0,) * (length - len(right))
    return left > right


# ------------------------------------------------------------ 경로·설정
def program_dir() -> Path:
    """캡처도구가 설치된 폴더."""
    return base_dir()


def work_dir() -> Path:
    """내려받은 파일을 두는 작업 폴더 (자료/업데이트)."""
    folder = CONFIG.data_dir() / WORK_DIR_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def token():
    """GitHub 접근 열쇠. 비공개 저장소일 때만 필요하다.

    환경 변수가 먼저고, 없으면 자료/업데이트토큰.txt 를 읽는다.
    이 파일은 .gitignore 에 들어 있어 저장소에 올라가지 않는다.
    """
    value = (os.environ.get(TOKEN_ENV) or "").strip()
    if value:
        return value
    try:
        path = CONFIG.data_dir() / TOKEN_FILE_NAME
        if path.is_file():
            # 설명을 적어 둬도 되도록, # 로 시작하지 않는 첫 줄만 쓴다
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except Exception:
        pass
    return ""


def token_path() -> Path:
    """열쇠 파일을 둘 자리 (안내 문구에 쓴다)."""
    return CONFIG.data_dir() / TOKEN_FILE_NAME


def repo():
    return (CONFIG.get("update_repo") or "").strip().strip("/")


def branch():
    return (CONFIG.get("update_branch") or "main").strip() or "main"


# ------------------------------------------------------------ 통신
class UpdateError(Exception):
    """사용자에게 그대로 보여 줄 수 있는 한국어 오류."""


def _headers():
    head = {
        "User-Agent": "CaptureTool/%s" % VERSION,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    key = token()
    if key:
        head["Authorization"] = "Bearer %s" % key
    return head


def _open(url, accept, timeout=TIMEOUT):
    head = _headers()
    head["Accept"] = accept
    request = urllib.request.Request(url, headers=head)
    try:
        context = ssl.create_default_context()
        return urllib.request.urlopen(request, timeout=timeout,
                                      context=context)
    except urllib.error.HTTPError as exc:
        raise UpdateError(_http_message(exc.code)) from exc
    except urllib.error.URLError as exc:
        raise UpdateError(
            "인터넷에 연결하지 못했습니다. 잠시 뒤 다시 시도해 주세요.\n(%s)"
            % exc.reason) from exc
    except Exception as exc:
        raise UpdateError("확인하는 중 문제가 생겼습니다.\n(%s)" % exc) from exc


def _http_message(code):
    if code in (401, 403):
        if not token():
            return ("비공개 저장소라 접근 열쇠가 필요합니다.\n"
                    "아래 파일에 GitHub 토큰 한 줄을 넣어 주세요.\n%s"
                    % token_path())
        return ("접근이 거부되었습니다. 토큰이 만료되었거나 권한이 "
                "모자랄 수 있습니다.\n%s 를 확인해 주세요." % token_path())
    if code == 404:
        return ("저장소나 버전 파일을 찾지 못했습니다.\n"
                "설정 > 업데이트 에서 저장소 주소를 확인해 주세요.")
    if code == 429:
        return "요청이 너무 잦습니다. 잠시 뒤 다시 시도해 주세요."
    return "서버가 응답하지 않았습니다. (오류 %s)" % code


def fetch_manifest():
    """저장소에 올라와 있는 버전정보.json 을 읽어 온다."""
    name = repo()
    if not name or "/" not in name:
        raise UpdateError("업데이트 저장소가 설정돼 있지 않습니다.")
    manifest_name = (CONFIG.get("update_manifest") or "버전정보.json").strip()
    url = "https://api.github.com/repos/%s/contents/%s?ref=%s" % (
        name, urllib.request.quote(manifest_name), urllib.request.quote(branch()))
    with _open(url, "application/vnd.github.raw") as response:
        raw = response.read(1024 * 256)
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise UpdateError("버전 정보를 읽지 못했습니다. (%s)" % exc) from exc
    if not isinstance(data, dict) or not data.get("version"):
        raise UpdateError("버전 정보의 모양이 올바르지 않습니다.")
    return data


# ------------------------------------------------------------ 확인
class Result:
    """확인 결과. status 는 none / available / error."""

    def __init__(self, status, version="", manifest=None, message=""):
        self.status = status
        self.version = version
        self.manifest = manifest or {}
        self.message = message

    @property
    def available(self):
        return self.status == "available"

    def notes(self):
        items = self.manifest.get("notes")
        if isinstance(items, list):
            return [str(i) for i in items if str(i).strip()]
        if items:
            return [str(items)]
        return []

    def title(self):
        return str(self.manifest.get("title") or "").strip()


def due_for_check():
    """정해 둔 주기가 지나 자동 확인을 할 때가 됐는지."""
    if not CONFIG.get("update_check_enabled", True):
        return False
    hours = max(1, int(CONFIG.get("update_check_hours", 24) or 24))
    last = str(CONFIG.get("update_last_check") or "")
    if not last:
        return True
    try:
        then = datetime.fromisoformat(last)
    except Exception:
        return True
    return (datetime.now() - then).total_seconds() >= hours * 3600


def record_check_time():
    """확인한 시각을 남긴다. 설정 파일을 건드리므로 주 스레드에서 부를 것."""
    CONFIG["update_last_check"] = datetime.now().isoformat(timespec="seconds")
    CONFIG.save()


def check(force=False):
    """새 버전이 있는지 확인한다. 네트워크를 쓰므로 별도 스레드에서 부를 것.

    설정 파일은 건드리지 않는다. 확인 시각 기록은 부른 쪽이
    record_check_time() 으로 주 스레드에서 한다.
    """
    try:
        manifest = fetch_manifest()
    except UpdateError as exc:
        return Result("error", message=str(exc))

    latest = str(manifest.get("version") or "").strip()
    if not is_newer(latest):
        return Result("none", latest, manifest,
                      "이미 최신 버전입니다. (v%s)" % VERSION)
    if not force and str(CONFIG.get("update_skip_version") or "") == latest:
        return Result("none", latest, manifest,
                      "v%s 는 건너뛰기로 해 두셨습니다." % latest)
    return Result("available", latest, manifest,
                  "새 버전 v%s 가 나왔습니다." % latest)


# ------------------------------------------------------------ 내려받기
def download(progress=None):
    """저장소를 통째로 내려받아 zip 파일 경로를 돌려준다.

    progress(받은바이트, 전체바이트) 를 중간중간 불러 준다.
    전체 크기를 서버가 알려 주지 않으면 전체는 0 이다.
    """
    name = repo()
    url = "https://api.github.com/repos/%s/zipball/%s" % (
        name, urllib.request.quote(branch()))
    target = work_dir() / "받은파일.zip"
    # 받는 중인 파일 이름을 매번 다르게 한다. 어쩌다 두 군데서 동시에 받아도
    # 같은 파일에 겹쳐 써서 내용이 뒤섞이는 일이 없도록 하기 위해서다.
    tmp = work_dir() / ("받는중_%d_%d.part" % (os.getpid(), int(time.time() * 1000)))

    with _open(url, "application/vnd.github+json", timeout=60) as response:
        try:
            total = int(response.headers.get("Content-Length") or 0)
        except Exception:
            total = 0
        got = 0
        with open(tmp, "wb") as handle:
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                got += len(chunk)
                if got > MAX_ZIP_BYTES:
                    handle.close()
                    tmp.unlink(missing_ok=True)
                    raise UpdateError(
                        "내려받을 파일이 너무 큽니다. 중단했습니다.")
                handle.write(chunk)
                if progress is not None:
                    progress(got, total)
    if got <= 0:
        tmp.unlink(missing_ok=True)
        raise UpdateError("내려받은 내용이 비어 있습니다.")
    target.unlink(missing_ok=True)
    tmp.replace(target)
    return target


def _safe_members(archive):
    """압축 안에서 바깥으로 빠져나가는 수상한 경로를 걸러 낸다."""
    out = []
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/"):
            continue
        out.append(info)
    return out


def extract(zip_path):
    """내려받은 꾸러미를 풀고, 캡처도구가 맞는지 확인한 뒤 그 폴더를 돌려준다."""
    folder = work_dir() / "새버전"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(folder, members=_safe_members(archive))
    except zipfile.BadZipFile as exc:
        raise UpdateError("받은 파일이 손상됐습니다. 다시 받아 주세요.") from exc

    # GitHub 꾸러미는 'hbd06060-cmd-capture-tool-abc1234/' 한 겹에 싸여 있다
    root = folder
    children = [c for c in folder.iterdir() if c.is_dir()]
    if len(children) == 1 and not (folder / "app").exists():
        root = children[0]

    marker = root / "app" / "config.py"
    if not marker.is_file():
        raise UpdateError("받은 꾸러미가 캡처도구가 아닌 것 같습니다. 중단했습니다.")
    found = _version_in(marker)
    if not found:
        raise UpdateError("받은 꾸러미에서 버전을 읽지 못했습니다. 중단했습니다.")
    if not is_newer(found):
        # 버전 안내만 먼저 올라가고 코드가 아직 안 올라온 경우. 그대로 바꾸면
        # 버전이 그대로라 다음에 또 새 버전으로 보여 끝없이 되풀이된다.
        raise UpdateError(
            "받아 온 코드가 v%s 로, 지금 쓰시는 v%s 보다 높지 않습니다.\n"
            "새 코드가 저장소에 아직 올라오지 않은 것 같습니다. 중단했습니다."
            % (found, VERSION))
    return root, found


def _version_in(config_py: Path):
    """새로 받은 config.py 안의 VERSION 값을 읽는다 (실행하지 않고 글자로)."""
    try:
        text = config_py.read_text(encoding="utf-8")
    except Exception:
        return ""
    match = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', text, re.M)
    return match.group(1) if match else ""


# ------------------------------------------------------------ 바꿔 끼우기
def _planned_files(source: Path):
    """(새 파일, 프로그램 폴더 기준 상대 경로) 목록."""
    plan = []
    for name in UPDATE_FILES:
        candidate = source / name
        if candidate.is_file():
            plan.append((candidate, Path(name)))
    for name in UPDATE_DIRS:
        folder = source / name
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in CODE_SUFFIX:
                continue
            if "__pycache__" in path.parts:
                continue
            plan.append((path, path.relative_to(source)))
    return plan


def _backup_dir() -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    folder = work_dir() / ("백업_v%s_%s" % (VERSION, stamp))
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def install(source: Path):
    """새 파일을 프로그램 폴더에 덮어쓴다.

    돌려주는 값: (바꾼 개수, 백업 폴더, 바꾸지 못한 파일 목록)
    중간에 실패하면 백업에서 모두 되돌리고 UpdateError 를 낸다.
    """
    target_root = program_dir()
    plan = _planned_files(source)
    if not plan:
        raise UpdateError("바꿀 파일을 찾지 못했습니다. 중단했습니다.")

    backup = _backup_dir()
    done = []        # 되돌릴 때 쓸 (상대경로, 원래 있었는지)
    skipped = []

    try:
        for new_file, relative in plan:
            destination = target_root / relative
            existed = destination.is_file()
            if existed:
                keep = backup / relative
                keep.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, keep)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(new_file, destination)
            done.append((relative, existed))
    except PermissionError as exc:
        _rollback(target_root, backup, done)
        raise UpdateError(
            "파일을 바꾸지 못했습니다. 프로그램이 두 개 떠 있거나 "
            "백신이 막고 있을 수 있습니다.\n(%s)" % exc) from exc
    except Exception as exc:
        _rollback(target_root, backup, done)
        raise UpdateError(
            "바꾸는 중 문제가 생겨 원래대로 되돌렸습니다.\n(%s)" % exc) from exc

    # 실행기(캡처도구.exe)는 쓰는 중이라 잠겨 있을 수 있다. 되면 바꾸고,
    # 안 되면 건너뛰고 알려 준다. 코드가 아니라 껍데기라 급하지 않다.
    launcher = source / "캡처도구.exe"
    if launcher.is_file():
        destination = target_root / "캡처도구.exe"
        try:
            if destination.is_file():
                keep = backup / "캡처도구.exe"
                shutil.copy2(destination, keep)
            shutil.copy2(launcher, destination)
        except Exception:
            skipped.append("캡처도구.exe")

    _forget_caches(target_root)
    return len(done), backup, skipped


def _rollback(target_root: Path, backup: Path, done):
    """바꾼 파일을 백업에서 되돌린다."""
    for relative, existed in reversed(done):
        destination = target_root / relative
        try:
            if existed:
                shutil.copy2(backup / relative, destination)
            else:
                destination.unlink(missing_ok=True)
        except Exception:
            pass


def _forget_caches(target_root: Path):
    """옛 코드가 __pycache__ 에 남아 되살아나지 않도록 지운다."""
    try:
        for folder in (target_root / "app").rglob("__pycache__"):
            shutil.rmtree(folder, ignore_errors=True)
    except Exception:
        pass


def cleanup_work(keep_backups=3):
    """작업 폴더를 정리한다. 백업은 최근 몇 개만 남긴다."""
    folder = work_dir()
    try:
        (folder / "받은파일.zip").unlink(missing_ok=True)
        for leftover in folder.glob("받는중_*.part"):
            leftover.unlink(missing_ok=True)
    except Exception:
        pass
    shutil.rmtree(folder / "새버전", ignore_errors=True)
    try:
        backups = sorted((p for p in folder.iterdir()
                          if p.is_dir() and p.name.startswith("백업_")),
                         key=lambda p: p.name)
        keep = max(0, int(keep_backups))
        stale = backups[:len(backups) - keep] if keep else backups
        for old in stale:
            shutil.rmtree(old, ignore_errors=True)
    except Exception:
        pass


# ------------------------------------------------------------ 다시 시작
def restart_command():
    """업데이트 뒤 프로그램을 다시 띄울 명령."""
    launcher = program_dir() / "캡처도구.exe"
    if launcher.is_file():
        return [str(launcher)]
    script = Path(__file__).resolve().parent / "main.pyw"
    quiet = Path(sys.executable).with_name("pythonw.exe")
    exe = quiet if quiet.exists() else Path(sys.executable)
    return [str(exe), str(script)]


def relaunch():
    """지금 프로그램을 새로 하나 띄운다 (끄는 것은 부른 쪽이 한다)."""
    import subprocess
    command = restart_command()
    extra = {}
    if os.name == "nt":
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0) |
                 getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        if flags:
            extra["creationflags"] = flags
    subprocess.Popen(command, cwd=str(program_dir()), close_fds=True, **extra)
