"""Anonymous, deterministic gray rollout and incremental release downloader."""
from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import uuid
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from easy_tools.core.paths import app_root, data_dir, resource_root

# Attempt the company service first. The public Tencent Cloud endpoint is used
# only when the company service cannot be reached or returns a server error.
# The public endpoint must present a certificate trusted by Windows/Python.
UPDATE_URLS = (
    ("https://192.168.99.206:9443", "company"),
    ("https://www.clblog.online:9443", "public"),
)
APP_ID = "easy-tools"
PLATFORM = "windows-x64"
CHANNEL = "stable"
MAX_CHUNK = 1024 * 1024
GEAR = [int.from_bytes(hashlib.sha256(bytes([i])).digest()[:8], "big") for i in range(256)]
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {p + str(i) for p in ("COM", "LPT") for i in range(1, 10)}


def _safe_path(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 240 or "\\" in name:
        raise ValueError("更新清单包含无效路径")
    path = PurePosixPath(name)
    if path.is_absolute() or str(path) != name:
        raise ValueError("更新清单包含越界路径")
    for item in path.parts:
        if (item in (".", "..") or item.endswith((" ", "."))
                or any(ord(ch) < 32 or ch in ':<>"|?*' for ch in item)
                or item.split(".")[0].upper() in RESERVED):
            raise ValueError("更新清单包含 Windows 保留路径")
    return name


def _validate_manifest(manifest: dict) -> dict[str, int]:
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise ValueError("更新清单版本不受支持")
    for key in ("app", "platform", "channel", "version"):
        if not isinstance(manifest.get(key), str) or not NAME_RE.fullmatch(manifest[key]):
            raise ValueError("更新清单包含无效应用或版本信息")
    if manifest["version"].lower() == "latest" or manifest["app"] != APP_ID:
        raise ValueError("更新清单应用身份无效")
    rollout = manifest.get("rollout_percent", 100)
    if type(rollout) is not int or not 0 <= rollout <= 100:
        raise ValueError("更新灰度比例无效")
    files = manifest.get("files")
    if not isinstance(files, list) or not 0 < len(files) <= 50000:
        raise ValueError("更新清单文件数量无效")
    result, seen, total = {}, set(), 0
    for f in files:
        try:
            name, size, sha, parts = f["path"], f["size"], f["sha256"], f["chunks"]
        except (TypeError, KeyError) as exc:
            raise ValueError("更新清单文件信息不完整") from exc
        _safe_path(name)
        if name.casefold() in seen or type(size) is not int or size < 0 or not HASH_RE.fullmatch(sha):
            raise ValueError("更新文件路径、大小或校验码无效")
        seen.add(name.casefold())
        count = 0
        for part in parts:
            h, n = part.get("sha256"), part.get("size")
            if not isinstance(h, str) or not HASH_RE.fullmatch(h) or type(n) is not int or not 0 < n <= MAX_CHUNK:
                raise ValueError("更新内容块信息无效")
            if h in result and result[h] != n:
                raise ValueError("更新内容块存在冲突")
            result[h] = n
            count += n
        if count != size:
            raise ValueError("更新文件内容块大小不匹配")
        total += size
    if total > 50 * 1024**3:
        raise ValueError("更新包超出大小限制")
    for name in seen:
        if any(str(p).casefold() in seen for p in PurePosixPath(name).parents if str(p) != "."):
            raise ValueError("更新路径同时指向文件和目录")
    return result


def _chunks(path: Path):
    buf, rolling = bytearray(), 0
    with path.open("rb") as src:
        while True:
            block = src.read(1024 * 1024)
            if not block:
                break
            for byte in block:
                buf.append(byte)
                rolling = ((rolling << 1) + GEAR[byte]) & 0xffffffffffffffff
                if len(buf) >= 65536 and ((rolling & 0x3ffff) == 0 or len(buf) >= MAX_CHUNK):
                    yield bytes(buf)
                    buf.clear()
                    rolling = 0
    if buf:
        yield bytes(buf)


def _version_key(value: str):
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)*)(?:[-+][0-9A-Za-z.-]+)?\s*", value or "")
    if not match:
        return (0,)
    return tuple(int(part) for part in match.group(1).split("."))


def _device_id() -> str:
    path = data_dir() / ".update-client-id"
    try:
        value = path.read_text(encoding="ascii").strip()
        return str(uuid.UUID(value))
    except (OSError, ValueError):
        value = str(uuid.uuid4())
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        try:
            with temp.open("x", encoding="ascii") as f:
                f.write(value)
            try:
                os.replace(str(temp), str(path))
            except OSError:
                temp.unlink(missing_ok=True)
        except FileExistsError:
            pass
        try:
            return str(uuid.UUID(path.read_text(encoding="ascii").strip()))
        except (OSError, ValueError):
            return value


def _cohort_bucket(device_id: str | None = None) -> int:
    raw = (APP_ID + ":" + (device_id or _device_id())).encode("ascii")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") % 10000


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("更新服务器重定向了请求，已停止以保护连接安全")


class UpdateAPI:
    def __init__(self):
        cert = resource_root() / "update" / "downloadcenter-root.crt"
        if not cert.is_file():
            raise RuntimeError("缺少更新服务器信任证书")
        company_context = ssl.create_default_context(cafile=str(cert))
        cloud_cert = resource_root() / "update" / "downloadcenter-cloud-root.crt"
        public_context = ssl.create_default_context(cafile=str(cloud_cert)) if cloud_cert.is_file() else ssl.create_default_context()
        self._openers = [
            (base_url, build_opener(ProxyHandler({}), HTTPSHandler(context=context), _NoRedirect()))
            for (base_url, _scope), context in zip(UPDATE_URLS, (company_context, public_context))
        ]

    def request(self, path: str, binary: bool = False):
        if not path.startswith("/v1/"):
            raise ValueError("无效的更新接口路径")
        last_error = None
        for index, (base_url, opener) in enumerate(self._openers):
            req = Request(base_url + path, headers={"Accept": "application/octet-stream" if binary else "application/json"})
            try:
                with opener.open(req, timeout=8 if index == 0 else 20) as response:
                    limit = MAX_CHUNK + 1 if binary else 64 * 1024 * 1024 + 1
                    body = response.read(limit)
                if len(body) >= limit:
                    raise ValueError("更新服务器响应过大")
                return body if binary else json.loads(body.decode("utf-8"))
            except HTTPError as exc:
                # A missing release on a healthy preferred server is meaningful;
                # don't silently switch catalogs. Retry the public endpoint only
                # for server-side failures.
                if exc.code < 500 or index == len(self._openers) - 1:
                    raise
                last_error = exc
            except (OSError, TimeoutError, ssl.SSLError, ValueError) as exc:
                if isinstance(exc, ValueError) and "重定向" in str(exc):
                    raise
                if index == len(self._openers) - 1:
                    raise
                last_error = exc
        raise RuntimeError("公司和腾讯云更新服务均不可用") from last_error


def check_update(current_version: str, device_id: str | None = None) -> dict:
    """Return the newest eligible candidate; cohort identifiers never leave this PC."""
    manifest = UpdateAPI().request("/v1/releases/{}/{}/{}/latest".format(APP_ID, PLATFORM, CHANNEL))
    _validate_manifest(manifest)
    if (manifest["platform"], manifest["channel"]) != (PLATFORM, CHANNEL):
        raise ValueError("更新服务器返回了其他平台或通道的版本")
    if _version_key(manifest["version"]) <= _version_key(current_version):
        return {"status": "current", "current_version": current_version}
    bucket = _cohort_bucket(device_id)
    percent = manifest.get("rollout_percent", 100)
    if bucket >= percent * 100:
        return {"status": "not_in_cohort", "current_version": current_version,
                "version": manifest["version"], "rollout_percent": percent,
                "bucket_percent": bucket / 100}
    return {"status": "available", "manifest": manifest, "bucket_percent": bucket / 100}


def _installed_path(root: Path, name: str) -> Path:
    _safe_path(name)
    p = root.joinpath(*PurePosixPath(name).parts)
    if root not in p.resolve().parents:
        raise ValueError("更新目标超出安装目录")
    for parent in (p, *p.parents):
        if parent != root and parent.is_symlink():
            raise ValueError("更新目标包含目录链接")
    return p


def stage_update(manifest: dict, progress=None) -> dict:
    """Fetch and reconstruct into APPDATA; installed program files stay untouched."""
    refs = _validate_manifest(manifest)
    data = data_dir().resolve()
    install = app_root().resolve()
    cache = data / "updates" / "chunks"
    cache.mkdir(parents=True, exist_ok=True)
    if cache.is_symlink() or data not in cache.resolve().parents:
        raise ValueError("更新缓存目录无效")
    # Stable blocks from the currently installed package avoid downloading bytes already on this PC.
    available_paths = []
    for f in manifest["files"]:
        p = _installed_path(install, f["path"])
        if p.is_file():
            available_paths.append(p)
    for path in available_paths:
        for block in _chunks(path):
            h = hashlib.sha256(block).hexdigest()
            if h in refs:
                target = cache / h
                intact = False
                try:
                    intact = not target.is_symlink() and target.is_file() and target.stat().st_size == len(block) and hashlib.sha256(target.read_bytes()).hexdigest() == h
                except OSError:
                    pass
                if not intact:
                    temp = target.with_suffix(".tmp")
                    temp.write_bytes(block)
                    os.replace(str(temp), str(target))
    api = UpdateAPI()
    missing = []
    for h, size in refs.items():
        path = cache / h
        intact = False
        try:
            intact = not path.is_symlink() and path.is_file() and path.stat().st_size == size and hashlib.sha256(path.read_bytes()).hexdigest() == h
        except OSError:
            pass
        if not intact:
            missing.append(h)
    total_download = sum(refs[h] for h in missing)
    downloaded = 0
    for index, h in enumerate(missing, 1):
        block = api.request("/v1/blobs/" + h, binary=True)
        if len(block) != refs[h] or hashlib.sha256(block).hexdigest() != h:
            raise ValueError("下载内容 SHA-256 校验失败")
        target = cache / h
        temp = target.with_suffix(".tmp")
        temp.write_bytes(block)
        os.replace(str(temp), str(target))
        downloaded += len(block)
        if progress:
            progress("下载增量 %s/%s · %s/%s MB" % (
                index, len(missing), downloaded // (1024 * 1024), max(1, total_download // (1024 * 1024))))
    root = data / "updates" / "staged"
    root.mkdir(parents=True, exist_ok=True)
    target = root / manifest["version"]
    if target.exists():
        import shutil
        shutil.rmtree(target)
    target.mkdir()
    try:
        for index, f in enumerate(manifest["files"], 1):
            out = _installed_path(target.resolve(), f["path"])
            out.parent.mkdir(parents=True, exist_ok=True)
            digest, written = hashlib.sha256(), 0
            with out.open("xb") as stream:
                for part in f["chunks"]:
                    block = (cache / part["sha256"]).read_bytes()
                    stream.write(block)
                    digest.update(block)
                    written += len(block)
            if written != f["size"] or digest.hexdigest() != f["sha256"]:
                raise ValueError("文件重建后 SHA-256 校验失败：" + f["path"])
            if progress:
                progress("校验文件 %s/%s" % (index, len(manifest["files"])))
        metadata = target / ".update-manifest.json"
        metadata.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    except Exception:
        import shutil
        shutil.rmtree(target, ignore_errors=True)
        raise
    return {"stage": str(target), "downloaded_bytes": downloaded,
            "full_bytes": sum(f["size"] for f in manifest["files"]), "version": manifest["version"]}
