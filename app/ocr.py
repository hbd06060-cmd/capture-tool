# -*- coding: utf-8 -*-
"""Windows 내장 OCR로 이미지 속 글자를 추출한다.

별도 프로그램 설치 없이 Windows 10/11에 기본 포함된 인식 엔진을 사용한다.
한글 인식은 Windows 언어 설정에 한국어가 있으면 바로 동작한다.
"""
import asyncio
import threading

from PySide6.QtCore import QBuffer, QObject, Qt, Signal
from PySide6.QtGui import QImage

MAX_DIMENSION = 4000
MIN_DIMENSION = 400


class OcrError(Exception):
    pass


def _init_apartment():
    """이 스레드를 WinRT 다중 스레드 방식으로 초기화한다.

    Qt 본 스레드는 단일 스레드(STA) 방식이라 비동기 결과가 돌아오지 못하고
    프로그램이 멈춘다. 그래서 글자 인식은 반드시 별도 스레드에서 돌린다.
    """
    try:
        from winrt.runtime import ApartmentType, init_apartment
        init_apartment(ApartmentType.MULTI_THREADED)
        return True
    except Exception:
        pass
    try:
        from winrt.runtime import init_apartment
        init_apartment(1)          # 1 = MULTI_THREADED
        return True
    except Exception:
        return False


def _png_bytes(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QBuffer.WriteOnly)
    image.save(buffer, "PNG")
    data = bytes(buffer.data())
    buffer.close()
    return data


def _prepare(image: QImage) -> QImage:
    """인식률을 위해 너무 작으면 확대, 너무 크면 축소한다."""
    if image.isNull():
        raise OcrError("이미지가 없습니다.")
    img = image.convertToFormat(QImage.Format_RGB32)
    longest = max(img.width(), img.height())
    if longest > MAX_DIMENSION:
        img = img.scaled(MAX_DIMENSION, MAX_DIMENSION, Qt.KeepAspectRatio,
                         Qt.SmoothTransformation)
    elif longest < MIN_DIMENSION:
        factor = min(3.0, MIN_DIMENSION / float(max(1, longest)))
        img = img.scaled(int(img.width() * factor), int(img.height() * factor),
                         Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return img


async def _recognize_async(png: bytes, language_tag="", with_boxes=False):
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import (DataWriter,
                                               InMemoryRandomAccessStream)

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(png)
    await writer.store_async()
    await writer.flush_async()
    writer.detach_stream()
    stream.seek(0)

    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()

    engine = None
    if language_tag:
        try:
            from winrt.windows.globalization import Language
            engine = OcrEngine.try_create_from_language(Language(language_tag))
        except Exception:
            engine = None
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        try:
            from winrt.windows.globalization import Language
            for tag in ("ko", "ko-KR", "en-US"):
                engine = OcrEngine.try_create_from_language(Language(tag))
                if engine is not None:
                    break
        except Exception:
            engine = None
    if engine is None:
        raise OcrError(
            "Windows 글자 인식 엔진을 찾지 못했습니다.\n\n"
            "트레이 아이콘 오른쪽 클릭 → [글자 추출 진단] 을 누르면 "
            "이 컴퓨터의 실제 상태와 해결 방법을 알려 드립니다.")

    result = await engine.recognize_async(bitmap)
    if with_boxes:
        # [[(단어, (x, y, w, h)), ...], ...] — 줄마다 단어와 위치
        boxed = []
        for line in result.lines:
            words = []
            for word in line.words:
                rect = word.bounding_rect
                words.append((word.text, (float(rect.x), float(rect.y),
                                          float(rect.width),
                                          float(rect.height))))
            if words:
                boxed.append(words)
        return boxed
    lines = []
    try:
        for line in result.lines:
            lines.append(line.text)
    except Exception:
        pass
    if lines:
        return "\n".join(lines)
    return result.text or ""


def available_languages():
    """이 컴퓨터가 읽을 수 있는 언어 목록. 반드시 별도 스레드에서."""
    _init_apartment()
    try:
        from winrt.windows.media.ocr import OcrEngine
        return [lang.language_tag for lang in
                OcrEngine.get_available_recognizer_languages()]
    except Exception:
        return []


INSTALL_COMMAND = ('Add-WindowsCapability -Online '
                   '-Name "Language.OCR~~~ko-KR~0.0.1.0"')


def _language_name(tag):
    known = {"ko": "한국어", "ko-KR": "한국어", "en": "영어",
             "en-US": "영어(미국)", "en-GB": "영어(영국)", "ja": "일본어",
             "ja-JP": "일본어", "zh-Hans-CN": "중국어(간체)",
             "zh-Hant-TW": "중국어(번체)", "de": "독일어", "fr": "프랑스어",
             "es": "스페인어", "ru": "러시아어", "vi": "베트남어",
             "th": "타이어", "id": "인도네시아어"}
    return known.get(tag, tag)


def check() -> dict:
    """글자 인식이 준비돼 있는지 실제로 확인한다.

    설정 화면 경로를 안내하는 대신, 이 컴퓨터의 실제 상태를 읽는다.
    반드시 별도 스레드에서 부를 것.
    """
    report = {"ok": False, "apartment": False, "import_error": "",
              "languages": [], "korean": False, "engine": False,
              "summary": "", "detail": ""}
    report["apartment"] = _init_apartment()
    try:
        from winrt.windows.media.ocr import OcrEngine
    except Exception as exc:
        report["import_error"] = str(exc)
        report["summary"] = "글자 인식 모듈을 불러오지 못했습니다."
        report["detail"] = (
            "이 컴퓨터에서는 Windows 내장 인식 기능을 쓸 수 없습니다.\n"
            "Windows 10 이상인지 확인해 주세요.\n\n%s" % exc)
        return report

    try:
        tags = [lang.language_tag for lang in
                OcrEngine.get_available_recognizer_languages()]
    except Exception as exc:
        report["import_error"] = str(exc)
        report["summary"] = "인식 언어 목록을 읽지 못했습니다."
        report["detail"] = str(exc)
        return report

    report["languages"] = tags
    report["korean"] = any(t.lower().startswith("ko") for t in tags)
    try:
        report["engine"] = \
            OcrEngine.try_create_from_user_profile_languages() is not None
    except Exception:
        report["engine"] = False

    listed = "\n".join("  · %s  (%s)" % (_language_name(t), t) for t in tags) \
        or "  (없음)"
    if report["korean"]:
        report["ok"] = True
        report["summary"] = "한국어 인식이 준비돼 있습니다."
        report["detail"] = ("읽을 수 있는 언어:\n%s\n\n"
                            "바로 [글자 추출]을 쓰시면 됩니다." % listed)
    elif tags:
        report["summary"] = "한국어만 빠져 있습니다."
        report["detail"] = (
            "지금 읽을 수 있는 언어:\n%s\n\n"
            "한국어를 추가하려면, 시작 메뉴에서 'Windows PowerShell' 을 "
            "오른쪽 클릭 → [관리자 권한으로 실행] 한 뒤 아래 한 줄을 "
            "붙여넣고 Enter 를 누르세요.\n\n  %s\n\n"
            "끝나면 캡처도구를 껐다 켜 주세요." % (listed, INSTALL_COMMAND))
    else:
        report["summary"] = "인식할 수 있는 언어가 하나도 없습니다."
        report["detail"] = (
            "Windows 에 글자 인식 기능이 설치돼 있지 않습니다.\n\n"
            "시작 메뉴에서 'Windows PowerShell' 을 오른쪽 클릭 → "
            "[관리자 권한으로 실행] 한 뒤 아래 한 줄을 붙여넣고 Enter:\n\n"
            "  %s\n\n끝나면 캡처도구를 껐다 켜 주세요." % INSTALL_COMMAND)
    return report


class CheckTask(QObject):
    """준비 상태 확인도 본 스레드를 막지 않게 별도로 돌린다."""

    finished = Signal(object)

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self.finished.emit(check())
        except Exception as exc:
            self.finished.emit({
                "ok": False, "languages": [], "korean": False,
                "summary": "확인 중 문제가 생겼습니다.",
                "detail": str(exc)})


TIMEOUT_SECONDS = 30


def recognize_bytes(png: bytes, language_tag="", with_boxes=False):
    """PNG 자료에서 글자를 뽑는다. 반드시 별도 스레드에서 호출할 것."""
    _init_apartment()
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                asyncio.wait_for(_recognize_async(png, language_tag,
                                                  with_boxes),
                                 TIMEOUT_SECONDS))
        finally:
            loop.close()
    except OcrError:
        raise
    except asyncio.TimeoutError:
        raise OcrError("글자 인식이 %d초 안에 끝나지 않았습니다.\n"
                       "이미지를 조금 작게 잡아 다시 시도해 보세요."
                       % TIMEOUT_SECONDS)
    except ImportError as exc:
        raise OcrError("글자 인식 모듈을 불러오지 못했습니다.\n%s" % exc)
    except Exception as exc:
        raise OcrError("글자 인식에 실패했습니다.\n%s" % exc)


def recognize(image: QImage, language_tag="") -> str:
    """이미지에서 글자를 추출한다 (별도 스레드에서 호출해야 한다)."""
    return recognize_bytes(_png_bytes(_prepare(image)), language_tag)


class OcrTask(QObject):
    """글자 인식을 별도 스레드에서 돌리고 결과를 알려 준다.

    Qt 본 스레드에서 그냥 부르면 프로그램이 멈추기 때문에 반드시 이걸 쓴다.
    """

    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, image: QImage, language_tag="", parent=None):
        super().__init__(parent)
        self._png = _png_bytes(_prepare(image))
        self._language = language_tag
        self._cancelled = False
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self):
        self._cancelled = True

    def _run(self):
        try:
            text = recognize_bytes(self._png, self._language)
        except OcrError as exc:
            if not self._cancelled:
                self.failed.emit(str(exc))
            return
        except Exception as exc:
            if not self._cancelled:
                self.failed.emit("글자 인식에 실패했습니다.\n%s" % exc)
            return
        if not self._cancelled:
            self.finished.emit(text)


class WordsTask(QObject):
    """글자와 함께 단어마다 위치를 알아낸다 (개인정보 자동 가리기용).

    finished(list) — [[(단어, (x, y, w, h)), ...], ...]  원본 이미지 좌표
    """

    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, image: QImage, language_tag="", parent=None):
        super().__init__(parent)
        prepared = _prepare(image)
        self._factor = prepared.width() / float(max(1, image.width()))
        self._png = _png_bytes(prepared)
        self._language = language_tag
        self._cancelled = False
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self):
        self._cancelled = True

    def _run(self):
        try:
            lines = recognize_bytes(self._png, self._language, with_boxes=True)
        except OcrError as exc:
            if not self._cancelled:
                self.failed.emit(str(exc))
            return
        except Exception as exc:
            if not self._cancelled:
                self.failed.emit("글자 인식에 실패했습니다.\n%s" % exc)
            return
        factor = self._factor or 1.0
        scaled = [[(text, (x / factor, y / factor, w / factor, h / factor))
                   for text, (x, y, w, h) in line] for line in lines]
        if not self._cancelled:
            self.finished.emit(scaled)
