from pathlib import Path

import pytest

from installer import installer_app


def test_atomic_copy_replaces_file_without_changing_payload(tmp_path):
    source = tmp_path / "source.dll"
    destination = tmp_path / "install" / "_internal" / "libcrypto-3-x64.dll"
    source.write_bytes(b"new payload")
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"old payload")

    installer_app.atomic_copy(source, destination, attempts=1, retry_delay=0)

    assert destination.read_bytes() == b"new payload"
    assert not list(destination.parent.glob("*.easy-tools-new"))
    assert not list(destination.parent.glob(".*.easy-tools-new"))


def test_atomic_copy_retries_access_denied_and_preserves_old_file_on_failure(tmp_path, monkeypatch):
    source = tmp_path / "source.dll"
    destination = tmp_path / "install" / "libcrypto-3-x64.dll"
    source.write_bytes(b"new payload")
    destination.parent.mkdir()
    destination.write_bytes(b"old payload")
    original_replace = installer_app.os.replace
    calls = 0

    def denied_twice_then_replace(staged, target):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PermissionError(5, "Access is denied", str(target))
        return original_replace(staged, target)

    monkeypatch.setattr(installer_app.os, "replace", denied_twice_then_replace)
    installer_app.atomic_copy(source, destination, attempts=3, retry_delay=0)
    assert calls == 3
    assert destination.read_bytes() == b"new payload"


def test_atomic_copy_reports_access_denied_and_keeps_old_file(tmp_path, monkeypatch):
    source = tmp_path / "source.dll"
    destination = tmp_path / "install" / "libcrypto-3-x64.dll"
    source.write_bytes(b"new payload")
    destination.parent.mkdir()
    destination.write_bytes(b"old payload")

    def always_denied(_staged, target):
        raise PermissionError(5, "Access is denied", str(target))

    monkeypatch.setattr(installer_app.os, "replace", always_denied)
    with pytest.raises(RuntimeError, match="原文件已保留"):
        installer_app.atomic_copy(source, destination, attempts=2, retry_delay=0)

    assert destination.read_bytes() == b"old payload"
    assert not list(destination.parent.glob("*.easy-tools-new"))
    assert not list(destination.parent.glob(".*.easy-tools-new"))


def test_make_shortcut_creates_link_without_shell_process(tmp_path):
    target = tmp_path / "Easy-Tools.exe"
    link = tmp_path / "Easy-Tools.lnk"
    target.write_bytes(b"synthetic executable")

    assert installer_app.make_shortcut(str(target), link, "Easy-Tools test")
    assert link.is_file() and link.stat().st_size > 0


def test_install_rolls_back_previous_files_when_locked_dll_cannot_be_replaced(tmp_path, monkeypatch):
    source = tmp_path / "payload"
    destination = tmp_path / "install"
    (source / "_internal").mkdir(parents=True)
    (destination / "_internal").mkdir(parents=True)
    (source / "Easy-Tools.exe").write_bytes(b"new exe")
    (source / "_internal" / "libcrypto-3-x64.dll").write_bytes(b"new dll")
    (source / "Uninstall.exe").write_bytes(b"new uninstaller")
    (destination / "Easy-Tools.exe").write_bytes(b"old exe")
    locked_dll = destination / "_internal" / "libcrypto-3-x64.dll"
    locked_dll.write_bytes(b"old dll")
    manifest = destination / installer_app.MANIFEST
    manifest.write_text("\n".join([
        installer_app.MANIFEST_HEADER, str(destination), "Easy-Tools.exe",
        "_internal/libcrypto-3-x64.dll", "Uninstall.exe", "",
    ]), encoding="utf-8")
    (destination / "Uninstall.exe").write_bytes(b"old uninstaller")
    (destination / "user-note.txt").write_bytes(b"keep")

    monkeypatch.setattr(installer_app, "kill_running", lambda _target: None)
    monkeypatch.setattr(installer_app, "uninstaller_source", lambda: source / "Uninstall.exe")
    monkeypatch.setattr(installer_app, "register_uninstall", lambda _target: None)
    original_replace = installer_app.replace_file_with_retry

    def fail_dll_replace(staged, target, **kwargs):
        if Path(target) == locked_dll and Path(staged).name.endswith(".easy-tools-new"):
            raise RuntimeError("simulated WinError 5")
        return original_replace(staged, target, **kwargs)

    monkeypatch.setattr(installer_app, "replace_file_with_retry", fail_dll_replace)
    with pytest.raises(RuntimeError, match="simulated WinError 5"):
        installer_app.perform_install(source, destination, shortcuts=False)

    assert (destination / "Easy-Tools.exe").read_bytes() == b"old exe"
    assert locked_dll.read_bytes() == b"old dll"
    assert (destination / "Uninstall.exe").read_bytes() == b"old uninstaller"
    assert (destination / "user-note.txt").read_bytes() == b"keep"
    assert manifest.read_text(encoding="utf-8").startswith(installer_app.MANIFEST_HEADER)
    assert not list(destination.rglob("*.easy-tools-backup"))
    assert not list(destination.rglob("*.easy-tools-new"))
