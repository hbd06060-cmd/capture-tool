# -*- coding: utf-8 -*-
"""QR 코드 읽기 — 외부 라이브러리 없이 캡처 속 QR 코드를 해석한다.

화면에 뜬 QR 코드는 사진과 달리 반듯하고(기울어짐·원근 없음) 선명하다.
그래서 모서리의 네모 세 개(위치 표시)만 찾으면 나머지 칸의 자리를
곧바로 계산할 수 있다. 흐리거나 일부가 가려져도 QR 에 들어 있는
오류 정정 정보(Reed-Solomon)로 복원한다.

decode(QImage) -> 글자 (못 찾으면 None)
"""
import re
from itertools import combinations

from PySide6.QtGui import QImage

# ---------------------------------------------------------- GF(256) 계산
_EXP = [0] * 512
_LOG = [0] * 256
_value = 1
for _i in range(255):
    _EXP[_i] = _value
    _LOG[_value] = _i
    _value <<= 1
    if _value & 0x100:
        _value ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _div(a, b):
    if a == 0:
        return 0
    return _EXP[(_LOG[a] - _LOG[b]) % 255]


def rs_remainder(data, nsym):
    """오류 정정 부호 계산 (시험용 QR 을 만들 때 쓴다)."""
    gen = [1]
    for i in range(nsym):
        nxt = [0] * (len(gen) + 1)
        for j, coef in enumerate(gen):
            nxt[j] ^= coef
            nxt[j + 1] ^= _mul(coef, _EXP[i])
        gen = nxt
    out = list(data) + [0] * nsym
    for i in range(len(data)):
        coef = out[i]
        if coef:
            for j in range(1, len(gen)):
                out[i + j] ^= _mul(gen[j], coef)
    return out[len(data):]


def rs_correct(block, nsym):
    """오류가 섞인 블록을 바로잡아 돌려준다. 못 고치면 None.

    block[0] 이 가장 높은 차수. 생성 다항식의 근은 α^0 … α^(nsym-1).
    """
    n = len(block)
    msg = list(block)
    synd = []
    for j in range(nsym):
        x = _EXP[j]
        y = 0
        for coef in msg:
            y = _mul(y, x) ^ coef
        synd.append(y)
    if not any(synd):
        return msg

    # Berlekamp-Massey — 오류 위치 다항식 Λ(x) (낮은 차수부터)
    lam = [1]
    prev = [1]
    length = 0
    gap = 1
    last = 1
    for step in range(nsym):
        delta = synd[step]
        for i in range(1, length + 1):
            if i < len(lam):
                delta ^= _mul(lam[i], synd[step - i])
        if delta == 0:
            gap += 1
            continue
        coef = _div(delta, last)
        grown = lam + [0] * max(0, len(prev) + gap - len(lam))
        for i, value in enumerate(prev):
            grown[i + gap] ^= _mul(coef, value)
        if 2 * length <= step:
            prev = lam
            length = step + 1 - length
            last = delta
            gap = 1
        else:
            gap += 1
        lam = grown
    while len(lam) > 1 and lam[-1] == 0:
        lam.pop()
    if length * 2 > nsym or len(lam) - 1 != length:
        return None

    # Chien 탐색 — Λ(X⁻¹) = 0 인 자리
    positions = []
    for degree in range(n):
        x_inv = _EXP[(255 - degree) % 255]
        y = 0
        for coef in reversed(lam):
            y = _mul(y, x_inv) ^ coef
        if y == 0:
            positions.append(degree)
    if len(positions) != length:
        return None

    # Forney — 오류 크기
    omega = [0] * nsym
    for i, s in enumerate(synd):
        if not s:
            continue
        for j, l in enumerate(lam):
            if i + j < nsym:
                omega[i + j] ^= _mul(s, l)
    for degree in positions:
        x = _EXP[degree % 255]
        x_inv = _EXP[(255 - degree) % 255]
        num = 0
        for coef in reversed(omega):
            num = _mul(num, x_inv) ^ coef
        den = 0
        for i in range(1, len(lam), 2):          # 형식적 미분 (홀수 차수만)
            term = lam[i]
            for _ in range(i - 1):
                term = _mul(term, x_inv)
            den ^= term
        if den == 0:
            return None
        msg[n - 1 - degree] ^= _mul(x, _div(num, den))
    # 다시 확인
    for j in range(nsym):
        x = _EXP[j]
        y = 0
        for coef in msg:
            y = _mul(y, x) ^ coef
        if y:
            return None
    return msg


# ------------------------------------------------------------ QR 규격 표
# 순서: L, M, Q, H
ECC_PER_BLOCK = [
    [-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28,
     30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30,
     30, 30, 30, 30, 30],
    [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28,
     26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28,
     28, 28, 28, 28, 28],
    [-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28,
     28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30,
     30, 30, 30, 30, 30],
    [-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28,
     28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30,
     30, 30, 30, 30, 30],
]
NUM_BLOCKS = [
    [-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9,
     10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25],
    [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16,
     17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45,
     47, 49],
    [-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20,
     23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62,
     65, 68],
    [-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25,
     25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74,
     77, 81],
]
FORMAT_ECL = {1: 0, 0: 1, 3: 2, 2: 3}      # 형식 정보 2비트 -> 표의 순서
ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"


def raw_modules(version):
    result = (16 * version + 128) * version + 64
    if version >= 2:
        count = version // 7 + 2
        result -= (25 * count - 10) * count - 55
        if version >= 7:
            result -= 36
    return result


def alignment_positions(version):
    if version == 1:
        return []
    count = version // 7 + 2
    size = version * 4 + 17
    step = (version * 8 + count * 3 + 5) // (count * 4 - 4) * 2
    result = [size - 7 - i * step for i in range(count - 1)] + [6]
    return list(reversed(result))


def data_codewords(version, ecl):
    return raw_modules(version) // 8 - \
        ECC_PER_BLOCK[ecl][version] * NUM_BLOCKS[ecl][version]


def format_bits(ecl_bits, mask):
    """형식 정보 15비트 (오류 정정 2비트 + 마스크 3비트 + BCH)."""
    data = (ecl_bits << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | rem) ^ 0x5412


_FORMATS = [(format_bits(e, m), e, m) for e in range(4) for m in range(8)]


def format_positions(size):
    """형식 정보 두 벌의 칸 자리 [(x, y)] × 15 (비트 0 부터)."""
    first = [(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)] + \
        [(14 - i, 8) for i in range(9, 15)]
    second = [(size - 1 - i, 8) for i in range(8)] + \
        [(8, size - 15 + i) for i in range(8, 15)]
    return first, second


def function_map(version):
    """데이터가 아닌 칸(위치 표시·타이밍·정렬·형식·버전 정보) 표."""
    size = version * 4 + 17
    grid = [[False] * size for _ in range(size)]

    def fill(x0, y0, w, h):
        for y in range(max(0, y0), min(size, y0 + h)):
            for x in range(max(0, x0), min(size, x0 + w)):
                grid[y][x] = True

    fill(0, 0, 9, 9)
    fill(size - 8, 0, 8, 9)
    fill(0, size - 8, 9, 8)
    fill(0, 6, size, 1)
    fill(6, 0, 1, size)
    positions = alignment_positions(version)
    last = len(positions) - 1
    for i, cy in enumerate(positions):
        for j, cx in enumerate(positions):
            if (i == 0 and j == 0) or (i == 0 and j == last) or \
                    (i == last and j == 0):
                continue
            fill(cx - 2, cy - 2, 5, 5)
    if version >= 7:
        fill(size - 11, 0, 3, 6)
        fill(0, size - 11, 6, 3)
    return grid


def mask_at(mask, x, y):
    if mask == 0:
        return (x + y) % 2 == 0
    if mask == 1:
        return y % 2 == 0
    if mask == 2:
        return x % 3 == 0
    if mask == 3:
        return (x + y) % 3 == 0
    if mask == 4:
        return (x // 3 + y // 2) % 2 == 0
    if mask == 5:
        return x * y % 2 + x * y % 3 == 0
    if mask == 6:
        return (x * y % 2 + x * y % 3) % 2 == 0
    return ((x + y) % 2 + x * y % 3) % 2 == 0


def zigzag(size):
    """데이터 칸을 읽는 순서 [(x, y)] (함수 칸 포함, 호출한 쪽에서 거른다)."""
    order = []
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        upward = ((right + 1) & 2) == 0
        for vert in range(size):
            y = size - 1 - vert if upward else vert
            for j in range(2):
                order.append((right - j, y))
        right -= 2
    return order


def block_layout(version, ecl):
    """블록마다 (데이터 길이, 오류 정정 길이)."""
    blocks = NUM_BLOCKS[ecl][version]
    ecc = ECC_PER_BLOCK[ecl][version]
    raw = raw_modules(version) // 8
    short = blocks - raw % blocks
    short_len = raw // blocks
    return [(short_len - ecc + (0 if i < short else 1), ecc)
            for i in range(blocks)]


# ------------------------------------------------------------ 해석
class _Bits:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def left(self):
        return len(self.data) * 8 - self.pos

    def read(self, count):
        value = 0
        for _ in range(count):
            byte = self.data[self.pos >> 3]
            value = (value << 1) | ((byte >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return value


def _count_bits(mode, version):
    group = 0 if version <= 9 else (1 if version <= 26 else 2)
    return {1: (10, 12, 14), 2: (9, 11, 13), 4: (8, 16, 16),
            8: (8, 10, 12)}[mode][group]


def parse_payload(data, version):
    """데이터 코드워드에서 글자를 꺼낸다."""
    bits = _Bits(data)
    raw = bytearray()
    charset = None
    while bits.left() >= 4:
        mode = bits.read(4)
        if mode == 0:
            break
        if mode == 7:                          # ECI (문자 코드 지정)
            first = bits.read(8)
            if first & 0x80 == 0:
                value = first
            elif first & 0xC0 == 0x80:
                value = ((first & 0x3F) << 8) | bits.read(8)
            else:
                value = ((first & 0x1F) << 16) | bits.read(16)
            charset = {26: "utf-8", 20: "shift_jis", 3: "latin-1",
                       4: "latin-1", 30: "euc-kr"}.get(value, charset)
            continue
        if mode in (5, 9):                     # FNC1 — 무시
            if mode == 9:
                bits.read(8)
            continue
        if mode == 3:                          # 구조적 연결 — 무시
            bits.read(16)
            continue
        if mode not in (1, 2, 4, 8):
            break
        count = bits.read(_count_bits(mode, version))
        if mode == 1:
            while count >= 3:
                raw += b"%03d" % bits.read(10)
                count -= 3
            if count == 2:
                raw += b"%02d" % bits.read(7)
            elif count == 1:
                raw += b"%d" % bits.read(4)
        elif mode == 2:
            while count >= 2:
                value = bits.read(11)
                raw += (ALNUM[value // 45] + ALNUM[value % 45]).encode()
                count -= 2
            if count:
                raw += ALNUM[bits.read(6)].encode()
        elif mode == 4:
            if bits.left() < count * 8:
                return None
            for _ in range(count):
                raw.append(bits.read(8))
        else:
            for _ in range(count):
                value = bits.read(13)
                code = ((value // 0xC0) << 8) | (value % 0xC0)
                code += 0x8140 if code + 0x8140 <= 0x9FFC else 0xC140
                try:
                    raw += bytes([code >> 8, code & 0xFF]).decode(
                        "shift_jis").encode("utf-8")
                except Exception:
                    raw += b"?"
    if not raw:
        return None
    for name in [charset, "utf-8", "cp949", "latin-1"]:
        if not name:
            continue
        try:
            return bytes(raw).decode(name)
        except Exception:
            continue
    return None


def decode_matrix(grid):
    """칸 표(grid[y][x], True=검정) 에서 글자를 꺼낸다. 실패하면 None."""
    size = len(grid)
    if size < 21 or (size - 17) % 4:
        return None
    version = (size - 17) // 4
    if not 1 <= version <= 40:
        return None

    first, second = format_positions(size)
    best = None
    for spots in (first, second):
        value = 0
        for i, (x, y) in enumerate(spots):
            if grid[y][x]:
                value |= 1 << i
        for code, ecl_bits, mask in _FORMATS:
            distance = bin(code ^ value).count("1")
            if best is None or distance < best[0]:
                best = (distance, ecl_bits, mask)
    if best is None or best[0] > 3:
        return None
    _distance, ecl_bits, mask = best
    ecl = FORMAT_ECL[ecl_bits]

    functions = function_map(version)
    total = raw_modules(version) // 8
    codewords = []
    current = 0
    count = 0
    for x, y in zigzag(size):
        if functions[y][x]:
            continue
        bit = grid[y][x] ^ mask_at(mask, x, y)
        current = (current << 1) | int(bit)
        count += 1
        if count == 8:
            codewords.append(current)
            current = 0
            count = 0
            if len(codewords) == total:
                break
    if len(codewords) < total:
        return None

    layout = block_layout(version, ecl)
    blocks = [[] for _ in layout]
    index = 0
    for i in range(max(d for d, _e in layout)):
        for b, (dlen, _e) in enumerate(layout):
            if i < dlen:
                blocks[b].append(codewords[index])
                index += 1
    for i in range(layout[0][1]):
        for b in range(len(layout)):
            blocks[b].append(codewords[index])
            index += 1

    data = []
    for block, (dlen, ecc) in zip(blocks, layout):
        fixed = rs_correct(block, ecc)
        if fixed is None:
            return None
        data.extend(fixed[:dlen])
    try:
        return parse_payload(bytes(data), version)
    except Exception:
        return None


# ------------------------------------------------------------ 찾기
_RUNS = re.compile(rb"\x00+|\x01+")


def _binarize(image: QImage):
    """(가로, 세로, 0/1 바이트열). 1 이 어두운 칸."""
    img = image.convertToFormat(QImage.Format_RGB32)
    width, height = img.width(), img.height()
    try:
        raw = bytes(img.constBits())
    except Exception:
        raw = bytes(img.bits())
    stride = img.bytesPerLine()
    if stride != width * 4:
        rows = [raw[y * stride:y * stride + width * 4] for y in range(height)]
        raw = b"".join(rows)
    green = raw[1::4]
    # Otsu 방식으로 밝고 어두운 경계값을 정한다 (띄엄띄엄 표본만)
    sample = green[::max(1, len(green) // 200000)]
    hist = [0] * 256
    for value in sample:
        hist[value] += 1
    total = len(sample)
    sum_all = sum(i * h for i, h in enumerate(hist))
    sum_b = 0
    weight_b = 0
    best_t, best_var = 128, -1.0
    for t in range(256):
        weight_b += hist[t]
        if weight_b == 0:
            continue
        weight_f = total - weight_b
        if weight_f == 0:
            break
        sum_b += t * hist[t]
        mean_b = sum_b / weight_b
        mean_f = (sum_all - sum_b) / weight_f
        var = weight_b * weight_f * (mean_b - mean_f) ** 2
        if var > best_var:
            best_var, best_t = var, t
    table = bytes(1 if i <= best_t else 0 for i in range(256))
    return width, height, green.translate(table)


def _runs(line):
    return [(m.start(), m.end() - m.start(), line[m.start()])
            for m in _RUNS.finditer(line)]


def _ratio_ok(lengths):
    total = sum(lengths)
    if total < 7:
        return None
    module = total / 7.0
    limit = module / 2.0
    if abs(module - lengths[0]) >= limit or abs(module - lengths[1]) >= limit \
            or abs(3 * module - lengths[2]) >= 3 * limit \
            or abs(module - lengths[3]) >= limit \
            or abs(module - lengths[4]) >= limit:
        return None
    return module


def _cross(line, center, dark=1):
    """line 에서 center 를 지나는 1:1:3:1:1 무늬를 찾아 (중심, 칸 크기)."""
    if not 0 <= center < len(line) or line[center] != dark:
        return None
    runs = []
    start = center
    while start > 0 and line[start - 1] == dark:
        start -= 1
    end = center
    while end + 1 < len(line) and line[end + 1] == dark:
        end += 1
    mid = (start, end - start + 1)
    # 가운데 검정 양옆으로 두 덩어리씩
    left = []
    pos = start - 1
    for want in (1 - dark, dark):
        run_end = pos
        while pos >= 0 and line[pos] == want:
            pos -= 1
        if run_end == pos:
            return None
        left.append(run_end - pos)
    right = []
    pos = end + 1
    for want in (1 - dark, dark):
        run_start = pos
        while pos < len(line) and line[pos] == want:
            pos += 1
        if run_start == pos:
            return None
        right.append(pos - run_start)
    runs = [left[1], left[0], mid[1], right[0], right[1]]
    module = _ratio_ok(runs)
    if module is None:
        return None
    return mid[0] + mid[1] / 2.0, module


def _find_candidates(width, height, bits):
    step = 1 if height <= 1400 else 2
    hits = []
    for y in range(0, height, step):
        line = bits[y * width:(y + 1) * width]
        if 1 not in line:
            continue
        runs = _runs(line)
        for i in range(len(runs) - 4):
            if runs[i][2] != 1:
                continue
            lengths = [runs[i + k][1] for k in range(5)]
            module = _ratio_ok(lengths)
            if module is None:
                continue
            cx = runs[i + 2][0] + runs[i + 2][1] / 2.0
            hits.append((cx, y, module))

    groups = []                       # [cx 합, y 목록, module 합, 개수]
    for cx, y, module in hits:
        for group in groups:
            gx = group[0] / group[3]
            gm = group[2] / group[3]
            if abs(cx - gx) <= max(1.5, gm) and \
                    abs(y - group[1][-1]) <= step * 2 + 1 and \
                    abs(module - gm) <= gm * 0.5:
                group[0] += cx
                group[1].append(y)
                group[2] += module
                group[3] += 1
                break
        else:
            groups.append([cx, [y], module, 1])

    found = []
    for sum_x, ys, sum_m, count in groups:
        cx = sum_x / count
        module = sum_m / count
        if count < max(1, int(module * 3 / step * 0.35)):
            continue
        cy = (ys[0] + ys[-1]) // 2
        column = bits[int(cx)::width]
        vertical = _cross(column, cy)
        if vertical is None:
            continue
        cy, module_v = vertical
        if abs(module_v - module) > module * 0.5:
            continue
        row = bits[int(cy) * width:(int(cy) + 1) * width]
        horizontal = _cross(row, int(cx))
        if horizontal is not None:
            cx, module_h = horizontal
            module = (module_h + module_v) / 2.0
        else:
            module = (module + module_v) / 2.0
        found.append((cx, cy, module, count))

    # 거의 같은 자리의 후보는 하나로
    merged = []
    for cand in sorted(found, key=lambda c: -c[3]):
        if all(abs(cand[0] - m[0]) > m[2] * 3 or abs(cand[1] - m[1]) > m[2] * 3
               for m in merged):
            merged.append(cand)
    return merged[:14]


def _sample_grid(width, height, bits, tl, tr, bl, size):
    grid = []
    span = float(size - 7)
    ux = (tr[0] - tl[0]) / span
    uy = (tr[1] - tl[1]) / span
    vx = (bl[0] - tl[0]) / span
    vy = (bl[1] - tl[1]) / span
    for my in range(size):
        row = []
        for mx in range(size):
            px = tl[0] + (mx - 3.0) * ux + (my - 3.0) * vx
            py = tl[1] + (mx - 3.0) * uy + (my - 3.0) * vy
            x = int(px)
            y = int(py)
            if 0 <= x < width and 0 <= y < height:
                row.append(bits[y * width + x] == 1)
            else:
                row.append(False)
        grid.append(row)
    return grid


def _try_triples(width, height, bits, candidates):
    tried = 0
    for a, b, c in combinations(candidates, 3):
        modules = [a[2], b[2], c[2]]
        if max(modules) > min(modules) * 1.5:
            continue
        points = [a, b, c]
        best = None
        for corner in range(3):
            p = points[corner]
            q = points[(corner + 1) % 3]
            r = points[(corner + 2) % 3]
            d1 = ((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2) ** 0.5
            d2 = ((r[0] - p[0]) ** 2 + (r[1] - p[1]) ** 2) ** 0.5
            d3 = ((q[0] - r[0]) ** 2 + (q[1] - r[1]) ** 2) ** 0.5
            if d1 < 1 or d2 < 1:
                continue
            if abs(d1 - d2) > max(d1, d2) * 0.15:
                continue
            if abs(d3 - (d1 * d1 + d2 * d2) ** 0.5) > d3 * 0.08:
                continue
            best = (p, q, r, (d1 + d2) / 2.0)
            break
        if best is None:
            continue
        p, q, r, leg = best
        cross = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        tl = (p[0], p[1])
        tr, bl = ((q[0], q[1]), (r[0], r[1])) if cross > 0 else \
            ((r[0], r[1]), (q[0], q[1]))
        module = sum(modules) / 3.0
        estimate = leg / module + 7
        version = int(round((estimate - 17) / 4.0))
        tried += 1
        for guess in (version, version - 1, version + 1):
            if not 1 <= guess <= 40:
                continue
            size = guess * 4 + 17
            grid = _sample_grid(width, height, bits, tl, tr, bl, size)
            text = decode_matrix(grid)
            if text:
                return text
        if tried > 40:
            break
    return None


MAX_SIDE = 2600


def decode(image: QImage):
    """이미지 속 QR 코드 하나를 읽어 글자로 돌려준다. 없으면 None."""
    if image is None or image.isNull():
        return None
    img = image
    longest = max(img.width(), img.height())
    if longest > MAX_SIDE:
        from PySide6.QtCore import Qt
        img = img.scaled(MAX_SIDE, MAX_SIDE, Qt.KeepAspectRatio,
                         Qt.FastTransformation)
    width, height, bits = _binarize(img)
    for current in (bits, bits.translate(bytes([1, 0]) + bytes(254))):
        candidates = _find_candidates(width, height, current)
        if len(candidates) >= 3:
            text = _try_triples(width, height, current, candidates)
            if text:
                return text
    return None
