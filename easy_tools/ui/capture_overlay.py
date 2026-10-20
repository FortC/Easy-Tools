"""微信式框选截图覆盖层。

原理（与微信一致）：触发瞬间先把整个屏幕抓成一张"底片"，然后全屏
显示这张压暗的底片，用户在底片上拖框，松手后从底片裁剪——因此覆盖层
自己永远不会出现在截图里。

DPI 纪律：抓图使用 Windows 的物理显示器范围；窗口使用 Qt 逻辑坐标。
按底片尺寸与窗口尺寸的实际比例裁剪，避免混合缩放显示器的原点偏移。
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..core import focus
from ..core.log import get_logger
from ..win32 import clipboard, gdi
from ..win32.window import monitor_rect_at_cursor

log = get_logger("capture")
_MIN_SEL = 8          # 选区最小边（逻辑 px），小于视为误触取消


def _screen_at_cursor():
    """Qt cursor and screen geometry share logical coordinates, including DPI islands."""
    return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()


class CaptureOverlay(QWidget):
    """全屏压暗底片 + 拖框选区。由 PanelBridge.request_capture 在 GUI 线程创建。"""

    done = Signal(str)
    answer_ready = Signal(str, str, str)
    delivery_finished = Signal()

    def __init__(self, service, auto_ocr: bool, copy_image: bool,
                 ask_question: bool = True) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self._service = service
        self._auto_ocr = bool(auto_ocr)
        self._copy_image = bool(copy_image)
        self._ask_question = bool(ask_question)
        self._origin: QPoint | None = None
        self._cur: QPoint | None = None
        self._phase = "selecting"
        self._selection_global = QRect()

        scr = _screen_at_cursor()
        if scr is None:
            raise RuntimeError("没有可用的显示器")
        self._dpr = float(scr.devicePixelRatio()) or 1.0
        g = scr.geometry()                       # 逻辑
        # Windows monitor origins are physical pixels. Multiplying Qt's logical
        # origin by this monitor's DPR is wrong on mixed-DPI/negative monitors.
        px, py, pw, ph = monitor_rect_at_cursor()
        cap = gdi.capture_rect(px, py, pw, ph)
        if cap is None:
            raise RuntimeError("屏幕抓取失败")
        if cap.width <= 0 or cap.height <= 0 or len(cap.bgra) != cap.width * cap.height * 4:
            raise RuntimeError("屏幕图像数据不完整")
        self._cap = cap
        self._scale_x = cap.width / max(1, g.width())
        self._scale_y = cap.height / max(1, g.height())
        # GDI does not guarantee the alpha byte. RGB32 treats screen pixels as
        # opaque; copy() gives Qt its own storage for asynchronous repaints.
        self._img = QImage(cap.bgra, cap.width, cap.height,
                           cap.width * 4, QImage.Format_RGB32).copy()
        self.setGeometry(g)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    def start(self) -> None:
        """显示覆盖层。必须先于 show 调用 remember，记到的才是用户原前台。"""
        focus.remember()
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.OtherFocusReason)
        log.info("框选覆盖层显示，逻辑尺寸=%sx%s，像素尺寸=%sx%s",
                 self.width(), self.height(), self._cap.width, self._cap.height)

    # ------------------------------------------------------------ 事件
    def mousePressEvent(self, ev) -> None:
        if self._phase != "selecting":
            return
        if ev.button() == Qt.LeftButton:
            self._origin = ev.position().toPoint()
            self._cur = self._origin
            self.update()
        elif ev.button() == Qt.RightButton:
            self._cancel()

    def mouseMoveEvent(self, ev) -> None:
        if self._origin is not None:
            self._cur = ev.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, ev) -> None:
        if self._phase != "selecting" or ev.button() != Qt.LeftButton or self._origin is None:
            return
        self._cur = ev.position().toPoint()
        r = QRect(self._origin, self._cur).normalized().intersected(self.rect())
        self._origin = None
        if r.width() < _MIN_SEL or r.height() < _MIN_SEL:
            self._cancel()
            return
        self._finish(r)

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key_Escape:
            self._cancel()

    # ------------------------------------------------------------ 完成/取消
    def _cancel(self) -> None:
        if self._phase != "selecting":
            return
        self._phase = "cancelled"
        self._close_quietly(delete_later=True)
        focus.restore()
        self.done.emit("已取消截图")

    def _finish(self, r: QRect) -> None:
        if self._phase != "selecting":
            return
        self._phase = "processing"
        self._selection_global = QRect(self.mapToGlobal(r.topLeft()), r.size())
        phys = (int(r.x() * self._scale_x), int(r.y() * self._scale_y),
                max(1, int(r.width() * self._scale_x)),
                max(1, int(r.height() * self._scale_y)))
        crop = gdi.crop_capture(self._cap, *phys)
        # Keep this QObject alive while the worker uses it to deliver OCR and
        # answer signals. Deleting it here silently drops those queued results.
        self._close_quietly(delete_later=False)
        # The worker needs only the crop, not two full-screen pixel buffers.
        self._cap = None
        self._img = QImage()
        focus.restore()
        log.info("框选完成，选区像素尺寸=%sx%s，后台处理开始", crop.width, crop.height)
        threading.Thread(target=self._deliver, args=(crop,),
                         name="capture-deliver", daemon=True).start()

    def _close_quietly(self, delete_later: bool = True) -> None:
        self.hide()
        if delete_later:
            self.deleteLater()

    def _deliver(self, cap: "gdi.Capture") -> None:
        parts = []
        try:
            self._deliver_content(cap, parts)
        except Exception as exc:               # noqa: BLE001 - always finish the session
            log.exception("截图提问处理异常")
            try:
                self.answer_ready.emit("", f"截图提问处理失败：{exc}", "")
            except RuntimeError:
                log.exception("截图提问窗口已销毁，无法发送错误反馈")
            parts.append("截图提问失败")
        finally:
            self.done.emit("、".join(parts) if parts else "截图完成")
            self.delivery_finished.emit()

    def _deliver_content(self, cap: "gdi.Capture", parts: list[str]) -> None:
        text = ""
        if self._copy_image:
            try:
                if clipboard.set_image_bgra(cap.bgra, cap.width, cap.height):
                    parts.append("图片已复制")
                else:
                    parts.append("图片复制失败")
            except Exception:                   # noqa: BLE001
                log.exception("截图复制到剪贴板失败")
                parts.append("图片复制失败")
        if self._auto_ocr:
            try:
                text = self._service.ocr.recognize_capture(cap)
            except Exception as exc:             # noqa: BLE001
                log.exception("截图 OCR 异常")
                self.answer_ready.emit("", f"截图 OCR 执行失败：{exc}", "")
                parts.append("OCR 执行失败")
            if text:
                try:
                    clipboard.set_text(text)
                except Exception:               # noqa: BLE001
                    log.exception("OCR 文字复制到剪贴板失败")
                parts.append("文字 %d 字已复制" % len(text))
                if self._ask_question:
                    try:
                        result = self._service.question_answer.answer(text)
                        details = "\n".join(
                            f"{m['source']} · {m['row']} · 答案列：{m.get('answer_column') or '答案'}\n题目：{m['question']}"
                            for m in result.get("matches", []))
                        answer = result.get("answer", "")
                        llm_cfg = self._service.store.section("llm", {})
                        if (llm_cfg.get("enabled", True)
                                and not (result.get("matched")
                                         and llm_cfg.get("prefer_bank_answer", True))):
                            try:
                                answer = self._service.answer_question(
                                    text, result.get("matches", []))
                            except Exception as exc:
                                if not result.get("matched"):
                                    answer = str(exc)
                                else:
                                    details = ("模型不可用，已回退知识库原答案。\n" + details).strip()
                        self.answer_ready.emit(text, answer, details)
                    except Exception as exc:
                        log.exception("截图知识库检索失败")
                        self.answer_ready.emit(
                            text, f"OCR 已完成，但知识库检索/回答失败：{exc}", "")
            else:
                # An exception path already reported its real error above.
                if not any(p == "OCR 执行失败" for p in parts):
                    try:
                        status = self._service.ocr.status()
                    except Exception as exc:     # noqa: BLE001
                        log.exception("读取 OCR 状态失败")
                        status = {"ready": False, "reason": str(exc)}
                    reason = ("识别后没有提取到文字，请框选文字清晰、对比度较高的区域。"
                              if status.get("ready") else str(status.get("reason") or "OCR 后端未就绪"))
                    self.answer_ready.emit("", f"截图 OCR 没有识别到文字。\n原因：{reason}", "")
                    parts.append("OCR 未识别到文字")

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, _ev) -> None:
        if self._phase != "selecting":
            return
        p = QPainter(self)
        try:
            p.drawImage(self.rect(), self._img)
            p.fillRect(self.rect(), QColor(0, 0, 0, 90))
            if self._origin is None or self._cur is None:
                p.fillRect(18, 18, 280, 32, QColor(15, 23, 42, 220))
                p.setPen(QColor("#ffffff"))
                p.drawText(28, 40, "拖动鼠标框选 · Esc / 右键取消")
                return
            r = QRect(self._origin, self._cur).normalized().intersected(self.rect())
            src = QRectF(r.x() * self._scale_x, r.y() * self._scale_y,
                         r.width() * self._scale_x, r.height() * self._scale_y)
            # Both rectangle arguments must use the same Qt overload family.
            p.drawImage(QRectF(r), self._img, src)
            p.setPen(QPen(QColor("#4f8cff"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
            tag = "%d × %d" % (int(src.width()), int(src.height()))
            p.fillRect(r.x(), max(0, r.y() - 20), 108, 18, QColor(15, 18, 24, 220))
            p.setPen(QColor("#ffffff"))
            p.drawText(r.x() + 6, max(13, r.y() - 6), tag)
        finally:
            # A failed Python paint override must never leave an active painter
            # attached to a widget that can be hidden/destroyed by later input.
            p.end()
