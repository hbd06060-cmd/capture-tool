# -*- coding: utf-8 -*-
"""여러 장의 캡처를 세로로 이어붙인다 (스크롤 캡처용).

각 줄(row)을 짧은 지문(signature)으로 바꾼 뒤 지문 배열을 비교해
두 장이 몇 픽셀 겹치는지 찾는다. 외부 라이브러리 없이 동작한다.
"""
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter

MIN_MATCH_ROWS = 24   # 겹친다고 인정할 최소 줄 수
QUANT = 0xF8          # 하위 3비트를 버려 미세한 차이를 무시
MATCH_RATIO = 0.70    # 이 비율 이상 줄이 같으면 겹친 것으로 본다
SAMPLE_TARGET = 160   # 한 번 판정할 때 훑어볼 줄 수
MIN_VARIETY = 5       # 맞은 줄들이 이만큼은 서로 달라야 (빈 화면 오인 방지)
HINT_RANGE = 26       # 앞 장에서 찾은 이동폭 근처를 먼저 살펴볼 범위
GOOD_ENOUGH = 0.92    # 근처에서 이만큼 맞으면 전체를 다시 훑지 않는다
NO_SCROLL = 0.98      # 밀지 않아도 이만큼 같으면 더 안 내려간 것으로 본다
MIN_OVERLAP_RATIO = 0.12  # 두 장이 최소 이만큼은 겹쳐야 이어붙인다
STILL_RATIO = 0.985   # 이만큼 같으면 화면이 멈춘 것으로 본다


_QUANT_TABLE = bytes(i & QUANT for i in range(256))
SCROLLBAR_MARGIN = 22     # 오른쪽 끝(스크롤바 자리)은 비교에서 뺀다


def row_signatures(image: QImage):
    """이미지의 각 줄을 정수 지문으로 바꾼 목록.

    v1.9 까지는 한 줄에서 24개 점만 뽑아 봤다. 글자가 얇고 띄엄띄엄한
    화면에서는 그 점들이 거의 흰 바탕에 떨어져 서로 다른 줄이 똑같아
    보였고, 그래서 겹친 자리를 못 찾거나 엉뚱한 자리에 붙였다.
    이제는 줄 전체(스크롤바 자리 제외)를 지문으로 만든다.
    """
    if image.isNull():
        return []
    img = image.convertToFormat(QImage.Format_RGB32)
    width, height = img.width(), img.height()
    bpl = img.bytesPerLine()
    try:
        data = bytes(img.constBits())
    except Exception:
        data = bytes(img.bits())
    data = data.translate(_QUANT_TABLE)       # 미세한 차이는 무시
    right = width - SCROLLBAR_MARGIN if width > SCROLLBAR_MARGIN * 4 else width
    start, end = 0, right * 4
    return [hash(data[y * bpl + start:y * bpl + end]) for y in range(height)]


def static_bands(signature_list):
    """모든 장에서 똑같은 위쪽/아래쪽 띠의 높이 (고정 메뉴바·상태바).

    웹페이지는 스크롤을 시작해야 고정 메뉴바가 나타나는 경우가 많다.
    그래서 첫 장을 뺀 나머지로 먼저 재보고, 거기서 못 찾으면 전체로 본다.
    """
    if len(signature_list) >= 3:
        top, bottom = _bands(signature_list[1:])
        if top or bottom:
            return top, bottom
    return _bands(signature_list)


def _bands(signature_list):
    if len(signature_list) < 2:
        return 0, 0
    height = min(len(s) for s in signature_list)
    if height < 10:
        return 0, 0
    first = signature_list[0]
    top = 0
    while top < height // 3:
        value = first[top]
        if all(sig[top] == value for sig in signature_list[1:]):
            top += 1
        else:
            break
    bottom = 0
    while bottom < height // 3:
        value = first[height - 1 - bottom]
        if all(sig[len(sig) - 1 - bottom] == value
               for sig in signature_list[1:]):
            bottom += 1
        else:
            break
    # 한 줄짜리 우연한 일치는 무시
    return (top if top >= 3 else 0), (bottom if bottom >= 3 else 0)


def sticky_top(prev_sig, next_sig, shift):
    """스크롤을 따라 내려오지 않은 위쪽 띠의 높이 (따라다니는 메뉴바).

    static_bands 는 '모든 장에서 똑같은 줄' 만 고정 띠로 본다. 그런데
    반투명 메뉴바(뒤로 내용이 비침), 스크롤하면 생기는 그림자, 크기가
    바뀌는 메뉴바는 장마다 픽셀이 달라서 놓치고, 이어붙인 결과 중간에
    메뉴바가 다시 끼어든다.
    그래서 거꾸로 본다. shift 만큼 내려갔다면 새 장의 r 번째 줄은 앞 장의
    r + shift 번째 줄과 같아야 한다. 그렇지 않은 줄은 스크롤을 따라오지
    않은 것, 즉 고정 띠다.
    """
    height = min(len(prev_sig), len(next_sig))
    if shift <= 0 or shift >= height:
        return 0
    limit = min(height // 3, height - shift)
    last = 0
    run = 0
    for r in range(limit):
        if next_sig[r] != prev_sig[r + shift]:
            last = r + 1
            run = 0
        else:
            run += 1
            # 메뉴바 안의 빈 줄은 우연히 맞을 수 있으니 조금 더 본다.
            # 한참 맞는 줄이 이어지면 거기부터는 내용이다.
            if run >= MIN_MATCH_ROWS:
                break
    return last if last >= 3 else 0


def _score(a, b, shift, step):
    """shift 만큼 밀었을 때 몇 %의 줄이 같은지."""
    overlap = len(a) - shift
    hit = 0
    total = 0
    for index in range(0, overlap, step):
        total += 1
        if a[shift + index] == b[index]:
            hit += 1
    if total == 0:
        return 0.0, 0
    return hit / float(total), total


def _variety(a, b, shift, step):
    """맞은 줄들이 얼마나 서로 다른지 (빈 화면에서 헛맞는 것을 막는다)."""
    overlap = len(a) - shift
    seen = set()
    # 건너뛰며 세면 글자가 드문 구간에서 종류가 모자라 보이므로 모든 줄을 본다
    for index in range(0, overlap):
        value = b[index]
        if a[shift + index] == value:
            seen.add(value)
            if len(seen) >= MIN_VARIETY:
                break
    return len(seen)


def similarity(a_sig, b_sig):
    """두 장이 줄 단위로 얼마나 같은지 (0~1). 화면이 멈췄는지 볼 때 쓴다.

    픽셀 하나까지 똑같아야 '멈췄다' 고 보면 깜빡이는 커서나 움직이는
    광고 하나 때문에 영영 멈추지 않은 것으로 판단하게 된다.
    """
    height = min(len(a_sig), len(b_sig))
    if height == 0:
        return 0.0
    step = max(1, height // (SAMPLE_TARGET * 2))
    total = hit = 0
    for index in range(0, height, step):
        total += 1
        if a_sig[index] == b_sig[index]:
            hit += 1
    return hit / float(total)


def find_shift(prev_sig, next_sig, top=0, bottom=0, min_shift=1, hint=None,
               max_shift=None):
    """prev 대비 next 가 몇 픽셀 내려갔는지 찾는다. 못 찾으면 None.

    줄이 전부 똑같아야 한다고 보면 고정 메뉴바나 스크롤바 하나에도
    실패하므로, 일정 비율 이상만 맞으면 겹친 것으로 인정한다.
    max_shift 를 주면 그보다 많이 내려간 경우는 보지 않는다
    (겹친 부분이 너무 적으면 우연히 맞는 것을 막기 위해).
    """
    height = min(len(prev_sig), len(next_sig))
    usable_end = height - bottom
    if usable_end - top < MIN_MATCH_ROWS * 2:
        return None
    a = prev_sig[top:usable_end]
    b = next_sig[top:usable_end]
    limit = len(a)
    step = max(1, limit // SAMPLE_TARGET)
    # 겹친 부분이 최소한 이만큼은 있어야 믿는다
    min_overlap = max(MIN_MATCH_ROWS, int(limit * MIN_OVERLAP_RATIO))
    upper = limit - min_overlap
    if max_shift is not None:
        upper = min(upper, int(max_shift) + 1)

    # 아예 안 내려간 경우를 먼저 걸러낸다 (바닥에 닿았을 때)
    same_score, _total = _score(a, b, 0, step)
    if same_score >= NO_SCROLL:
        return 0

    state = {"shift": None, "score": 0.0}

    def consider(shift):
        score, total = _score(a, b, shift, step)
        if total < 6:
            return False
        if score > state["score"] + 1e-9:
            state["score"] = score
            state["shift"] = shift
        return score >= 0.995

    def accept():
        shift = state["shift"]
        if shift is None:
            return None
        if _variety(a, b, shift, step) < MIN_VARIETY:
            return None        # 거의 빈 화면 — 믿을 수 없다
        return shift

    # 같은 휠 조작이므로 앞 장에서 찾은 폭 근처일 가능성이 높다
    if hint:
        for delta in range(0, HINT_RANGE):
            for shift in ({hint - delta, hint + delta} if delta
                          else {hint}):
                if min_shift <= shift < upper:
                    if consider(shift):
                        return _prefer_still(accept(), state["score"],
                                             same_score)
        if state["score"] >= GOOD_ENOUGH:
            # 여기서 끝내면 전체를 훑지 않아도 된다
            return _prefer_still(accept(), state["score"], same_score)

    for shift in range(min_shift, upper):
        if consider(shift):
            return _prefer_still(accept(), state["score"], same_score)

    if state["score"] < MATCH_RATIO:
        # 어느 쪽으로도 잘 안 맞지만 거의 그대로라면 '안 내려감' 이다
        return 0 if same_score >= STILL_WHEN_UNMATCHED else None
    return _prefer_still(accept(), state["score"], same_score)


STILL_WHEN_UNMATCHED = 0.9


def _prefer_still(shift, best_score, same_score):
    """깜빡이는 커서처럼 몇 줄만 바뀐 화면을 '내려갔다' 로 착각하지 않게.

    제자리(0) 로 볼 때 맞는 정도가, 내려갔다고 볼 때보다 못하지 않으면
    내려가지 않은 것으로 본다.
    """
    if shift is None:
        return 0 if same_score >= STILL_WHEN_UNMATCHED else None
    if same_score >= STILL_WHEN_UNMATCHED and same_score >= best_score - 0.01:
        return 0
    return shift


def compose(frames, shifts, top=0, bottom=0) -> QImage:
    """frames[0] 을 기준으로 shifts 만큼씩 내려 이어붙인다."""
    frames = [f for f in frames if f is not None and not f.isNull()]
    if not frames:
        return QImage()
    if len(frames) == 1:
        return frames[0]
    width = frames[0].width()
    height = frames[0].height()
    total = height + sum(shifts)
    total = min(total, 32000)
    out = QImage(width, total, QImage.Format_RGB32)
    out.fill(Qt.white)
    painter = QPainter(out)
    painter.drawImage(0, 0, frames[0])
    y = 0
    for index in range(1, len(frames)):
        y += shifts[index - 1]
        frame = frames[index]
        # 고정된 위쪽 띠는 다시 그리지 않는다
        src_top = top if top else 0
        src = QRect(0, src_top, width, frame.height() - src_top)
        if y + src_top >= total:
            break
        painter.drawImage(0, y + src_top, frame, src.x(), src.y(),
                          src.width(), min(src.height(), total - y - src_top))
    painter.end()
    if bottom and len(frames) > 1:
        # 마지막 장의 고정 아래 띠는 그대로 두고, 중간 장들의 것은 이미 덮였다
        pass
    return out


class Stitcher:
    """한 장 찍을 때마다 바로 맞춰 보며 이어붙인다.

    예전에는 다 찍은 뒤에 한꺼번에 맞춰 봤다. 그러면 중간에 한 장이
    안 맞았을 때, 그다음 장은 '두 칸 떨어진 장' 과 비교하게 되어
    겹치는 부분이 없으니 뒤로 줄줄이 실패했다. 이제는 맞지 않는 장을
    그 자리에서 알려 주므로, 찍는 쪽이 덜 굴려서 다시 찍을 수 있다.
    """

    FIRST, OK, SAME, FAIL = "first", "ok", "same", "fail"

    def __init__(self):
        self.frames = []          # 이어붙이기에 쓰는 장들
        self.signatures = []
        self.shifts = []          # frames[i+1] 이 frames[i] 보다 내려간 픽셀
        self.hint = None
        self.top = 0              # 지금까지 짐작한 고정 띠 (위 / 아래)
        self.bottom = 0
        self.sticky = 0           # 스크롤을 따라오지 않은 위쪽 띠 (sticky_top)
        self.failures = 0

    def __len__(self):
        return len(self.frames)

    def usable_height(self):
        if not self.frames:
            return 0
        return max(1, self.frames[-1].height() - self.top - self.bottom)

    def compare(self, image, signature=None):
        """마지막 장과 비교만 한다. (상태, 이동폭, 지문)"""
        sig = signature if signature is not None else row_signatures(image)
        if not self.frames:
            return self.FIRST, 0, sig
        prev = self.signatures[-1]
        if len(sig) != len(prev):
            return self.FAIL, None, sig
        top, bottom = _bands([prev, sig])
        # 두 장만 보고 짐작한 띠는 넉넉하게 잡힐 수 있어 절반만 믿는다
        top = max(self.top, top // 2)
        bottom = max(self.bottom, bottom // 2)
        shift = find_shift(prev, sig, top, bottom, hint=self.hint)
        if shift is None:
            # 따라다니는 메뉴바가 처음 나타났거나 반투명이면 위 띠를 못 찾아
            # 메뉴바 줄들이 비교를 망친다. 위쪽을 더 빼고 다시 맞춰 본다.
            height = len(sig)
            for guess in (height // 8, height // 5):
                if guess > top:
                    shift = find_shift(prev, sig, guess, bottom,
                                       hint=self.hint)
                    if shift is not None:
                        break
        if shift is None:
            return self.FAIL, None, sig
        if shift <= 0:
            return self.SAME, 0, sig
        return self.OK, shift, sig

    def add(self, image, signature=None):
        """맞으면 붙이고 (상태, 이동폭) 을 돌려준다."""
        status, shift, sig = self.compare(image, signature)
        if status == self.FIRST:
            self.frames.append(image)
            self.signatures.append(sig)
        elif status == self.OK:
            self.sticky = max(self.sticky,
                              sticky_top(self.signatures[-1], sig, shift))
            self.frames.append(image)
            self.signatures.append(sig)
            self.shifts.append(shift)
            self.hint = shift
            if len(self.signatures) >= 3:
                self.top, self.bottom = static_bands(self.signatures)
            self.top = self._safe_top(max(self.top, self.sticky), self.bottom)
        elif status == self.FAIL:
            self.failures += 1
        return status, shift

    def _safe_top(self, top, bottom):
        """위 띠를 빼고도 장과 장 사이에 빈틈이 생기지 않게 줄인다.

        각 장은 [top, 높이) 부분만 그려지므로, 가장 많이 내려간 폭이
        그 부분보다 크면 사이에 흰 틈이 생긴다.
        """
        if not self.frames:
            return top
        height = self.frames[-1].height()
        widest = max(self.shifts) if self.shifts else 0
        return max(0, min(top, height - bottom - widest - 1))

    def offset_of_last(self):
        return sum(self.shifts)

    def result(self):
        if not self.frames:
            return QImage(), {"frames": 0, "shifts": [], "failures": 0}
        if len(self.frames) == 1:
            return self.frames[0], {"frames": 1, "shifts": [], "top": 0,
                                    "bottom": 0, "failures": self.failures}
        top, bottom = static_bands(self.signatures)
        top = self._safe_top(max(top, self.sticky), bottom)
        image = compose(self.frames, self.shifts, top, bottom)
        return image, {"frames": len(self.frames),
                       "shifts": list(self.shifts), "top": top,
                       "bottom": bottom, "failures": self.failures}


def stitch(frames):
    """캡처 목록을 이어붙인 이미지와 진단 정보를 돌려준다."""
    frames = [f for f in frames if f is not None and not f.isNull()]
    if not frames:
        return QImage(), {"frames": 0}
    stitcher = Stitcher()
    for frame in frames:
        stitcher.add(frame)
    return stitcher.result()
