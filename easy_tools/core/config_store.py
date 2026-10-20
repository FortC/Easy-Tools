"""配置存储：线程安全 + 保存即生效（热更新）。

三条硬需求来自方案 2.7 / 决策 4：
- 保存即生效，无需重启  -> save() 后广播 change 事件，钩子/面板/规则实时重载
- 可导入导出          -> export_json()/import_json()
- 单文件 JSON 可读     -> data/config.json，UTF-8，缩进 2

并发约定：
- 本进程内可能同时有：GUI 主线程、钩子线程、OCR 线程、链执行线程。
- 所有读写都过 RLock；回调列表在广播时拷贝一份再调用，避免回调里
  再次 save() 造成死锁。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..config import schema
from ..core.log import get_logger
from ..core.paths import config_path

log = get_logger("config")

ChangeHandler = Callable[[Dict[str, Any]], None]


class ConfigStore:
    """全局唯一的配置门面。"""

    _instance: Optional["ConfigStore"] = None
    _singleton_lock = threading.Lock()

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else config_path()
        self._lock = threading.RLock()
        self._cfg: Dict[str, Any] = schema.normalize({})
        self._handlers: List[ChangeHandler] = []
        self._mtime: float = 0.0
        self._watching = False
        self._watch_stop = threading.Event()

    # ---------------------------------------------------------------- 单例
    @classmethod
    def instance(cls, path: Optional[Path] = None) -> "ConfigStore":
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls(path)
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """测试用：丢弃单例。"""
        with cls._singleton_lock:
            cls._instance = None

    # ---------------------------------------------------------------- 读
    @property
    def path(self) -> Path:
        return self._path

    def get(self) -> Dict[str, Any]:
        """返回当前配置的**深拷贝**。

        刻意返回副本：调用方（UI/引擎）随便改也不会污染全局状态，
        想落盘必须走 update()/save()，避免"改了没保存"的幽灵状态。
        """
        with self._lock:
            return json.loads(json.dumps(self._cfg))

    def section(self, name: str, default: Any = None) -> Any:
        with self._lock:
            return json.loads(json.dumps(self._cfg.get(name, default)))

    # ---------------------------------------------------------------- 事件
    def subscribe(self, handler: ChangeHandler) -> Callable[[], None]:
        with self._lock:
            self._handlers.append(handler)

        def unsubscribe() -> None:
            with self._lock:
                if handler in self._handlers:
                    self._handlers.remove(handler)

        return unsubscribe

    def _broadcast(self) -> None:
        with self._lock:
            handlers = list(self._handlers)
            snapshot = json.loads(json.dumps(self._cfg))
        for h in handlers:
            try:
                h(snapshot)
            except Exception:  # 单个订阅者出错不能拖垮全局热更新
                log.exception("配置变更回调异常: %r", h)

    # ---------------------------------------------------------------- 写
    def update(self, cfg: Dict[str, Any], *, persist: bool = True, validate: bool = True) -> List[str]:
        """整体替换配置。返回错误列表；非空表示**未生效**。

        顺序：normalize -> validate -> 落盘 -> 广播。
        先落盘再广播，保证"内存已生效则磁盘一定已生效"。
        """
        merged = schema.normalize(cfg)
        if validate:
            errs = schema.validate(merged)
            if errs:
                log.warning("配置校验失败，已拒绝本次更新: %s", errs)
                return errs
        if persist:
            self._atomic_write(merged)
        with self._lock:
            self._cfg = merged
        self._broadcast()
        return []

    def patch(self, section: str, value: Any, *, persist: bool = True) -> List[str]:
        """只改一个顶层 section 并校验，用于设置页的即时保存。"""
        cfg = self.get()
        cfg[section] = value
        return self.update(cfg, persist=persist)

    def save(self) -> None:
        """把当前内存配置落盘（设置页保存按钮）。"""
        with self._lock:
            self._atomic_write(self._cfg)

    def reload(self, *, broadcast: bool = True) -> bool:
        """从磁盘重读。返回是否真的发生变化。"""
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            # 首次启动：把默认配置**真正落盘**，而不是只在内存里用默认值。
            # 理由：用户需要一个能打开、能编辑、能备份、能用导入导出替换的
            # 真实文件；README 也承诺了"首次启动从 defaults.json 复制"。
            # 只在内存里兜底会让"配置文件在哪"这个问题没有答案。
            log.info("配置文件不存在，写入默认配置: %s", self._path)
            raw = {}
            self._cfg = schema.normalize(raw)
            with self._lock:
                self._atomic_write(self._cfg)
                self._mtime = self._current_mtime()
            return True
        except Exception as e:
            log.error("配置文件解析失败，保留当前内存配置: %s", e)
            return False
        merged = schema.normalize(raw)
        with self._lock:
            changed = json.dumps(merged, sort_keys=True) != json.dumps(self._cfg, sort_keys=True)
            self._cfg = merged
            self._mtime = self._current_mtime()
        if changed and broadcast:
            log.info("配置已重载（外部修改）")
            self._broadcast()
        return changed

    # ---------------------------------------------------------------- 导入导出
    def export_json(self) -> str:
        return json.dumps(self.get(), ensure_ascii=False, indent=2)

    def import_json(self, text: str) -> List[str]:
        """从 JSON 字符串导入并立即生效。失败返回错误列表。"""
        try:
            raw = json.loads(text)
        except Exception as e:
            return [f"JSON 解析失败：{e}"]
        if not isinstance(raw, dict):
            return ["配置根节点必须是对象"]
        return self.update(raw)

    # ---------------------------------------------------------------- 文件监听
    def start_watching(self, interval: float = 1.5) -> None:
        """轮询 mtime 实现外部改动热加载。

        为什么不用 watchdog：那是又一个原生依赖，而本项目刻意保持
        "Win32 层零第三方依赖"。1.5s 轮询对配置文件完全够用。
        """
        if self._watching:
            return
        self._watching = True
        self._watch_stop.clear()

        def loop() -> None:
            while not self._watch_stop.wait(interval):
                mt = self._current_mtime()
                if mt and abs(mt - self._mtime) > 1e-6:
                    self.reload()
                    self._mtime = mt

        threading.Thread(target=loop, name="config-watcher", daemon=True).start()

    def stop_watching(self) -> None:
        self._watch_stop.set()
        self._watching = False

    # ---------------------------------------------------------------- 内部
    def _current_mtime(self) -> float:
        try:
            return os.path.getmtime(self._path)
        except OSError:
            return 0.0

    def _atomic_write(self, cfg: Dict[str, Any]) -> None:
        """先写临时文件再 os.replace 原子替换，避免半截配置被读。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
            self._mtime = self._current_mtime()
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


_store: Optional[ConfigStore] = None


def store() -> ConfigStore:
    """模块级便捷入口。"""
    global _store
    if _store is None:
        _store = ConfigStore.instance()
    return _store


def on_change(handler: ChangeHandler) -> Callable[[], None]:
    return store().subscribe(handler)
