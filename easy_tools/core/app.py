"""应用服务层（组合根，无 Qt）。

把配置、上下文、OCR、事件链、钩子接成一个可用的整体，并把"面板需要什么"
收敛成两个纯数据方法：
    public_groups()  -> 公共操作区
    context_groups() -> 特定操作区
UI 只做渲染与交互，全部业务判断留在这里，便于无 GUI 测试。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ..win32 import window as w32
from . import focus
from .actions import base as action_base
from .actions.base import ActionContext, ActionResult
from .chain import ChainRunner
from .config_store import ConfigStore
from .context import ContextResolver
from .hooks import HookManager
from .log import get_logger
from .ocr import OcrService
from .question_bank import QuestionAnswerService, verified_bank_answer, strip_choice_labels
from .ccswitch import CCSwitchModel
from .builtin_model import BuiltinModel

log = get_logger("app")


@dataclass
class PanelItem:
    action_id: str
    name: str
    icon: str
    group: str
    zone: str            # public / context
    source: str          # 触发它的规则名，公共区为空

    def matches(self, keyword: str) -> bool:
        k = (keyword or "").strip().lower()
        if not k:
            return True
        return k in self.name.lower() or k in self.group.lower()


class AppService:
    """全局唯一的服务对象。UI、托盘、钩子都通过它交互。"""

    _instance: Optional["AppService"] = None

    def __init__(self, store: Optional[ConfigStore] = None) -> None:
        self.store = store or ConfigStore.instance()
        self.resolver = ContextResolver(self.store)
        self.ocr = OcrService(self.store)
        self.question_answer = QuestionAnswerService(self.store)
        self.ccswitch_model = CCSwitchModel()
        self.builtin_model = BuiltinModel()
        self.runner = ChainRunner(self.store)
        self.hooks: Optional[HookManager] = None
        self._ui_callbacks: Dict[str, Callable] = {}
        self._last_window = (0, 0)
        self._watch_stop = threading.Event()
        self._watcher: Optional[threading.Thread] = None

    @classmethod
    def instance(cls) -> "AppService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------ UI 回调注入
    def bind_ui(self, **callbacks: Callable) -> None:
        """UI 层启动时把"打开设置/显示面板/通知"等回调注入进来。"""
        self._ui_callbacks.update({k: v for k, v in callbacks.items() if callable(v)})

    def action_context(self) -> ActionContext:
        return ActionContext(
            store=self.store,
            ocr=self.ocr,
            chain_runner=self.runner,
            open_settings=self._ui_callbacks.get("open_settings"),
            show_panel=self._ui_callbacks.get("show_panel"),
            notify=self._ui_callbacks.get("notify"),
            start_capture=self._ui_callbacks.get("start_capture"),
            ask_question=self._ui_callbacks.get("ask_question"),
        )

    # ------------------------------------------------------------ 启动/停止
    def start(self) -> dict:
        log.info("Easy-Tools 服务启动")
        self.store.start_watching()
        status = {}
        if self.hooks is None:
            self.hooks = HookManager(
                store=self.store,
                on_tap=self._on_middle_tap,
                on_scroll=self._on_drag_scroll,
                on_hotkey=self._on_hotkey,
                on_other_down=self._on_other_down,
            )
        status = self.hooks.start()
        self.ocr.start_worker()
        self._start_window_watcher()
        log.info("钩子状态：%s", status)
        return status

    def stop(self) -> None:
        self._watch_stop.set()
        self.ocr.stop_worker()
        watcher = self._watcher
        if watcher and watcher is not threading.current_thread():
            watcher.join(timeout=1.0)
        if watcher is None or not watcher.is_alive():
            self._watcher = None
        if self.hooks:
            self.hooks.stop()
            self.hooks = None
        self.builtin_model.stop()
        self.store.stop_watching()
        log.info("Easy-Tools 服务已停止")

    def answer_question(self, prompt: str, evidence: list[dict] | None = None) -> str:
        """Route a single-turn question to the embedded local model or CC Switch."""
        if evidence and (evidence[0].get("match_warning") or evidence[0].get("answer_content_missing")):
            return str(evidence[0]["answer"])
        verified = verified_bank_answer(evidence)
        if verified is not None:
            return verified
        cfg = self.store.section("llm", {})
        if not cfg.get("enabled", True):
            raise RuntimeError("请先在配置中心启用问答功能。")
        if cfg.get("answer_content_only", False) and evidence:
            prompt += "\n只输出正确答案的内容，不输出选项字母或知识库的 A/B/C/D 序号；选项可能已重新排列。"
        if cfg.get("provider", "builtin") == "ccswitch":
            answer = self.ccswitch_model.answer(
                prompt, evidence, selected_app=str(cfg.get("ccswitch_app") or ""),
                allow_cloud=bool(cfg.get("allow_cloud", False)),
                expected_url=str(cfg.get("ccswitch_base_url") or ""))
        else:
            answer = self.builtin_model.answer(prompt, evidence)
        if cfg.get("answer_content_only", False) and evidence and evidence[0].get("question_type") in {"single", "multiple"}:
            return strip_choice_labels(answer)
        return answer

    # ------------------------------------------------------------ 钩子回调
    def _on_middle_tap(self, event) -> None:
        log.info("中键短按 @ (%s,%s) %.0fms", event.x, event.y, event.duration_ms)
        # 面板已开时再次中键 = 关闭（切换语义）：面板开着时用户最自然的
        # 动作就是再按一下中键。未注册 toggle_panel 时退回 show_panel。
        toggle = self._ui_callbacks.get("toggle_panel")
        if toggle:
            try:
                toggle(event.x, event.y)
            except Exception:
                log.exception("切换面板回调异常")
            return
        show = self._ui_callbacks.get("show_panel")
        if show:
            try:
                show(event.x, event.y)
            except Exception:
                log.exception("面板显示回调异常")
        else:
            log.info("（无 UI 环境，仅记录触发）")

    def _on_drag_scroll(self, event) -> None:
        log.debug("中键拖动滚动 %d tick", event.ticks)

    def _on_other_down(self, x: int, y: int) -> None:
        """左/右键按下（物理坐标）：交给 UI 判断是否点在面板外。"""
        cb = self._ui_callbacks.get("outside_click")
        if cb:
            try:
                cb(x, y)
            except Exception:
                log.exception("面板外点击回调异常")

    def _on_hotkey(self, name: str) -> None:
        log.info("热键触发：%s", name)
        if name == "panel":
            show = self._ui_callbacks.get("show_panel")
            if show:
                x, y = w32.cursor_pos()
                show(x, y)

    def _start_window_watcher(self) -> None:
        """轮询前台窗口变化：驱动 L2 OCR 防抖刷新与 window_switch 触发器。"""
        if self._watcher is not None:
            return
        self._watch_stop.clear()

        def loop() -> None:
            while not self._watch_stop.wait(0.35):
                try:
                    fg = w32.get_foreground()
                    key = (fg.hwnd, fg.process)
                    if key != self._last_window:
                        self._last_window = key
                        self.resolver.invalidate()
                        if (not w32.is_our_window(fg.hwnd)
                                and self.resolver.needs_ocr()):
                            self.ocr.on_window_changed(fg.hwnd)
                        self._fire_triggers(fg)
                except Exception:
                    log.exception("窗口监听异常")
        self._watcher = threading.Thread(target=loop, name="win-watcher", daemon=True)
        self._watcher.start()

    def _fire_triggers(self, fg) -> None:
        """窗口切换触发器。"""
        for chain in self.store.section("chains", []) or []:
            trig = chain.get("trigger") or {}
            if trig.get("type") != "window_switch":
                continue
            procs = [str(p).lower() for p in (trig.get("processes") or [])]
            if procs and fg.process not in procs:
                continue
            log.info("触发事件链（窗口切换）：%s", chain.get("name"))
            threading.Thread(target=self.runner.run_chain,
                             args=(chain, self.action_context()),
                             daemon=True).start()

    # ------------------------------------------------------------ 面板数据
    def public_groups(self) -> Dict[str, List[PanelItem]]:
        cfg = self.store.get()
        out: Dict[str, List[PanelItem]] = {}
        for a in sorted(cfg.get("actions", []), key=lambda a: a.get("order", 100)):
            if a.get("zone", "public") != "public":
                continue
            out.setdefault(a.get("group", "未分组"), []).append(PanelItem(
                action_id=a.get("id", ""), name=a.get("name", ""), icon=a.get("icon", "star"),
                group=a.get("group", "未分组"), zone="public", source=""))
        return out

    def context_groups(self) -> Dict[str, List[PanelItem]]:
        state = self.resolver.current()
        out: Dict[str, List[PanelItem]] = {}
        for rule_name, actions in self.resolver.actions_for(state).items():
            # 规则仅展示明确标为软件专属的动作，避免公共动作被规则重复显示。
            out[rule_name] = [PanelItem(action_id=a.get("id", ""), name=a.get("name", ""),
                                        icon=a.get("icon", "star"), group=a.get("group", rule_name),
                                        zone="context", source=rule_name)
                              for a in actions if a.get("zone") == "context"]
            if not out[rule_name]:
                del out[rule_name]
        return out

    def panel_snapshot(self) -> Dict[str, Any]:
        st = self.resolver.current()
        return {
            "public": self.public_groups(),
            "context": self.context_groups(),
            "window": st.window.to_dict(),
            "ocr_ready": bool(self.ocr.status().get("ready")),
            "ocr_has_text": bool(st.ocr_text),
        }

    def refresh_context(self, with_ocr: bool = False) -> None:
        """面板弹出前调用：更新 L1，并按需同步跑一次 L2。

        方案决策 2：面板不等待 OCR。这里默认不跑 OCR，
        只有用户在配置里要求"弹出前同步识别"时才付出这个代价。
        """
        self.resolver.current(refresh=True)
        if with_ocr:
            text = self.ocr.refresh(force=True)
            self.resolver.update_ocr(text, self.ocr.status().get("backend", "none"))

    # ------------------------------------------------------------ 动作执行
    def find_action(self, action_id: str) -> Optional[dict]:
        for a in self.store.section("actions", []) or []:
            if a.get("id") == action_id:
                return a
        return None

    def run_action(self, action_id: str, background: bool = True) -> ActionResult:
        """执行动作。默认放到后台线程，避免长文本手打阻塞面板关闭。"""
        action = self.find_action(action_id)
        if action is None:
            return ActionResult.failure("动作不存在：%s" % action_id)
        kind = action.get("kind", "")
        params = dict(action.get("params") or {})

        def _run() -> ActionResult:
            return action_base.run_action(kind, self.action_context(), params)

        if not background:
            return _run()
        out: Dict[str, ActionResult] = {}

        def worker() -> None:
            try:
                r = _run()
            except Exception as e:                  # noqa: BLE001
                log.exception("动作执行异常：%s", action_id)
                r = ActionResult.failure("动作执行异常：%s" % e)
            out["r"] = r
            # 后台执行拿不到返回值，失败必须在这里自己报告出来——
            # 否则用户点按钮"什么也没发生"，还不知道是执行失败。
            if r.cancelled:
                notify = self._ui_callbacks.get("notify")
                if notify:
                    try:
                        notify("info", r.message)
                    except Exception:
                        log.exception("notify 回调异常")
            elif not r.ok:
                log.warning("动作「%s」失败：%s", action.get("name", action_id), r.message)
                notify = self._ui_callbacks.get("notify")
                if notify:
                    try:
                        notify("warning", "「%s」失败：%s"
                               % (action.get("name", action_id), r.message))
                    except Exception:               # noqa: BLE001
                        log.exception("notify 回调异常")
            else:
                log.info("动作「%s」：%s", action.get("name", action_id), r.message)

        threading.Thread(target=worker, name="action-%s" % action_id, daemon=True).start()
        return ActionResult(True, "已开始执行 %s" % action.get("name", action_id))

    def run_chain(self, chain_id: str, background: bool = True) -> None:
        chain = self.runner.find_chain(chain_id)
        if chain is None:
            log.warning("事件链不存在：%s", chain_id)
            return
        if not background:
            self.runner.run_chain(chain, self.action_context())
            return
        threading.Thread(target=self.runner.run_chain,
                         args=(chain, self.action_context()),
                         name="chain-%s" % chain_id, daemon=True).start()

    # ------------------------------------------------------------ 托盘状态
    def status_text(self) -> str:
        st = self.hooks.status() if self.hooks else {"mouse": False, "keyboard": False}
        fg = self.resolver.current(refresh=True)
        ocr = self.ocr.status()
        return "中键钩子：%s | 热键钩子：%s | 前台：%s | OCR：%s" % (
            "运行" if st["mouse"] else "未启用",
            "运行" if st["keyboard"] else "未启用",
            fg.window.process or "-",
            "就绪" if ocr.get("ready") else str(ocr.get("reason", "不可用"))[:40],
        )
