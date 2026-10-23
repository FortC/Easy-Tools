"""Bundled offline SmolLM2 model served by the bundled llama.cpp runtime."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from .paths import data_dir, resource_root
from .question_bank import format_bank_evidence


class BuiltinModel:
    MODEL_NAME = "SmolLM2-360M-Instruct-Q4_K_M.gguf"
    MODEL_SIZE = 270590880
    MODEL_SHA256 = "2fa3f013dcdd7b99f9b237717fa0b12d75bbb89984cc1274be1471a465bac9c2"
    MODEL_URL = ("https://huggingface.co/bartowski/SmolLM2-360M-Instruct-GGUF/resolve/"
                 "7be6f65f1db715fe5dc5a4634c0d459b4eed42ec/" + MODEL_NAME)

    def __init__(self):
        self._lock = threading.RLock()
        self._process = None
        self._port = 0
        self._stop_requested = threading.Event()
        self._answer_gate = threading.Lock()
        self._job = None
        self._last_used = 0.0

    def assets(self) -> dict:
        root = resource_root()
        runtime = root / "llama"
        if not (runtime / "llama-server.exe").is_file():
            runtime = root / "vendor" / "llama"
        exe = runtime / "llama-server.exe"
        bundled_model = root / "models" / self.MODEL_NAME
        user_model = data_dir() / "models" / self.MODEL_NAME
        model = bundled_model if bundled_model.is_file() else user_model
        return {"available": exe.is_file() and model.is_file(),
                "runtime": exe.is_file(), "model": model.is_file(),
                "bundled_model": bundled_model.is_file(),
                "model_path": str(model), "runtime_path": str(exe)}

    @classmethod
    def download_model(cls, progress=None, cancel_event=None) -> Path:
        """Download the pinned public model into the writable per-user data folder."""
        import hashlib

        target = data_dir() / "models" / cls.MODEL_NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.stat().st_size == cls.MODEL_SIZE:
            digest = hashlib.sha256()
            with target.open("rb") as existing:
                for block in iter(lambda: existing.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() == cls.MODEL_SHA256:
                return target

        part = target.with_name(target.name + ".part-" + uuid.uuid4().hex)
        request = urllib.request.Request(cls.MODEL_URL, headers={"User-Agent": "Easy-Tools model downloader"})
        received = 0
        digest = hashlib.sha256()
        try:
            with urllib.request.urlopen(request, timeout=60) as response, part.open("xb") as output:
                final_url = response.geturl()
                final_host = (urlparse(final_url).hostname or "").lower()
                if not final_url.lower().startswith("https://") or not (
                        final_host == "huggingface.co" or final_host.endswith(".huggingface.co") or
                        final_host.endswith(".hf.co")):
                    raise ValueError("模型下载跳转到了未受信任的 HTTPS 域名。")
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise InterruptedError("已取消模型下载。")
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    received += len(block)
                    if received > cls.MODEL_SIZE:
                        raise ValueError("模型文件大小超过预期，已停止下载。")
                    output.write(block)
                    digest.update(block)
                    if progress:
                        progress(received, cls.MODEL_SIZE)
                output.flush()
                os.fsync(output.fileno())
            if received != cls.MODEL_SIZE or digest.hexdigest() != cls.MODEL_SHA256:
                raise ValueError("模型文件大小或 SHA-256 校验失败；原模型文件未替换。")
            os.replace(part, target)
            return target
        finally:
            part.unlink(missing_ok=True)

    def answer(self, prompt: str, evidence: list[dict] | None = None) -> str:
        if not self._answer_gate.acquire(blocking=False):
            raise RuntimeError("内置模型正在处理上一条提问，请等待完成后再试。")
        try:
            return self._answer(prompt, evidence)
        finally:
            self._last_used = time.monotonic()
            self._answer_gate.release()

    def release_if_idle(self, seconds=300):
        """Release model memory without disabling the next on-demand start."""
        if not self._last_used or time.monotonic() - self._last_used < seconds:
            return
        if not self._answer_gate.acquire(False):
            return
        try:
            if not self._lock.acquire(False):
                return
            try:
                if self._job:
                    self._job.close()
                    self._job = None
                process, self._process = self._process, None
                if process and process.poll() is None:
                    process.kill()
                self._last_used = 0
            finally:
                self._lock.release()
        finally:
            self._answer_gate.release()

    def _answer(self, prompt: str, evidence: list[dict] | None = None) -> str:
        if self._stop_requested.is_set():
            raise RuntimeError("内置模型已停止。")
        endpoint = self._ensure_server()
        references = format_bank_evidence(evidence)
        message = (prompt or "")[:10000]
        if references:
            message += ("\n\n对同一道题优先采用正确答案列及其选项内容。题型、难度和解析不是答案；"
                        "不得把单选/多选当作回答，不得自行替换知识库正确项。无法确定匹配题目时明确说明。\n"
                        + references[:5000])
        payload = {"model": "easy-tools-smollm2", "temperature": 0.1,
                   "max_tokens": 512,
                   "messages": [{"role": "system", "content": "你是中文单轮问答助手。回答简洁；证据不足时明确说不知道。"},
                                {"role": "user", "content": message}]}
        request = urllib.request.Request(
            endpoint + "/v1/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                result = json.loads(response.read(1_000_000).decode("utf-8"))
            answer = str(result["choices"][0]["message"]["content"]).strip()
        except (urllib.error.URLError, TimeoutError, OSError, ValueError,
                KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("内置离线模型运行失败：%s" % exc) from exc
        if not answer:
            raise RuntimeError("内置离线模型没有返回答案。")
        return answer[:12000]

    def _ensure_server(self) -> str:
        with self._lock:
            if self._stop_requested.is_set():
                raise RuntimeError("内置模型已停止。")
            assets = self.assets()
            if not assets["available"]:
                missing = "模型权重" if not assets["model"] else "llama.cpp 运行时"
                raise RuntimeError("内置离线模型缺少%s，请重新安装完整安装包。" % missing)
            from .runtime import prepare_model_runtime
            runtime_path = prepare_model_runtime(assets['runtime_path'])
            if self._process and self._process.poll() is None:
                if self._ready(self._port):
                    return f"http://127.0.0.1:{self._port}"
                self._process.terminate()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                self._port = sock.getsockname()[1]
            log_path = Path(os.environ.get("TEMP", str(Path.home()))) / "easy-tools-llama.log"
            args = [runtime_path, "--model", assets["model_path"],
                    "--alias", "easy-tools-smollm2", "--host", "127.0.0.1",
                    "--port", str(self._port), "--ctx-size", "2048",
                    "--parallel", "1", "--batch-size", "128", "--ubatch-size", "128",
                    "--n-predict", "512", "--threads", str(max(1, min(2, (os.cpu_count() or 2) - 1))),
                    "--no-webui", '--log-disable']
            try:
                with log_path.open("ab") as log_file:
                    from .process_job import spawn_worker
                    if self._job:
                        self._job.close()
                    self._process, self._job = spawn_worker(
                        args, cwd=str(Path(runtime_path).parent),
                        stdin=subprocess.DEVNULL, stdout=log_file,
                        stderr=subprocess.STDOUT, creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                       | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)))
            except OSError as exc:
                raise RuntimeError("无法启动内置离线模型运行时：%s" % exc) from exc
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if self._stop_requested.is_set():
                    self._process.terminate()
                    raise RuntimeError("内置模型启动已取消。")
                if self._process.poll() is not None:
                    raise RuntimeError("内置离线模型启动失败，请查看 %s" % log_path)
                if self._ready(self._port):
                    return f"http://127.0.0.1:{self._port}"
                self._stop_requested.wait(0.25)
            self._process.terminate()
            raise RuntimeError("内置离线模型启动超时，请检查可用内存。")

    @staticmethod
    def _ready(port: int) -> bool:
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{port}/health")
            with urllib.request.urlopen(request, timeout=0.5) as response:
                return response.status == 200
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    def stop(self):
        # Never acquire the server-start lock here: startup can take 90 seconds.
        # This method is called by the GUI while quitting.
        self._stop_requested.set()
        if self._job:
            self._job.close()
        process = self._process
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

        def reap():
            with self._lock:
                process, self._process = self._process, None
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
        threading.Thread(target=reap, name="model-stop", daemon=True).start()
