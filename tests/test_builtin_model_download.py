import hashlib
import io

import pytest

from easy_tools.core import builtin_model


class FakeResponse:
    def __init__(self, body, final_url="https://huggingface.co/model"):
        self.body = io.BytesIO(body)
        self.final_url = final_url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self.final_url

    def read(self, size=-1):
        return self.body.read(size)


def test_download_pinned_model_verifies_and_installs_atomically(tmp_path, monkeypatch):
    body = b"test model bytes"
    monkeypatch.setattr(builtin_model, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(builtin_model.BuiltinModel, "MODEL_SIZE", len(body))
    monkeypatch.setattr(builtin_model.BuiltinModel, "MODEL_SHA256", hashlib.sha256(body).hexdigest())
    monkeypatch.setattr(builtin_model.urllib.request, "urlopen", lambda *_a, **_k: FakeResponse(body))
    progress = []

    result = builtin_model.BuiltinModel.download_model(lambda done, total: progress.append((done, total)))

    assert result.read_bytes() == body
    assert progress[-1] == (len(body), len(body))
    assert list(result.parent.glob("*.part-*")) == []


def test_download_rejects_bad_hash_and_cleans_partial_file(tmp_path, monkeypatch):
    body = b"bad model"
    monkeypatch.setattr(builtin_model, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(builtin_model.BuiltinModel, "MODEL_SIZE", len(body))
    monkeypatch.setattr(builtin_model.BuiltinModel, "MODEL_SHA256", "0" * 64)
    monkeypatch.setattr(builtin_model.urllib.request, "urlopen", lambda *_a, **_k: FakeResponse(body))

    with pytest.raises(ValueError, match="SHA-256"):
        builtin_model.BuiltinModel.download_model()

    model_dir = tmp_path / "models"
    assert not (model_dir / builtin_model.BuiltinModel.MODEL_NAME).exists()
    assert list(model_dir.glob("*.part-*")) == []


def test_download_rejects_untrusted_redirect_host(tmp_path, monkeypatch):
    body = b"test"
    monkeypatch.setattr(builtin_model, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(builtin_model.BuiltinModel, "MODEL_SIZE", len(body))
    monkeypatch.setattr(builtin_model.BuiltinModel, "MODEL_SHA256", hashlib.sha256(body).hexdigest())
    monkeypatch.setattr(builtin_model.urllib.request, "urlopen",
                        lambda *_a, **_k: FakeResponse(body, "https://example.com/model"))

    with pytest.raises(ValueError, match="未受信任"):
        builtin_model.BuiltinModel.download_model()

    assert list((tmp_path / "models").glob("*.part-*")) == []
