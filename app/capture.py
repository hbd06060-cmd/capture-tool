# -*- coding: utf-8 -*-
"""화면 캡처 엔진.

Qt 화면 단위로 캡처한 뒤 논리 좌표계에서 합성한다.
모니터마다 배율(DPI)이 달라도 원본 해상도를 유지한다.
"""
from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter

import winutil


def build_monitor_map():
    """Win32 물리 좌표 <-> Qt 논리 좌표 매핑표. [(물리 rect, 논리 QRect, 배율)]"""
    mapping = []
    if not winutil.IS_WIN:
        for screen in QGuiApplication.screens():
            geo = screen.geometry()
            dpr = screen.devicePixelRatio() or 1.0
            mapping.append(((geo.x(), geo.y(), geo.width(), geo.height()),
                            geo, dpr))
        return mapping
    physical = winutil.physical_monitors()
    screens = QGuiApplication.screens()
    by_name = {s.name(): s for s in screens}
    used = set()
    for device, prect in physical:
        screen = by_name.get(device)
        if screen is None:
            for candidate in screens:
                if candidate.name() not in used:
                    screen = candidate
                    break
        if screen is None:
            continue
        used.add(screen.name())
        geo = screen.geometry()
        dpr = prect[2] / float(geo.width()) if geo.width() else 1.0
        mapping.append((prect, geo, dpr))
    return mapping


def phys_to_logical_rect(prect, mapping=None):
    """Win32 물리 사각형 (x, y, w, h) -> Qt 논리 QRect."""
    if prect is None:
        return None
    if mapping is None:
        mapping = build_monitor_map()
    px, py, pw, ph = prect
    cx, cy = px + pw / 2.0, py + ph / 2.0
    entry = None
    for pr, geo, dpr in mapping:
        if pr[0] <= cx < pr[0] + pr[2] and pr[1] <= cy < pr[1] + pr[3]:
            entry = (pr, geo, dpr)
            break
    if entry is None:
        if mapping:
            entry = mapping[0]
        else:
            return QRect(int(px), int(py), int(pw), int(ph))
    pr, geo, dpr = entry
    if dpr <= 0:
        dpr = 1.0
    x = geo.x() + (px - pr[0]) / dpr
    y = geo.y() + (py - pr[1]) / dpr
    return QRect(int(round(x)), int(round(y)),
                 int(round(pw / dpr)), int(round(ph / dpr)))


class ScreenShot:
    """전체 가상 화면의 스냅샷.

    shots : [(QRect 논리좌표 화면영역, QImage 원본픽셀, dpr)]
    """

    def __init__(self):
        self.shots = []
        self.virtual_rect = QRect()
        self._monitor_map = []   # [(물리 rect, 논리 QRect, dpr)]
        self.grab()

    # ------------------------------------------------------------ 캡처
    def grab(self):
        self.shots = []
        rect = QRect()
        for screen in QGuiApplication.screens():
            geo = screen.geometry()
            try:
                pixmap = screen.grabWindow(0)
            except Exception:
                continue
            if pixmap.isNull():
                continue
            image = pixmap.toImage().convertToFormat(QImage.Format_RGB32)
            dpr = pixmap.devicePixelRatio() or 1.0
            if image.width() and geo.width():
                dpr = image.width() / float(geo.width())
            self.shots.append((geo, image, dpr))
            rect = geo if rect.isNull() else rect.united(geo)
        self.virtual_rect = rect
        self._build_monitor_map()

    def _build_monitor_map(self):
        self._monitor_map = build_monitor_map()

    # ------------------------------------------------- 좌표 변환 (물리→논리)
    def physical_to_logical_rect(self, prect):
        return phys_to_logical_rect(prect, self._monitor_map)

    def logical_to_physical_point(self, point):
        """Qt 논리 좌표 -> Win32 물리 좌표."""
        for prect, geo, dpr in self._monitor_map:
            if geo.contains(point):
                x = prect[0] + (point.x() - geo.x()) * dpr
                y = prect[1] + (point.y() - geo.y()) * dpr
                return (int(round(x)), int(round(y)))
        return (int(point.x()), int(point.y()))

    # ------------------------------------------------------------ 합성
    def image_for(self, logical_rect: QRect) -> QImage:
        """논리 좌표 영역을 원본 해상도 이미지로 잘라낸다."""
        sel = QRect(logical_rect).normalized()
        if sel.width() <= 0 or sel.height() <= 0:
            return QImage()
        overlapping = [(g, im, d) for (g, im, d) in self.shots
                       if g.intersects(sel)]
        if not overlapping:
            overlapping = self.shots
        if not overlapping:
            return QImage()
        dpr = max(d for _, _, d in overlapping) or 1.0
        out = QImage(max(1, int(round(sel.width() * dpr))),
                     max(1, int(round(sel.height() * dpr))),
                     QImage.Format_RGB32)
        out.fill(Qt.black)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        for geo, image, sdpr in overlapping:
            inter = geo.intersected(sel)
            if inter.isEmpty():
                continue
            src = QRectF((inter.x() - geo.x()) * sdpr,
                         (inter.y() - geo.y()) * sdpr,
                         inter.width() * sdpr, inter.height() * sdpr)
            dst = QRectF((inter.x() - sel.x()) * dpr,
                         (inter.y() - sel.y()) * dpr,
                         inter.width() * dpr, inter.height() * dpr)
            painter.drawImage(dst, image, src)
        painter.end()
        return out

    def full_image(self) -> QImage:
        return self.image_for(self.virtual_rect)

    def pixel_at(self, logical_point):
        """논리 좌표의 화면 색상 (스포이드용)."""
        for geo, image, dpr in self.shots:
            if geo.contains(logical_point):
                x = int((logical_point.x() - geo.x()) * dpr)
                y = int((logical_point.y() - geo.y()) * dpr)
                if 0 <= x < image.width() and 0 <= y < image.height():
                    return image.pixelColor(x, y)
        return None

    def _raw(self, index):
        """모니터 그림의 픽셀 바이트 (화면 자가 빠르게 훑기 위해 한 번만 읽는다)."""
        cache = getattr(self, "_raw_cache", None)
        if cache is None:
            cache = self._raw_cache = {}
        if index not in cache:
            image = self.shots[index][1]
            try:
                cache[index] = bytes(image.constBits())
            except Exception:
                cache[index] = bytes(image.bits())
        return cache[index]

    def measure_at(self, logical_point, tolerance=3):
        """화면 자 — 마우스 자리와 같은 색이 이어지는 가로·세로 범위.

        화면 픽셀은 사진과 달리 잡티가 없어서 허용 폭을 좁게 둔다.
        (넓게 두면 흰 패널과 거의 흰 배경을 한 덩어리로 재 버린다)

        돌려주는 값: {"width": 물리 px, "height": 물리 px,
                      "left"/"right"/"top"/"bottom": 논리 좌표(실수)}
        표 칸 사이 여백이나 버튼 크기를 잴 때 쓴다.
        """
        for index, (geo, image, dpr) in enumerate(self.shots):
            if not geo.contains(logical_point):
                continue
            width, height = image.width(), image.height()
            x = int((logical_point.x() - geo.x()) * dpr)
            y = int((logical_point.y() - geo.y()) * dpr)
            if not (0 <= x < width and 0 <= y < height):
                return None
            data = self._raw(index)
            stride = image.bytesPerLine()

            def near(px, py, base):
                offset = py * stride + px * 4
                return (abs(data[offset] - base[0]) <= tolerance and
                        abs(data[offset + 1] - base[1]) <= tolerance and
                        abs(data[offset + 2] - base[2]) <= tolerance)

            start = y * stride + x * 4
            base = (data[start], data[start + 1], data[start + 2])
            left = x
            while left > 0 and near(left - 1, y, base):
                left -= 1
            right = x
            while right < width - 1 and near(right + 1, y, base):
                right += 1
            top = y
            while top > 0 and near(x, top - 1, base):
                top -= 1
            bottom = y
            while bottom < height - 1 and near(x, bottom + 1, base):
                bottom += 1
            return {
                "width": right - left + 1,
                "height": bottom - top + 1,
                "left": geo.x() + left / dpr,
                "right": geo.x() + (right + 1) / dpr,
                "top": geo.y() + top / dpr,
                "bottom": geo.y() + (bottom + 1) / dpr,
            }
        return None

    # --------------------------------------------------------- 창 목록
    def window_rects(self, exclude_hwnds=()):
        """감지된 창들의 논리 좌표 사각형 목록 (앞→뒤 순서)."""
        rects = []
        for _hwnd, prect, _title in winutil.enumerate_windows(exclude_hwnds):
            r = self.physical_to_logical_rect(prect)
            if r is not None and not r.isEmpty():
                rects.append(r)
        return rects

    # 내용 영역까지 살펴볼 창 수 (앞쪽 몇 개만 — 너무 오래 걸리지 않게)
    DEEP_WINDOW_LIMIT = 12

    def window_details(self, exclude_hwnds=(), deep=True):
        """창마다 [전체 / 내용 영역 / 안쪽 영역들] 을 논리 좌표로 모은다.

        '단위영역 캡처' 를 위한 것이다. 제목표시줄 위에 마우스가 있으면
        창 전체를, 내용 안쪽에 있으면 내용 영역만 잡을 수 있게 해 준다.
        """
        details = []
        for index, (hwnd, prect, title) in \
                enumerate(winutil.enumerate_windows(exclude_hwnds)):
            rect = self.physical_to_logical_rect(prect)
            if rect is None or rect.isEmpty():
                continue
            client = None
            children = []
            if deep and index < self.DEEP_WINDOW_LIMIT:
                try:
                    client = self.physical_to_logical_rect(
                        winutil.client_rect(hwnd))
                except Exception:
                    client = None
                try:
                    for child in winutil.child_rects(hwnd):
                        inner = self.physical_to_logical_rect(child)
                        if inner is not None and not inner.isEmpty():
                            children.append(inner)
                except Exception:
                    children = []
            details.append({"hwnd": hwnd, "rect": rect, "client": client,
                            "children": children, "title": title})
        return details

    def foreground_rect(self):
        return self.physical_to_logical_rect(winutil.foreground_window_rect())


def grab_screens() -> ScreenShot:
    return ScreenShot()


def grab_region(logical_rect: QRect) -> QImage:
    """지정한 논리 영역만 빠르게 다시 캡처한다 (스크롤 캡처용).

    영역이 걸친 모니터 하나만 읽으므로 전체 캡처보다 훨씬 빠르다.
    """
    rect = QRect(logical_rect).normalized()
    if rect.width() <= 0 or rect.height() <= 0:
        return QImage()
    center = rect.center()
    target = None
    for screen in QGuiApplication.screens():
        if screen.geometry().contains(center):
            target = screen
            break
    if target is None:
        target = QGuiApplication.primaryScreen()
    if target is None:
        return QImage()
    geo = target.geometry()
    try:
        pixmap = target.grabWindow(0)
    except Exception:
        return QImage()
    if pixmap.isNull():
        return QImage()
    image = pixmap.toImage().convertToFormat(QImage.Format_RGB32)
    dpr = image.width() / float(geo.width()) if geo.width() else 1.0
    inter = geo.intersected(rect)
    if inter.isEmpty():
        return QImage()
    src = QRect(int(round((inter.x() - geo.x()) * dpr)),
                int(round((inter.y() - geo.y()) * dpr)),
                max(1, int(round(inter.width() * dpr))),
                max(1, int(round(inter.height() * dpr))))
    return image.copy(src)
