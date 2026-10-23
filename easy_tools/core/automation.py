"""One input transaction at a time, including workflows, text and macros."""
import threading
from functools import wraps

input_lock = threading.RLock()


def input_transaction(handler):
    @wraps(handler)
    def run(ctx, params):
        from .actions.base import ActionResult
        if not input_lock.acquire(False):
            return ActionResult.failure('另一项输入操作正在执行，请稍后重试')
        try:
            if ctx.cancel_event is not None and ctx.cancel_event.is_set():
                return ActionResult(False, '已取消', cancelled=True)
            return handler(ctx, params)
        finally:
            input_lock.release()
    return run
