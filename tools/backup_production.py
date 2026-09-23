"""Audited production-volume backup and restore command (Phase 21C).

The production Compose file names a *logical* volume.  Docker Compose derives
the runtime volume name from the effective project name, so operator scripts
must never guess that name.  This command asks ``docker compose config`` for
the effective service mount and top-level volume name, verifies the volume
already exists, verifies every container using it is stopped, and only then
creates a checked archive.

Backup never creates a Docker volume.  Restore may recreate the exact volume
resolved from Compose, but only with both ``--yes`` and ``--create-volume``.
No shell is used for Docker invocations and rendered environment values are
never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import stat
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Sequence


_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_COMPOSE_FILE = _REPO_ROOT / "docker-compose.prod.yml"
_DATA_TARGET = "/data"
_APP_SERVICE = "procedural-detective"
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")

Runner = Callable[..., subprocess.CompletedProcess[str]]


class BackupError(RuntimeError):
    """Expected fail-closed operator error (safe to print without traceback)."""


@dataclass(frozen=True)
class VolumePlan:
    compose_file: Path
    project_directory: Path
    project_name: str
    logical_name: str
    runtime_name: str
    database_relative_path: str
    helper_image: str


@dataclass(frozen=True)
class BackupResult:
    archive: Path
    checksum_file: Path
    sha256: str
    runtime_volume: str


def _completed(
    runner: Runner,
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            list(command),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BackupError("Docker command could not be executed") from exc


def _compose_command(
    plan_file: Path,
    project_name: str | None,
    env_file: Path | None,
    project_directory: Path,
) -> list[str]:
    command = [
        "docker",
        "compose",
        "--project-directory",
        str(project_directory),
    ]
    if env_file is not None:
        command.extend(["--env-file", str(env_file)])
    if project_name:
        command.extend(["--project-name", project_name])
    command.extend(["-f", str(plan_file)])
    return command


def _database_relative_path(database_url: object, mount_target: str) -> str:
    if not isinstance(database_url, str) or not database_url.startswith("sqlite:///"):
        raise BackupError("production DATABASE_URL is not a file-backed SQLite URL")
    raw_path = database_url[len("sqlite:///") :]
    if not raw_path or raw_path == ":memory:" or "?" in raw_path or "#" in raw_path:
        raise BackupError("production DATABASE_URL is not a plain SQLite file path")
    absolute = posixpath.normpath(raw_path)
    target = posixpath.normpath(mount_target)
    if not absolute.startswith("/") or absolute == target:
        raise BackupError("production SQLite file is not inside the persistent volume")
    prefix = target.rstrip("/") + "/"
    if not absolute.startswith(prefix):
        raise BackupError("production SQLite file is not inside the persistent volume")
    relative = absolute[len(prefix) :]
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or ".." in path.parts:
        raise BackupError("production SQLite path is ambiguous")
    return path.as_posix()


def resolve_volume_plan(
    compose_file: Path = _DEFAULT_COMPOSE_FILE,
    *,
    project_name: str | None = None,
    env_file: Path | None = None,
    project_directory: Path | None = None,
    runner: Runner = subprocess.run,
) -> VolumePlan:
    """Resolve the actual volume name from Docker Compose's own render."""

    compose_file = compose_file.resolve()
    if not compose_file.is_file():
        raise BackupError("production Compose file does not exist")
    effective_project_directory = (
        project_directory.expanduser().resolve()
        if project_directory is not None
        else compose_file.parent
    )
    if not effective_project_directory.is_dir():
        raise BackupError("Compose project directory does not exist")
    effective_env_file = env_file.expanduser().resolve() if env_file is not None else None
    if effective_env_file is not None and not effective_env_file.is_file():
        raise BackupError("explicit Compose env file does not exist")
    command = _compose_command(
        compose_file,
        project_name,
        effective_env_file,
        effective_project_directory,
    ) + ["config", "--format", "json"]
    result = _completed(runner, command, cwd=effective_project_directory)
    if result.returncode != 0:
        raise BackupError("Docker Compose could not render the production configuration")
    try:
        rendered = json.loads(result.stdout)
        services = rendered["services"]
        app = services[_APP_SERVICE]
        mounts = [
            mount
            for mount in app.get("volumes", [])
            if isinstance(mount, dict)
            and mount.get("type") == "volume"
            and mount.get("target") == _DATA_TARGET
        ]
        if len(mounts) != 1:
            raise BackupError("production service must have exactly one /data volume")
        logical_name = mounts[0].get("source")
        if not isinstance(logical_name, str) or not logical_name:
            raise BackupError("production data-volume source is ambiguous")
        volume_config = rendered["volumes"][logical_name]
        runtime_name = volume_config.get("name")
        effective_project = rendered.get("name")
        environment = app.get("environment", {})
        helper_image = app.get("image")
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise BackupError("Docker Compose returned an invalid production configuration") from exc
    if not isinstance(runtime_name, str) or not runtime_name:
        raise BackupError("Docker Compose did not resolve the production data volume")
    if not isinstance(effective_project, str) or not effective_project:
        raise BackupError("Docker Compose did not resolve a project name")
    if not isinstance(environment, dict):
        raise BackupError("production service environment is invalid")
    if not isinstance(helper_image, str) or not helper_image:
        raise BackupError("production service image is not resolved")
    database_relative = _database_relative_path(
        environment.get("DATABASE_URL"), _DATA_TARGET
    )
    return VolumePlan(
        compose_file=compose_file,
        project_directory=effective_project_directory,
        project_name=effective_project,
        logical_name=logical_name,
        runtime_name=runtime_name,
        database_relative_path=database_relative,
        helper_image=helper_image,
    )


def _inspect_volume(
    plan: VolumePlan,
    *,
    runner: Runner,
    required: bool,
) -> bool:
    result = _completed(
        runner,
        ["docker", "volume", "inspect", "--format", "{{json .}}", plan.runtime_name],
        cwd=plan.project_directory,
    )
    if result.returncode != 0:
        if required:
            raise BackupError(
                "resolved production volume does not exist; backup refused without creating it"
            )
        return False
    try:
        inspected = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BackupError("Docker returned invalid volume metadata") from exc
    if not isinstance(inspected, dict) or inspected.get("Name") != plan.runtime_name:
        raise BackupError("Docker volume identity does not match the Compose render")
    labels = inspected.get("Labels") or {}
    if not isinstance(labels, dict) or (
        labels.get("com.docker.compose.project") != plan.project_name
        or labels.get("com.docker.compose.volume") != plan.logical_name
    ):
        raise BackupError("resolved volume is not owned by the effective Compose project")
    return True


def _json_rows(raw: str) -> list[dict[str, Any]]:
    text = raw.strip()
    if not text:
        return []
    try:
        decoded = json.loads(text)
        if isinstance(decoded, dict):
            return [decoded]
        if isinstance(decoded, list) and all(isinstance(row, dict) for row in decoded):
            return decoded
    except json.JSONDecodeError:
        pass
    rows: list[dict[str, Any]] = []
    try:
        for line in text.splitlines():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError
            rows.append(row)
    except (json.JSONDecodeError, ValueError) as exc:
        raise BackupError("Docker returned invalid container-state metadata") from exc
    return rows


def verify_writers_stopped(plan: VolumePlan, *, runner: Runner = subprocess.run) -> None:
    """Refuse unless every container mounting the data volume is stopped."""

    result = _completed(
        runner,
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"volume={plan.runtime_name}",
            "--format",
            "{{json .}}",
        ],
        cwd=plan.project_directory,
    )
    if result.returncode != 0:
        raise BackupError("Docker could not verify production writer state")
    for row in _json_rows(result.stdout):
        state = str(row.get("State", "")).strip().lower()
        if state not in {"created", "exited"}:
            raise BackupError(
                "a container using the production data volume is not stopped"
            )


def _safe_tar_members(archive: Path, expected_database: str) -> list[tarfile.TarInfo]:
    if not archive.is_file() or archive.stat().st_size <= 0:
        raise BackupError("backup archive is missing or empty")
    try:
        with tarfile.open(archive, "r:gz") as handle:
            members = handle.getmembers()
    except (OSError, tarfile.TarError) as exc:
        raise BackupError("backup archive is not a valid gzip tar archive") from exc
    if not members:
        raise BackupError("backup archive contains no files")
    expected_found = False
    for member in members:
        raw = member.name.replace("\\", "/")
        canonical = raw
        while canonical.startswith("./"):
            canonical = canonical[2:]
        normalized = posixpath.normpath(canonical)
        parts = PurePosixPath(canonical).parts
        if raw.startswith("/") or normalized == ".." or ".." in parts:
            raise BackupError("backup archive contains an unsafe path")
        if not (member.isdir() or member.isfile()):
            raise BackupError("backup archive contains an unsupported entry type")
        if normalized == expected_database:
            if not member.isfile() or member.size <= 0:
                raise BackupError("backup archive contains an empty SQLite database")
            expected_found = True
    if not expected_found:
        raise BackupError("backup archive does not contain the expected SQLite database")
    return members


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BackupError("backup archive is missing or unreadable") from exc
    return digest.hexdigest()


def _write_checksum(path: Path, digest: str, archive_name: str) -> None:
    try:
        path.write_text(f"{digest}  {archive_name}\n", encoding="ascii")
    except OSError as exc:
        raise BackupError("backup checksum sidecar could not be written") from exc


def _prepare_private_output_directory(path: Path) -> Path:
    """Create/validate the host backup directory without weakening privacy.

    POSIX permission bits are authoritative on Unix hosts.  Windows ACLs do
    not have a portable stdlib owner-only test, so Windows relies on the
    operator selecting an ACL-protected directory as documented.
    """

    try:
        resolved = path.expanduser().resolve()
        resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise BackupError("backup output directory is unavailable") from exc
    if os.name != "nt":
        permissions = stat.S_IMODE(metadata.st_mode)
        if metadata.st_uid != os.geteuid():
            raise BackupError("backup output directory is not owned by the current operator")
        if permissions & 0o077:
            raise BackupError(
                "backup output directory permits group/other access; require mode 0700"
            )
    return resolved


def _verify_private_archive(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        metadata = path.stat()
    except OSError as exc:
        raise BackupError("backup archive is missing or unreadable") from exc
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise BackupError("backup archive owner/mode is not private (required: operator 0600)")


_BACKUP_SCRIPT = r"""
import os, pathlib, sys, tarfile
source = pathlib.Path('/source')
destination = pathlib.Path('/backup') / sys.argv[1]
temporary = pathlib.Path(str(destination) + '.partial')
database = source / pathlib.PurePosixPath(sys.argv[2])
owner_uid = int(sys.argv[3])
owner_gid = int(sys.argv[4])
if not database.is_file() or database.stat().st_size <= 0:
    raise SystemExit(41)
if destination.exists() or temporary.exists():
    raise SystemExit(42)
try:
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        with tarfile.open(fileobj=output, mode='w:gz') as archive:
            archive.add(source, arcname='.')
    if temporary.stat().st_size <= 0:
        raise SystemExit(43)
    if owner_uid >= 0:
        os.chown(temporary, owner_uid, owner_gid)
    os.chmod(temporary, 0o600)
    temporary.replace(destination)
except BaseException:
    temporary.unlink(missing_ok=True)
    raise
""".strip()


_RESTORE_SCRIPT = r"""
import pathlib, sys, tarfile
target = pathlib.Path('/target')
archive_path = pathlib.Path('/backup') / sys.argv[1]
database = target / pathlib.PurePosixPath(sys.argv[2])
if any(target.iterdir()):
    raise SystemExit(51)
with tarfile.open(archive_path, 'r:gz') as archive:
    archive.extractall(target, filter='data')
if not database.is_file() or database.stat().st_size <= 0:
    raise SystemExit(52)
""".strip()


def _docker_backup_command(plan: VolumePlan, output_dir: Path, archive_name: str) -> list[str]:
    if os.name == "nt":
        owner_uid, owner_gid = -1, -1
    else:
        owner_uid, owner_gid = os.geteuid(), os.getegid()
    return [
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--network",
        "none",
        "--read-only",
        "--user",
        "0:0",
        "--entrypoint",
        "python",
        "--mount",
        f"type=volume,source={plan.runtime_name},target=/source,readonly",
        "--mount",
        f"type=bind,source={output_dir},target=/backup",
        plan.helper_image,
        "-c",
        _BACKUP_SCRIPT,
        archive_name,
        plan.database_relative_path,
        str(owner_uid),
        str(owner_gid),
    ]


def create_backup(
    output_dir: Path,
    *,
    compose_file: Path = _DEFAULT_COMPOSE_FILE,
    project_name: str | None = None,
    env_file: Path | None = None,
    project_directory: Path | None = None,
    runner: Runner = subprocess.run,
    now: datetime | None = None,
) -> BackupResult:
    plan = resolve_volume_plan(
        compose_file,
        project_name=project_name,
        env_file=env_file,
        project_directory=project_directory,
        runner=runner,
    )
    _inspect_volume(plan, runner=runner, required=True)
    verify_writers_stopped(plan, runner=runner)

    output_dir = _prepare_private_output_directory(output_dir)
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    archive_name = f"procedural-detective-{stamp:%Y%m%dT%H%M%S%fZ}.tar.gz"
    archive = output_dir / archive_name
    checksum_file = output_dir / f"{archive_name}.sha256"
    if archive.exists() or checksum_file.exists():
        raise BackupError("timestamped backup destination already exists")

    result = _completed(
        runner,
        _docker_backup_command(plan, output_dir, archive_name),
        cwd=plan.project_directory,
        timeout=1800,
    )
    if result.returncode != 0:
        archive.unlink(missing_ok=True)
        archive.with_name(archive.name + ".partial").unlink(missing_ok=True)
        raise BackupError(
            "backup helper failed; no verified backup was produced (check that the database exists)"
        )
    try:
        _safe_tar_members(archive, plan.database_relative_path)
        _verify_private_archive(archive)
    except BackupError:
        archive.unlink(missing_ok=True)
        raise
    digest = _sha256(archive)
    try:
        _write_checksum(checksum_file, digest, archive.name)
    except BackupError:
        archive.unlink(missing_ok=True)
        checksum_file.unlink(missing_ok=True)
        raise
    return BackupResult(archive, checksum_file, digest, plan.runtime_name)


def _read_expected_checksum(archive: Path, checksum_file: Path | None) -> str:
    sidecar = checksum_file or archive.with_name(archive.name + ".sha256")
    if not sidecar.is_file():
        raise BackupError("backup checksum sidecar is missing")
    try:
        first = sidecar.read_text(encoding="ascii").strip().split()
    except (OSError, UnicodeError) as exc:
        raise BackupError("backup checksum sidecar is unreadable") from exc
    if not first or not _HEX_SHA256.fullmatch(first[0].lower()):
        raise BackupError("backup checksum sidecar is invalid")
    return first[0].lower()


def _create_restore_volume(plan: VolumePlan, *, runner: Runner) -> None:
    command = [
        "docker",
        "volume",
        "create",
        "--label",
        f"com.docker.compose.project={plan.project_name}",
        "--label",
        f"com.docker.compose.volume={plan.logical_name}",
        plan.runtime_name,
    ]
    result = _completed(runner, command, cwd=plan.project_directory)
    if result.returncode != 0 or result.stdout.strip() != plan.runtime_name:
        raise BackupError("Docker could not create the exact Compose data volume")
    _inspect_volume(plan, runner=runner, required=True)


def _docker_restore_command(plan: VolumePlan, archive: Path) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--network",
        "none",
        "--read-only",
        "--user",
        "0:0",
        "--entrypoint",
        "python",
        "--mount",
        f"type=volume,source={plan.runtime_name},target=/target",
        "--mount",
        f"type=bind,source={archive.parent},target=/backup,readonly",
        plan.helper_image,
        "-c",
        _RESTORE_SCRIPT,
        archive.name,
        plan.database_relative_path,
    ]


def restore_backup(
    archive: Path,
    *,
    yes: bool,
    create_volume: bool,
    checksum_file: Path | None = None,
    compose_file: Path = _DEFAULT_COMPOSE_FILE,
    project_name: str | None = None,
    env_file: Path | None = None,
    project_directory: Path | None = None,
    runner: Runner = subprocess.run,
) -> VolumePlan:
    if not yes:
        raise BackupError("restore refused without --yes")
    archive = archive.expanduser().resolve()
    expected_checksum = _read_expected_checksum(archive, checksum_file)
    if _sha256(archive) != expected_checksum:
        raise BackupError("backup checksum does not match")
    plan = resolve_volume_plan(
        compose_file,
        project_name=project_name,
        env_file=env_file,
        project_directory=project_directory,
        runner=runner,
    )
    _safe_tar_members(archive, plan.database_relative_path)
    _verify_private_archive(archive)

    exists = _inspect_volume(plan, runner=runner, required=False)
    if exists:
        verify_writers_stopped(plan, runner=runner)
    else:
        if not create_volume:
            raise BackupError(
                "resolved production volume is missing; pass --create-volume "
                "with --yes to restore it"
            )
        _create_restore_volume(plan, runner=runner)
        verify_writers_stopped(plan, runner=runner)

    result = _completed(
        runner,
        _docker_restore_command(plan, archive),
        cwd=plan.project_directory,
        timeout=1800,
    )
    if result.returncode != 0:
        raise BackupError(
            "restore helper failed; target must be an empty, stopped production volume"
        )
    return plan


def _common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=_DEFAULT_COMPOSE_FILE,
        help="production Compose file (default: repository docker-compose.prod.yml)",
    )
    parser.add_argument(
        "--project-name",
        help="Compose project name override; omit to use Compose's effective environment/default",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help=(
            "the exact explicit Docker Compose --env-file used for startup; "
            "omit for Compose's automatic project .env"
        ),
    )
    parser.add_argument(
        "--project-directory",
        type=Path,
        help=(
            "the exact Docker Compose --project-directory used for startup; "
            "defaults to the Compose file directory"
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.backup_production",
        description=(
            "Audited backup/restore for the actual Compose production data volume. "
            "All containers mounting the volume must be stopped."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    backup_parser = commands.add_parser("backup", help="create and verify a backup")
    backup_parser.add_argument("--output-dir", type=Path, required=True)
    _common_options(backup_parser)
    restore_parser = commands.add_parser("restore", help="verify and restore a backup")
    restore_parser.add_argument("archive", type=Path)
    restore_parser.add_argument("--checksum-file", type=Path)
    restore_parser.add_argument("--yes", action="store_true")
    restore_parser.add_argument(
        "--create-volume",
        action="store_true",
        help="create only the exact Compose-resolved volume when it is missing",
    )
    _common_options(restore_parser)
    args = parser.parse_args(argv)

    try:
        if args.command == "backup":
            result = create_backup(
                args.output_dir,
                compose_file=args.compose_file,
                project_name=args.project_name,
                env_file=args.env_file,
                project_directory=args.project_directory,
            )
            print(f"production volume resolved: {result.runtime_volume}")
            print(f"verified backup: {result.archive}")
            print(f"SHA-256: {result.sha256}")
            print(f"checksum file: {result.checksum_file}")
            if os.name == "nt":
                print(
                    "privacy note: Python cannot portably verify Windows ACLs; "
                    "confirm the output directory is restricted to the operator account"
                )
        else:
            plan = restore_backup(
                args.archive,
                yes=args.yes,
                create_volume=args.create_volume,
                checksum_file=args.checksum_file,
                compose_file=args.compose_file,
                project_name=args.project_name,
                env_file=args.env_file,
                project_directory=args.project_directory,
            )
            print(f"verified restore completed: {plan.runtime_name}")
            print("start the stack and verify readiness before returning it to service")
        return 0
    except BackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
