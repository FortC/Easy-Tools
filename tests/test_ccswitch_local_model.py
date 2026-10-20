import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from threading import Thread
from unittest.mock import patch

import pytest

from easy_tools.core.ccswitch import (CCSwitchModel, _codex_provider,
                                      _loopback, _valid_endpoint)


def _db(home, settings):
    folder = home / ".cc-switch"
    folder.mkdir(parents=True)
    db = sqlite3.connect(folder / "cc-switch.db")
    db.execute("CREATE TABLE providers (app_type TEXT, name TEXT, settings_config TEXT, is_current INTEGER)")
    db.execute("INSERT INTO providers VALUES ('claude', 'local', ?, 1)",
               (json.dumps(settings),))
    db.commit()
    db.close()


def test_only_loopback_model_addresses_are_accepted():
    assert _loopback("http://127.0.0.1:1234/v1")
    assert _loopback("http://[::1]:1234/v1")
    assert not _loopback("https://api.example.com/v1")
    assert not _loopback("http://localhost/v1?key=secret")
    assert _valid_endpoint("https://api.example.com/v1", allow_cloud=True)
    assert not _valid_endpoint("https://api.example.com/v1", allow_cloud=False)
    assert not _valid_endpoint("http://api.example.com/v1", allow_cloud=True)


def test_codex_provider_reads_wire_format_and_supports_responses():
    provider = _codex_provider({
        "config": 'model = "qwen-local"\nmodel_provider = "local"\n'
                  '[model_providers.local]\nbase_url = "http://127.0.0.1:9000/v1"\n'
                  'wire_api = "responses"\n',
        "auth": {"OPENAI_API_KEY": ""},
    })
    assert provider["api"] == "responses"
    url, _headers, payload = CCSwitchModel._request(provider, "hello")
    assert url == "http://127.0.0.1:9000/v1/responses"
    assert payload["input"][0]["content"][0]["text"] == "hello"


def test_discovery_requires_cloud_enable_for_remote(tmp_path):
    _db(tmp_path, {"env": {"ANTHROPIC_BASE_URL": "https://remote.example/v1",
                             "ANTHROPIC_MODEL": "model", "ANTHROPIC_AUTH_TOKEN": "secret"}})
    client = CCSwitchModel(tmp_path)
    result = client.discover()
    assert result["available"] is False
    assert "云端模型" in result["reason"]
    assert "secret" not in str(result)
    imported = client.discover(allow_cloud=True)
    assert imported["available"] is True
    assert imported["local"] is False
    assert "secret" not in str(imported)
    assert not client.discover(allow_cloud=True,
                               expected_url="https://other.example/v1")["available"]


def test_cloud_answer_requires_explicit_opt_in_and_uses_https(tmp_path):
    _db(tmp_path, {"env": {"ANTHROPIC_BASE_URL": "https://remote.example/v1",
                             "ANTHROPIC_MODEL": "remote-model",
                             "ANTHROPIC_AUTH_TOKEN": "cloud-token"}})
    client = CCSwitchModel(tmp_path)
    with pytest.raises(RuntimeError, match="云端模型"):
        client.answer("sensitive OCR")

    class Response(BytesIO):
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            self.close()

    class Opener:
        def open(self, request, timeout):
            assert request.full_url.startswith("https://remote.example/")
            assert request.get_header("X-api-key") == "cloud-token"
            assert "sensitive OCR" in request.data.decode()
            return Response(json.dumps({"content": [{"type": "text", "text": "cloud answer"}]}).encode())

    with patch("easy_tools.core.ccswitch.urllib.request.build_opener", return_value=Opener()):
        answer = client.answer("sensitive OCR", selected_app="claude",
                               allow_cloud=True,
                               expected_url="https://remote.example/v1")
    assert answer == "cloud answer"


def test_local_claude_endpoint_can_answer_without_leaking_settings(tmp_path):
    client = CCSwitchModel(tmp_path)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert payload["model"] == "local-model"
            assert self.headers["x-api-key"] == "local-token"
            body = json.dumps({"content": [{"type": "text", "text": "本机模型回答"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        # Use an ephemeral local endpoint in the test fixture only.
        _db(tmp_path, {"env": {"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}/v1",
                                 "ANTHROPIC_MODEL": "local-model",
                                 "ANTHROPIC_AUTH_TOKEN": "local-token"}})
        assert "local-token" not in str(client.discover())
        assert client.answer("测试问题") == "本机模型回答"
    finally:
        server.shutdown()


def test_local_model_redirect_is_blocked(tmp_path):
    class RedirectHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(302)
            self.send_header("Location", "https://example.invalid/collect")
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        _db(tmp_path, {"env": {"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}/v1",
                                 "ANTHROPIC_MODEL": "local-model"}})
        with pytest.raises(RuntimeError, match="本机模型请求失败"):
            CCSwitchModel(tmp_path).answer("敏感识别文字")
    finally:
        server.shutdown()
