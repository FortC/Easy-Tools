"""Explicitly trusted, bounded Office keyboard macros. No recording or shell."""
import hashlib
import threading
import time
from .base import register, ActionResult
from .. import focus
from ...win32 import input as winput, window as w32

from ..automation import input_lock as _macro_lock
OFFICE_PROCESSES = {'winword.exe', 'excel.exe', 'wps.exe', 'et.exe', 'wpsword.exe'}


def macro_hash(sequence):
    return hashlib.sha256(sequence.strip().encode('utf-8')).hexdigest()


def validate_sequence(sequence):
    lines = [line.strip().lower() for line in sequence.splitlines() if line.strip()]
    if not 1 <= len(lines) <= 30:
        raise ValueError('宏应包含 1～30 行快捷键或等待指令')
    for line in lines:
        if line.startswith('wait '):
            if not 0 <= float(line[5:]) <= 5:
                raise ValueError('单次等待应在 0～5 秒之间')
        else:
            winput.parse_combo(line)
    return lines


@register('office.macro', 'Office 快捷键宏', '按顺序发送快捷键；需逐个授权，仅用于 Office/WPS')
def run_macro(ctx, params):
    sequence = str(params.get('sequence', ''))
    if params.get('trusted') is not True or params.get('trusted_hash') != macro_hash(sequence):
        return ActionResult.failure('宏未授权或已修改，请在编辑器中检查并重新授权')
    lines = validate_sequence(sequence)
    target = ctx.target_hwnd or focus.target_hwnd()
    if not target or not focus.restore_to(target, ctx.target_pid):
        return ActionResult.failure('没有有效的 Office 目标窗口，请先在目标软件中呼出面板')
    if not _macro_lock.acquire(False):
        return ActionResult.failure('另一项宏正在执行')
    cancel = ctx.cancel_event if ctx.cancel_event is not None else threading.Event()
    deadline = ctx.deadline or time.monotonic() + 120
    def wait(seconds):
        until = min(deadline, time.monotonic() + seconds)
        while time.monotonic() < until and not cancel.is_set():
            if winput.user32.GetAsyncKeyState(0x1B) & 0x8000:
                cancel.set()
                break
            cancel.wait(min(0.05, max(0, until - time.monotonic())))
    try:
        for line in lines:
            if winput.user32.GetAsyncKeyState(0x1B) & 0x8000:
                cancel.set()
            if cancel.is_set():
                return ActionResult(False, '宏已停止；已完成的按键操作不会自动撤销', cancelled=True)
            if time.monotonic() >= deadline:
                return ActionResult.failure('宏已达到总运行时间上限')
            fg = w32.get_foreground()
            if fg.hwnd != target or fg.process.lower() not in OFFICE_PROCESSES:
                return ActionResult.failure('前台窗口已变化或不是 Office/WPS，已停止宏')
            if line.startswith('wait '):
                wait(float(line[5:]))
            else:
                winput.hotkey(line)
                wait(0.15)
        return ActionResult.success('宏执行完成')
    finally:
        _macro_lock.release()
