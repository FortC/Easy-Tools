"""Publish one Easy-Tools directory package to both DownloadCenter nodes."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADCENTER_ROOT = ROOT.parent / "downloadcenter"
if str(DOWNLOADCENTER_ROOT) not in sys.path:
    sys.path.insert(0, str(DOWNLOADCENTER_ROOT))

from downloadcenter.client import API, build_manifest, publish  # noqa: E402
from downloadcenter.protocol import valid_name  # noqa: E402

COMPANY_URL = "https://192.168.99.206:9443"
CLOUD_URL = "https://www.clblog.online:9443"
APP = "easy-tools"
PLATFORM = "windows-x64"
CHANNEL = "stable"
KEEP_RELEASES = 3
ARCHIVE_ROOT = ROOT / "release-artifacts"
COMPANY_CA = DOWNLOADCENTER_ROOT / "deploy" / "server.crt"


def stable_manifest(manifest):
    return {key: manifest.get(key) for key in
            ("schema", "app", "platform", "channel", "version", "notes", "rollout_percent", "files", "release_type")
            if key in manifest}


def get_published(api, version):
    path = f"/v1/releases/{APP}/{PLATFORM}/{CHANNEL}/{version}"
    try:
        return api.request(path)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def publish_or_verify(api, manifest, cache):
    existing = get_published(api, manifest["version"])
    if existing is not None:
        if stable_manifest(existing) != stable_manifest(manifest):
            raise RuntimeError("该节点已存在不同内容的同版本发布，停止以保护不可变版本。")
        cleanup = api.request(f"/v1/retention/{APP}/{PLATFORM}/{CHANNEL}", {}, method="POST")
        result = {"release": {key: existing.get(key) for key in
                  ("app", "platform", "channel", "version", "sequence", "published_at", "rollout_percent")},
                  "already_published": True, "retention": cleanup.get("retention", {})}
    else:
        result = publish(api, manifest, cache, progress=lambda _message: None)
    retention = result.get("retention", result.get("release", {}).get("retention", {}))
    if retention.get("error"):
        raise RuntimeError("版本已发布，但服务器清理旧版本失败：" + retention["error"])

    latest = api.request(f"/v1/releases/{APP}/{PLATFORM}/{CHANNEL}/latest")
    if stable_manifest(latest) != stable_manifest(manifest):
        raise RuntimeError("节点最新清单与本次发布不一致。")
    history = api.request(f"/v1/history/{APP}/{PLATFORM}/{CHANNEL}").get("releases", [])
    if not history or history[0].get("version") != manifest["version"]:
        raise RuntimeError("节点版本历史没有指向本次发布。")
    total_size = sum(item["size"] for item in manifest["files"])
    current = history[0]
    if current.get("files") != len(manifest["files"]) or current.get("size") != total_size:
        raise RuntimeError("节点文件数或总大小与本地清单不一致。")
    if len(history) > KEEP_RELEASES:
        raise RuntimeError(f"节点仍保留 {len(history)} 个版本，超过 {KEEP_RELEASES} 个；本地产物不会清理。")
    return {"version": manifest["version"], "files": len(manifest["files"]),
            "bytes": total_size, "retained_versions": len(history)}


def safe_remove_version(folder):
    if folder.is_symlink():
        raise ValueError("拒绝清理符号链接发布目录。")
    folder = folder.resolve()
    if folder.parent != ARCHIVE_ROOT.resolve() or folder.name.startswith((".", "_")):
        raise ValueError("拒绝清理发布归档目录之外的文件。")
    shutil.rmtree(folder)


def commit_local_archive(source, version, published_at, manifest):
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    version = valid_name(version)
    target = ARCHIVE_ROOT / version
    source = source.resolve()
    if target.resolve() != source:
        if target.exists():
            existing, _cache = build_manifest(target, APP, version, PLATFORM, CHANNEL,
                                               cache=ARCHIVE_ROOT / ".update-cache",
                                               notes=manifest.get("notes", ""),
                                               rollout_percent=manifest.get("rollout_percent", 100))
            if stable_manifest(existing) != stable_manifest(manifest):
                raise RuntimeError("本地发布归档中存在不同内容的同版本目录。")
        else:
            pending = ARCHIVE_ROOT / (".pending-" + uuid.uuid4().hex)
            try:
                shutil.copytree(source, pending)
                os.replace(pending, target)
            finally:
                if pending.exists():
                    shutil.rmtree(pending)

    metadata_root = ARCHIVE_ROOT / ".metadata"
    metadata_root.mkdir(exist_ok=True)
    metadata = {"version": version, "published_at": published_at, "manifest": manifest}
    marker = metadata_root / (version + ".json")
    marker.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    releases = []
    for folder in ARCHIVE_ROOT.iterdir():
        if not folder.is_dir() or folder.name.startswith((".", "_")) or folder.is_symlink():
            continue
        info_path = metadata_root / (folder.name + ".json")
        if not info_path.is_file():
            continue
        info = json.loads(info_path.read_text("utf-8"))
        releases.append((str(info.get("published_at", "")), folder.name, folder))
    releases.sort(reverse=True)
    removed_versions = []
    for _published_at, old_version, folder in releases[KEEP_RELEASES:]:
        safe_remove_version(folder)
        old_marker = metadata_root / (old_version + ".json")
        old_marker.unlink(missing_ok=True)
        removed_versions.append(old_version)

    retained_hashes = set()
    for marker_path in metadata_root.glob("*.json"):
        record = json.loads(marker_path.read_text("utf-8"))
        retained_hashes.update(part["sha256"] for file in record["manifest"]["files"]
                               for part in file["chunks"])
    cache = ARCHIVE_ROOT / ".update-cache"
    if cache.is_dir():
        for chunk in cache.iterdir():
            if chunk.is_file() and chunk.name not in retained_hashes:
                chunk.unlink()
    return removed_versions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Easy-Tools 本地产品版本号，例如 0.4.1")
    parser.add_argument("release_folder", type=Path, help="已验证的 Easy-Tools 目录包（含 Easy-Tools.exe）")
    parser.add_argument("--rollout-percent", type=int, default=100)
    parser.add_argument("--notes", default="")
    parser.add_argument("--release-type", choices=("major", "minor", "fix"))
    args = parser.parse_args()
    if not (0 <= args.rollout_percent <= 100):
        parser.error("--rollout-percent 必须介于 0 和 100")
    version = valid_name(args.version)
    source = args.release_folder.resolve()
    if not source.is_dir() or not (source / "Easy-Tools.exe").is_file():
        parser.error("release_folder 必须是包含 Easy-Tools.exe 的最终目录包")
    company_token = os.environ.get("DOWNLOADCENTER_COMPANY_TOKEN", "")
    cloud_token = os.environ.get("DOWNLOADCENTER_CLOUD_TOKEN", "")
    if len(company_token) < 32 or len(cloud_token) < 32:
        parser.error(
            "请分别在当前进程环境中设置 DOWNLOADCENTER_COMPANY_TOKEN 和 "
            "DOWNLOADCENTER_CLOUD_TOKEN；不要把令牌写入脚本或参数"
        )
    if not COMPANY_CA.is_file():
        parser.error(f"缺少公司节点 TLS 信任证书：{COMPANY_CA}")

    cache = ARCHIVE_ROOT / ".update-cache"
    manifest, _ = build_manifest(source, APP, version, PLATFORM, CHANNEL, cache=cache,
                                 notes=args.notes, rollout_percent=args.rollout_percent)
    if args.release_type:
        manifest["release_type"] = args.release_type

    company = API(COMPANY_URL, str(COMPANY_CA), company_token)
    cloud = API(CLOUD_URL, token=cloud_token)
    # Publish and verify each independent node explicitly. A partial success is
    # reported and can be retried idempotently if the existing manifest matches.
    node_errors = []
    for label, api in (("公司内网", company), ("腾讯云", cloud)):
        print(f"发布并核验{label}节点…")
        try:
            result = publish_or_verify(api, manifest, cache)
            print(label + "节点通过：", json.dumps(result, ensure_ascii=False))
        except Exception as exc:
            node_errors.append((label, str(exc)))
            print(label + "节点未通过：", str(exc), file=sys.stderr)
    if node_errors:
        print("至少一个节点发布/保留检查失败；本地发布归档不会清理。", file=sys.stderr)
        return 1

    company_latest = company.request(f"/v1/releases/{APP}/{PLATFORM}/{CHANNEL}/latest")
    removed = commit_local_archive(source, version, company_latest["published_at"], manifest)
    print("双节点核验完成；本地归档仅保留最新三个版本。清理版本：",
          json.dumps(removed, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
