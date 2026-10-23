"""Killable pure transformations for large inputs or regular expressions."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import threading
from .process_job import spawn_worker

_heavy_gate = threading.BoundedSemaphore(1)


def run_processor(pid, text, args, cancel=None, timeout=20):
    deadline = time.monotonic() + timeout
    while not _heavy_gate.acquire(timeout=0.05):
        if cancel is not None and cancel.is_set():
            raise RuntimeError('处理已取消')
        if time.monotonic() >= deadline:
            raise TimeoutError('等待处理资源超时')
    try:
        if cancel is not None and cancel.is_set():
            raise RuntimeError('处理已取消')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('等待处理资源超时')
        return _run_processor(pid, text, args, cancel, remaining)
    finally:
        _heavy_gate.release()


def _run_processor(pid, text, args, cancel=None, timeout=20):
    entry = Path(__file__).resolve().parents[2] / 'main.py'
    command = [sys.executable, '--run-processor'] if getattr(sys, 'frozen', False) else [sys.executable, str(entry), '--run-processor']
    process, job = spawn_worker(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL,
                               memory_limit=512 * 1024 * 1024,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    payload = json.dumps(dict(id=pid, text=text, args=args), ensure_ascii=False).encode('utf-8')
    deadline = time.monotonic() + timeout
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise RuntimeError('处理已取消')
            if time.monotonic() >= deadline:
                raise TimeoutError('处理超时，请缩小输入范围或简化正则表达式')
            try:
                output, _ = process.communicate(payload, timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                payload = None
        result = json.loads(output.decode('utf-8'))
        if not result.get('ok'):
            raise ValueError(result.get('error', '处理失败'))
        return result['output']
    finally:
        job.close()
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=2)


def main():
    try:
        from .actions.processors import get_processor, eval_condition
        from .worker_stdio import read_payload
        data = read_payload()
        if len(data['text']) > 1_000_000:
            raise ValueError('输入超过 100 万字符')
        if data['id'] == '__condition__':
            output = eval_condition(data['args']['expr'], data['text'], data['args'].get('variables'))
        else:
            processor = get_processor(data['id'])
            if not processor:
                raise ValueError('处理器不存在')
            output = processor.run(data['text'], data.get('args'))
        if isinstance(output, str) and len(output) > 1_000_000:
            raise ValueError('结果超过 100 万字符，请缩小输入范围')
        result = dict(ok=True, output=output)
    except Exception as exc:
        result = dict(ok=False, error='处理失败：' + type(exc).__name__ + '。请检查参数与输入格式。')
    from .worker_stdio import write_payload
    write_payload(result)
    return 0
