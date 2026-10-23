"""Bounded, lazy daemon workers. Task history never retains inputs or outputs."""
from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict, deque


class TaskManager:
    def __init__(self, workers=2, capacity=8):
        self.workers, self.capacity = workers, capacity
        self._condition = threading.Condition()
        self._queue = deque()
        self._tasks = OrderedDict()
        self._threads = []
        self._closed = False

    def submit(self, title, work, *, key=None, cancel=None, finished=None):
        with self._condition:
            if self._closed or len(self._queue) >= self.capacity:
                return None
            if key and any(t['key'] == key and t['state'] in ('queued', 'running')
                           for t in self._tasks.values()):
                return None
            task_id = uuid.uuid4().hex
            task = dict(id=task_id, title=title, key=key, state='queued',
                        created=time.monotonic(), started=None, ended=None,
                        event=threading.Event(), cancel=cancel)
            self._tasks[task_id] = task
            self._queue.append((task, work, finished))
            if not self._threads:
                for i in range(self.workers):
                    thread = threading.Thread(target=self._worker, name=f'task-worker-{i}', daemon=True)
                    self._threads.append(thread)
                    thread.start()
            self._condition.notify()
            return task_id

    def _worker(self):
        while True:
            with self._condition:
                while not self._queue and not self._closed:
                    self._condition.wait()
                if self._closed and not self._queue:
                    return
                task, work, finished = self._queue.popleft()
                task['state'] = 'running'
                task['started'] = time.monotonic()
            value, error = None, ''
            try:
                if not task['event'].is_set():
                    value = work(task['event'])
            except TimeoutError:
                error = '处理超时，请缩小数据范围或简化正则表达式。'
            except ValueError:
                error = '输入或参数无效，请检查列设置、数字和内容格式。'
            except Exception:
                # Exception values may contain clipboard/OCR/script data.
                error = '任务执行异常，请检查步骤配置后重试。'
            with self._condition:
                task['state'] = ('cancelled' if task['event'].is_set() else
                                 'failed' if error or getattr(value, 'ok', True) is False else 'succeeded')
                task['ended'] = time.monotonic()
                task['cancel'] = None
                task['trace'] = getattr(value, 'trace', [])[:200]
                for tid in list(self._tasks):
                    if len(self._tasks) <= 48:
                        break
                    if self._tasks[tid]['state'] not in ('queued', 'running'):
                        del self._tasks[tid]
            if finished:
                try:
                    finished(value, '已取消' if task['event'].is_set() else error)
                except Exception:
                    pass  # Receiver failure must not kill a worker or retain data.
            # Do not retain the previous closure/input/output while waiting idle.
            task = work = finished = value = None

    def cancel(self, task_id):
        with self._condition:
            task = self._tasks.get(task_id)
            if not task or task['state'] not in ('queued', 'running'):
                return False
            task['event'].set()
            hook = task['cancel']
        if hook:
            hook()
        return True

    def snapshot(self):
        now = time.monotonic()
        with self._condition:
            return [dict(id=t['id'], title=t['title'], state=t['state'],
                         trace=[dict(entry) for entry in t.get('trace', [])],
                         seconds=round((t['ended'] or now) - (t['started'] or t['created']), 1))
                    for t in reversed(list(self._tasks.values()))]

    def shutdown(self, timeout=0):
        with self._condition:
            self._closed = True
            ids = list(self._tasks)
            self._condition.notify_all()
        for tid in ids:
            self.cancel(tid)
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(max(0, deadline - time.monotonic()))
