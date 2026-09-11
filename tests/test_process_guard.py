"""pytest suite for tools.process_guard.

All dangerous-process scenarios use ONLY the isolated dummy fixture
(tests/fixtures/dummy_process.py) and never target arbitrary system processes
(REQUIREMENTS 65A.7).  Teardown terminates ONLY pids this suite itself spawned.
"""

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.process_guard import (  # noqa: E402
    PortConflictError,
    ProcessIdentity,
    ProcessMetadata,
    _authorized_for_kill,
    collect_descendants,
    find_listener_pid,
    generate_run_id,
    identity_matches,
    load_metadata,
    main,
    port_in_use,
    probe_ownership,
    process_exists,
    record_metadata,
    shutdown_process,
    snapshot_process,
    terminate_process,
    verify_port_available,
)

FIXTURE = Path(__file__).parent / "fixtures" / "dummy_process.py"


# ---------------------------------------------------------------------------
# lifecycle helpers + teardown (65A.7)
# ---------------------------------------------------------------------------
KNOWN_PIDS: list = []
SPAWNED_PROCS: list = []


@pytest.fixture(autouse=True, scope="module")
def _no_surviving_processes():
    """Module teardown: kill only processes this suite spawned (65A.7)."""
    yield
    for pid in list(KNOWN_PIDS):
        try:
            if process_exists(pid):
                terminate_process(pid)
        except Exception:
            pass
    KNOWN_PIDS.clear()
    for proc in list(SPAWNED_PROCS):
        try:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        except Exception:
            pass
    SPAWNED_PROCS.clear()


def wait_until(predicate, timeout=10.0, interval=0.1):
    deadline = time.monotonic() + timeout
    value = None
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


def wait_snapshot(pid, timeout=10.0):
    snap = {"identity": None}

    def _got():
        snap["identity"] = snapshot_process(pid)
        return snap["identity"] is not None

    wait_until(_got, timeout)
    return snap["identity"]


def spawn_dummy(*argv):
    proc = subprocess.Popen(
        [sys.executable, str(FIXTURE), *[str(a) for a in argv]],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    SPAWNED_PROCS.append(proc)
    return proc


def register_pid(pid):
    KNOWN_PIDS.append(pid)


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _identity(pid, create_time, executable=None, parent_pid=None, token=None):
    return ProcessIdentity(
        pid=pid, create_time=create_time, executable=executable,
        cmdline=None, parent_pid=parent_pid, token=token,
    )


# ---------------------------------------------------------------------------
# 65A.1 — identity verification (never kill by PID alone)
# ---------------------------------------------------------------------------
def test_identity_matches_exact_match():
    expected = _identity(10, 1000.0, executable="dummy_app.exe", parent_pid=7, token="pdr-a")
    actual = _identity(10, 1000.0, executable="dummy_app.exe", parent_pid=7, token="pdr-a")
    ok, reasons = identity_matches(expected, actual)
    assert ok is True
    assert reasons == []


def test_identity_matches_create_time_mismatch():
    expected = _identity(10, 1000.0, executable="dummy_app.exe", parent_pid=7)
    actual = _identity(10, 2000.0, executable="dummy_app.exe", parent_pid=7)
    ok, reasons = identity_matches(expected, actual)
    assert ok is False
    assert any("create time" in r for r in reasons)


def test_identity_matches_executable_mismatch():
    expected = _identity(10, 1000.0, executable="app_a.exe", parent_pid=7)
    actual = _identity(10, 1000.0, executable="app_b.exe", parent_pid=7)
    ok, reasons = identity_matches(expected, actual)
    assert ok is False
    assert any("executable" in r for r in reasons)


def test_identity_matches_parent_mismatch_respects_require_parent():
    expected = _identity(10, 1000.0, executable="dummy_app.exe", parent_pid=7)
    actual = _identity(10, 1000.0, executable="dummy_app.exe", parent_pid=99)
    ok, reasons = identity_matches(expected, actual)
    assert ok is False
    assert any("parent" in r for r in reasons)
    ok_loose, reasons_loose = identity_matches(expected, actual, require_parent=False)
    assert ok_loose is True
    assert reasons_loose == []


def test_identity_matches_unknown_attributes_are_not_mismatches():
    expected = _identity(10, 1000.0, executable=None, parent_pid=None)
    actual = _identity(10, 1000.0, executable=None, parent_pid=None)
    ok, reasons = identity_matches(expected, actual)
    assert ok is True
    assert reasons == []


def test_identity_matches_token_mismatch_only_when_both_known():
    expected = _identity(10, 1000.0, token="pdr-aaa")
    actual = _identity(10, 1000.0, token="pdr-bbb")
    ok, reasons = identity_matches(expected, actual)
    assert ok is False
    assert any("token" in r for r in reasons)
    # a live snapshot cannot report the env token -> never blocks verification
    expected2 = _identity(10, 1000.0, token="pdr-aaa")
    actual2 = _identity(10, 1000.0, token=None)
    ok2, _ = identity_matches(expected2, actual2)
    assert ok2 is True


def test_snapshot_missing_pid_returns_none():
    assert snapshot_process(999999) is None


def test_process_exists_basics():
    proc = spawn_dummy("sleep", "--seconds", "10")
    assert process_exists(proc.pid) is True
    assert process_exists(999999) is False


def test_create_time_is_stable_across_snapshots():
    proc = spawn_dummy("sleep", "--seconds", "10")
    snap1 = wait_snapshot(proc.pid)
    time.sleep(1.1)
    snap2 = wait_snapshot(proc.pid)
    assert snap1 is not None and snap2 is not None
    assert abs(snap1.create_time - snap2.create_time) < 1e-6


def test_pid_reuse_is_never_killed_and_metadata_retained(tmp_path):
    """65A.1 + 65B.1: a stored PID whose recorded attributes no longer match the
    live process (simulated PID reuse) MUST NOT be terminated, and the metadata
    file describing it MUST stay on disk."""
    proc = spawn_dummy("sleep", "--seconds", "30")
    snap = wait_snapshot(proc.pid)
    assert snap is not None
    pid = proc.pid

    forged = ProcessMetadata(
        run_id="pdr-forged",
        pid=pid,
        create_time=snap.create_time - 3600.0,  # PID now belongs to another process
        executable=snap.executable,
        cmdline=None,
        parent_pid=snap.parent_pid,
        port=None,
        launched_at=None,
        status="STARTED",
        terminate_error=None,
        surviving_descendants=[],
        notes=[],
    )
    meta_path = tmp_path / "meta.json"
    record_metadata(forged, str(meta_path))

    result = shutdown_process(str(meta_path))

    assert result["status"] == "UNVERIFIED"
    assert result["metadata_retained"] is True
    assert result["reasons"], "expected human-readable mismatch reasons"
    assert proc.poll() is None, "the dummy process must NOT have been killed"
    assert meta_path.exists(), "metadata must be retained after PID-reuse refusal"
    assert load_metadata(str(meta_path)).status == "UNVERIFIED"


# ---------------------------------------------------------------------------
# 65A.2 — ownership verification during startup (never claim foreign listeners)
# ---------------------------------------------------------------------------
def test_verify_port_available_raises_on_occupied_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        with pytest.raises(PortConflictError) as excinfo:
            verify_port_available(port)
        assert excinfo.value.port == port
    finally:
        s.close()


def test_foreign_listener_is_not_claimed_nor_terminated(tmp_path):
    """65A.2 + 65B.2: a foreign process bound to the expected port must be
    reported as a port conflict, never claimed as owned, and never terminated."""
    port = free_port()
    dummy = spawn_dummy("bind", "--port", str(port), "--seconds", "30")
    assert wait_until(lambda: find_listener_pid(port) is not None, timeout=15.0)

    with pytest.raises(PortConflictError) as excinfo:
        verify_port_available(port)
    assert excinfo.value.port == port

    # "our" expected identity: a process we (pretend to have) launched.
    dummy_snap = wait_snapshot(dummy.pid)
    our_expected = _identity(999998, 0.0, executable=dummy_snap.executable, parent_pid=None)

    owned, listener_pid, _identity_of_listener, _reasons = probe_ownership(port, our_expected)
    assert owned is False
    assert listener_pid == dummy.pid

    # Metadata claiming the foreign pid as ours, but with forged attributes.
    forged = ProcessMetadata(
        run_id="pdr-foreign",
        pid=dummy.pid,
        create_time=dummy_snap.create_time - 3600.0,
        executable=dummy_snap.executable,
        cmdline=None,
        parent_pid=dummy_snap.parent_pid,
        port=port,
        launched_at=None,
        status="STARTED",
        terminate_error=None,
        surviving_descendants=[],
        notes=[],
    )
    meta_path = tmp_path / "meta.json"
    record_metadata(forged, str(meta_path))

    result = shutdown_process(str(meta_path))
    assert result["status"] == "UNVERIFIED"
    assert result["metadata_retained"] is True
    assert dummy.poll() is None, "foreign listener must remain untouched"
    assert meta_path.exists()

    # cleanup: terminate ONLY the dummy we spawned
    ok, err = terminate_process(dummy.pid)
    assert ok is True, err


# ---------------------------------------------------------------------------
# 65A.4 — never discard tracking metadata after failed shutdown
# ---------------------------------------------------------------------------
def test_failed_termination_retains_metadata_then_clean_shutdown(tmp_path):
    """65A.4 + 65B.3: a failing termination must keep the metadata (with status
    CLEANUP_INCOMPLETE) and leave processes untouched; after the tracked child
    is gone a real shutdown verifies and erases the metadata."""
    child_pidfile = tmp_path / "child.pid"
    root = spawn_dummy(
        "sleep", "--seconds", "60", "--child-duration", "60",
        "--pidfile", str(child_pidfile),
    )
    snap = wait_snapshot(root.pid)
    assert snap is not None
    assert wait_until(lambda: child_pidfile.exists())
    child_pid = int(child_pidfile.read_text(encoding="utf-8").strip())
    register_pid(child_pid)
    assert wait_until(lambda: process_exists(child_pid)), "child must come up"

    meta = ProcessMetadata(
        run_id="pdr-fail",
        pid=root.pid,
        create_time=snap.create_time,
        executable=snap.executable,
        cmdline=None,
        parent_pid=snap.parent_pid,
        port=None,
        launched_at=None,
        status="VERIFIED",
        terminate_error=None,
        surviving_descendants=[],
        notes=[],
    )
    meta_path = tmp_path / "meta.json"
    record_metadata(meta, str(meta_path))

    def failing_terminate(pid):
        raise OSError("simulated termination failure")

    result = shutdown_process(str(meta_path), terminate_fn=failing_terminate)
    assert result["status"] == "CLEANUP_INCOMPLETE"
    assert result["metadata_retained"] is True
    assert meta_path.exists()
    assert load_metadata(str(meta_path)).status == "CLEANUP_INCOMPLETE"
    assert root.poll() is None, "root must still be alive after simulated failure"
    assert process_exists(child_pid), "child must still be alive"

    # Reap our tracked child (the tool never force-terminates unproven
    # descendants; this test owns it), then run a REAL verified shutdown.
    ok, err = terminate_process(child_pid)
    assert ok is True, err

    result2 = shutdown_process(str(meta_path))
    assert result2["status"] == "SHUTDOWN_OK"
    assert result2["metadata_retained"] is False
    assert not meta_path.exists()
    assert wait_until(lambda: root.poll() is not None)
    assert wait_until(lambda: not process_exists(root.pid))


def test_surviving_child_blocks_shutdown_and_is_reported(tmp_path):
    """65A.4 + 65B.3: root terminated but a tracked descendant still alive ->
    CLEANUP_INCOMPLETE, metadata retained, surviving descendant reported."""
    child_pidfile = tmp_path / "child.pid"
    root = spawn_dummy(
        "sleep", "--seconds", "60", "--child-duration", "60",
        "--pidfile", str(child_pidfile),
    )
    snap = wait_snapshot(root.pid)
    assert snap is not None
    assert wait_until(lambda: child_pidfile.exists())
    child_pid = int(child_pidfile.read_text(encoding="utf-8").strip())
    register_pid(child_pid)
    assert wait_until(lambda: process_exists(child_pid)), "child must come up"

    meta = ProcessMetadata(
        run_id="pdr-survivor",
        pid=root.pid,
        create_time=snap.create_time,
        executable=snap.executable,
        cmdline=None,
        parent_pid=snap.parent_pid,
        port=None,
        launched_at=None,
        status="VERIFIED",
        terminate_error=None,
        surviving_descendants=[],
        notes=[],
    )
    meta_path = tmp_path / "meta.json"
    record_metadata(meta, str(meta_path))

    def kill_only_root(pid):
        ok, err = terminate_process(pid)
        assert ok is True, err

    result = shutdown_process(str(meta_path), terminate_fn=kill_only_root)

    assert result["status"] == "CLEANUP_INCOMPLETE"
    assert result["metadata_retained"] is True
    assert child_pid in result["surviving_descendants"]
    assert meta_path.exists()
    assert wait_until(lambda: root.poll() is not None), "root must be terminated"
    assert process_exists(child_pid), "orphaned child remains alive -> not clean"

    on_disk = load_metadata(str(meta_path))
    assert on_disk.status == "CLEANUP_INCOMPLETE"
    assert child_pid in on_disk.surviving_descendants

    # final cleanup of OUR orphaned child (65A.7)
    ok, err = terminate_process(child_pid)
    assert ok is True, err
    assert wait_until(lambda: not process_exists(child_pid))


# ---------------------------------------------------------------------------
# Happy path regression (65A.2/65A.3/65A.7 + 65B.6)
# ---------------------------------------------------------------------------
def test_happy_path_verify_record_shutdown(tmp_path):
    """Launch-like flow: port free -> spawn -> identity verified -> metadata
    recorded -> shutdown -> SHUTDOWN_OK, metadata erased, port released."""
    port = free_port()
    meta_path = tmp_path / "meta.json"

    verify_port_available(port)  # 65A.2 pre-launch check

    dummy = spawn_dummy("bind", "--port", str(port), "--seconds", "30")
    assert wait_until(lambda: find_listener_pid(port) is not None, timeout=15.0)
    snap = wait_snapshot(dummy.pid)
    assert snap is not None

    expected = _identity(
        snap.pid, snap.create_time, executable=snap.executable,
        parent_pid=snap.parent_pid, token="pdr-happy",
    )
    owned, listener_pid, listener_id, reasons = probe_ownership(port, expected)
    assert owned is True, reasons
    assert listener_pid == dummy.pid
    assert listener_id is not None

    meta = ProcessMetadata(
        run_id="pdr-happy",
        pid=snap.pid,
        create_time=snap.create_time,
        executable=snap.executable,
        cmdline=None,
        parent_pid=snap.parent_pid,
        port=port,
        launched_at=None,
        status="VERIFIED",
        terminate_error=None,
        surviving_descendants=[],
        notes=[],
    )
    record_metadata(meta, str(meta_path))  # written ONLY after verification (65A.2)
    assert meta_path.exists()

    result = shutdown_process(str(meta_path))
    assert result["status"] == "SHUTDOWN_OK"
    assert result["metadata_retained"] is False
    assert not meta_path.exists()
    assert wait_until(lambda: not port_in_use(port)), "port must be released"
    assert port_in_use(port) is False


# ---------------------------------------------------------------------------
# 65A.3 / 65A.7 — CLI smoke
# ---------------------------------------------------------------------------
def test_cli_generate_run_id_unique():
    def _run():
        out = subprocess.run(
            [sys.executable, "-m", "tools.process_guard", "generate-run-id"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, check=False, timeout=30,
        )
        assert out.returncode == 0, out.stderr
        return out.stdout.strip()

    first = _run()
    second = _run()
    assert first.startswith("pdr-") and second.startswith("pdr-")
    assert first and first != second


def test_cli_launch_and_stop_roundtrip(tmp_path):
    meta_path = tmp_path / "cli_meta.json"
    launch = subprocess.run(
        [
            sys.executable, "-m", "tools.process_guard", "launch",
            "--cmd", sys.executable,
            "--meta", str(meta_path),
            "--args", str(FIXTURE), "sleep", "--seconds", "30",
        ],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False, timeout=60,
    )
    assert launch.returncode == 0, launch.stderr
    assert meta_path.exists()
    metadata = load_metadata(str(meta_path))
    assert metadata is not None
    assert metadata.status == "VERIFIED"
    assert metadata.run_id.startswith("pdr-")
    assert process_exists(metadata.pid)

    stop = subprocess.run(
        [sys.executable, "-m", "tools.process_guard", "stop", "--meta", str(meta_path)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False, timeout=60,
    )
    assert stop.returncode == 0, stop.stdout + stop.stderr
    assert not meta_path.exists()
    assert wait_until(lambda: not process_exists(metadata.pid))


# ---------------------------------------------------------------------------
# ADV-003 / F7 — fail-closed kill authorization gate
# ---------------------------------------------------------------------------
def test_authorized_for_kill_requires_confirmed_attribute():
    """F7 primitive: pid + known create-time alone never authorizes; one
    confirmed extra attribute (executable, parent or token) unlocks it."""
    pid, ct = 123, 1000.0
    bare = _identity(pid, ct)
    ok, reasons = _authorized_for_kill(bare, bare)
    assert ok is False
    assert any("additional identity attribute" in r for r in reasons)

    ok_exe, reasons_exe = _authorized_for_kill(
        _identity(pid, ct, executable="dummy_app.exe"),
        _identity(pid, ct, executable="dummy_app.exe"),
    )
    assert ok_exe is True
    assert reasons_exe == []

    ok_tok, _ = _authorized_for_kill(
        _identity(pid, ct, token="pdr-t"),
        _identity(pid, ct, token="pdr-t"),
    )
    assert ok_tok is True

    # create time unknown on both sides -> deny even with a matching executable
    ok_unknown_ct, reasons_ct = _authorized_for_kill(
        ProcessIdentity(pid=pid, create_time=None, executable="app.exe"),
        ProcessIdentity(pid=pid, create_time=None, executable="app.exe"),
    )
    assert ok_unknown_ct is False
    assert any("create time unknown" in r for r in reasons_ct)


def test_shutdown_denied_when_only_pid_and_create_time_known(tmp_path):
    """ADV-003 / 65B.1: metadata with pid + a within-tolerance create time but
    NO confirmed executable/parent/run token must fail closed (UNVERIFIED) and
    never kill the live process."""
    proc = spawn_dummy("sleep", "--seconds", "30")
    snap = wait_snapshot(proc.pid)
    assert snap is not None
    meta = ProcessMetadata(
        run_id="",  # no run token
        pid=proc.pid,
        create_time=snap.create_time,  # within the 5s tolerance
        executable=None,
        cmdline=None,
        parent_pid=None,
        port=None,
        launched_at=None,
        status="STARTED",
        terminate_error=None,
        surviving_descendants=[],
        notes=[],
    )
    meta_path = tmp_path / "meta.json"
    record_metadata(meta, str(meta_path))

    result = shutdown_process(str(meta_path))

    assert result["status"] == "UNVERIFIED"
    assert result["metadata_retained"] is True
    assert any("additional identity attribute" in r for r in result["reasons"])
    assert proc.poll() is None, "dummy must NOT be killed on an unproven identity"
    assert meta_path.exists()
    assert load_metadata(str(meta_path)).status == "UNVERIFIED"


def test_shutdown_authorized_with_confirmed_executable(tmp_path):
    """F7: a matching executable (the shape produced by the launch CLI after the
    ADV-012 parent_pid=None change) passes the gate -> SHUTDOWN_OK."""
    proc = spawn_dummy("sleep", "--seconds", "30")
    snap = wait_snapshot(proc.pid)
    assert snap is not None
    meta = ProcessMetadata(
        run_id="pdr-exec",
        pid=proc.pid,
        create_time=snap.create_time,
        executable=snap.executable,  # confirmed matching attribute
        cmdline=None,
        parent_pid=None,
        port=None,
        launched_at=None,
        status="VERIFIED",
        terminate_error=None,
        surviving_descendants=[],
        notes=[],
    )
    meta_path = tmp_path / "meta.json"
    record_metadata(meta, str(meta_path))

    result = shutdown_process(str(meta_path))

    assert result["status"] == "SHUTDOWN_OK"
    assert result["metadata_retained"] is False
    assert not meta_path.exists()
    assert wait_until(lambda: not process_exists(proc.pid))


def test_shutdown_authorized_with_confirmed_run_token(tmp_path):
    """F7: a matching run token (both sides must carry it) passes the gate."""
    proc = spawn_dummy("sleep", "--seconds", "30")
    snap = wait_snapshot(proc.pid)
    assert snap is not None
    meta = ProcessMetadata(
        run_id="pdr-gate-token",
        pid=proc.pid,
        create_time=snap.create_time,
        executable=None,
        cmdline=None,
        parent_pid=None,
        port=None,
        launched_at=None,
        status="VERIFIED",
        terminate_error=None,
        surviving_descendants=[],
        notes=[],
    )
    meta_path = tmp_path / "meta.json"
    record_metadata(meta, str(meta_path))

    def token_snapshot(pid):
        return ProcessIdentity(
            pid=pid, create_time=snap.create_time, executable=None,
            cmdline=None, parent_pid=None, token="pdr-gate-token",
        )

    result = shutdown_process(str(meta_path), snapshot_fn=token_snapshot)

    assert result["status"] == "SHUTDOWN_OK"
    assert not meta_path.exists()
    assert wait_until(lambda: not process_exists(proc.pid))


# ---------------------------------------------------------------------------
# ADV-004 / F8 — port validation
# ---------------------------------------------------------------------------
def test_cli_launch_rejects_out_of_range_port(tmp_path):
    meta_path = tmp_path / "meta.json"
    result = subprocess.run(
        [sys.executable, "-m", "tools.process_guard", "launch",
         "--cmd", sys.executable, "--port", "99999",
         "--meta", str(meta_path), "--args", str(FIXTURE), "sleep", "--seconds", "30"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False, timeout=60,
    )
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "invalid port" in result.stderr
    assert not meta_path.exists()


def test_cli_launch_rejects_port_zero(tmp_path):
    meta_path = tmp_path / "meta.json"
    result = subprocess.run(
        [sys.executable, "-m", "tools.process_guard", "launch",
         "--cmd", sys.executable, "--port", "0",
         "--meta", str(meta_path), "--args", str(FIXTURE), "sleep", "--seconds", "30"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False, timeout=60,
    )
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "invalid port" in result.stderr
    assert not meta_path.exists()


# ---------------------------------------------------------------------------
# ADV-006 / F9 — corrupt-vs-missing metadata
# ---------------------------------------------------------------------------
def test_corrupt_metadata_is_unreadable_and_retained(tmp_path):
    """F9: unreadable metadata (present on disk) is NOT_RUNNING's opposite —
    UNREADABLE_METADATA with metadata_retained True; missing metadata stays
    NOT_RUNNING. Nothing is ever killed."""
    meta_path = tmp_path / "corrupt.json"
    meta_path.write_bytes(b'{"pid": "not-an-int"\n')  # invalid JSON
    result = shutdown_process(str(meta_path))
    assert result["status"] == "UNREADABLE_METADATA"
    assert result["metadata_retained"] is True
    assert meta_path.exists()

    bad_types = tmp_path / "badtypes.json"
    bad_types.write_bytes(b'{"pid": "not-an-int"}')  # valid JSON, wrong types
    result2 = shutdown_process(str(bad_types))
    assert result2["status"] == "UNREADABLE_METADATA"
    assert result2["metadata_retained"] is True
    assert bad_types.exists()

    missing = tmp_path / "missing.json"
    result3 = shutdown_process(str(missing))
    assert result3["status"] == "NOT_RUNNING"
    assert result3["metadata_retained"] is False


# ---------------------------------------------------------------------------
# ADV-011 / F10 — idempotent CLI stop
# ---------------------------------------------------------------------------
def test_cli_stop_is_idempotent(tmp_path):
    meta_path = tmp_path / "idem.json"
    launch = subprocess.run(
        [sys.executable, "-m", "tools.process_guard", "launch",
         "--cmd", sys.executable,
         "--meta", str(meta_path),
         "--args", str(FIXTURE), "sleep", "--seconds", "30"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False, timeout=60,
    )
    assert launch.returncode == 0, launch.stderr

    stop1 = subprocess.run(
        [sys.executable, "-m", "tools.process_guard", "stop", "--meta", str(meta_path)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False, timeout=60,
    )
    assert stop1.returncode == 0, stop1.stdout + stop1.stderr
    assert not meta_path.exists()

    # a retried stop of an already-stopped app is a successful no-op (exit 0)
    stop2 = subprocess.run(
        [sys.executable, "-m", "tools.process_guard", "stop", "--meta", str(meta_path)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False, timeout=60,
    )
    assert stop2.returncode == 0, stop2.stdout + stop2.stderr
    assert json.loads(stop2.stdout)["status"] == "NOT_RUNNING"


# ---------------------------------------------------------------------------
# DEF-002 / F12 — clean launch errors
# ---------------------------------------------------------------------------
def test_cli_launch_missing_executable_is_clean_error(tmp_path):
    meta_path = tmp_path / "bad.json"
    result = subprocess.run(
        [sys.executable, "-m", "tools.process_guard", "launch",
         "--cmd", "definitely-not-a-real-exe-xyz",
         "--meta", str(meta_path)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False, timeout=60,
    )
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "cannot launch" in result.stderr
    assert not meta_path.exists()


# ---------------------------------------------------------------------------
# DEF-019a — a failed launch via a .cmd wrapper must not orphan the spawned
# tree (the recorded cmd.exe is verified, its listener descendant is reaped)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="wrapper-launch orphan path is Windows-specific")
def test_failed_launch_via_cmd_wrapper_does_not_orphan(tmp_path):
    """A launch failure AFTER a successful Popen (a .cmd wrapper was recorded as
    the direct child but the real listener is a descendant) must terminate the
    whole spawned tree: no listener may stay on the port and no process whose
    command line carries our wrapper/port may survive, even with no metadata
    written."""
    port = free_port()
    wrapper = tmp_path / "dummy_wrapper.cmd"
    wrapper.write_text(
        f'@echo off\r\n"{sys.executable}" "{FIXTURE}" bind --port {port} --seconds 60\r\n',
        encoding="utf-8",
    )
    meta_path = tmp_path / "meta.json"

    launch = subprocess.run(
        [sys.executable, "-m", "tools.process_guard", "launch",
         "--cmd", str(wrapper), "--port", str(port), "--meta", str(meta_path),
         "--wait", "25"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False, timeout=120,
    )
    assert launch.returncode != 0
    assert "does not match the launched process" in launch.stderr
    assert not meta_path.exists(), "a failed launch must never write metadata"

    # The spawned tree must be fully terminated: port free again, no listener.
    assert wait_until(lambda: not port_in_use(port), timeout=10.0), \
        "the listener spawned via the wrapper must be terminated (port free)"
    assert find_listener_pid(port) is None
    time.sleep(1.0)
    assert port_in_use(port) is False

    # No stray process whose command line still references our unique markers.
    try:
        scan = subprocess.run(
            ["wmic", "process", "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, text=True, errors="replace", check=False, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        scan = None
    if scan is not None and scan.stdout:
        stray = [
            line for line in scan.stdout.splitlines()
            if f"bind --port {port}" in line and "dummy_process.py" in line
        ]
        assert not stray, f"spawned-tree processes survived the failed launch: {stray}"


# ---------------------------------------------------------------------------
# DEF-019b — a transient netstat PID 0 must never settle ownership and must
# never authorize terminating the just-spawned healthy child
# ---------------------------------------------------------------------------
def test_launch_tolerates_transient_pid_zero_listener(tmp_path, monkeypatch):
    """The first ownership probe reports a transient PID 0 (netstat row). The
    launch must NOT settle on it (which would have killed the spawned dummy and
    raised PortConflictError); it must keep polling and succeed once the real
    non-zero listener pid appears with a matching identity."""
    port = free_port()
    meta_path = tmp_path / "meta.json"
    real_probe = probe_ownership
    state = {"calls": 0}

    def probing_probe(port_, expected_):
        state["calls"] += 1
        if state["calls"] == 1:
            # the transient row: listener pid reported as 0 ("identity unreadable")
            return (False, 0, None, ["listener identity unreadable"])
        return real_probe(port_, expected_)

    monkeypatch.setattr("tools.process_guard.probe_ownership", probing_probe)

    code = main([
        "launch", "--cmd", sys.executable,
        "--meta", str(meta_path),
        "--port", str(port),
        "--args", str(FIXTURE), "bind", "--port", str(port), "--seconds", "30",
    ])
    assert code == 0, "a transient PID 0 must not break the launch"
    metadata = load_metadata(str(meta_path))
    assert metadata is not None
    assert metadata.status == "VERIFIED"
    register_pid(metadata.pid)
    assert process_exists(metadata.pid), "the spawned dummy must still be alive"

    stop = main(["stop", "--meta", str(meta_path)])
    assert stop == 0, "the tracked dummy must stop cleanly"
    assert wait_until(lambda: not process_exists(metadata.pid))