"""Phase 21C production-volume backup/restore regression tests.

Docker is mocked deliberately: these tests prove command construction and all
fail-closed decisions without touching a developer or production daemon.  The
release gate separately runs the same CLI against a disposable Compose project.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools import backup_production as backup  # noqa: E402


RUNTIME_VOLUME = "phase21c-proof_pd-data"
DATABASE_RELATIVE = "procedural_detective.db"


def _volume_metadata(name: str = RUNTIME_VOLUME, *, project: str = "phase21c-proof"):
    return {
        "Name": name,
        "Labels": {
            "com.docker.compose.project": project,
            "com.docker.compose.volume": "pd-data",
        },
    }


def _rendered_config() -> str:
    return json.dumps(
        {
            "name": "phase21c-proof",
            "services": {
                "procedural-detective": {
                    "image": "procedural-detective:proof",
                    "environment": {
                        "DATABASE_URL": "sqlite:////data/procedural_detective.db",
                        "ENVIRONMENT": "production",
                    },
                    "volumes": [
                        {
                            "type": "volume",
                            "source": "pd-data",
                            "target": "/data",
                        }
                    ],
                }
            },
            "volumes": {"pd-data": {"name": RUNTIME_VOLUME}},
        }
    )


def _result(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


class FakeDocker:
    def __init__(self, handler=None):
        self.calls: list[list[str]] = []
        self.handler = handler

    def __call__(self, command, **kwargs):
        command = list(command)
        self.calls.append(command)
        if self.handler:
            handled = self.handler(command)
            if handled is not None:
                return handled
        if command[-3:] == ["config", "--format", "json"]:
            return _result(command, stdout=_rendered_config())
        if command[:4] == ["docker", "volume", "inspect", "--format"]:
            return _result(command, stdout=json.dumps(_volume_metadata()))
        if command[:3] == ["docker", "ps", "-a"]:
            return _result(command, stdout="")
        return _result(command)


def _write_archive(path: Path, database_name: str = DATABASE_RELATIVE) -> None:
    payload = b"SQLite format 3\x00known-row"
    with tarfile.open(path, "w:gz") as handle:
        info = tarfile.TarInfo(database_name)
        info.size = len(payload)
        handle.addfile(info, io.BytesIO(payload))
    if os.name != "nt":
        path.chmod(0o600)


def _write_checksum(path: Path) -> Path:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    sidecar = path.with_name(path.name + ".sha256")
    sidecar.write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return sidecar


def test_backup_resolves_runtime_volume_and_creates_verified_archive(tmp_path):
    output = tmp_path / "secure-backups"

    def handler(command):
        if command[:2] == ["docker", "run"]:
            archive_name = command[-4]
            _write_archive(output / archive_name)
            return _result(command)
        return None

    docker = FakeDocker(handler)
    compose = tmp_path / "docker-compose.prod.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    result = backup.create_backup(
        output,
        compose_file=compose,
        runner=docker,
        now=datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc),
    )

    assert result.runtime_volume == RUNTIME_VOLUME
    assert result.archive.is_file() and result.archive.stat().st_size > 0
    assert result.checksum_file.read_text(encoding="ascii").startswith(result.sha256)
    helper = next(call for call in docker.calls if call[:2] == ["docker", "run"])
    assert any(f"source={RUNTIME_VOLUME},target=/source,readonly" in arg for arg in helper)
    assert "pd-data" not in helper  # the logical name is never mounted directly
    assert "-v" not in helper
    assert not any(call[:3] == ["docker", "volume", "create"] for call in docker.calls)


def test_explicit_env_file_is_forwarded_and_controls_resolved_project(tmp_path):
    env_file = tmp_path / "production.env"
    env_file.write_text("COMPOSE_PROJECT_NAME=envfile-proof\n", encoding="utf-8")
    rendered = json.loads(_rendered_config())
    rendered["name"] = "envfile-proof"
    rendered["volumes"]["pd-data"]["name"] = "envfile-proof_pd-data"

    def handler(command):
        if command[-3:] == ["config", "--format", "json"]:
            position = command.index("--env-file")
            assert command[position + 1] == str(env_file.resolve())
            return _result(command, stdout=json.dumps(rendered))
        return None

    docker = FakeDocker(handler)
    compose = tmp_path / "docker-compose.prod.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    plan = backup.resolve_volume_plan(compose, env_file=env_file, runner=docker)

    assert plan.project_name == "envfile-proof"
    assert plan.runtime_name == "envfile-proof_pd-data"


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose CLI unavailable")
def test_real_compose_env_file_changes_project_and_volume_resolution(tmp_path):
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "services:\n"
        "  procedural-detective:\n"
        "    image: procedural-detective:test\n"
        "    environment:\n"
        "      DATABASE_URL: sqlite:////data/procedural_detective.db\n"
        "    volumes:\n"
        "      - pd-data:/data\n"
        "volumes:\n"
        "  pd-data:\n",
        encoding="utf-8",
    )
    env_file = tmp_path / "production.env"
    env_file.write_text("COMPOSE_PROJECT_NAME=envfile-real-proof\n", encoding="utf-8")

    try:
        plan = backup.resolve_volume_plan(compose, env_file=env_file)
    except backup.BackupError as exc:
        if "could not be executed" in str(exc) or "could not render" in str(exc):
            pytest.skip("Docker Compose plugin unavailable")
        raise

    assert plan.project_name == "envfile-real-proof"
    assert plan.runtime_name == "envfile-real-proof_pd-data"


def test_backup_helper_uses_private_exclusive_archive_creation():
    assert "os.O_EXCL" in backup._BACKUP_SCRIPT
    assert "0o600" in backup._BACKUP_SCRIPT
    assert "os.chmod(temporary, 0o600)" in backup._BACKUP_SCRIPT


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission-bit regression")
def test_posix_output_directory_and_archive_are_operator_only(tmp_path):
    output = backup._prepare_private_output_directory(tmp_path / "private")
    assert stat.S_IMODE(output.stat().st_mode) == 0o700

    archive = output / "backup.tar.gz"
    archive.write_bytes(b"private")
    archive.chmod(0o600)
    backup._verify_private_archive(archive)

    archive.chmod(0o644)
    with pytest.raises(backup.BackupError, match="0600"):
        backup._verify_private_archive(archive)


def test_backup_refuses_missing_volume_without_creating_one(tmp_path):
    def handler(command):
        if command[:4] == ["docker", "volume", "inspect", "--format"]:
            return _result(command, returncode=1, stderr="not found")
        return None

    docker = FakeDocker(handler)
    compose = tmp_path / "docker-compose.prod.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    with pytest.raises(backup.BackupError, match="does not exist"):
        backup.create_backup(tmp_path / "out", compose_file=compose, runner=docker)

    assert not any(call[:2] == ["docker", "run"] for call in docker.calls)
    assert not any(call[:3] == ["docker", "volume", "create"] for call in docker.calls)


def test_backup_refuses_running_container_on_resolved_volume(tmp_path):
    def handler(command):
        if command[:3] == ["docker", "ps", "-a"]:
            return _result(
                command,
                stdout=json.dumps(
                    {"ID": "redacted", "Names": "app", "State": "running"}
                ),
            )
        return None

    docker = FakeDocker(handler)
    compose = tmp_path / "docker-compose.prod.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    with pytest.raises(backup.BackupError, match="not stopped"):
        backup.create_backup(tmp_path / "out", compose_file=compose, runner=docker)
    assert not any(call[:2] == ["docker", "run"] for call in docker.calls)


def test_backup_refuses_volume_inspect_identity_mismatch(tmp_path):
    def handler(command):
        if command[:4] == ["docker", "volume", "inspect", "--format"]:
            return _result(command, stdout=json.dumps(_volume_metadata("unrelated_pd-data")))
        return None

    docker = FakeDocker(handler)
    compose = tmp_path / "docker-compose.prod.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    with pytest.raises(backup.BackupError, match="identity"):
        backup.create_backup(tmp_path / "out", compose_file=compose, runner=docker)


def test_backup_refuses_same_name_volume_owned_by_unrelated_project(tmp_path):
    def handler(command):
        if command[:4] == ["docker", "volume", "inspect", "--format"]:
            return _result(
                command,
                stdout=json.dumps(_volume_metadata(project="some-other-project")),
            )
        return None

    docker = FakeDocker(handler)
    compose = tmp_path / "docker-compose.prod.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    with pytest.raises(backup.BackupError, match="not owned"):
        backup.create_backup(tmp_path / "out", compose_file=compose, runner=docker)
    assert not any(call[:2] == ["docker", "run"] for call in docker.calls)


def test_backup_helper_missing_database_never_emits_verified_files(tmp_path):
    output = tmp_path / "out"

    def handler(command):
        if command[:2] == ["docker", "run"]:
            return _result(command, returncode=41)
        return None

    docker = FakeDocker(handler)
    compose = tmp_path / "docker-compose.prod.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    with pytest.raises(backup.BackupError, match="database exists"):
        backup.create_backup(output, compose_file=compose, runner=docker)
    assert list(output.iterdir()) == []


def test_restore_verifies_checksum_and_creates_only_resolved_volume(tmp_path):
    archive = tmp_path / "known.tar.gz"
    _write_archive(archive)
    _write_checksum(archive)
    created = False

    def handler(command):
        nonlocal created
        if command[:4] == ["docker", "volume", "inspect", "--format"]:
            if not created:
                return _result(command, returncode=1)
            return _result(command, stdout=json.dumps(_volume_metadata()))
        if command[:3] == ["docker", "volume", "create"]:
            created = True
            return _result(command, stdout=RUNTIME_VOLUME + "\n")
        return None

    docker = FakeDocker(handler)
    compose = tmp_path / "docker-compose.prod.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    plan = backup.restore_backup(
        archive,
        yes=True,
        create_volume=True,
        compose_file=compose,
        runner=docker,
    )

    assert plan.runtime_name == RUNTIME_VOLUME
    create = next(call for call in docker.calls if call[:3] == ["docker", "volume", "create"])
    assert create[-1] == RUNTIME_VOLUME
    assert "com.docker.compose.project=phase21c-proof" in create
    assert "com.docker.compose.volume=pd-data" in create
    restore = [call for call in docker.calls if call[:2] == ["docker", "run"]]
    assert len(restore) == 1
    assert any(f"source={RUNTIME_VOLUME},target=/target" in arg for arg in restore[0])


def test_restore_refuses_missing_volume_without_explicit_creation(tmp_path):
    archive = tmp_path / "known.tar.gz"
    _write_archive(archive)
    _write_checksum(archive)

    def handler(command):
        if command[:4] == ["docker", "volume", "inspect", "--format"]:
            return _result(command, returncode=1)
        return None

    docker = FakeDocker(handler)
    compose = tmp_path / "docker-compose.prod.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    with pytest.raises(backup.BackupError, match="--create-volume"):
        backup.restore_backup(
            archive,
            yes=True,
            create_volume=False,
            compose_file=compose,
            runner=docker,
        )
    assert not any(call[:3] == ["docker", "volume", "create"] for call in docker.calls)


def test_restore_rejects_wrong_checksum_before_any_docker_call(tmp_path):
    archive = tmp_path / "known.tar.gz"
    _write_archive(archive)
    archive.with_name(archive.name + ".sha256").write_text(
        f"{'0' * 64}  {archive.name}\n", encoding="ascii"
    )
    docker = FakeDocker()
    with pytest.raises(backup.BackupError, match="checksum does not match"):
        backup.restore_backup(archive, yes=True, create_volume=True, runner=docker)
    assert docker.calls == []


def test_archive_validation_rejects_path_traversal(tmp_path):
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        payload = b"x"
        traversal = tarfile.TarInfo("../procedural_detective.db")
        traversal.size = len(payload)
        handle.addfile(traversal, io.BytesIO(payload))
    with pytest.raises(backup.BackupError, match="unsafe path"):
        backup._safe_tar_members(archive, DATABASE_RELATIVE)


def test_real_docs_never_mount_literal_runtime_volume_name():
    operations = (_REPO_ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
    privacy = (_REPO_ROOT / "docs" / "PRIVACY.md").read_text(encoding="utf-8")
    combined = operations + privacy
    assert "-v pd-data:" not in combined
    assert "/var/lib/docker/volumes/pd-data" not in combined
    assert "python -m tools.backup_production backup" in combined
    assert "python -m tools.backup_production restore" in combined
    assert "windows acl" in combined.lower()
