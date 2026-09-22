# -*- coding: utf-8 -*-
"""영역 선택 오버레이 — 화면을 고정시킨 뒤 드래그로 범위를 지정한다."""
from PySide6.QtCore import (QObject, QPoint, QRect, QSize, Qt, QTimer,
                            Signal)
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetrics, QGuiApplication,
                           QImage, QPainter, QPen)
from PySide6.QtWidgets import (QHBoxLayout, QPushButton, QToolTip, QWidget)

import regions
import winutil
from capture import ScreenShot
from config import CONFIG

HANDLE = 8          # 조절점 크기(논리 픽셀)
MIN_SIZE = 4

# 화면에 띄우는 단축키 안내 (배그식 키 안내판)
HINTS_BEFORE = [
    (["드래그"], "캡처할 영역 지정 (경계선에 자동으로 붙습니다)"),
    (["클릭"], "파랗게 잡힌 영역 그대로 캡처"),
    (["휠"], "잡는 단위 바꾸기 (칸 ↔ 내용 영역 ↔ 창 전체)"),
    (["Ctrl"], "누르고 있는 동안 창 전체 (Ctrl+Shift 모니터 전체)"),
    (["Alt"], "누르고 있으면 경계선에 붙지 않음"),
    (["Shift"], "누르고 있으면 정사각형"),
    (["방향키"], "마우스를 1픽셀씩 옮기기 (Shift 10픽셀)"),
    (["Space"], "마우스 없이 시작점·끝점 찍기"),
    (["C"], "마우스 아래 색 코드 복사 (Shift+C 는 RGB)"),
    ([",", "."], "예전에 잡았던 영역 다시 불러오기"),
    (["R"], "화면 자 켜기/끄기 — 빈 공간의 가로·세로 길이"),
    (["Tab"], "핀 · 전역 단축키 안내 보기"),
    (["Esc"], "취소   ·   F1 이 안내 켜기/끄기"),
]

HINTS_AFTER = [
    (["Enter"], "기본 동작 실행"),
    (["E"], "편집창에서 열기"),
    (["P"], "화면에 핀 고정"),
    (["T"], "글자 추출"),
    (["Q"], "QR 코드 읽기"),
    (["S"], "스크롤 캡처 (긴 화면 이어붙이기)"),
    (["Ctrl", "C"], "복사"),
    (["Ctrl", "S"], "저장"),
    (["방향키"], "1픽셀 이동 (Shift 10픽셀 · Alt 크기 조절)"),
    ([",", "."], "예전에 잡았던 영역으로 바꾸기"),
    (["Tab"], "핀 · 전역 단축키 안내 보기"),
    (["Esc"], "다시 선택"),
]

# Tab 으로 넘겨 보는 두 번째 안내 — 캡처 화면 밖에서 쓰는 키들
HINTS_PIN = [
    (["휠"], "핀 크기 · Ctrl+휠 투명도 · 가운데 버튼 원래대로"),
    (["L"], "핀 잠금 · F 작게 접기 · 1~4 회전·뒤집기"),
    (["T", "Q"], "핀 이미지에서 글자 추출 / QR 읽기"),
    (["Ctrl", "끌기"], "핀을 카톡·메일 창에 바로 놓기"),
    (["더블클릭"], "핀 복사 · Shift+더블클릭 작게 접기"),
]


def _key_caps(hotkey):
    """'Ctrl+Shift+Print' → ['Ctrl', 'Shift', 'PrtSc']"""
    names = {"PRINT": "PrtSc", "PRINTSCREEN": "PrtSc"}
    return [names.get(part.upper(), part)
            for part in (hotkey or "").split("+") if part]


def global_hint_rows():
    from config import HOTKEY_LABELS
    rows = []
    for action, label in HOTKEY_LABELS.items():
        if action == "region":
            continue
        keys = CONFIG.hotkey(action)
        if keys:
            rows.append((_key_caps(keys), label))
    rows.extend(HINTS_PIN)
    rows.append((["Tab"], "캡처 안내로 돌아가기"))
    return rows

HINTS_FIXED = [
    (["마우스"], "정해진 크기의 틀을 옮깁니다"),
    (["클릭"], "이 자리로 확정"),
    (["방향키"], "1픽셀씩 옮기기"),
    (["Esc"], "취소"),
]

LEVEL_NAMES = {0: "칸", 1: "창 전체", 2: "모니터 전체"}
STEP_NAMES = regions.NAMES

HINT_FADE_MS = 4000
HINT_FADED = 0.34

_BAR_STYLE = """
QWidget#actionbar {
    background: rgba(32, 34, 38, 235);
    border: 1px solid rgba(255,255,255,40);
    border-radius: 8px;
}
QPushButton {
    color: #f2f3f5;
    background: transparent;
    border: none;
    padding: 7px 11px;
    font-size: 13px;
    border-radius: 6px;
}
QPushButton:hover { background: rgba(255,255,255,32); }
QPushButton:pressed { background: rgba(255,255,255,60); }
QPushButton#primary { color: #7cc4ff; font-weight: 600; }
QPushButton#cancel { color: #ff9a9a; }
"""


class SelectionOverlay(QWidget):
    """전체 가상 화면을 덮는 선택 창.

    completed(QImage, str action) — action: edit / pin / ocr / copy / save / default
    cancelled()
    """

    completed = Signal(QImage, str)
    cancelled = Signal()

    def __init__(self, shot: ScreenShot, show_action_bar=True,
                 fixed_size=None, parent=None, screen_geo=None):
        """screen_geo 를 주면 그 모니터 하나만 맡는다.

        모니터마다 배율(125% / 100% 등)이 다를 수 있는데, 창 하나에는
        배율을 하나밖에 줄 수 없다. 그래서 배율이 섞인 환경에서는
        모니터마다 창을 따로 띄워야 각자 제 배율로 그려진다.
        """
        super().__init__(parent)
        self.shot = shot
        self.show_action_bar = show_action_bar
        self.fixed_size = fixed_size
        self._placing = fixed_size is not None
        self.screen_geo = QRect(screen_geo) if screen_geo is not None \
            else QRect(shot.virtual_rect)
        self.origin = self.screen_geo.topLeft()
        self.group = None              # 여러 모니터를 묶어 주는 관리자

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                            Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setGeometry(self.screen_geo)

        self.selection = QRect()        # 위젯 좌표
        self._dragging = False
        self._drag_start = QPoint()
        self._moving = False
        self._move_offset = QPoint()
        self._resize_handle = None
        self._finished_selection = False
        self._window_rects = []
        self._hover_rect = None
        self._closing = False
        self.edges = None
        self._hover_level = 0          # 0 칸 · 1 창 · 2 모니터 (Ctrl 용)
        self._hover_from = None        # 마지막으로 계산한 마우스 위치
        self.ladder = None             # 단위영역 층 정보
        self._foreign = QRect()        # 옆 모니터에서 끌고 있는 선택 영역
        self._step_offset = 0          # 휠로 옮긴 층 (기본 위치에서 몇 칸)
        self._hover_kind = regions.BOX  # 지금 잡고 있는 것의 종류
        self._step_count = 1           # 이 자리에서 고를 수 있는 층 수
        self._ruler = False            # 화면 자 켜짐
        self._measure = None           # 마지막으로 잰 결과
        self._measure_from = None
        self._hint_page = 0            # 0 캡처 안내 · 1 핀·전역 단축키
        self._message = ""             # 화면 위쪽에 잠깐 띄우는 안내
        self._message_timer = QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.timeout.connect(self._clear_message)

        self.bar = self._build_bar()
        self.bar.hide()

        self._hint_opacity = 1.0
        self._hint_visible = self._decide_hint_visible()
        self._fade_timer = QTimer(self)
        self._fade_timer.setSingleShot(True)
        self._fade_timer.timeout.connect(self._fade_hint)
        if self._hint_visible and CONFIG["hint_mode"] == "fade":
            self._fade_timer.start(HINT_FADE_MS)

    # ------------------------------------------------------- 단축키 안내
    def _decide_hint_visible(self):
        mode = CONFIG["hint_mode"]
        if mode == "off":
            return False
        if mode == "count":
            shown = int(CONFIG["hint_shown"] or 0)
            limit = int(CONFIG["hint_count_limit"] or 20)
            if shown >= limit:
                return False
            CONFIG["hint_shown"] = shown + 1
            CONFIG.save()
        return True

    def _refresh_hint(self):
        if not self._hint_visible:
            return
        self._hint_opacity = 1.0
        if CONFIG["hint_mode"] == "fade":
            self._fade_timer.start(HINT_FADE_MS)

    def _fade_hint(self):
        self._hint_opacity = HINT_FADED
        self.update()

    def _toggle_hint(self):
        if not self._hint_visible:
            self._hint_visible = True
            self._hint_opacity = 1.0
        elif self._hint_opacity < 1.0:
            self._hint_opacity = 1.0
        else:
            self._hint_visible = False
        if self._hint_visible and CONFIG["hint_mode"] == "fade":
            self._fade_timer.start(HINT_FADE_MS)
        self.update()

    def _toggle_hint_page(self):
        """Tab — 캡처 안내 ↔ 핀·전역 단축키 안내."""
        self._hint_page = 0 if self._hint_page else 1
        self._hint_visible = True
        self._hint_opacity = 1.0
        if CONFIG["hint_mode"] == "fade":
            self._fade_timer.start(HINT_FADE_MS * 2)
        self.update()

    def focusNextPrevChild(self, forward):
        return False                 # Tab 을 안내 넘기기에 쓴다

    # ------------------------------------------------------- 짧은 안내
    def show_message(self, text, ms=1600):
        self._message = text
        self._message_timer.start(ms)
        self.update()

    def _clear_message(self):
        self._message = ""
        self.update()

    def _draw_message(self, painter):
        if not self._message:
            return
        screen_rect = self.rect()          # 창 하나가 모니터 하나를 맡는다
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        metrics = QFontMetrics(font)
        width = metrics.horizontalAdvance(self._message) + 36
        height = metrics.height() + 18
        box = QRect(screen_rect.center().x() - width // 2,
                    screen_rect.top() + 28, width, height)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor(255, 255, 255, 50), 1))
        painter.setBrush(QColor(16, 18, 23, 232))
        painter.drawRoundedRect(box, 9, 9)
        painter.setFont(font)
        painter.setPen(QColor(240, 244, 250))
        painter.drawText(box, Qt.AlignCenter, self._message)
        painter.restore()

    # ------------------------------------------------------- 색 코드 복사
    def _copy_color(self, as_rgb=False):
        point = QCursor.pos()
        color = self.shot.pixel_at(point)
        if color is None:
            return
        if as_rgb:
            text = "%d, %d, %d" % (color.red(), color.green(), color.blue())
        else:
            text = color.name().upper()
        QGuiApplication.clipboard().setText(text)
        try:
            import toast
            toast.show("%s 복사했습니다" % text, color=QColor(color), at=point)
        except Exception:
            pass
        self._cancel()

    # ------------------------------------------------------- 영역 기록
    def _step_history(self, delta):
        """쉼표(,) 는 더 예전 영역, 마침표(.) 는 더 최근 영역."""
        history = CONFIG.region_history()
        if not history:
            self.show_message("아직 기록된 영역이 없습니다")
            return
        if self.group is not None:
            self.group.step_history(delta, history)
            return
        index = max(-1, min(len(history) - 1,
                            getattr(self, "_history_index", -1) + delta))
        self._history_index = index
        self.apply_history(index, history)

    def apply_history(self, index, history):
        if index < 0:
            self._reset_selection()
            self.show_message("예전 영역 불러오기를 그만뒀습니다")
            return
        x, y, w, h = history[index]
        rect = self._to_widget_rect(QRect(x, y, w, h)).intersected(self.rect())
        if rect.width() < MIN_SIZE or rect.height() < MIN_SIZE:
            return
        self._placing = False
        self._dragging = False
        self.selection = rect
        self._finish_selection()
        self.show_message("예전 영역 %d / %d   ( , 더 예전 · . 최근 )"
                          % (index + 1, len(history)))

    # ------------------------------------------------------------ 화면 자
    def _toggle_ruler(self):
        self._ruler = not self._ruler
        self._measure = None
        self._measure_from = None
        self.show_message("화면 자 켬 — 같은 색이 이어지는 길이를 잽니다 (R 로 끄기)"
                          if self._ruler else "화면 자 끔", 1800)
        self.update()

    def _update_measure(self, pos):
        if self._measure_from is not None and self._measure_from == pos:
            return
        self._measure_from = QPoint(pos)
        try:
            self._measure = self.shot.measure_at(pos + self.origin)
        except Exception:
            self._measure = None

    def _draw_ruler(self, painter):
        pos = self.mapFromGlobal(QCursor.pos())
        if not self.rect().contains(pos):
            return
        self._update_measure(pos)
        found = self._measure
        if not found:
            return
        ox, oy = self.origin.x(), self.origin.y()
        left = found["left"] - ox
        right = found["right"] - ox
        top = found["top"] - oy
        bottom = found["bottom"] - oy
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, False)
        accent = QColor(255, 64, 129)
        painter.setPen(QPen(accent, 1))
        y = pos.y()
        x = pos.x()
        painter.drawLine(int(left), y, int(right) - 1, y)
        painter.drawLine(x, int(top), x, int(bottom) - 1)
        for cap_x in (int(left), int(right) - 1):
            painter.drawLine(cap_x, y - 5, cap_x, y + 5)
        for cap_y in (int(top), int(bottom) - 1):
            painter.drawLine(x - 5, cap_y, x + 5, cap_y)
        text = "↔ %d   ↕ %d  px" % (found["width"], found["height"])
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        w = metrics.horizontalAdvance(text) + 14
        h = metrics.height() + 8
        box = QRect(x - w - 12, y - h - 12, w, h)
        if box.left() < 2:
            box.moveLeft(x + 12)
        if box.top() < 2:
            box.moveTop(y + 12)
        painter.setPen(Qt.NoPen)
        painter.setBrush(accent)
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(box, Qt.AlignCenter, text)
        painter.restore()

    # ------------------------------------------------------------ 준비
    def prepare(self, exclude_hwnds=()):
        try:
            self._window_rects = [self._to_widget_rect(r)
                                  for r in self.shot.window_rects(exclude_hwnds)]
        except Exception:
            self._window_rects = []
        self._build_ladder(exclude_hwnds)
        if self.fixed_size is not None:
            self._place_fixed(self.mapFromGlobal(QCursor.pos()))
        if CONFIG["auto_region"] or CONFIG["snap_edges"]:
            try:
                from edges import ScreenEdges
                self.edges = ScreenEdges(self.shot)
            except Exception:
                self.edges = None

    def _build_bar(self):
        bar = QWidget(self)
        bar.setObjectName("actionbar")
        bar.setStyleSheet(_BAR_STYLE)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)
        buttons = [
            ("편집", "edit", "primary", "선택 영역을 편집창에서 엽니다 (Enter)"),
            ("핀 고정", "pin", "", "화면 위에 항상 띄워 둡니다"),
            ("글자 추출", "ocr", "", "이미지 속 글자를 텍스트로 뽑습니다 (T)"),
            ("QR", "qr", "", "QR 코드를 읽습니다 (Q)"),
            ("스크롤", "scroll", "", "아래로 스크롤하며 여러 장을 이어붙입니다 (S)"),
            ("복사", "copy", "", "클립보드에만 복사합니다 (Ctrl+C)"),
            ("저장", "save", "", "파일로 바로 저장합니다 (Ctrl+S)"),
            ("취소", "cancel", "cancel", "선택을 취소합니다 (Esc)"),
        ]
        for text, action, obj, tip in buttons:
            btn = QPushButton(text, bar)
            if obj:
                btn.setObjectName(obj)
            btn.setToolTip(tip)
            btn.setCursor(Qt.ArrowCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.clicked.connect(lambda _=False, a=action: self._bar_clicked(a))
            layout.addWidget(btn)
        bar.adjustSize()
        return bar

    # -------------------------------------------------------- 좌표 변환
    def _to_widget_rect(self, rect: QRect) -> QRect:
        return QRect(rect.topLeft() - self.origin, rect.size())

    def _to_screen_rect(self, rect: QRect) -> QRect:
        return QRect(rect.topLeft() + self.origin, rect.size())

    # --------------------------------------------------- 크기지정 캡처
    def _place_fixed(self, center):
        """정해진 크기의 틀을 마우스 위치로 옮긴다."""
        size = self.fixed_size
        rect = QRect(0, 0, min(size.width(), self.width()),
                     min(size.height(), self.height()))
        rect.moveCenter(center)
        rect.moveLeft(max(0, min(rect.left(), self.width() - rect.width())))
        rect.moveTop(max(0, min(rect.top(), self.height() - rect.height())))
        self.selection = rect

    # ------------------------------------------- 흡착 · 비율 · 자동 인식
    @staticmethod
    def _rect_from_points(a: QPoint, b: QPoint) -> QRect:
        """두 점을 감싸는 사각형. 끄는 방향과 무관하게 크기가 같다."""
        x1, x2 = (a.x(), b.x()) if a.x() <= b.x() else (b.x(), a.x())
        y1, y2 = (a.y(), b.y()) if a.y() <= b.y() else (b.y(), a.y())
        return QRect(QPoint(x1, y1), QPoint(x2, y2))

    def _make_drag_rect(self, pos, modifiers):
        """드래그 중인 사각형 — Shift 정사각형, 아니면 경계선 흡착."""
        start = self._drag_start
        if modifiers & Qt.ShiftModifier:
            dx = pos.x() - start.x()
            dy = pos.y() - start.y()
            side = max(abs(dx), abs(dy))
            end = QPoint(start.x() + (side if dx >= 0 else -side),
                         start.y() + (side if dy >= 0 else -side))
            return self._rect_from_points(start, end)
        return self._snap(self._rect_from_points(start, pos), modifiers)

    def _snap(self, rect, modifiers):
        """네 변을 가까운 경계선에 붙인다. Alt 를 누르고 있으면 붙지 않는다."""
        if (self.edges is None or not CONFIG["snap_edges"]
                or (modifiers & Qt.AltModifier)):
            return rect
        try:
            snapped = self.edges.snap_rect(self._to_screen_rect(rect))
            return self._to_widget_rect(snapped)
        except Exception:
            return rect

    def _update_hover(self, pos):
        """마우스가 올라간 자리의 후보 영역을 갱신한다."""
        if self._hover_from is not None and \
                abs(pos.x() - self._hover_from.x()) < 3 and \
                abs(pos.y() - self._hover_from.y()) < 3:
            return
        self._hover_from = QPoint(pos)
        self._hover_rect = self._hover_candidate(pos)

    def _build_ladder(self, exclude_hwnds=()):
        """창마다 [전체 / 내용 영역] 을 미리 알아 둔다 (단위영역 캡처)."""
        self.ladder = None
        try:
            details = self.shot.window_details(exclude_hwnds)
        except Exception:
            details = []
        widget_details = []
        for item in details:
            try:
                widget_details.append({
                    "rect": self._to_widget_rect(item["rect"]),
                    "client": (self._to_widget_rect(item["client"])
                               if item.get("client") is not None else None),
                    "children": [self._to_widget_rect(c)
                                 for c in (item.get("children") or ())],
                    "title": item.get("title", ""),
                })
            except Exception:
                continue
        screens = []
        try:
            for geo, _image, _dpr in self.shot.shots:
                screens.append(self._to_widget_rect(geo))
        except Exception:
            pass
        if not screens:
            screens = [self.rect()]
        try:
            self.ladder = regions.RegionLadder.from_details(widget_details,
                                                            screens)
        except Exception:
            self.ladder = None

    def _ladder(self):
        """층 정보가 없으면 지금 아는 창 목록만으로라도 만들어 쓴다."""
        if self.ladder is not None and self.ladder.windows:
            return self.ladder
        details = [{"rect": rect} for rect in self._window_rects]
        screens = [self._current_screen_rect()]
        return regions.RegionLadder.from_details(details, screens)

    def _detect_box(self, pos):
        """화면 그림을 보고 마우스 자리의 '칸' 을 찾는다."""
        if self.edges is None or not CONFIG["auto_region"]:
            return None
        try:
            found = self.edges.box_at(pos + self.origin)
        except Exception:
            return None
        return self._to_widget_rect(found) if found is not None else None

    def _hover_candidate(self, pos):
        screen = self._current_screen_rect()
        ladder = self._ladder()
        if self._hover_level >= 2:            # Ctrl+Shift — 모니터 전체
            self._hover_kind = regions.SCREEN
            self._step_count = 1
            return screen
        if self._hover_level == 1:            # Ctrl — 창 전체
            layer = ladder.window_at(pos)
            self._hover_kind = regions.WINDOW
            self._step_count = 1
            return layer.rect if layer is not None else screen

        steps = ladder.ladder_at(pos, self._detect_box(pos))
        if not steps:
            self._hover_kind = regions.SCREEN
            self._step_count = 1
            return screen
        index = ladder.default_index(pos, steps) + self._step_offset
        rect, kind = regions.pick(steps, index)
        self._hover_kind = kind
        self._step_count = len(steps)
        return rect or screen

    def step_level(self, delta):
        """휠을 굴려 잡는 단위를 넓히거나 좁힌다."""
        if self._hover_level != 0:
            return False
        self._step_offset = max(-4, min(4, self._step_offset + delta))
        self._hover_from = None
        self._update_hover(self.mapFromGlobal(QCursor.pos()))
        self.update()
        return True

    def _move_cursor(self, dx, dy):
        """방향키로 마우스를 1픽셀씩 옮긴다 (드래그 전에도 동작)."""
        pos = self.mapFromGlobal(QCursor.pos())
        target = QPoint(max(0, min(pos.x() + dx, self.width() - 1)),
                        max(0, min(pos.y() + dy, self.height() - 1)))
        screen_point = target + self.origin
        moved = False
        if winutil.IS_WIN:
            px, py = self.shot.logical_to_physical_point(screen_point)
            moved = winutil.set_cursor_pos(px, py)
        if not moved:
            QCursor.setPos(screen_point)
        if self._dragging:
            self.selection = self._make_drag_rect(target, Qt.NoModifier)
        else:
            self._hover_from = None
            self._update_hover(target)
        self.update()

    def _toggle_keyboard_drag(self):
        """Space 로 시작점과 끝점을 찍어 마우스 없이 캡처한다."""
        pos = self.mapFromGlobal(QCursor.pos())
        if self._dragging:
            self._dragging = False
            self._finish_selection()
        else:
            self._dragging = True
            self._finished_selection = False
            self._drag_start = pos
            self.selection = QRect(pos, QSize(0, 0))
            self._hide_bar()
        self.update()

    def _apply_hover_level(self, modifiers, point=None):
        """Ctrl 을 누르고 있는 동안만 범위를 넓힌다."""
        if modifiers & Qt.ControlModifier:
            level = 2 if (modifiers & Qt.ShiftModifier) else 1
        else:
            level = 0
        if level == self._hover_level and self._hover_rect is not None:
            return
        self._hover_level = level
        self._hover_from = None
        if point is None:
            point = self.mapFromGlobal(QCursor.pos())
        self._update_hover(point)
        self.update()

    # 예전 이름 (검사용)
    def _cycle_hover_level(self, point=None):
        self._hover_level = (self._hover_level + 1) % 3
        self._hover_from = None
        if point is None:
            point = self.mapFromGlobal(QCursor.pos())
        self._update_hover(point)
        self.update()

    # -------------------------------------------------------------- 그리기
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # 1. 고정된 화면
        for geo, image, _dpr in self.shot.shots:
            painter.drawImage(self._to_widget_rect(geo), image)

        sel = self.selection.normalized()
        if sel.isEmpty() and not self._foreign.isEmpty():
            # 옆 모니터에서 끌고 있는 영역이 내 화면까지 걸쳐 있는 경우
            sel = self._foreign.normalized().intersected(self.rect())

        # 2. 어둡게 덮기
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))
        if sel.width() > 0 and sel.height() > 0:
            painter.save()
            painter.setClipRect(sel)
            for geo, image, _dpr in self.shot.shots:
                painter.drawImage(self._to_widget_rect(geo), image)
            painter.restore()

        # 3. 창/칸 자동 감지 표시 (화면 자를 켜면 자를 대신 보여 준다)
        if sel.isEmpty() and self._ruler and not self._dragging:
            self._draw_ruler(painter)
        elif sel.isEmpty() and self._hover_rect is not None:
            painter.setPen(QPen(QColor(90, 170, 255, 220), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self._hover_rect.adjusted(0, 0, -1, -1))
            self._draw_level_chip(painter, self._hover_rect)

        # 4. 선택 테두리 + 조절점
        if not sel.isEmpty():
            painter.setPen(QPen(QColor(60, 150, 255), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(sel.adjusted(0, 0, -1, -1))
            if self._finished_selection:
                painter.setPen(QPen(QColor(255, 255, 255), 1))
                painter.setBrush(QColor(60, 150, 255))
                for point in self._handle_points(sel).values():
                    painter.drawRect(QRect(point.x() - HANDLE // 2,
                                           point.y() - HANDLE // 2,
                                           HANDLE, HANDLE))
            self._draw_size_label(painter, sel)

        # 5. 단축키 안내 / 돋보기
        if self._hint_visible:
            self._draw_key_hints(painter)
        if not self._finished_selection:
            self._draw_magnifier(painter)
        self._draw_message(painter)
        painter.end()

    # ------------------------------------------------- 단축키 안내판 그리기
    def _hint_fonts(self):
        cap = QFont()
        cap.setPointSize(9)
        cap.setBold(True)
        desc = QFont()
        desc.setPointSize(10)
        return cap, desc

    def _hint_size(self, rows):
        cap_font, desc_font = self._hint_fonts()
        cap_metrics = QFontMetrics(cap_font)
        desc_metrics = QFontMetrics(desc_font)
        key_h = cap_metrics.height() + 9
        row_h = max(key_h, desc_metrics.height()) + 9
        keys_w = 0
        desc_w = 0
        for keys, text in rows:
            width = 0
            for key in keys:
                width += max(key_h, cap_metrics.horizontalAdvance(key) + 14) + 5
            keys_w = max(keys_w, width)
            desc_w = max(desc_w, desc_metrics.horizontalAdvance(text))
        pad = 16
        return (pad * 2 + keys_w + 14 + desc_w + 8,
                pad * 2 + row_h * len(rows) - 6,
                keys_w, row_h, key_h)

    def _hint_box(self, width, height):
        """선택 영역을 가리지 않는 구석을 고른다."""
        screen_rect = self._current_screen_rect()
        margin = 34
        corners = [
            QRect(screen_rect.left() + margin,
                  screen_rect.bottom() - height - margin, width, height),
            QRect(screen_rect.right() - width - margin,
                  screen_rect.bottom() - height - margin, width, height),
            QRect(screen_rect.left() + margin,
                  screen_rect.top() + margin, width, height),
            QRect(screen_rect.right() - width - margin,
                  screen_rect.top() + margin, width, height),
        ]
        sel = self.selection.normalized()
        blockers = [r for r in (sel, self.bar.geometry() if self.bar.isVisible()
                                else QRect()) if not r.isEmpty()]
        best = corners[0]
        best_overlap = None
        for box in corners:
            overlap = 0
            for blocker in blockers:
                inter = box.intersected(blocker)
                overlap += inter.width() * inter.height()
            if overlap == 0:
                return box
            if best_overlap is None or overlap < best_overlap:
                best_overlap = overlap
                best = box
        return best

    def _draw_key_hints(self, painter):
        if self._placing:
            rows = HINTS_FIXED
        elif self._hint_page == 1:
            rows = global_hint_rows()
        else:
            rows = HINTS_AFTER if self._finished_selection else HINTS_BEFORE
        width, height, keys_w, row_h, key_h = self._hint_size(rows)
        box = self._hint_box(width, height)
        cap_font, desc_font = self._hint_fonts()
        cap_metrics = QFontMetrics(cap_font)

        painter.save()
        painter.setOpacity(self._hint_opacity)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor(255, 255, 255, 34), 1))
        painter.setBrush(QColor(16, 18, 23, 224))
        painter.drawRoundedRect(box, 11, 11)

        pad = 16
        y = box.top() + pad
        for keys, text in rows:
            x = box.left() + pad
            for key in keys:
                key_w = max(key_h, cap_metrics.horizontalAdvance(key) + 14)
                cap = QRect(x, y + (row_h - 9 - key_h) // 2, key_w, key_h)
                painter.setPen(QPen(QColor(255, 255, 255, 92), 1))
                painter.setBrush(QColor(255, 255, 255, 26))
                painter.drawRoundedRect(cap, 4, 4)
                painter.setFont(cap_font)
                painter.setPen(QColor(238, 242, 250))
                painter.drawText(cap, Qt.AlignCenter, key)
                x += key_w + 5
            painter.setFont(desc_font)
            painter.setPen(QColor(184, 192, 208))
            painter.drawText(QRect(box.left() + pad + keys_w + 14, y,
                                   box.width(), row_h),
                             Qt.AlignLeft | Qt.AlignVCenter, text)
            y += row_h
        painter.restore()

    def _level_text(self):
        """지금 잡고 있는 단위 이름 (여러 층이 있으면 휠 안내도 함께)."""
        if self._hover_level >= 2:
            return LEVEL_NAMES[2]
        if self._hover_level == 1:
            return LEVEL_NAMES[1]
        name = STEP_NAMES.get(self._hover_kind, "칸")
        if self._step_count > 1:
            return "%s  (휠: 단위 바꾸기)" % name
        return name

    def wheelEvent(self, event):
        """휠로 잡는 단위를 바꾼다 — 칸 ↔ 내용 영역 ↔ 창 전체 ↔ 모니터."""
        if self._dragging or self.fixed_size is not None:
            event.ignore()
            return
        delta = event.angleDelta().y()
        if not delta:
            event.ignore()
            return
        # 위로 굴리면 넓게, 아래로 굴리면 좁게
        if self.step_level(1 if delta > 0 else -1):
            event.accept()
        else:
            event.ignore()

    def _draw_level_chip(self, painter, rect):
        """지금 무엇을 잡고 있는지 (칸 / 창 전체 / 모니터 전체)."""
        dpr = self._selection_dpr()
        text = "%s   %d × %d" % (self._level_text(),
                                 int(round(rect.width() * dpr)),
                                 int(round(rect.height() * dpr)))
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        width = metrics.horizontalAdvance(text) + 16
        height = metrics.height() + 8
        x = rect.left()
        y = rect.top() - height - 5
        if y < 2:
            y = rect.top() + 5
        x = max(2, min(x, self.width() - width - 2))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(30, 110, 220, 235))
        painter.drawRoundedRect(QRect(x, y, width, height), 4, 4)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(QRect(x, y, width, height), Qt.AlignCenter, text)

    def _draw_size_label(self, painter, sel):
        dpr = self._selection_dpr()
        text = "%d × %d" % (int(round(sel.width() * dpr)),
                            int(round(sel.height() * dpr)))
        font = QFont()
        font.setPointSize(10)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        pad = 6
        w = metrics.horizontalAdvance(text) + pad * 2
        h = metrics.height() + pad
        x = sel.left()
        y = sel.top() - h - 6
        if y < 4:
            y = min(sel.top() + 6, self.height() - h - 4)
        x = max(2, min(x, self.width() - w - 2))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 200))
        painter.drawRoundedRect(QRect(x, y, w, h), 4, 4)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(QRect(x, y, w, h), Qt.AlignCenter, text)

    def _draw_magnifier(self, painter):
        pos = self.mapFromGlobal(QCursor.pos())
        if not self.rect().contains(pos):
            return
        zoom = 6
        view_w, view_h = 130, 96
        src_w, src_h = view_w // zoom, view_h // zoom
        screen_point = pos + self.origin
        source = QRect(screen_point.x() - src_w // 2,
                       screen_point.y() - src_h // 2, src_w, src_h)
        patch = self.shot.image_for(source)
        if patch.isNull():
            return
        box = QRect(pos.x() + 18, pos.y() + 18, view_w, view_h + 36)
        if box.right() > self.width() - 4:
            box.moveLeft(pos.x() - box.width() - 18)
        if box.bottom() > self.height() - 4:
            box.moveTop(pos.y() - box.height() - 18)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(15, 16, 20, 235))
        painter.drawRoundedRect(box.adjusted(-3, -3, 3, 3), 6, 6)
        view = QRect(box.x(), box.y(), view_w, view_h)
        painter.save()
        painter.setClipRect(view)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        painter.drawImage(view, patch)
        painter.restore()
        painter.setPen(QPen(QColor(80, 200, 255, 200), 1))
        painter.drawLine(view.center().x(), view.top(),
                         view.center().x(), view.bottom())
        painter.drawLine(view.left(), view.center().y(),
                         view.right(), view.center().y())
        painter.setPen(QPen(QColor(255, 255, 255, 90), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(view.adjusted(0, 0, -1, -1))
        color = self.shot.pixel_at(screen_point)
        label = "%d, %d" % (screen_point.x(), screen_point.y())
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(QColor(225, 228, 235))
        painter.drawText(QRect(box.x(), view.bottom() + 2, view_w, 16),
                         Qt.AlignCenter, label)
        if color is not None:
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawRect(QRect(box.x() + 2, view.bottom() + 21, 10, 10))
            painter.setPen(QColor(225, 228, 235))
            painter.drawText(QRect(box.x() + 14, view.bottom() + 18, view_w - 14, 16),
                             Qt.AlignLeft | Qt.AlignVCenter,
                             "%s  ·  C 복사" % color.name().upper())

    # ------------------------------------------------------------ 조절점
    def _handle_points(self, sel):
        cx = sel.center().x()
        cy = sel.center().y()
        return {
            "tl": QPoint(sel.left(), sel.top()),
            "tr": QPoint(sel.right(), sel.top()),
            "bl": QPoint(sel.left(), sel.bottom()),
            "br": QPoint(sel.right(), sel.bottom()),
            "t": QPoint(cx, sel.top()),
            "b": QPoint(cx, sel.bottom()),
            "l": QPoint(sel.left(), cy),
            "r": QPoint(sel.right(), cy),
        }

    def _handle_at(self, pos):
        if not self._finished_selection or self.selection.isEmpty():
            return None
        for name, point in self._handle_points(self.selection.normalized()).items():
            if abs(point.x() - pos.x()) <= HANDLE and \
               abs(point.y() - pos.y()) <= HANDLE:
                return name
        return None

    _CURSORS = {"tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
                "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
                "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
                "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor}

    # ----------------------------------------------- 여러 모니터 연동
    def set_foreign_selection(self, global_rect):
        """옆 모니터에서 끌고 있는 영역을 내 화면 몫만 그려 준다."""
        if global_rect is None or global_rect.isEmpty():
            new = QRect()
        else:
            new = self._to_widget_rect(QRect(global_rect))
        if new == self._foreign:
            return
        self._foreign = new
        self.update()

    def _share_selection(self):
        """지금 끌고 있는 영역을 옆 모니터 창들에게도 알려 준다."""
        if self.group is None:
            return
        sel = self.selection.normalized()
        self.group.share_selection(
            self, self._to_screen_rect(sel) if not sel.isEmpty() else None)

    def _hold_pointer(self):
        """드래그 중에는 마우스를 붙잡아 옆 모니터까지 따라가게 한다."""
        if self.group is None:
            return
        try:
            self.grabMouse()
        except Exception:
            pass

    def _release_pointer(self):
        if self.group is None:
            return
        try:
            self.releaseMouse()
        except Exception:
            pass

    def enterEvent(self, event):
        # 마우스가 올라온 모니터의 창이 키 입력을 받아야 한다
        if self.group is not None:
            self.group.focus_on(self)
        super().enterEvent(event)

    # -------------------------------------------------------------- 입력
    def mousePressEvent(self, event):
        if self._placing and event.button() == Qt.LeftButton:
            self._placing = False
            self._finish_selection()
            return
        if event.button() == Qt.RightButton:
            if self._finished_selection or not self.selection.isEmpty():
                self._reset_selection()
            else:
                self._cancel()
            return
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()
        handle = self._handle_at(pos)
        if handle:
            self._resize_handle = handle
            self._hide_bar()
            return
        if self._finished_selection and \
                self.selection.normalized().contains(pos):
            self._moving = True
            self._move_offset = pos - self.selection.normalized().topLeft()
            self._hide_bar()
            self.setCursor(Qt.SizeAllCursor)
            return
        self._dragging = True
        self._finished_selection = False
        self._drag_start = pos
        self.selection = QRect(pos, QSize(0, 0))
        self._hide_bar()
        self._hold_pointer()
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self._placing:
            self._place_fixed(pos)
            self.update()
            return
        if self._dragging:
            self.selection = self._make_drag_rect(pos, event.modifiers())
            self._share_selection()          # 옆 모니터에도 보여 준다
            self.update()
            return
        if self._moving:
            sel = self.selection.normalized()
            new_top_left = pos - self._move_offset
            new_top_left.setX(max(0, min(new_top_left.x(),
                                         self.width() - sel.width())))
            new_top_left.setY(max(0, min(new_top_left.y(),
                                         self.height() - sel.height())))
            self.selection = QRect(new_top_left, sel.size())
            self.update()
            return
        if self._resize_handle:
            self._apply_resize(pos)
            self.selection = self._snap(self.selection.normalized(),
                                        event.modifiers())
            self.update()
            return
        if self._finished_selection:
            handle = self._handle_at(pos)
            if handle:
                self.setCursor(self._CURSORS[handle])
            elif self.selection.normalized().contains(pos):
                self.setCursor(Qt.SizeAllCursor)
            else:
                self.setCursor(Qt.CrossCursor)
            return
        # 선택 전: 칸 / 창 / 모니터 자동 감지
        self._update_hover(pos)
        self.update()

    def _apply_resize(self, pos):
        sel = self.selection.normalized()
        left, top, right, bottom = (sel.left(), sel.top(),
                                    sel.right(), sel.bottom())
        h = self._resize_handle
        if "l" in h or h == "tl" or h == "bl":
            left = pos.x()
        if "r" in h or h == "tr" or h == "br":
            right = pos.x()
        if h.startswith("t") or h == "t":
            top = pos.y()
        if h.startswith("b") or h == "b":
            bottom = pos.y()
        self.selection = self._rect_from_points(QPoint(left, top),
                                                QPoint(right, bottom))

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if self._dragging or self._moving or self._resize_handle:
            self._release_pointer()
        if self._dragging:
            self._dragging = False
            sel = self.selection.normalized()
            if sel.width() < MIN_SIZE or sel.height() < MIN_SIZE:
                # 클릭 = 창 캡처
                pos = event.position().toPoint()
                self._update_hover(pos)
                target = self._hover_rect
                if target is not None:
                    self.selection = QRect(target)
                else:
                    self.selection = QRect()
                    self.update()
                    return
            self._finish_selection()
            return
        if self._moving:
            self._moving = False
            self.setCursor(Qt.CrossCursor)
            self._finish_selection()
            return
        if self._resize_handle:
            self._resize_handle = None
            self._finish_selection()

    def mouseDoubleClickEvent(self, event):
        if self._finished_selection and \
                self.selection.normalized().contains(event.position().toPoint()):
            self._emit("default")

    def _finish_selection(self):
        sel = self.selection.normalized()
        if sel.width() < MIN_SIZE or sel.height() < MIN_SIZE:
            self._reset_selection()
            return
        self.selection = sel
        self._finished_selection = True
        self._refresh_hint()
        if self.show_action_bar:
            self._place_bar()
            self.bar.show()
            self.bar.raise_()
        self.update()

    def _reset_selection(self):
        self.selection = QRect()
        self._finished_selection = False
        self._hover_level = 0
        self._step_offset = 0
        self._hover_from = None
        self._refresh_hint()
        self._hide_bar()
        self.setCursor(Qt.CrossCursor)
        self.update()

    def _place_bar(self):
        sel = self.selection.normalized()
        self.bar.adjustSize()
        bw, bh = self.bar.width(), self.bar.height()
        x = sel.right() - bw
        y = sel.bottom() + 10
        if y + bh > self.height() - 4:
            y = sel.top() - bh - 10
        if y < 4:
            y = max(4, sel.bottom() - bh - 10)
        x = max(4, min(x, self.width() - bw - 4))
        self.bar.move(x, y)

    def _hide_bar(self):
        if self.bar.isVisible():
            self.bar.hide()

    # ------------------------------------------------------------ 키보드
    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()
        if key == Qt.Key_F1:
            self._toggle_hint()
            return
        if key in (Qt.Key_Tab, Qt.Key_Backtab):
            self._toggle_hint_page()
            return
        if key in (Qt.Key_Comma, Qt.Key_Less) and not self._dragging:
            self._step_history(1)
            return
        if key in (Qt.Key_Period, Qt.Key_Greater) and not self._dragging:
            self._step_history(-1)
            return
        if not self._finished_selection and not self._placing and \
                not (mods & (Qt.ControlModifier | Qt.AltModifier)):
            if key == Qt.Key_C:
                self._copy_color(as_rgb=bool(mods & Qt.ShiftModifier))
                return
            if key == Qt.Key_R:
                self._toggle_ruler()
                return
        if key in (Qt.Key_Control, Qt.Key_Shift) and \
                not self._finished_selection:
            self._apply_hover_level(event.modifiers() | (
                Qt.ControlModifier if key == Qt.Key_Control else Qt.NoModifier))
            return
        if key == Qt.Key_Escape:
            if self._finished_selection:
                self._reset_selection()
            else:
                self._cancel()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if self._placing:
                self._placing = False
                self._finish_selection()
            elif self._finished_selection:
                self._emit("default")
            return
        if self._placing:
            step = 10 if mods & Qt.ShiftModifier else 1
            deltas = {Qt.Key_Left: (-step, 0), Qt.Key_Right: (step, 0),
                      Qt.Key_Up: (0, -step), Qt.Key_Down: (0, step)}
            if key in deltas:
                dx, dy = deltas[key]
                self.selection = self.selection.translated(dx, dy)
                self.update()
                return
        if not self._finished_selection and not self._placing:
            step = 10 if mods & Qt.ShiftModifier else 1
            moves = {Qt.Key_Left: (-step, 0), Qt.Key_Right: (step, 0),
                     Qt.Key_Up: (0, -step), Qt.Key_Down: (0, step)}
            if key in moves:
                self._move_cursor(*moves[key])
                return
            if key == Qt.Key_Space:
                self._toggle_keyboard_drag()
                return

        if mods & Qt.ControlModifier:
            if key == Qt.Key_A:
                screen_rect = self._current_screen_rect()
                self.selection = screen_rect
                self._finish_selection()
                return
            if key == Qt.Key_C and self._finished_selection:
                self._emit("copy")
                return
            if key == Qt.Key_S and self._finished_selection:
                self._emit("save")
                return
        if self._finished_selection:
            if key == Qt.Key_E:
                self._emit("edit")
                return
            if key == Qt.Key_P:
                self._emit("pin")
                return
            if key == Qt.Key_T:
                self._emit("ocr")
                return
            if key == Qt.Key_Q:
                self._emit("qr")
                return
            if key == Qt.Key_S and not (mods & Qt.ControlModifier):
                self._emit("scroll")
                return
            step = 10 if mods & Qt.ShiftModifier else 1
            deltas = {Qt.Key_Left: (-step, 0), Qt.Key_Right: (step, 0),
                      Qt.Key_Up: (0, -step), Qt.Key_Down: (0, step)}
            if key in deltas:
                dx, dy = deltas[key]
                sel = self.selection.normalized()
                if mods & Qt.AltModifier:
                    self.selection = QRect(sel.topLeft(),
                                           QSize(max(MIN_SIZE, sel.width() + dx),
                                                 max(MIN_SIZE, sel.height() + dy)))
                else:
                    self.selection = sel.translated(dx, dy)
                self._place_bar()
                self.update()
                return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.isAutoRepeat():
            return
        if event.key() in (Qt.Key_Control, Qt.Key_Shift):
            modifiers = event.modifiers()
            if event.key() == Qt.Key_Control:
                modifiers &= ~Qt.ControlModifier
            if event.key() == Qt.Key_Shift:
                modifiers &= ~Qt.ShiftModifier
            self._apply_hover_level(modifiers)
            return
        super().keyReleaseEvent(event)

    def _current_screen_rect(self):
        pos = QCursor.pos()
        for geo, _image, _dpr in self.shot.shots:
            if geo.contains(pos):
                return self._to_widget_rect(geo)
        return self.rect()

    # ------------------------------------------------------------ 마무리
    def _selection_dpr(self):
        sel = self._to_screen_rect(self.selection.normalized())
        best = 1.0
        for geo, _image, dpr in self.shot.shots:
            if geo.intersects(sel):
                best = max(best, dpr)
        return best

    def _bar_clicked(self, action):
        if action == "cancel":
            self._cancel()
        else:
            self._emit(action)

    def _emit(self, action):
        if self._closing:
            return
        sel = self.selection.normalized()
        if sel.width() < MIN_SIZE or sel.height() < MIN_SIZE:
            self._cancel()
            return
        image = self.shot.image_for(self._to_screen_rect(sel))
        self._closing = True
        self._hide_bar()
        self.hide()
        self.completed.emit(image, action)
        self.close()

    def _cancel(self):
        if self._closing:
            return
        self._closing = True
        self._hide_bar()
        self.hide()
        self.cancelled.emit()
        self.close()

    def closeEvent(self, event):
        QToolTip.hideText()
        if not self._closing:
            self._closing = True
            self.cancelled.emit()
        super().closeEvent(event)


# ---------------------------------------------------------------- 여러 모니터
class OverlayGroup(QObject):
    """모니터마다 캡처 화면을 하나씩 띄우고 묶어서 관리한다.

    창 하나로 모든 모니터를 덮으면, 모니터마다 배율(125% / 100% 등)이
    다를 때 한쪽이 작게 그려진다. Windows 도 Qt 도 '창 하나에 배율 하나'
    이기 때문이다. 그래서 모니터마다 따로 띄운다.

    바깥에서 보기에는 예전의 캡처 화면 하나와 똑같이 동작한다.
    """

    completed = Signal(QImage, str)
    cancelled = Signal()

    def __init__(self, shot: ScreenShot, show_action_bar=True,
                 fixed_size=None, parent=None):
        super().__init__(parent)
        self.shot = shot
        self.overlays = []
        self._closing = False
        self.last_rect = None          # 확정된 선택 영역 (전체 논리 좌표)

        geometries = [geo for geo, _image, _dpr in shot.shots]
        if not geometries:
            geometries = [shot.virtual_rect]
        if fixed_size is not None:
            # 크기지정 캡처는 마우스가 있는 모니터에서만 틀을 놓는다
            here = QCursor.pos()
            chosen = None
            for geo in geometries:
                if geo.contains(here):
                    chosen = geo
                    break
            geometries = [chosen or geometries[0]]

        for geo in geometries:
            overlay = SelectionOverlay(shot, show_action_bar, fixed_size,
                                       screen_geo=geo)
            overlay.group = self
            overlay.completed.connect(self._on_completed)
            overlay.cancelled.connect(self._on_cancelled)
            self.overlays.append(overlay)

    # ------------------------------------------------------------ 준비
    def hwnds(self):
        """우리 창들의 손잡이 — 창 목록에서 빼기 위해."""
        handles = []
        for overlay in self.overlays:
            try:
                handles.append(int(overlay.winId()))
            except Exception:
                pass
        return handles

    def prepare(self, exclude_hwnds=()):
        exclude = list(exclude_hwnds) + self.hwnds()
        for overlay in self.overlays:
            overlay.prepare(exclude)

    def show(self):
        for overlay in self.overlays:
            overlay.show()
            overlay.raise_()
        self.focus_on(self._under_cursor() or
                      (self.overlays[0] if self.overlays else None))

    def _under_cursor(self):
        here = QCursor.pos()
        for overlay in self.overlays:
            if overlay.screen_geo.contains(here):
                return overlay
        return None

    # ------------------------------------------------------- 창 사이 연동
    def focus_on(self, overlay):
        """마우스가 올라간 모니터의 창이 키 입력을 받게 한다."""
        if overlay is None or self._closing:
            return
        try:
            overlay.raise_()
            overlay.activateWindow()
            overlay.setFocus(Qt.OtherFocusReason)
            if winutil.IS_WIN:
                import ctypes
                winutil.user32.SetForegroundWindow(
                    ctypes.c_void_p(int(overlay.winId())))
        except Exception:
            pass

    def step_history(self, delta, history):
        """예전 영역을 그 영역이 있는 모니터의 창에 띄운다."""
        index = max(-1, min(len(history) - 1,
                            getattr(self, "history_index", -1) + delta))
        self.history_index = index
        if index < 0:
            for overlay in self.overlays:
                overlay._reset_selection()
            target = self._under_cursor() or \
                (self.overlays[0] if self.overlays else None)
            if target is not None:
                target.show_message("예전 영역 불러오기를 그만뒀습니다")
            return
        x, y, w, h = history[index]
        center = QRect(x, y, w, h).center()
        target = None
        for overlay in self.overlays:
            if overlay.screen_geo.contains(center):
                target = overlay
                break
        if target is None:
            target = self._under_cursor() or \
                (self.overlays[0] if self.overlays else None)
        for overlay in self.overlays:
            if overlay is not target:
                overlay._reset_selection()
        if target is not None:
            target.apply_history(index, history)
            self.focus_on(target)

    def share_selection(self, source, global_rect):
        """한 창에서 끌고 있는 영역을 다른 창들에게도 보여 준다."""
        for overlay in self.overlays:
            if overlay is not source:
                try:
                    overlay.set_foreign_selection(global_rect)
                except RuntimeError:
                    pass

    # ------------------------------------------------------------ 마무리
    def _on_completed(self, image, action):
        if self._closing:
            return
        # 창을 닫기 전에 선택 영역을 기억해 둔다 (스크롤 캡처가 이어서 쓴다)
        source = self.sender()
        try:
            if source is not None:
                self.last_rect = source._to_screen_rect(
                    source.selection.normalized())
        except Exception:
            self.last_rect = None
        self._closing = True
        self._close_all()
        self.completed.emit(image, action)

    def _on_cancelled(self):
        if self._closing:
            return
        self._closing = True
        self._close_all()
        self.cancelled.emit()

    def _close_all(self):
        for overlay in list(self.overlays):
            try:
                if not overlay._closing:
                    overlay._closing = True      # 취소 신호가 겹치지 않게
                overlay.close()
            except RuntimeError:
                pass
        self.overlays = []

    def close(self):
        self._on_cancelled()
