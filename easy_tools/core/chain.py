"""事件链引擎（方案 2.5）。

执行模型：解释器，不是编译器。
- 每执行一个 action 步骤，计数 +1，并记录 stepN.output 与 last.output；
- 步骤的 input 字段是模板，先做变量展开再执行；
- 变量语法：{clipboard} {selection} {ocr_text} {last.output} {step3.output} {var:x}

刻意不支持的：
- 不支持任意表达式（eval）——配置是用户可手写的 JSON，跑 eval 等于
  把任意代码执行权限交给"谁改了 config.json"，这不是能力，是安全债；
- 条件只用 processors.eval_condition 里那一组可枚举表达式。

链步骤的 id 解析优先级：
  1) 剪贴板处理器（text.* / table.* / number.* / case.* / date.* / data.*）
  2) 已注册动作 kind（clipboard.copy / input.paste / ...）
这样"每个处理器就是可复用的链步骤"（方案 2.5）天然成立，无需两套注册。
"""

from __future__ import annotations

import re
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional

from ..win32 import clipboard as clip
from .actions import base as action_base
from .actions.base import ActionContext, ActionResult
from .actions.processors import eval_condition, get_processor
from .log import get_logger

log = get_logger("chain")

_VAR = re.compile(r"\{([^{}]+)\}")
MAX_LOOP = 1000
_SMALL_SAFE_PROCESSORS = {
    'text.strip', 'text.drop_blank_lines', 'text.collapse_spaces', 'text.remove_invisible',
    'case.upper', 'case.lower', 'case.title', 'case.camel', 'case.snake', 'case.kebab',
    'case.reverse_words', 'word.clean_paragraphs', 'word.remove_list_marks',
    'word.extract_emails', 'data.normalize_width',
}


@dataclass
class ChainResult:
    ok: bool = True
    message: str = ""
    output: str = ""
    steps_run: int = 0
    log: List[str] = field(default_factory=list)
    trace: List[dict] = field(default_factory=list)

    def fail(self, msg: str) -> "ChainResult":
        self.ok = False
        self.message = msg
        return self


class ChainContext:
    """一次链执行的变量环境。线程内独占，不需要加锁。"""

    def __init__(self, ocr_text: str = "", sample: Optional[str] = None) -> None:
        self.locals: Dict[str, str] = {}
        self.outputs: Dict[int, str] = {}
        self.last: str = ""
        self.step_no: int = 0
        self._ocr = ocr_text
        self.sample = sample

    def current_text(self) -> str:
        """条件判定与"隐式输入"用的当前文本。"""
        return self.last

    def providers(self) -> Dict[str, Any]:
        return {
            "clipboard": lambda: self.sample if self.sample is not None else clip.get_text() or "",
            "selection": lambda: self.sample if self.sample is not None else clip.get_text() or "",
            "ocr_text": lambda: self._ocr,
            "last": lambda: self.last,
            "last.output": lambda: self.last,
        }

    def resolve(self, template: Optional[str], default: str = "") -> str:
        """展开变量。未知变量原样保留；{stepN.output} 取不到时回退到 last。"""
        if template is None:
            return default
        text = str(template)
        if "{" not in text:
            return text
        prov = self.providers()

        def repl(m: "re.Match") -> str:
            key = m.group(1).strip()
            if key.startswith("step"):
                mo = re.fullmatch(r"step(\d+)\.output", key)
                if mo:
                    idx = int(mo.group(1))
                    return self.outputs.get(idx, self.last)
                return m.group(0)
            if key.startswith("var:"):
                return self.locals.get(key[4:].strip(), m.group(0))
            fn = prov.get(key)
            if fn is None:
                return m.group(0)
            try:
                return fn() or ""
            except Exception:
                log.exception("变量求值异常: %s", key)
                return m.group(0)

        out = _VAR.sub(repl, text)
        return out if out != "" or "{step" not in text else default

    def set_step_output(self, text: str) -> None:
        if len(text) > 1_000_000 or sum(map(len, self.outputs.values())) + len(text) > 8_000_000:
            raise ValueError('步骤输出超过内存预算，请缩小输入范围')
        self.step_no += 1
        self.outputs[self.step_no] = text
        self.last = text


class ChainRunner:
    """执行事件链。可在任意线程调用。"""

    def __init__(self, store=None, *, preview=False, sample="", timeout=120) -> None:
        self.store = store
        self._cancel = threading.Event()
        self._python_process = None
        self._python_process_lock = threading.Lock()
        self.preview, self.sample, self.timeout = preview, sample, timeout
        self._depth = 0
        self._stack = []
        self._budget = 10000
        self._deadline = time.monotonic() + timeout
        self._job = None

    def cancel(self) -> None:
        self._cancel.set()
        with self._python_process_lock:
            process = self._python_process
        if process is not None:
            try:
                process.kill()
            except OSError:
                pass
        if self._job:
            self._job.close()

    # ------------------------------------------------------------ 入口
    def find_chain(self, chain_id: str) -> Optional[Dict[str, Any]]:
        cfg = self.store.get() if self.store else {"chains": []}
        for c in cfg.get("chains", []):
            if c.get("id") == chain_id:
                return c
        return None

    def run_chain(self, chain: Dict[str, Any], ctx: ActionContext,
                  cctx: Optional[ChainContext] = None) -> ChainResult:
        name = chain.get("name", chain.get("id", "chain"))
        cid = str(chain.get('id') or id(chain))
        if cid in self._stack or len(self._stack) >= 12:
            return ChainResult(False, "工作流递归调用或嵌套过深，已停止")
        from .automation import input_lock
        root = not self._stack
        if root and not self.preview and not input_lock.acquire(False):
            return ChainResult(False, '另一项输入操作正在执行，请稍后重试')
        if root:
            self._budget = 10000
            self._deadline = time.monotonic() + self.timeout
        self._stack.append(cid)
        log.info("事件链开始：%s", name)
        try:
            context = cctx or ChainContext(sample=self.sample if self.preview else None)
            if self.preview and cctx is None:
                context.last = self.sample
            res = self.run_steps(chain.get("steps", []), replace(ctx, chain_runner=self, deadline=self._deadline), context)
        finally:
            self._stack.pop()
            if root and not self.preview:
                input_lock.release()
        res.log.insert(0, "链「%s」%s：%s" % (name, "成功" if res.ok else "失败",
                                            res.message or f"执行 {res.steps_run} 步"))
        log.info("工作流结束 ok=%s steps=%s", res.ok, res.steps_run)
        return res

    def run_by_id(self, chain_id: str, ctx: ActionContext) -> ChainResult:
        chain = self.find_chain(chain_id)
        if chain is None:
            return ChainResult(False, "事件链不存在：%s" % chain_id)
        return self.run_chain(chain, ctx)

    # ------------------------------------------------------------ 步骤
    def run_steps(self, steps: List[Dict[str, Any]], ctx: ActionContext,
                  cctx: ChainContext) -> ChainResult:
        res = ChainResult()
        try:
            self._exec(steps, ctx, cctx, res)
        except Exception as e:
            log.exception("事件链执行异常")
            res.fail("执行异常：%s" % e)
        for entry in res.trace:
            if entry['status'] == 'running':
                entry['status'] = 'cancelled' if self._cancel.is_set() else 'failed'
        res.output = cctx.last
        return res

    def _exec(self, steps: List[Dict[str, Any]], ctx: ActionContext,
              cctx: ChainContext, res: ChainResult) -> None:
        if self._depth >= 12:
            res.fail("工作流嵌套不能超过 12 层")
            return
        self._depth += 1
        try:
            self._exec_body(steps, ctx, cctx, res)
        finally:
            self._depth -= 1

    def _exec_body(self, steps, ctx, cctx, res):
        for idx, step in enumerate(steps or []):
            self._budget -= 1
            if self._budget < 0 or time.monotonic() >= self._deadline:
                res.fail("工作流超过总步数或运行时间上限")
                return
            if len(cctx.last) > 1_000_000 or sum(map(len, cctx.outputs.values())) > 8_000_000:
                res.fail("工作流结果超过内存预算，请缩小输入范围")
                return
            if self._cancel.is_set():
                res.fail("已被取消")
                return
            if not res.ok:
                return
            stype = str(step.get("type", "action"))
            started = time.monotonic()
            if len(res.trace) < 200:
                entry = dict(step=str(step.get('id') if stype == 'action' else stype),
                             status='running', seconds=0.0)
                res.trace.append(entry)
            else:
                entry = None
            if stype == "action":
                self._do_action(step, ctx, cctx, res)
            elif stype == "condition":
                self._do_condition(step, ctx, cctx, res)
            elif stype == "wait":
                secs = self._number(cctx.resolve(str(step.get("seconds", "0.3"))), 0.3)
                if not self.preview and self._cancel.wait(min(max(0.0, secs), 60.0,
                                                               max(0, self._deadline - time.monotonic()))):
                    res.fail("已被取消")
                    return
            elif stype == "setvar":
                cctx.locals[str(step.get("name", "x"))] = cctx.resolve(str(step.get("value", "")))
            elif stype == "python":
                if self.preview:
                    res.fail("预览不运行 Python 脚本；请检查授权后使用真实运行")
                else:
                    self._do_python(step, ctx, cctx, res)
            elif stype == "loop":
                self._do_loop(step, ctx, cctx, res)
            elif stype == "stop":
                res.fail(cctx.resolve(str(step.get("message", "链已中止"))))
            else:
                res.fail("第 %d 步类型非法：%s" % (idx + 1, stype))
            if entry is not None:
                entry.update(status='succeeded' if res.ok else 'failed', seconds=round(time.monotonic() - started, 3))

    def _do_loop(self, step: Dict[str, Any], ctx: ActionContext,
                 cctx: ChainContext, res: ChainResult) -> None:
        times = int(self._number(cctx.resolve(str(step.get("times", "1"))), 1))
        times = max(1, min(MAX_LOOP, times))
        body = step.get("steps", [])
        for i in range(times):
            cctx.locals["loop_index"] = str(i)
            self._exec(body, ctx, cctx, res)
            if not res.ok:
                return

    def _do_condition(self, step: Dict[str, Any], ctx: ActionContext,
                      cctx: ChainContext, res: ChainResult) -> None:
        expr = str(step.get("expr", "always"))
        # 注意：这里必须用 .get("input") 取到 None，而不是 .get("input", "") 取到空串。
        # 空串会让 has_table 等条件恒为 False，分支永远走错——这类 bug 静默且致命。
        subject = cctx.resolve(step.get("input"), cctx.current_text())
        try:
            if expr.startswith('regex:'):
                from .processor_worker import run_processor
                hit = run_processor('__condition__', subject, dict(expr=expr, variables={'var': cctx.locals}),
                                    self._cancel, timeout=min(5, max(0.1, self._deadline - time.monotonic())))
            else:
                hit = eval_condition(expr, subject, {"var": cctx.locals})
        except ValueError as e:
            res.fail(str(e))
            return
        branch = step.get("then") if hit else step.get("else")
        self._exec(branch or [], ctx, cctx, res)

    def _do_action(self, step: Dict[str, Any], ctx: ActionContext,
                   cctx: ChainContext, res: ChainResult) -> None:
        sid = str(step.get("id", "")).strip()
        if not sid:
            res.fail("action 步骤缺少 id")
            return
        text_in = cctx.resolve(step.get("input"), cctx.current_text())   # 同上：None 才有回退语义
        args = dict(step.get("args") or {})

        if self.preview and get_processor(sid) is None:
            reads = {"clipboard.get", "clipboard.copy", "ocr.copy_text"}
            outputs = {"clipboard.set", "clipboard.clear", "input.paste", "input.type", "text.input"}
            if sid in reads | outputs:
                cctx.set_step_output(self.sample if sid in reads else text_in)
                res.steps_run += 1
                res.log.append("预览：模拟 %s，未改变剪贴板或目标窗口" % sid)
            else:
                res.fail("预览已阻止具有外部副作用或未知能力的步骤：%s" % sid)
            return

        # 1) 剪贴板处理器
        proc = get_processor(sid)
        if proc is not None:
            try:
                if len(text_in) > 100000 or sid not in _SMALL_SAFE_PROCESSORS:
                    from .processor_worker import run_processor
                    out = run_processor(sid, text_in, args, self._cancel,
                                        timeout=max(0.1, min(20, self._deadline - time.monotonic())))
                else:
                    out = proc.run(text_in, args)
            except Exception as e:
                res.fail("处理器 %s 失败：%s" % (sid, e))
                return
            cctx.set_step_output(out)
            res.steps_run += 1
            return

        # 2) 已注册动作
        params = dict(step.get("params") or {})
        params["input"] = text_in
        params.update(args)
        r: ActionResult = action_base.run_action(sid, ctx, params)
        res.steps_run += 1
        if not r.ok:
            res.fail("步骤 %s 失败：%s" % (sid, r.message))
            return
        cctx.set_step_output(r.output or text_in)

    def _do_python(self, step: Dict[str, Any], ctx: ActionContext,
                   cctx: ChainContext, res: ChainResult) -> None:
        """Run an explicitly trusted Python node out of process.

        This protects the GUI from hangs/crashes, not the user's account from
        arbitrary code. The editor requires an explicit trust acknowledgement.
        """
        if step.get("trusted") is not True:
            res.fail("Python 脚本尚未获信任授权；请在事件链编辑器中确认脚本权限后再运行")
            return
        source = str(step.get("code", ""))
        import hashlib
        digest = step.get('trusted_hash')
        if digest and digest != hashlib.sha256(source.encode('utf-8')).hexdigest():
            res.fail("Python 脚本已修改，请重新确认信任授权")
            return
        if not source.strip():
            res.fail("Python 脚本内容为空")
            return
        text_in = cctx.resolve(step.get("input"), cctx.current_text())
        payload = json.dumps({"code": source, "input_text": text_in,
                              "variables": cctx.locals}, ensure_ascii=False).encode("utf-8")
        timeout = self._number(cctx.resolve(str(step.get("timeout_seconds", 5))), 5)
        timeout = max(1.0, min(30.0, timeout))
        timeout = min(timeout, max(0.1, self._deadline - time.monotonic()))
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--run-python-node"]
        else:
            from pathlib import Path
            entry = Path(__file__).resolve().parents[2] / "main.py"
            command = [sys.executable, str(entry), "--run-python-node"]
        flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                 | getattr(subprocess, "DETACHED_PROCESS", 0))
        try:
            from .process_job import spawn_worker
            process, self._job = spawn_worker(command, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=os.path.abspath(os.getenv("TEMP", ".")),
                memory_limit=512 * 1024 * 1024,
                creationflags=flags)
        except OSError as exc:
            res.fail("启动 Python 节点失败：%s" % exc)
            return
        with self._python_process_lock:
            self._python_process = process
            if self._cancel.is_set():
                try:
                    process.kill()
                except OSError:
                    pass
        try:
            try:
                from .process_job import communicate_bounded
                stdout, stderr = communicate_bounded(process, payload, self._job, self._cancel, timeout)
            except subprocess.TimeoutExpired:
                self._job.close()
                process.kill()
                res.fail("Python 脚本执行超时（%.0f 秒），子进程已终止" % timeout)
                return
            if self._cancel.is_set():
                res.fail("Python 脚本已取消")
                return
            try:
                response = json.loads(stdout.decode("utf-8", errors="replace").strip().splitlines()[-1])
            except (ValueError, IndexError):
                detail = stderr.decode("utf-8", errors="replace")[-1000:]
                res.fail("Python 脚本进程返回无效结果%s" % ("：" + detail if detail else ""))
                return
            if not response.get("ok"):
                res.fail("Python 脚本错误：%s" % str(response.get("error", "未知错误"))[:1000])
                return
            output = str(response.get("output", ""))
            cctx.set_step_output(output)
            res.steps_run += 1
        finally:
            if self._job:
                self._job.close()
                self._job = None
            with self._python_process_lock:
                if self._python_process is process:
                    self._python_process = None

    @staticmethod
    def _number(text: str, default: float) -> float:
        try:
            return float(re.sub(r"[^0-9.\-]", "", str(text)) or default)
        except ValueError:
            return default
