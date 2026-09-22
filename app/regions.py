# -*- coding: utf-8 -*-
"""마우스 아래에서 고를 수 있는 영역을 '층' 으로 정리한다 (단위영역 캡처).

작은 것부터 큰 것 순서로 이런 층이 생긴다.

    칸  →  내용 영역  →  창 전체  →  모니터 전체

'칸' 은 화면 그림을 보고 찾아낸 상자(edges.py)이고, '내용 영역' 은
창 안에서 실제로 내용이 그려지는 부분이다. 크롬이라면 탭·주소창·북마크를
뺀 웹 페이지 부분이 여기에 해당한다.

제목표시줄 위에 마우스가 있으면 창 전체가, 내용 안쪽으로 조금만 내리면
내용 영역이 기본으로 잡힌다. 휠을 굴리면 층 사이를 오갈 수 있다.
"""
from PySide6.QtCore import QRect

BOX = "box"
CONTENT = "content"
WINDOW = "window"
SCREEN = "screen"

NAMES = {BOX: "칸", CONTENT: "내용 영역",
         WINDOW: "창 전체", SCREEN: "모니터 전체"}

# 내용 영역으로 인정할 조건
MIN_CONTENT_W = 120
MIN_CONTENT_H = 80
CONTENT_MAX_SHARE = 0.97   # 창의 이만큼을 넘으면 '창 전체' 와 다를 바 없다
SAME_SLACK = 6             # 이 정도 차이는 같은 사각형으로 본다


def _area(rect):
    return max(0, rect.width()) * max(0, rect.height())


def same_rect(a, b, slack=SAME_SLACK):
    """거의 같은 사각형인지 (한두 픽셀 차이는 무시)."""
    if a is None or b is None:
        return a is None and b is None
    return (abs(a.x() - b.x()) <= slack and abs(a.y() - b.y()) <= slack and
            abs(a.width() - b.width()) <= slack * 2 and
            abs(a.height() - b.height()) <= slack * 2)


class WindowLayers:
    """창 하나의 영역 층 (전체 / 내용 / 안쪽 영역들)."""

    __slots__ = ("rect", "client", "children", "title")

    def __init__(self, rect, client=None, children=(), title=""):
        self.rect = QRect(rect)
        self.client = QRect(client) if client is not None else None
        self.title = title or ""
        inner = []
        for child in children:
            if child is None:
                continue
            box = QRect(child)
            if box.width() < MIN_CONTENT_W or box.height() < MIN_CONTENT_H:
                continue
            if not self.rect.intersects(box):
                continue
            box = box.intersected(self.rect)
            if _area(box) >= _area(self.rect) * CONTENT_MAX_SHARE:
                continue          # 창과 사실상 같은 크기
            inner.append(box)
        inner.sort(key=_area)
        self.children = inner

    # ------------------------------------------------------------ 판정
    def on_title_bar(self, point):
        """제목표시줄(또는 테두리) 위에 있는지 — 여기서는 창 전체를 잡는다."""
        if not self.rect.contains(point):
            return False
        if self.client is None:
            return False
        return not self.client.contains(point)

    def content_at(self, point):
        """마우스를 감싸는 가장 작은 '내용 영역'. 없으면 None."""
        for box in self.children:            # 이미 작은 것부터 정렬돼 있다
            if box.contains(point):
                return QRect(box)
        if self.client is not None and self.client.contains(point):
            if _area(self.client) < _area(self.rect) * CONTENT_MAX_SHARE:
                return QRect(self.client)
        return None


class RegionLadder:
    """지금 화면 전체에 대한 영역 층 정보."""

    def __init__(self, windows=(), screens=()):
        self.windows = list(windows)          # WindowLayers, 앞→뒤 순서
        self.screens = [QRect(r) for r in screens]

    # ------------------------------------------------------- 만들기
    @classmethod
    def from_details(cls, details, screens=()):
        layers = []
        for item in details or ():
            try:
                layers.append(WindowLayers(item["rect"], item.get("client"),
                                           item.get("children") or (),
                                           item.get("title", "")))
            except Exception:
                continue
        return cls(layers, screens)

    # ------------------------------------------------------- 조회
    def window_at(self, point):
        for layer in self.windows:            # 앞에 있는 창이 먼저
            if layer.rect.contains(point):
                return layer
        return None

    def screen_at(self, point):
        for rect in self.screens:
            if rect.contains(point):
                return QRect(rect)
        return QRect(self.screens[0]) if self.screens else None

    def on_title_bar(self, point):
        layer = self.window_at(point)
        return layer.on_title_bar(point) if layer is not None else False

    def ladder_at(self, point, box=None):
        """작은 것부터 [(사각형, 종류), ...]. 항상 하나 이상을 돌려준다."""
        steps = []
        layer = self.window_at(point)
        window_rect = layer.rect if layer is not None else None

        if box is not None and not box.isEmpty():
            # 창보다 크거나 창을 벗어나면 '칸' 으로 쓸모가 없다
            usable = True
            if window_rect is not None:
                if not window_rect.contains(box.center()):
                    usable = False
                elif _area(box) >= _area(window_rect) * CONTENT_MAX_SHARE:
                    usable = False
            if usable:
                steps.append((QRect(box), BOX))

        if layer is not None:
            content = layer.content_at(point)
            if content is not None:
                steps.append((content, CONTENT))
            steps.append((QRect(layer.rect), WINDOW))

        screen = self.screen_at(point)
        if screen is not None:
            steps.append((screen, SCREEN))

        # 거의 같은 것끼리는 하나만 남긴다 (작은 쪽 이름을 쓴다)
        cleaned = []
        for rect, kind in steps:
            if cleaned and same_rect(cleaned[-1][0], rect):
                continue
            if cleaned and _area(rect) <= _area(cleaned[-1][0]):
                continue                      # 커지지 않으면 층이 아니다
            cleaned.append((rect, kind))
        return cleaned or ([(screen, SCREEN)] if screen is not None else [])

    def default_index(self, point, steps):
        """기본으로 고를 층.

        제목표시줄 위면 '창 전체', 그 밖에는 가장 안쪽(칸 또는 내용 영역).
        ez캡처의 단위영역 캡처와 같은 느낌이 나도록 한 것이다.
        """
        if not steps:
            return 0
        if self.on_title_bar(point):
            for index, (_rect, kind) in enumerate(steps):
                if kind == WINDOW:
                    return index
        return 0


def pick(steps, index):
    """층 목록에서 index 번째를 안전하게 고른다."""
    if not steps:
        return None, SCREEN
    index = max(0, min(index, len(steps) - 1))
    return steps[index]
