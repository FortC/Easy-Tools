"""Small stdin/stdout worker for user-authored Python event-chain nodes.

This is process isolation for responsiveness only. It is deliberately not
presented as a security sandbox: trusted scripts run with the user's account
permissions, including filesystem/network/screen-capture access.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import traceback

MAX_OUTPUT = 1_000_000


class _LimitedOutput:
    def __init__(self, limit: int = MAX_OUTPUT) -> None:
        self.limit = limit
        self.parts = []
        self.size = 0

    def write(self, value: str) -> int:
        value = str(value)
        remaining = max(0, self.limit - self.size)
        if remaining:
            part = value[:remaining]
            self.parts.append(part)
            self.size += len(part)
        return len(value)

    def flush(self) -> None:
        return None

    def getvalue(self) -> str:
        return "".join(self.parts)


def run_worker() -> int:
    try:
        from .worker_stdio import read_payload
        payload = read_payload()
        code = str(payload.get("code", ""))
        namespace = {"input_text": str(payload.get("input_text", "")),
                     "variables": dict(payload.get("variables") or {}),
                     "result": None}
        captured = _LimitedOutput()
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            exec(compile(code, "<Easy-Tools Python 节点>", "exec"), namespace, namespace)
        output = namespace.get("result")
        if output is None:
            output = captured.getvalue()
        result = {"ok": True, "output": str(output)[:MAX_OUTPUT]}
        _emit(result)
        return 0
    except BaseException as exc:  # return script errors to the chain instead of crashing the UI
        frame = sys.exc_info()[2]
        line = 0
        while frame is not None:
            line = frame.tb_lineno
            frame = frame.tb_next
        # Do not echo exception values or source snippets: scripts often receive
        # OCR/clipboard content and may include it in an exception message.
        message = "%s at line %d" % (type(exc).__name__, line)
        _emit({"ok": False, "error": message})
        return 1


def _emit(value: dict) -> None:
    from .worker_stdio import write_payload
    write_payload(value)
