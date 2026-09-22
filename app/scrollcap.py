# -*- coding: utf-8 -*-
"""스크롤 캡처 — 창을 아래로 굴리며 여러 장을 찍어 하나로 이어붙인다.

v1.10 에서 달라진 점
  * 한 장 찍을 때마다 바로 맞춰 본다 (다 찍고 나서 실패를 알던 문제).
  * 휠을 몇 칸 굴릴지 고정하지 않는다. 한 번 굴려서 실제로 몇 픽셀
    내려가는지 재고, 영역 높이의 절반 정도만 내려가도록 칸 수를 맞춘다.
    (선택 영역이 작을 때 한 번에 너무 많이 내려가 겹치는 부분이
    사라지던 것이 가장 흔한 실패 원인이었다)
  * 안 맞으면 되돌려서 덜 굴려 다시 찍는다. 그래도 안 되면 지금까지
    찍은 것은 살려 둔 채 '직접 스크롤' 로 넘어간다.
  * 휠이 먹지 않는 창은 ↓ 키로 대신 내려 본다.
  * 직접 스크롤 모드에서는 화면이 멈출 때마다 알아서 한 장씩 붙인다.
"""
from PySide6.QtCore import QObject, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

import capture
import stitch
import winutil
from config import CONFIG

SETTLE_INTERVAL = 70      # 화면이 멈췄는지 확인하는 간격(ms)
SETTLE_TRIES = 9
POLL_INTERVAL = 180       # 직접 스크롤 모드에서 화면을 살피는 간격(ms)
TARGET_FILL = 0.5         # 한 번에 영역 높이의 이만큼만 내려가게 한다
GUESS_PX_PER_CLICK = 100  # 처음 한 번은 휠 한 칸에 이 정도(논리 픽셀)로 짐작
KEY_PRESSES_PER_STEP = 3  # 휠 대신 ↓ 키를 쓸 때 한 단계에 누를 횟수
PREVIEW_W = 96
PREVIEW_H = 124
RAW_KEEP = 80             # 진단용으로 남겨 둘 낱장 수


def _same(a: QImage, b: QImage) -> bool:
    if a is None or b is None:
        return False
    if a.size() != b.size():
        return False
    return a == b


class ProgressPanel(QWidget):
    """진행 상황을 보여주는 작은 창 — 이어붙인 결과 미리보기 포함."""

    cancelled = Signal()
    manualShot = Signal()
    manualDone = Signal()

    GOOD = QColor(76, 200, 120)
    BAD = QColor(240, 96, 96)
    IDLE = QColor(150, 160, 176)

    def __init__(self, manual=False, avoid=None, parent=None):
        super().__init__(parent)
        self.manual = manual
        self.count = 0
        self.message = ""
        self.state = "idle"               # idle | good | bad
        self._pieces = []                 # [(작게 줄인 장, y)]
        self._preview_height = 0
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                            Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setFixedSize(430, 150)
        self._build_buttons()
        self._sync_buttons()
        self._place(avoid)

    def _build_buttons(self):
        holder = QWidget(self)
        holder.setGeometry(14, self.height() - 42, 300, 32)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        style = ("QPushButton { color: #eef2f8; background: rgba(255,255,255,26);"
                 " border: 1px solid rgba(255,255,255,50); border-radius: 5px;"
                 " padding: 4px 10px; font-size: 12px; }"
                 "QPushButton:hover { background: rgba(255,255,255,52); }")
        self.done_btn = QPushButton("완료", holder)
        self.shot_btn = QPushButton("한 장 추가 (Space)", holder)
        self.cancel_btn = QPushButton("취소 (Esc)", holder)
        for button in (self.done_btn, self.shot_btn, self.cancel_btn):
            button.setStyleSheet(style)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            row.addWidget(button)
        row.addStretch(1)
        self.done_btn.clicked.connect(self.manualDone.emit)
        self.shot_btn.clicked.connect(self.manualShot.emit)
        self.cancel_btn.clicked.connect(self.cancelled.emit)

    def _sync_buttons(self):
        self.shot_btn.setVisible(self.manual)
        self.done_btn.setText("완료 (Enter)" if self.manual else "여기까지 완료")

    def set_manual(self, manual, message=""):
        self.manual = manual
        self._sync_buttons()
        self.set_status(self.count, message, "bad" if message else "idle")

    def _place(self, avoid=None):
        """캡처할 영역을 가리지 않는 자리에 놓는다."""
        screen = None
        if avoid is not None:
            screen = QGuiApplication.screenAt(avoid.center())
        screen = screen or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        w, h = self.width(), self.height()
        spots = [
            QRect(area.center().x() - w // 2, area.top() + 20, w, h),
            QRect(area.center().x() - w // 2, area.bottom() - h - 20, w, h),
            QRect(area.left() + 20, area.bottom() - h - 20, w, h),
            QRect(area.right() - w - 20, area.bottom() - h - 20, w, h),
            QRect(area.left() + 20, area.top() + 20, w, h),
            QRect(area.right() - w - 20, area.top() + 20, w, h),
        ]
        best, best_overlap = spots[0], None
        for spot in spots:
            if avoid is None:
                best = spot
                break
            inter = spot.intersected(avoid)
            overlap = inter.width() * inter.height() if not inter.isEmpty() else 0
            if overlap == 0:
                best = spot
                break
            if best_overlap is None or overlap < best_overlap:
                best_overlap, best = overlap, spot
        self.move(best.topLeft())

    # ---------------------------------------------------------- 상태
    def set_count(self, count, message=""):
        self.set_status(count, message, self.state)

    def set_status(self, count, message="", state="idle"):
        self.count = count
        self.message = message
        self.state = state
        self.update()

    def add_piece(self, image: QImage, y_px: int, top_px: int = 0):
        """이어붙인 장을 미리보기에 더한다 (작게 줄여서)."""
        if image is None or image.isNull() or not image.width():
            return
        scale = PREVIEW_W / float(image.width())
        src = image
        y = y_px
        if top_px and self._pieces:
            src = image.copy(0, top_px, image.width(), image.height() - top_px)
            y = y_px + top_px
        small = src.scaled(PREVIEW_W, max(1, int(src.height() * scale)),
                           Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        y_small = int(y * scale)
        self._pieces.append((small, y_small))
        self._preview_height = max(self._preview_height,
                                   y_small + small.height())
        self.update()

    # ---------------------------------------------------------- 그리기
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
        painter.setBrush(QColor(16, 18, 23, 238))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 10, 10)

        text_w = self.width() - PREVIEW_W - 44
        color = {"good": self.GOOD, "bad": self.BAD}.get(self.state, self.IDLE)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(18, 20, 10, 10)

        title = QFont()
        title.setPointSize(11)
        title.setBold(True)
        painter.setFont(title)
        painter.setPen(QColor(240, 244, 250))
        head = ("직접 스크롤 캡처 · %d장" if self.manual
                else "스크롤 캡처 · %d장") % self.count
        painter.drawText(QRect(36, 12, text_w - 20, 26),
                         Qt.AlignLeft | Qt.AlignVCenter, head)

        small = QFont()
        small.setPointSize(9)
        painter.setFont(small)
        painter.setPen(color if self.message else QColor(170, 180, 196))
        if self.message:
            guide = self.message
        elif self.manual:
            guide = ("천천히 스크롤하세요. 화면이 멈출 때마다 알아서 붙입니다.\n"
                     "다 내렸으면 Enter.")
        else:
            guide = "자동으로 내려가는 중입니다. Esc 를 누르면 멈춥니다."
        painter.drawText(QRect(18, 40, text_w, 58),
                         Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, guide)

        # 미리보기
        box = QRect(self.width() - PREVIEW_W - 14, 13, PREVIEW_W, PREVIEW_H)
        painter.setPen(QPen(QColor(255, 255, 255, 50), 1))
        painter.setBrush(QColor(0, 0, 0, 90))
        painter.drawRect(box.adjusted(-1, -1, 0, 0))
        if self._pieces:
            painter.save()
            painter.setClipRect(box)
            shift = max(0, self._preview_height - PREVIEW_H)   # 최근 부분이 보이게
            for piece, y in self._pieces:
                painter.drawImage(box.left(), box.top() + y - shift, piece)
            painter.restore()
        painter.end()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Escape:
            self.cancelled.emit()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.manualDone.emit()
            return
        if self.manual and key == Qt.Key_Space:
            self.manualShot.emit()
            return
        super().keyPressEvent(event)


class ScrollCapture(QObject):
    """자동/수동 스크롤 캡처 진행자."""

    finished = Signal(QImage, dict)
    failed = Signal(str)

    def __init__(self, logical_rect: QRect, shot, manual=False, parent=None):
        super().__init__(parent)
        self.rect = QRect(logical_rect).normalized()
        self.shot = shot
        self.manual = manual
        self.stitcher = stitch.Stitcher()
        self.frames = []                 # 찍은 낱장 전부 (진단 저장용)
        self.cancelled = False
        self._saved_cursor = None
        self._settle_prev = None
        self._settle_prev_sig = None
        self._settle_tries = 0
        self._settle_cb = None
        self.panel = None
        self.limit = max(2, int(CONFIG["scroll_frame_limit"] or 60))
        self.delay = max(80, int(CONFIG["scroll_delay_ms"]))
        self.max_clicks = max(1, int(CONFIG["scroll_step"]))
        self.clicks = 1
        self.method = "wheel"            # wheel | keys
        self._tried_keys = False
        self._fail_streak = 0
        self._same_confirmed = False
        self._target_point = None
        self._panel_must_hide = False
        self._attempts = 0
        self._poll_timer = None
        self._poll_last = None
        self._poll_handled = False
        self._esc_timer = None

    # ------------------------------------------------------------ 시작
    def start(self):
        center = self.rect.center()
        self._target_point = self.shot.logical_to_physical_point(center)
        self._saved_cursor = winutil.get_cursor_pos()

        self.panel = ProgressPanel(self.manual, avoid=self.rect)
        self.panel.cancelled.connect(self.cancel)
        self.panel.manualShot.connect(self._manual_shot)
        self.panel.manualDone.connect(self._finish)
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()
        self.panel.setFocus(Qt.OtherFocusReason)

        # 안내창이 캡처에 찍히면 이어붙이기가 망가진다.
        # Windows 10 2004 이상은 '캡처에서 제외'가 되고,
        # 그보다 낮으면 찍는 순간에만 잠깐 숨긴다.
        excluded = winutil.exclude_from_capture(int(self.panel.winId()))
        if not excluded and self.panel.geometry().intersects(self.rect):
            self._panel_must_hide = True

        if not self.manual:
            hwnd = winutil.window_from_point(*self._target_point)
            if hwnd:
                winutil.activate_window(hwnd)
                # 창을 활성화하면 우리 안내판이 뒤로 가므로 다시 올린다
                QTimer.singleShot(60, self._raise_panel)

        # 대상 창에 입력이 가 있어도 Esc 로 멈출 수 있게 살핀다
        self._esc_timer = QTimer(self)
        self._esc_timer.timeout.connect(self._check_escape)
        self._esc_timer.start(60)
        QTimer.singleShot(180, lambda: self._settle(self._first_frame))

    def _raise_panel(self):
        if self.panel is not None:
            self.panel.raise_()

    def _check_escape(self):
        if self.cancelled:
            return
        if winutil.key_down(winutil.VK_ESCAPE):
            self.cancel()

    # ---------------------------------------------- 화면이 멈출 때까지 대기
    def _settle(self, callback, first_delay=None):
        self._settle_prev = None
        self._settle_prev_sig = None
        self._settle_tries = 0
        self._settle_cb = callback
        if self._panel_must_hide and self.panel is not None:
            self.panel.hide()
        QTimer.singleShot(self.delay if first_delay is None else first_delay,
                          self._settle_step)

    def _settle_step(self):
        if self.cancelled:
            return
        image = capture.grab_region(self.rect)
        if image.isNull():
            self._fail("화면을 읽지 못했습니다.")
            return
        if self._settle_prev is not None:
            if _same(self._settle_prev, image):
                self._settle_done(image)
                return
            # 깜빡이는 커서·움직이는 광고 정도의 차이는 멈춘 것으로 본다
            sig = stitch.row_signatures(image)
            if self._settle_prev_sig is None:
                self._settle_prev_sig = stitch.row_signatures(self._settle_prev)
            if stitch.similarity(self._settle_prev_sig, sig) >= \
                    stitch.STILL_RATIO:
                self._settle_done(image)
                return
            self._settle_prev_sig = sig
        self._settle_prev = image
        self._settle_tries += 1
        if self._settle_tries >= SETTLE_TRIES:
            self._settle_done(image)
            return
        QTimer.singleShot(SETTLE_INTERVAL, self._settle_step)

    def _settle_done(self, image):
        if self._panel_must_hide and self.panel is not None:
            self.panel.show()
            self.panel.raise_()
        callback = self._settle_cb
        self._settle_cb = None
        if callback and not self.cancelled:
            callback(image)

    # ------------------------------------------------------------ 기록
    def _keep_raw(self, image):
        self._attempts += 1
        if len(self.frames) < RAW_KEEP:
            self.frames.append(image)

    def _dpr(self, image):
        if self.rect.width() <= 0:
            return 1.0
        return max(0.5, image.width() / float(self.rect.width()))

    def _report(self, status, shift, image):
        """패널에 결과를 알리고 미리보기를 더한다."""
        if self.panel is None:
            return
        count = len(self.stitcher)
        if status in (stitch.Stitcher.FIRST, stitch.Stitcher.OK):
            y = self.stitcher.offset_of_last()
            self.panel.add_piece(image, y, self.stitcher.top)
            message = "" if status == stitch.Stitcher.FIRST else \
                "이어붙였습니다 (+%d px)" % shift
            self.panel.set_status(count, message, "good")

    # ------------------------------------------------------------ 자동
    def _first_frame(self, image):
        self._keep_raw(image)
        status, shift = self.stitcher.add(image)
        self._report(status, shift, image)
        # 처음 칸 수 짐작: 영역 높이의 절반쯤 내려가도록
        guess = GUESS_PX_PER_CLICK * self._dpr(image)
        self.clicks = max(1, min(self.max_clicks,
                                 int(image.height() * TARGET_FILL / guess)))
        if self.manual:
            self._start_polling()
            return
        QTimer.singleShot(0, self._next_step)

    def _scroll(self, amount):
        if amount == 0:
            return
        if self.method == "keys":
            winutil.set_cursor_pos(*self._target_point)
            vk = winutil.VK_DOWN if amount > 0 else winutil.VK_UP
            winutil.send_key(vk, abs(amount) * KEY_PRESSES_PER_STEP)
        else:
            winutil.send_wheel(self._target_point[0], self._target_point[1],
                               amount)

    def _next_step(self):
        if self.cancelled:
            return
        if len(self.stitcher) >= self.limit:
            self._finish("최대 %d장까지 찍었습니다." % self.limit)
            return
        self._scroll(self.clicks)
        self._settle(self._got_frame)

    def _got_frame(self, image):
        if self.cancelled:
            return
        self._keep_raw(image)
        status, shift = self.stitcher.add(image)

        if status == stitch.Stitcher.OK:
            self._fail_streak = 0
            self._same_confirmed = False
            self._adapt(shift)
            self._report(status, shift, image)
            QTimer.singleShot(0, self._next_step)
            return

        if status == stitch.Stitcher.SAME:
            if len(self.stitcher) == 1:
                if self.method == "wheel" and not self._tried_keys:
                    # 휠이 안 먹는 창 — ↓ 키로 내려 본다
                    self._tried_keys = True
                    self.method = "keys"
                    self.clicks = 1
                    if self.panel:
                        self.panel.set_status(1, "휠이 먹지 않아 ↓ 키로 내려 봅니다",
                                              "idle")
                    QTimer.singleShot(0, self._next_step)
                    return
                self._fail_no_scroll()
                return
            if not self._same_confirmed:
                # 늦게 불러오는 페이지일 수 있으니 잠깐 기다렸다 한 번 더 굴려 본다
                self._same_confirmed = True
                QTimer.singleShot(500, self._next_step)
                return
            self._finish()
            return

        # 안 맞음
        self._fail_streak += 1
        if self.panel:
            self.panel.set_status(len(self.stitcher),
                                  "겹치는 부분을 못 찾아 덜 굴려서 다시 찍습니다",
                                  "bad")
        if self.clicks > 1:
            back = self.clicks
            self.clicks = max(1, self.clicks // 2)
            self._scroll(-back)
            self._settle(lambda _img: self._next_step())
            return
        if self._fail_streak <= 2:
            # 화면이 아직 움직이는 중이었을 수 있다 — 굴리지 않고 다시 본다
            self._settle(self._got_frame, first_delay=400)
            return
        # 자동으로는 어려운 화면 — 한 칸 되돌린 뒤 직접 스크롤로 넘긴다
        self._scroll(-1)
        self._settle(lambda _img: self._switch_to_manual(
            "자동으로 맞추기 어려운 화면입니다. 직접 천천히 스크롤해 주세요 "
            "(찍은 것은 그대로 있습니다)"))

    def _adapt(self, shift):
        """실제로 내려간 폭을 보고 다음에 굴릴 칸 수를 정한다."""
        if not shift or self.clicks <= 0:
            return
        per_click = shift / float(self.clicks)
        if per_click <= 0:
            return
        usable = self.stitcher.usable_height()
        wanted = int(usable * TARGET_FILL / per_click)
        self.clicks = max(1, min(self.max_clicks, wanted))

    # ------------------------------------------------------------ 수동
    def _switch_to_manual(self, message):
        if self.cancelled:
            return
        self.manual = True
        if self.panel is not None:
            self.panel.set_manual(True, message)
            self.panel.raise_()
        self._start_polling()

    def _start_polling(self):
        if self._panel_must_hide:
            return                  # 안내창이 찍히는 환경이면 Space 로만
        if self._poll_timer is None:
            self._poll_timer = QTimer(self)
            self._poll_timer.timeout.connect(self._poll)
        self._poll_last = None
        self._poll_handled = True
        self._poll_timer.start(POLL_INTERVAL)

    def _poll(self):
        """직접 스크롤 — 화면이 멈추면 알아서 한 장 붙인다."""
        if self.cancelled:
            return
        image = capture.grab_region(self.rect)
        if image.isNull():
            return
        if self._poll_last is None or not _same(self._poll_last, image):
            self._poll_last = image
            self._poll_handled = False
            return
        if self._poll_handled:
            return
        self._poll_handled = True           # 멈춘 화면은 한 번만 본다
        self._try_manual(image, quiet_same=True)

    def _manual_shot(self):
        if self.cancelled:
            return
        if self._panel_must_hide and self.panel is not None:
            self.panel.hide()
            QTimer.singleShot(60, self._manual_grab)
            return
        self._manual_grab()

    def _manual_grab(self):
        image = capture.grab_region(self.rect)
        if self._panel_must_hide and self.panel is not None:
            self.panel.show()
            self.panel.raise_()
        if image.isNull():
            return
        self._poll_last = image
        self._poll_handled = True
        self._try_manual(image, quiet_same=False)

    def _try_manual(self, image, quiet_same):
        if len(self.stitcher) >= self.limit:
            self._finish("최대 %d장까지 찍었습니다." % self.limit)
            return
        self._keep_raw(image)
        status, shift = self.stitcher.add(image)
        if status in (stitch.Stitcher.FIRST, stitch.Stitcher.OK):
            self._report(status, shift, image)
        elif status == stitch.Stitcher.SAME:
            if not quiet_same and self.panel:
                self.panel.set_status(len(self.stitcher),
                                      "화면이 그대로입니다 · 조금 내린 뒤 다시",
                                      "idle")
        elif self.panel:
            self.panel.set_status(
                len(self.stitcher),
                "앞 장과 겹치는 부분이 없습니다. 조금 위로 올려 겹치게 해 주세요",
                "bad")

    # ------------------------------------------------------------ 종료
    def _stop_timers(self):
        for timer in (self._poll_timer, self._esc_timer):
            if timer is not None:
                timer.stop()

    def cancel(self):
        if self.cancelled:
            return
        self.cancelled = True
        self._stop_timers()
        self._close_panel()
        self._restore_cursor()
        if len(self.stitcher) >= 2:
            self._emit_result("중단했습니다. 여기까지 이어붙였습니다.")
        else:
            self.failed.emit("")

    def _fail_no_scroll(self):
        self.cancelled = True
        self._stop_timers()
        self._close_panel()
        self._restore_cursor()
        self.failed.emit(
            "이 창은 자동으로 스크롤되지 않았습니다.\n\n"
            "마우스가 올라가면 스크롤되는 영역인지 확인해 주세요.\n"
            "그래도 안 되면 '직접 스크롤하며 찍기'를 사용하시면 됩니다.")

    def _fail(self, message):
        self.cancelled = True
        self._stop_timers()
        self._close_panel()
        self._restore_cursor()
        self.failed.emit(message)

    def _finish(self, note=""):
        if self.cancelled:
            return
        self.cancelled = True
        self._stop_timers()
        self._close_panel()
        self._restore_cursor()
        if not len(self.stitcher):
            self.failed.emit("찍은 화면이 없습니다.")
            return
        self._emit_result(note)

    def _emit_result(self, note=""):
        image, info = self.stitcher.result()
        info["note"] = note
        info["captured"] = self._attempts
        self.finished.emit(image, info)

    def _close_panel(self):
        if self.panel is not None:
            panel = self.panel
            self.panel = None
            panel.close()

    def _restore_cursor(self):
        if self._saved_cursor:
            winutil.set_cursor_pos(*self._saved_cursor)
            self._saved_cursor = None
