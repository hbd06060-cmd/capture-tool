# -*- coding: utf-8 -*-
"""개인정보 자동 찾기 — 글자 인식 결과에서 가려야 할 자리를 고른다.

찾는 것: 이메일 · 휴대폰 · 전화번호 · 주민(외국인)등록번호 · 카드번호
글자 인식은 '010 1234 5678' 처럼 한 번호를 여러 단어로 쪼개기도 해서
이웃한 단어를 붙여 가며 맞춰 본다.
"""
import re

from PySide6.QtCore import QRect

PATTERNS = [
    # '@' 앞은 글자 인식이 o 를 '이' 로 읽는 일도 있어 넉넉하게 받는다
    ("이메일", re.compile(r"[^\s@:：]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")),
    ("주민등록번호", re.compile(
        r"\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])-?[1-8]\d{6}")),
    ("카드번호", re.compile(r"(?:\d{4}-?){3}\d{4}")),
    ("휴대폰", re.compile(r"01[016789][-.]?\d{3,4}[-.]?\d{4}")),
    ("전화번호", re.compile(r"0(?:2|[3-6][1-5]|70|50\d)[-.)]?\d{3,4}[-.]?\d{4}")),
]

MAX_JOIN = 5            # 최대 몇 단어까지 붙여서 볼지
_EDGE = "()[]{}<>,:;'\"·"


def _clean(text):
    return text.replace(" ", "").strip(_EDGE)


def _union(boxes):
    left = min(b[0] for b in boxes)
    top = min(b[1] for b in boxes)
    right = max(b[0] + b[2] for b in boxes)
    bottom = max(b[1] + b[3] for b in boxes)
    return left, top, right - left, bottom - top


def _to_rect(box, image_size=None):
    x, y, w, h = box
    pad_x = 3
    pad_y = max(2, h * 0.18)
    rect = QRect(int(x - pad_x), int(y - pad_y),
                 int(w + pad_x * 2 + 1), int(h + pad_y * 2 + 1))
    if image_size is not None:
        rect = rect.intersected(QRect(0, 0, image_size[0], image_size[1]))
    return rect


def _kind_of(text):
    for kind, pattern in PATTERNS:
        if pattern.fullmatch(text):
            return kind
    return None


# 글자 인식은 'hong@example.com' 을 'hong@example' 과 'com' 으로 쪼개고
# 사이의 점을 빠뜨리기도 한다. '@' 가 든 단어로 시작할 때만 점으로 이어 본다.
_EMAIL_DOTTED = re.compile(r"[^\s@:：]+@[A-Za-z0-9-]{2,}(?:\.[A-Za-z0-9-]{2,})+")


def _kind_of_words(texts):
    joined = _clean("".join(texts))
    kind = _kind_of(joined)
    if kind:
        return kind
    if len(texts) > 1 and "@" in texts[0]:
        dotted = _clean(".".join(t.strip(".") for t in texts))
        if _EMAIL_DOTTED.fullmatch(dotted):
            return "이메일"
    return None


def find(lines, image_size=None):
    """[(종류, QRect)] — lines 는 ocr.WordsTask 결과."""
    found = []
    for words in lines:
        count = len(words)
        index = 0
        while index < count:
            matched = None
            # 길게 붙인 것부터 — 가장 넓게 맞는 것을 고른다
            for end in range(min(count, index + MAX_JOIN) - 1, index - 1, -1):
                kind = _kind_of_words([w[0] for w in words[index:end + 1]])
                if kind:
                    boxes = [w[1] for w in words[index:end + 1]]
                    matched = (kind, _union(boxes), end)
                    break
            if matched:
                kind, box, end = matched
                found.append((kind, _to_rect(box, image_size)))
                index = end + 1
                continue
            # 한 단어 안에 섞여 있는 경우 ('연락처:010-1234-5678')
            text, (x, y, w, h) = words[index]
            for kind, pattern in PATTERNS:
                hit = pattern.search(text)
                if hit and len(text):
                    ratio = w / float(len(text))
                    box = (x + hit.start() * ratio, y,
                           (hit.end() - hit.start()) * ratio, h)
                    found.append((kind, _to_rect(box, image_size)))
                    break
            index += 1
    return found


def summary(found):
    counts = {}
    for kind, _rect in found:
        counts[kind] = counts.get(kind, 0) + 1
    return ", ".join("%s %d" % (k, v) for k, v in counts.items())
