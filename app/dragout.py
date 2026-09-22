# -*- coding: utf-8 -*-
"""이미지를 끌어서 다른 프로그램(카톡·메일·탐색기)에 놓기."""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QMimeData, Qt, QUrl
from PySide6.QtGui import QDrag, QImage

from config import CONFIG

_KEEP = 20          # 끌어놓기용 임시 파일은 최근 것만 남긴다


def _temp_folder() -> Path:
    folder = CONFIG.data_dir() / "끌어놓기"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _prune(folder: Path):
    try:
        files = sorted(folder.glob("*.png"), key=lambda p: p.stat().st_mtime,
                       reverse=True)
        for old in files[_KEEP:]:
            old.unlink()
    except Exception:
        pass


def file_for(image: QImage, path=None):
    """끌어놓기에 쓸 파일 경로. 이미 저장된 파일이 있으면 그것을 쓴다."""
    if path and Path(path).is_file():
        return str(path)
    folder = _temp_folder()
    now = datetime.now()
    name = "캡처_%04d%02d%02d_%02d%02d%02d.png" % (
        now.year, now.month, now.day, now.hour, now.minute, now.second)
    target = folder / name
    index = 2
    while target.exists():
        target = folder / ("캡처_%04d%02d%02d_%02d%02d%02d (%d).png" % (
            now.year, now.month, now.day, now.hour, now.minute, now.second,
            index))
        index += 1
    if not image.save(str(target), "PNG"):
        return None
    _prune(folder)
    return str(target)


def start_drag(source_widget, image: QImage, path=None, thumb_side=140):
    """드래그를 시작한다. 놓은 곳이 받아들였으면 True."""
    if image is None or image.isNull():
        return False
    mime = QMimeData()
    mime.setImageData(image)
    file_path = file_for(image, path)
    if file_path:
        mime.setUrls([QUrl.fromLocalFile(file_path)])
    drag = QDrag(source_widget)
    drag.setMimeData(mime)
    from PySide6.QtGui import QPixmap
    thumb = image.scaled(thumb_side, thumb_side, Qt.KeepAspectRatio,
                         Qt.SmoothTransformation)
    drag.setPixmap(QPixmap.fromImage(thumb))
    drag.setHotSpot(thumb.rect().center())
    result = drag.exec(Qt.CopyAction)
    return result != Qt.IgnoreAction
