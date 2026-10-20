"""OCR 服务（方案 2.1 的 L2）。

设计约束全部来自方案决策 2：
- 后台线程 + 防抖 + 缓存；
- **面板弹出不等 OCR**：先按 L1 出图，OCR 结果到达后再动态补充。

后端可插拔的现实原因：本机 Python 3.14 没有 onnxruntime 的 cp314 wheel，
装不上是常态而不是意外。所以：
- RapidOCR 可用就用，不可用就明确"不可用 + 原因"，
- 绝不静默假装成功——L2 规则不命中和 OCR 坏了，用户必须分得清。
"""

from __future__ import annotations

import threading
import os
import time
from typing import Dict, Optional

from ..win32 import gdi, window as w32
from .log import get_logger

log = get_logger("ocr")


class OcrBackend:
    name = "none"
    available_reason = "未初始化"

    def available(self) -> bool:
        return False

    def recognize(self, image: "gdi.Capture") -> str:   # pragma: no cover - 抽象
        raise NotImplementedError

    def describe(self) -> str:
        return self.available_reason


class RapidOcrBackend(OcrBackend):
    name = "rapidocr_onnxruntime"

    def __init__(self) -> None:
        self._engine = None
        self._tried = False
        self._reason = ""

    def available(self) -> bool:
        if not self._tried:
            self._tried = True
            try:
                from rapidocr_onnxruntime import RapidOCR
                self._engine = RapidOCR(intra_op_num_threads=max(1, min(2, (os.cpu_count() or 2) - 1)),
                                        inter_op_num_threads=1)
                self._reason = "正常"
            except Exception as e:
                self._engine = None
                self._reason = "RapidOCR 不可用：%s" % e
                log.info(self._reason)
        return self._engine is not None

    def describe(self) -> str:
        return self._reason or ("可用" if self.available() else "不可用")

    def recognize(self, image: "gdi.Capture") -> str:
        import numpy as np
        arr = np.frombuffer(image.bgra, dtype=np.uint8).reshape(image.height, image.width, 4)
        rgb = arr[:, :, :3][:, :, ::-1]          # BGRA -> RGB
        out, _ = self._engine(rgb)
        if not out:
            return ""
        lines = [item[1] for item in out if len(item) > 1]
        return "\n".join(lines)


class NullBackend(OcrBackend):
    name = "none"

    def __init__(self, reason: str = "OCR 已在配置中关闭") -> None:
        self.available_reason = reason

    def available(self) -> bool:
        return False

    def describe(self) -> str:
        return self.available_reason


class OcrService:
    """带防抖与缓存的 OCR 服务。线程安全。"""

    def __init__(self, store=None) -> None:
        self.store = store
        self._lock = threading.RLock()
        self._backend_init_lock = threading.Lock()
        # Background context OCR and explicit screenshot OCR share one engine.
        # Serialize inference so its preprocessing/session state is not mutated
        # concurrently by independent workers. This never blocks the GUI.
        self._inference_lock = threading.Lock()
        self._backend: Optional[OcrBackend] = None
        self._text = ""
        self._at = 0.0
        self._hwnd = 0
        self._pending = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_duration = 0.0
        self._target_hwnd = 0
        self._initializing = False

    # ------------------------------------------------------------ 后端
    def _ensure_backend(self) -> OcrBackend:
        with self._backend_init_lock:
            with self._lock:
                if self._backend is not None:
                    return self._backend
                self._initializing = True
            try:
                cfg = self.store.section("ocr", {}) if self.store else {}
                if not cfg.get("enabled", True):
                    backend: OcrBackend = NullBackend("OCR 已在配置中关闭")
                else:
                    candidate = RapidOcrBackend()
                    backend = candidate if candidate.available() else NullBackend(candidate.describe())
                with self._lock:
                    self._backend = backend
                    return backend
            finally:
                with self._lock:
                    self._initializing = False

    def status(self) -> Dict[str, object]:
        with self._lock:
            b = self._backend
            configured = bool((self.store.section("ocr", {}) if self.store else {}).get(
                "enabled", True))
            return {
                # Status is queried by Qt widgets; never import/load ONNX here.
                "ready": bool(b and b.available()),
                "loading": self._initializing,
                "enabled": configured,
                "backend": b.name if b else "rapidocr_onnxruntime" if configured else "none",
                "reason": b.describe() if b else ("等待后台首次识别" if configured else "OCR 已在配置中关闭"),
                "has_text": bool(self._text),
                "text_len": len(self._text),
                "last_duration_ms": round(self._last_duration * 1000, 1),
            }

    # ------------------------------------------------------------ 结果
    def last_text(self) -> str:
        with self._lock:
            return self._text

    def cached_text(self, ttl_ms: int) -> str:
        with self._lock:
            if self._text and (time.time() - self._at) * 1000.0 <= ttl_ms:
                return self._text
        return ""

    # ------------------------------------------------------------ 采集
    def recognize_capture(self, cap: "gdi.Capture") -> str:
        """对给定画面做一次 OCR（不碰缓存与工作线程；失败返回空串）。"""
        backend = self._ensure_backend()
        if not backend.available():
            return ""
        try:
            with self._inference_lock:
                return backend.recognize(cap) or ""
        except Exception:                       # noqa: BLE001
            log.exception("截图 OCR 失败")
            return ""

    def refresh(self, force: bool = False, hwnd: int = 0) -> str:
        """同步识别一次。force=False 时命中缓存直接返回。"""
        b = self._ensure_backend()
        if not b.available():
            return self.last_text()
        cfg = self.store.section("ocr", {}) if self.store else {}
        fg = w32.get_foreground()
        if not fg.valid or (hwnd and fg.hwnd != hwnd):
            return self.last_text()
        with self._lock:
            if (not force and fg.hwnd == self._hwnd and self._text
                    and (time.time() - self._at) * 1000.0 <= int(cfg.get("cache_ttl_ms", 5000))):
                return self._text
            self._hwnd = fg.hwnd

        t0 = time.perf_counter()
        cap = gdi.capture_window(fg.hwnd, str(cfg.get("region", "client")),
                                 int(cfg.get("max_width", 1280)))
        if cap is None:
            log.info("窗口截取失败：%s", fg.process)
            return self.last_text()
        try:
            with self._inference_lock:
                text = b.recognize(cap)
        except Exception as e:
            log.exception("OCR 识别失败")
            log.info("识别失败：%s" % e)
            return self.last_text()
        if w32.get_foreground().hwnd != fg.hwnd:
            log.debug("丢弃过期 OCR 结果（窗口已切换）")
            return self.last_text()
        with self._lock:
            self._text = text
            self._at = time.time()
            self._last_duration = time.perf_counter() - t0
        log.info("OCR 完成 %d 字，耗时 %.0fms（%s）", len(text), self._last_duration * 1000, b.name)
        return text

    # ------------------------------------------------------------ 防抖调度
    def on_window_changed(self, hwnd: int) -> None:
        """窗口切换后延迟触发一次识别（方案要求的 ~300ms 防抖）。"""
        if not self.store:
            return
        cfg = self.store.section("ocr", {})
        if not cfg.get("enabled", True):
            return
        with self._lock:
            if self._target_hwnd == int(hwnd):
                return
            self._target_hwnd = int(hwnd)
        self._pending.set()

    def start_worker(self, interval: float = 0.2) -> None:
        if self._worker is not None:
            return
        self._stop.clear()

        def loop() -> None:
            try:
                while not self._stop.is_set():
                    if self._pending.wait(timeout=interval):
                        self._pending.clear()
                        deb = 0.3
                        if self.store:
                            deb = float(self.store.section("ocr", {}).get("debounce_ms", 300)) / 1000.0
                        # Coalesce rapid focus changes and only recognize the latest target.
                        while True:
                            if self._stop.wait(deb):
                                break
                            if self._pending.is_set():
                                self._pending.clear()
                                continue
                            break
                        if self._stop.is_set():
                            break
                        with self._lock:
                            target = self._target_hwnd
                        try:
                            if target:
                                self.refresh(hwnd=target)
                        except Exception:
                            log.exception("OCR 后台刷新失败")
            finally:
                with self._lock:
                    if self._worker is threading.current_thread():
                        self._worker = None
        self._worker = threading.Thread(target=loop, name="ocr-worker", daemon=True)
        self._worker.start()

    def stop_worker(self) -> None:
        self._stop.set()
        self._pending.set()
        worker = self._worker
        if worker and worker is not threading.current_thread():
            worker.join(timeout=2.0)
        if worker is None or not worker.is_alive():
            self._worker = None
