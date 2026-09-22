# -*- coding: utf-8 -*-
"""화면 그림에서 경계선을 찾아낸다.

두 가지 용도로 쓴다.
  1. 마우스를 올린 자리를 감싸는 상자 찾기 (단위영역 인식)
  2. 드래그하는 테두리를 가까운 경계선에 착 붙이기 (흡착)

창 구조를 읽지 않고 화면에 보이는 그림만 보기 때문에
크롬이든 전자문서든 프로그램 종류를 가리지 않는다.
"""
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QImage

DIFF = 16            # 경계로 볼 밝기 차이
AGREE = 0.72         # 이 비율 이상의 표본에서 경계가 보여야 진짜 경계선
SAMPLES = 9          # 한 번 판정할 때 훑어볼 줄 수
TIGHT_SPREAD = 5     # 작은 버튼·아이콘을 찾을 때
WIDE_SPREAD = 26     # 넓은 패널을 찾을 때
SPREAD = WIDE_SPREAD
MAX_WALK = 1200      # 경계선을 찾아 나아갈 최대 거리
MIN_BOX = 14         # 이보다 작은 상자는 무시
SNAP_RANGE = 9       # 흡착이 걸리는 거리
REFINE_PASSES = 3    # 찾은 상자를 다시 다듬는 횟수

# ------------------------------------------------- 빈 색 덩어리 걸러내기
# 사진 속 흰 여백이나 페이지의 빈 공간처럼 '넓은데 안이 텅 빈' 상자는
# 사람이 잡고 싶어 하는 대상이 아니다. 이런 상자는 한 겹 바깥의
# 경계선까지 넓혀서, 내용이 들어 있는 진짜 덩어리를 잡는다.
FLAT_TOLERANCE = 7       # 이 정도 밝기 차이는 '같은 색'으로 본다
FLAT_RATIO = 0.96        # 안쪽이 이 비율 이상 한 색이면 텅 빈 것
FLAT_MIN_W = 240         # 이보다 커야 '넓은' 상자로 친다
FLAT_MIN_H = 150
FLAT_GRID = 11           # 안쪽을 훑어볼 격자 (11x11 = 121곳)
GROW_PASSES = 3          # 텅 빈 상자를 넓혀 볼 횟수
HUGE_RATIO = 0.92        # 모니터의 이만큼을 넘으면 그냥 바탕화면이다


class _Plane:
    """모니터 한 대의 흑백 화면 자료."""

    __slots__ = ("geo", "data", "bpl", "w", "h")

    def __init__(self, geo, image: QImage):
        self.geo = geo
        gray = image
        if gray.width() != geo.width() or gray.height() != geo.height():
            gray = gray.scaled(max(1, geo.width()), max(1, geo.height()),
                               Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        gray = gray.convertToFormat(QImage.Format_Grayscale8)
        self.w = gray.width()
        self.h = gray.height()
        self.bpl = gray.bytesPerLine()
        try:
            self.data = bytes(gray.constBits())
        except Exception:
            self.data = bytes(gray.bits())

    def at(self, x, y):
        return self.data[y * self.bpl + x]


class ScreenEdges:
    """캡처해 둔 화면 전체에 대한 경계선 분석기."""

    def __init__(self, shot):
        self.planes = []
        for geo, image, _dpr in shot.shots:
            try:
                self.planes.append(_Plane(geo, image))
            except Exception:
                pass

    # -------------------------------------------------------------- 내부
    def _plane_at(self, point):
        for plane in self.planes:
            if plane.geo.contains(point):
                return plane
        return None

    @staticmethod
    def _samples(center, low, high, count=SAMPLES, spread=SPREAD):
        """center 주변에 표본 위치를 흩뿌린다 (화면 밖은 제외)."""
        if high - low <= 2:
            return []
        step = max(1, (spread * 2) // max(1, count - 1))
        out = []
        for index in range(count):
            value = center - spread + index * step
            if low <= value < high:
                out.append(value)
        return out or [max(low, min(center, high - 1))]

    def _column_score(self, plane, x, rows):
        """세로 경계선다움 — x 열에서 좌우 밝기가 튀는 표본의 비율."""
        if x <= plane.geo.x() or x >= plane.geo.x() + plane.w:
            return 0.0
        px = x - plane.geo.x()
        hit = 0
        for y in rows:
            py = y - plane.geo.y()
            base = py * plane.bpl
            if abs(plane.data[base + px] - plane.data[base + px - 1]) >= DIFF:
                hit += 1
        return hit / float(len(rows)) if rows else 0.0

    def _row_score(self, plane, y, cols):
        """가로 경계선다움."""
        if y <= plane.geo.y() or y >= plane.geo.y() + plane.h:
            return 0.0
        py = y - plane.geo.y()
        base = py * plane.bpl
        prev = base - plane.bpl
        hit = 0
        for x in cols:
            px = x - plane.geo.x()
            if abs(plane.data[base + px] - plane.data[prev + px]) >= DIFF:
                hit += 1
        return hit / float(len(cols)) if cols else 0.0

    # ------------------------------------------------- 1. 상자 자동 인식
    def box_at(self, point: QPoint):
        """point 를 감싸는 사각형을 찾는다. 못 찾으면 None.

        두 단계로 한다. 먼저 마우스 바로 주변만 좁게 보고 대충 잡은 뒤,
        잡힌 상자의 변을 따라 표본을 다시 뿌려 '변 전체가 정말 테두리인지'
        확인하며 좁혀 간다. 가로와 세로를 번갈아 고치기 때문에
        작은 아이콘도 잡히고, 글자 덩어리를 테두리로 오해하지도 않는다.
        """
        plane = self._plane_at(point)
        if plane is None:
            return None
        geo = plane.geo
        rows = self._samples(point.y(), geo.y() + 1, geo.y() + plane.h - 1,
                             spread=TIGHT_SPREAD)
        cols = self._samples(point.x(), geo.x() + 1, geo.x() + plane.w - 1,
                             spread=TIGHT_SPREAD)
        left, right = self._walk_h(plane, point, rows)
        top, bottom = self._walk_v(plane, point, cols)
        if None in (left, right, top, bottom):
            return None

        for _ in range(REFINE_PASSES):
            previous = (left, right, top, bottom)
            rows = self._even_samples(top + 1, bottom - 1,
                                      geo.y() + 1, geo.y() + plane.h - 1)
            if rows:
                new_left, new_right = self._walk_h(plane, point, rows)
                if new_left is not None and new_right is not None \
                        and new_right - new_left >= 2:
                    left, right = new_left, new_right
            cols = self._even_samples(left + 1, right - 1,
                                      geo.x() + 1, geo.x() + plane.w - 1)
            if cols:
                new_top, new_bottom = self._walk_v(plane, point, cols)
                if new_top is not None and new_bottom is not None \
                        and new_bottom - new_top >= 2:
                    top, bottom = new_top, new_bottom
            if (left, right, top, bottom) == previous:
                break

        # 텅 빈 색 덩어리를 잡았으면 한 겹씩 바깥으로 넓혀 본다
        for _ in range(GROW_PASSES):
            if not self._is_blank_box(plane, left, right, top, bottom):
                break
            grown = self._grow(plane, left, right, top, bottom)
            if grown is None:
                break
            left, right, top, bottom = grown

        rect = QRect(QPoint(left, top), QPoint(right - 1, bottom - 1))
        if rect.width() < MIN_BOX or rect.height() < MIN_BOX:
            return None
        # 넓히고도 여전히 텅 비어 있으면 쓸모없는 후보다
        if self._is_blank_box(plane, left, right, top, bottom):
            return None
        # 모니터를 거의 다 덮으면 '칸'이라고 할 수 없다
        if rect.width() >= geo.width() * HUGE_RATIO and \
                rect.height() >= geo.height() * HUGE_RATIO:
            return None
        return rect

    # ------------------------------------------- 텅 빈 상자인지 판정
    def _is_blank_box(self, plane, left, right, top, bottom):
        """넓은데 안쪽이 거의 한 가지 색이면 True (사진 여백·빈 공간)."""
        width = right - left
        height = bottom - top
        if width < FLAT_MIN_W and height < FLAT_MIN_H:
            return False              # 작은 상자는 단색이어도 버튼일 수 있다
        return self.flatness(plane, left, right, top, bottom) >= FLAT_RATIO

    def flatness(self, plane, left, right, top, bottom):
        """상자 안쪽에서 가장 흔한 밝기가 차지하는 비율 (0~1)."""
        geo = plane.geo
        inner_left = max(geo.x(), left + 2)
        inner_right = min(geo.x() + plane.w - 1, right - 2)
        inner_top = max(geo.y(), top + 2)
        inner_bottom = min(geo.y() + plane.h - 1, bottom - 2)
        if inner_right - inner_left < 2 or inner_bottom - inner_top < 2:
            return 0.0
        xs = self._even_samples(inner_left, inner_right,
                                geo.x(), geo.x() + plane.w, count=FLAT_GRID)
        ys = self._even_samples(inner_top, inner_bottom,
                                geo.y(), geo.y() + plane.h, count=FLAT_GRID)
        if not xs or not ys:
            return 0.0
        counts = {}
        total = 0
        for y in ys:
            base = (y - geo.y()) * plane.bpl
            for x in xs:
                value = plane.data[base + (x - geo.x())]
                bucket = value // (FLAT_TOLERANCE + 1)
                counts[bucket] = counts.get(bucket, 0) + 1
                total += 1
        if not total:
            return 0.0
        return max(counts.values()) / float(total)

    def _grow(self, plane, left, right, top, bottom):
        """텅 빈 상자를 한 겹 넓힌다.

        가로만 · 세로만 · 양쪽 세 가지를 모두 만들어 보고, 그중
        '내용이 들어 있으면서 가장 작은' 것을 고른다. 네 변을 한꺼번에
        밀면 가로가 이미 딱 맞는데도 더 벌어져서 화면 전체로 튀어 버린다.
        """
        geo = plane.geo
        current = (left, right, top, bottom)

        rows = self._even_samples(top + 1, bottom - 1,
                                  geo.y() + 1, geo.y() + plane.h - 1)
        wider = None
        if rows:
            new_left = self._walk(lambda x: self._column_score(plane, x, rows),
                                  left - 1, -1, geo.x() + 1)
            new_right = self._walk(lambda x: self._column_score(plane, x, rows),
                                   right + 1, 1, geo.x() + plane.w - 1)
            if new_left is not None and new_right is not None:
                wider = (new_left, new_right)

        cols = self._even_samples(left + 1, right - 1,
                                  geo.x() + 1, geo.x() + plane.w - 1)
        taller = None
        if cols:
            new_top = self._walk(lambda y: self._row_score(plane, y, cols),
                                 top - 1, -1, geo.y() + 1)
            new_bottom = self._walk(lambda y: self._row_score(plane, y, cols),
                                    bottom + 1, 1, geo.y() + plane.h - 1)
            if new_top is not None and new_bottom is not None:
                taller = (new_top, new_bottom)

        candidates = []
        if taller:
            candidates.append((left, right, taller[0], taller[1]))
        if wider:
            candidates.append((wider[0], wider[1], top, bottom))
        if wider and taller:
            candidates.append((wider[0], wider[1], taller[0], taller[1]))
        candidates = [c for c in candidates if c != current]
        if not candidates:
            return None

        filled = [c for c in candidates if not self._is_blank_box(plane, *c)]
        pool = filled or candidates
        return min(pool, key=lambda c: (c[1] - c[0]) * (c[3] - c[2]))

    def _walk_h(self, plane, point, rows):
        geo = plane.geo
        left = self._walk(lambda x: self._column_score(plane, x, rows),
                          point.x(), -1, geo.x() + 1)
        right = self._walk(lambda x: self._column_score(plane, x, rows),
                           point.x() + 1, 1, geo.x() + plane.w - 1)
        return left, right

    def _walk_v(self, plane, point, cols):
        geo = plane.geo
        top = self._walk(lambda y: self._row_score(plane, y, cols),
                         point.y(), -1, geo.y() + 1)
        bottom = self._walk(lambda y: self._row_score(plane, y, cols),
                            point.y() + 1, 1, geo.y() + plane.h - 1)
        return top, bottom

    @staticmethod
    def _even_samples(low, high, limit_low, limit_high, count=SAMPLES):
        """상자 안쪽에 고르게 표본을 뿌린다 (변 전체가 테두리인지 보려고)."""
        low = max(low, limit_low)
        high = min(high, limit_high - 1)
        if high - low < 2:
            return []
        if high - low < count:
            return list(range(low, high + 1))
        step = (high - low) / float(count - 1)
        return [int(round(low + step * i)) for i in range(count)]

    @staticmethod
    def _walk(score, start, direction, limit):
        """경계선이 나올 때까지 한쪽 방향으로 나아간다."""
        value = start
        steps = 0
        while steps < MAX_WALK:
            if direction < 0 and value <= limit:
                return limit
            if direction > 0 and value >= limit:
                return limit
            if score(value) >= AGREE:
                return value
            value += direction
            steps += 1
        return None

    # ---------------------------------------------------- 2. 테두리 흡착
    def snap_rect(self, rect: QRect, enabled=True) -> QRect:
        """선택 사각형의 네 변을 가까운 경계선에 붙인다."""
        if not enabled or rect.width() < 4 or rect.height() < 4:
            return rect
        plane = self._plane_at(rect.center())
        if plane is None:
            return rect
        geo = plane.geo
        rows = self._samples(rect.center().y(), max(geo.y() + 1, rect.top()),
                             min(geo.y() + plane.h - 1, rect.bottom()),
                             spread=max(6, rect.height() // 3))
        cols = self._samples(rect.center().x(), max(geo.x() + 1, rect.left()),
                             min(geo.x() + plane.w - 1, rect.right()),
                             spread=max(6, rect.width() // 3))
        if not rows or not cols:
            return rect

        left = self._best(lambda x: self._column_score(plane, x, rows),
                          rect.left(), geo.x() + 1, geo.x() + plane.w - 1)
        right = self._best(lambda x: self._column_score(plane, x, rows),
                           rect.right() + 1, geo.x() + 1,
                           geo.x() + plane.w - 1)
        top = self._best(lambda y: self._row_score(plane, y, cols),
                         rect.top(), geo.y() + 1, geo.y() + plane.h - 1)
        bottom = self._best(lambda y: self._row_score(plane, y, cols),
                            rect.bottom() + 1, geo.y() + 1,
                            geo.y() + plane.h - 1)
        new = QRect(QPoint(left if left is not None else rect.left(),
                           top if top is not None else rect.top()),
                    QPoint((right - 1) if right is not None else rect.right(),
                           (bottom - 1) if bottom is not None else rect.bottom()))
        if new.width() < 2 or new.height() < 2:
            return rect
        return new

    @staticmethod
    def _best(score, target, low, high):
        """target 근처에서 가장 뚜렷한 경계선 위치. 없으면 None."""
        best = None
        best_score = AGREE
        for offset in range(0, SNAP_RANGE + 1):
            for value in ({target - offset, target + offset}
                          if offset else {target}):
                if not (low <= value < high):
                    continue
                current = score(value)
                if current > best_score:
                    best_score = current
                    best = value
            if best is not None:
                return best          # 가까운 것을 우선한다
        return best
