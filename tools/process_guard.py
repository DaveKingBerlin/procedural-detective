"""RAD process-lifecycle safety guard (REQUIREMENTS 65A.1/65A.2/65A.3/65A.4, 65B).

Stdlib-only.  On Windows, ctypes is used (psutil is NOT available).
Safety contract: never kill by PID alone; verify identity before termination;
retain metadata on any uncertainty; never claim or terminate foreign processes.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as _datetime
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple

ENV_VAR = "PROCEDURAL_DETECTIVE_RUN_ID"

_WIN = sys.platform == "win32"

_POLL_INTERVAL = 0.1


@dataclass
class ProcessIdentity:
    """Identity snapshot of one process, used for multi-attribute verification (65A.1).

    `create_time` is a platform-normalized epoch-seconds value that is stable
    across snapshots of the same process lifetime on the same machine.
    `token` is an optional unique-run-id supplied through ProcessIdentity
    construction only (see `launch`); live snapshots cannot read another
    process's environment, so the token is compared only when both sides carry
    one (best effort).
    """

    pid: int
    create_time: float
    executable: Optional[str] = None
    cmdline: Optional[Tuple[str, ...]] = None
    parent_pid: Optional[int] = None
    token: Optional[str] = None


_VALID_STATUSES = ("STARTED", "VERIFIED", "SHUTDOWN_OK", "CLEANUP_INCOMPLETE", "UNVERIFIED",
                   "NOT_RUNNING", "NOT_RUNNING_UNVERIFIED", "UNREADABLE_METADATA")


@dataclass
class ProcessMetadata:
    """Persisted runtime tracking metadata for one launched process."""

    run_id: str = ""
    pid: int = -1
    create_time: float = 0.0
    executable: Optional[str] = None
    cmdline: Optional[List[str]] = None
    parent_pid: Optional[int] = None
    port: Optional[int] = None
    launched_at: Optional[str] = None
    status: str = "STARTED"
    terminate_error: Optional[str] = None
    surviving_descendants: List[int] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "ProcessMetadata":
        if not isinstance(data, dict):
            raise ValueError("metadata payload must be a JSON object")

        def _val(key, default):
            value = data.get(key, default)
            return default if value is None else value

        cmdline = _val("cmdline", None)
        if not isinstance(cmdline, (list, tuple)):
            cmdline = None
        cmdline = list(cmdline) if cmdline is not None else None
        survivors = _val("surviving_descendants", [])
        survivors = [int(s) for s in survivors] if isinstance(survivors, (list, tuple)) else []
        notes = _val("notes", [])
        notes = [str(n) for n in notes] if isinstance(notes, (list, tuple)) else []
        port = _val("port", None)
        if port is not None:
            port = int(port)
        return cls(
            run_id=str(_val("run_id", "")),
            pid=int(_val("pid", -1)),
            create_time=float(_val("create_time", 0.0)),
            executable=_val("executable", None),
            cmdline=cmdline,
            parent_pid=_val("parent_pid", None),
            port=port,
            launched_at=_val("launched_at", None),
            status=str(_val("status", "UNVERIFIED")),
            terminate_error=_val("terminate_error", None),
            surviving_descendants=survivors,
            notes=notes,
        )

    @staticmethod
    def write(metadata, path, now_fn=time.time) -> None:
        """Atomic JSON write: temp file in the same directory, then os.replace."""
        data = metadata.to_json()
        data["written_at"] = _iso_timestamp(now_fn())
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)

    @staticmethod
    def load(path) -> Optional["ProcessMetadata"]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        try:
            return ProcessMetadata.from_json(data)
        except (TypeError, ValueError, KeyError):
            return None


# ---------------------------------------------------------------------------
# Windows ctypes building blocks (loaded only on win32).
# ---------------------------------------------------------------------------
if _WIN:
    import ctypes
    from ctypes import wintypes

    _PROCESS_QUERY_INFORMATION = 0x0400  # PROCESS_QUERY_INFORMATION
    _STILL_ACTIVE = 259

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.CloseHandle.restype = None
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.GetExitCodeProcess.restype = wintypes.DWORD
    _kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]

    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    _kernel32.GetProcessTimes.restype = wintypes.BOOL
    _kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
    ]

    # GetModuleFileNameW cannot resolve an arbitrary process handle on modern
    # Windows (returns ERROR_MODULE_NOT_FOUND); GetModuleFileNameExW (psapi)
    # takes the process handle directly and needs only PROCESS_QUERY_INFORMATION.
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)
    _psapi.GetModuleFileNameExW.restype = wintypes.DWORD
    _psapi.GetModuleFileNameExW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, wintypes.DWORD,
    ]

    _ntdll = ctypes.WinDLL("ntdll", use_last_error=True)

    class _PROCESS_BASIC_INFO(ctypes.Structure):
        # Well-known PROCESS_BASIC_INFORMATION layout for NT (64-bit):
        # ExitStatus, pad, PebBaseAddress, AffinityMask, BasePriority,
        # UniqueProcessId, ParentProcessId.
        _fields_ = [
            ("exit_status", ctypes.c_ulong),
            ("_pad", ctypes.c_ulong),
            ("peb_base_address", ctypes.c_size_t),
            ("affinity_mask", ctypes.c_size_t),
            ("base_priority", ctypes.c_size_t),
            ("unique_process_id", ctypes.c_size_t),
            ("parent_process_id", ctypes.c_size_t),
        ]

    _ntdll.NtQueryInformationProcess.restype = ctypes.c_long
    _ntdll.NtQueryInformationProcess.argtypes = [
        wintypes.HANDLE, ctypes.c_uint, ctypes.c_void_p, ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
    ]

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("dwInternalFlags", wintypes.DWORD),
            ("dwProcessId", wintypes.DWORD),
            ("dwParentProcessId", wintypes.DWORD),
            ("dwThreadCount", wintypes.DWORD),
            ("dwPriority", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),  # MAX_PATH
            ("dwReserved", wintypes.DWORD * 5),
        ]

    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.Process32FirstW.restype = wintypes.BOOL
    _kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    _kernel32.Process32NextW.restype = wintypes.BOOL
    _kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _iso_timestamp(ts: Optional[float] = None) -> str:
    ts = time.time() if ts is None else ts
    return _datetime.datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def generate_run_id() -> str:
    return "pdr-" + uuid.uuid4().hex


def canonical_executable(path: str) -> str:
    """Absolute real path; case-folded on Windows so paths compare case-insensitively."""
    if not path:
        return path
    real = os.path.abspath(os.path.realpath(str(path)))
    if _WIN:
        real = real.casefold()
    return real


def _valid_port(port) -> bool:
    """True only for a real TCP port; rejects 0 and anything above 65535 (ADV-004)."""
    return isinstance(port, int) and 1 <= port <= 65535


# ---------------------------------------------------------------------------
# Process snapshotting
# ---------------------------------------------------------------------------
def snapshot_process(pid: int) -> Optional[ProcessIdentity]:
    """Return the identity of a live process, or None if it is absent or unreadable.

    Never raises for a missing/permission-denied process.  Creation timestamp is
    mandatory; a snapshot without one is discarded.
    """
    if _WIN:
        return _snapshot_process_windows(pid)
    return _snapshot_process_posix(pid)


if _WIN:

    def _snapshot_process_windows(pid: int) -> Optional[ProcessIdentity]:
        try:
            handle = _kernel32.OpenProcess(_PROCESS_QUERY_INFORMATION, False, int(pid))
        except (OSError, ValueError):
            return None
        if not handle:
            return None
        try:
            create_time = _win32_create_time(handle)
            executable = _win32_executable_path(handle)
            parent_pid = _win32_parent_pid(handle, int(pid))
        finally:
            try:
                _kernel32.CloseHandle(handle)
            except Exception:
                pass
        if create_time is None:
            return None
        # cmdline is intentionally None: reading another process's memory safely
        # is out of scope for a stdlib-only guard; NULL cmdline never blocks
        # identity verification (65A.1 identity tuple is pid+create_time+exe+parent).
        return ProcessIdentity(
            pid=int(pid),
            create_time=create_time,
            executable=executable,
            cmdline=None,
            parent_pid=parent_pid,
        )

    def _win32_create_time(handle) -> Optional[float]:
        try:
            create = _FILETIME()
            _dummy = _FILETIME()
            ok = _kernel32.GetProcessTimes(
                handle, ctypes.byref(create), ctypes.byref(_dummy),
                ctypes.byref(_dummy), ctypes.byref(_dummy),
            )
            if not ok:
                return None
            lo = create.dwLowDateTime
            hi = create.dwHighDateTime
            # FILETIME = 100ns intervals since 1601-01-01 -> UNIX epoch.
            return ((hi << 32 | lo) / 1e7) - 11644473600.0
        except Exception:
            return None

    def _win32_executable_path(handle) -> Optional[str]:
        try:
            buffer = ctypes.create_unicode_buffer(16384)
            n = _psapi.GetModuleFileNameExW(handle, 0, buffer, len(buffer))
            if not n:
                return None
            return canonical_executable(buffer[: n])
        except Exception:
            return None

    def _win32_parent_pid(handle, pid: int) -> Optional[int]:
        try:
            info = _PROCESS_BASIC_INFO()
            status = _ntdll.NtQueryInformationProcess(
                handle, 0, ctypes.byref(info), ctypes.sizeof(info),
                ctypes.byref(ctypes.c_ulong(0)),
            )
            if status != 0:
                return None
            ppid = int(info.parent_process_id)
            return None if ppid <= 0 or ppid == pid else ppid
        except Exception:
            return None


def _snapshot_process_posix(pid: int) -> Optional[ProcessIdentity]:
    try:
        with open(f"/proc/{int(pid)}/stat", "r", encoding="ascii", errors="ignore") as f:
            stat = f.read()
    except (OSError, ValueError):
        return None
    try:
        body = stat.rsplit(")", 1)[1].split()  # comm may contain ')' and spaces
        ppid = int(body[1]) if len(body) > 1 else None  # field 4
        start_ticks = int(body[19]) if len(body) > 19 else None  # field 22
    except (IndexError, ValueError):
        return None
    if start_ticks is None:
        return None
    return ProcessIdentity(
        pid=int(pid),
        create_time=_boot_time() + start_ticks / _clk_tck(),
        executable=_posix_exe(pid),
        cmdline=_posix_cmdline(pid),
        parent_pid=ppid,
    )


def _posix_exe(pid: int) -> Optional[str]:
    try:
        return canonical_executable(os.readlink(f"/proc/{pid}/exe"))
    except OSError:
        return None


def _posix_cmdline(pid: int) -> Optional[Tuple[str, ...]]:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            data = f.read()
    except OSError:
        return None
    parts = [p.decode("utf-8", "replace") for p in data.split(b"\x00") if p]
    return tuple(parts) if parts else None


_boot_time_cache = None


def _boot_time() -> float:
    global _boot_time_cache
    if _boot_time_cache is not None:
        return _boot_time_cache
    try:
        with open("/proc/stat", "r", encoding="ascii", errors="ignore") as f:
            for line in f:
                if line.startswith("btime"):
                    _boot_time_cache = float(line.split()[1])
                    return _boot_time_cache
    except (OSError, ValueError, IndexError):
        pass
    _boot_time_cache = time.time()
    return _boot_time_cache


_clk_tck_cache = None


def _clk_tck() -> float:
    global _clk_tck_cache
    if _clk_tck_cache is None:
        try:
            _clk_tck_cache = float(os.sysconf("SC_CLK_TCK"))
        except (OSError, ValueError, AttributeError):
            _clk_tck_cache = 100.0
    return _clk_tck_cache


# ---------------------------------------------------------------------------
# Existence / identity
# ---------------------------------------------------------------------------
def process_exists(pid: int) -> bool:
    """True when *pid* refers to a live process on this machine.

    Windows uses GetExitCodeProcess (STILL_ACTIVE) instead of os.kill(pid, 0),
    because a terminated-but-unreaped Windows process still answers os.kill(pid, 0).
    POSIX uses os.kill(pid, 0) and treats /proc zombies as not existing.
    """
    if _WIN:
        return _win32_process_exists(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # e.g. PermissionError: likely exists; never kill blindly
    return _posix_not_zombie(pid)


if _WIN:

    def _win32_process_exists(pid: int) -> bool:
        try:
            handle = _kernel32.OpenProcess(_PROCESS_QUERY_INFORMATION, False, int(pid))
        except (OSError, ValueError):
            return False
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            ok = _kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == _STILL_ACTIVE
        except Exception:
            return True  # unknown state -> assume present (identity still required)
        finally:
            try:
                _kernel32.CloseHandle(handle)
            except Exception:
                pass


def _posix_not_zombie(pid: int) -> bool:
    try:
        with open(f"/proc/{int(pid)}/stat", "r", encoding="ascii", errors="ignore") as f:
            body = f.read().rsplit(")", 1)[1]
        state = body.split()[0] if body.strip() else ""
        return state != "Z"
    except (OSError, ValueError, IndexError):
        return True  # no /proc (e.g. macOS): trust os.kill(pid, 0)


def identity_matches(
    expected: ProcessIdentity,
    actual: ProcessIdentity,
    *,
    create_time_tolerance: float = 5.0,
    require_parent: bool = True,
) -> Tuple[bool, List[str]]:
    """Compare a recorded identity against a live snapshot (65A.1).

    Mismatch of any verified attribute means the PID was reused or belongs to a
    foreign process and MUST NOT be terminated.  Unknown attributes (None on
    either side) are never treated as a mismatch; the run token is compared only
    when both sides carry one.
    """
    reasons: List[str] = []

    if expected.pid != actual.pid:
        reasons.append(f"pid mismatch: recorded {expected.pid}, live {actual.pid}")

    if expected.create_time is not None and actual.create_time is not None:
        if abs(expected.create_time - actual.create_time) > create_time_tolerance:
            reasons.append(
                f"create time mismatch: recorded {expected.create_time}, "
                f"live {actual.create_time} (tolerance {create_time_tolerance}s)"
            )

    if expected.executable is not None and actual.executable is not None:
        if canonical_executable(expected.executable) != canonical_executable(actual.executable):
            reasons.append(f"executable mismatch: recorded {expected.executable}, live {actual.executable}")

    if require_parent and expected.parent_pid is not None and actual.parent_pid is not None:
        if expected.parent_pid != actual.parent_pid:
            reasons.append(f"parent pid mismatch: recorded {expected.parent_pid}, live {actual.parent_pid}")

    if expected.token is not None and actual.token is not None:
        if expected.token != actual.token:
            reasons.append(f"run token mismatch: recorded {expected.token}, live {actual.token}")

    return (not reasons, reasons)


def _authorized_for_kill(
    expected: ProcessIdentity,
    actual: ProcessIdentity,
    *,
    create_time_tolerance: float = 5.0,
) -> Tuple[bool, List[str]]:
    """Fail-closed kill authorization (ADV-003, 65A.1).

    Termination is authorized ONLY when ALL of the following hold:

      (a) pid is equal;
      (b) create_time is KNOWN on both sides and within the tolerance window;
      (c) at least ONE additional independent attribute is CONFIRMED equal among
          {executable, parent_pid, token} — parent and token only when both sides
          carry a value, executable compared via canonical_executable.

    Unknown/None attributes never count as a match: a live Windows snapshot can
    legitimately have executable AND parent both None, so pid + one fuzzy
    create-time value alone must never authorize a kill. This is the KILL gate;
    `identity_matches` remains the exact-attribute comparison primitive.
    """
    reasons: List[str] = []

    if expected.pid != actual.pid:
        reasons.append(f"pid mismatch: recorded {expected.pid}, live {actual.pid}")
        return (False, reasons)

    if expected.create_time is None or actual.create_time is None:
        reasons.append(
            "create time unknown on one side; cannot authorize termination (65A.1)"
        )
        return (False, reasons)
    if abs(expected.create_time - actual.create_time) > create_time_tolerance:
        reasons.append(
            f"create time mismatch: recorded {expected.create_time}, "
            f"live {actual.create_time} (tolerance {create_time_tolerance}s)"
        )
        return (False, reasons)

    confirmed: List[str] = []
    if expected.executable is not None and actual.executable is not None:
        if canonical_executable(expected.executable) == canonical_executable(actual.executable):
            confirmed.append("executable")
        else:
            reasons.append(
                f"executable mismatch: recorded {expected.executable}, live {actual.executable}")
            return (False, reasons)
    if expected.parent_pid is not None and actual.parent_pid is not None:
        if expected.parent_pid == actual.parent_pid:
            confirmed.append("parent_pid")
        else:
            reasons.append(
                f"parent pid mismatch: recorded {expected.parent_pid}, live {actual.parent_pid}")
            return (False, reasons)
    if expected.token is not None and actual.token is not None:
        if expected.token == actual.token:
            confirmed.append("token")
        else:
            reasons.append(f"run token mismatch: recorded {expected.token}, live {actual.token}")
            return (False, reasons)

    if not confirmed:
        reasons.append(
            "no additional identity attribute confirmed (executable/parent_pid/run "
            "token); pid + fuzzy create-time alone cannot authorize termination (65A.1)"
        )
        return (False, reasons)
    return (True, reasons)


# ---------------------------------------------------------------------------
# Metadata persistence
# ---------------------------------------------------------------------------
def record_metadata(metadata: ProcessMetadata, path: str) -> None:
    """Atomically persist *metadata*.

    Safety contract (65A.2): this file must be written ONLY after ownership of
    the running process was verified (snapshot identity matches the spawned
    pid, and the port was free/verified).  The `launch` CLI implements that
    ordering; other callers must too.
    """
    ProcessMetadata.write(metadata, path)


def load_metadata(path: str) -> Optional[ProcessMetadata]:
    """Load metadata; None when missing or corrupt (a corrupt file is left on disk)."""
    return ProcessMetadata.load(path)


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------
class PortConflictError(Exception):
    """Raised when a port that must be reserved is already occupied (65A.2)."""

    def __init__(self, port: int, message: str = None):
        self.port = port
        super().__init__(message or f"port {port} is already in use by another process")


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """True when *port* cannot be bound on *host*. Never touches the occupier."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, int(port)))
        return False
    except OSError:
        return True
    finally:
        s.close()


def verify_port_available(port: int) -> None:
    """Pre-launch check (65A.2): raise PortConflictError when the port is taken."""
    if port_in_use(port):
        raise PortConflictError(port)


def find_listener_pid(port: int) -> Optional[int]:
    """Best-effort listener lookup. Never raises; None on any failure.

    Windows parses `netstat -ano` local-address tokens (locale-independent: the
    status word, e.g. LISTENING/ABHOEREN, is intentionally not matched).
    """
    if _WIN:
        return _find_listener_windows(port)
    return _find_listener_posix(port)


def _find_listener_windows(port: int) -> Optional[int]:
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True,
            errors="replace", check=False, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    local_address = re.compile(r":%d(?!\d)$" % port)
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        if not parts[0].upper().startswith("TCP"):
            continue
        if local_address.search(parts[1]):  # local address token
            last = parts[-1]
            if last.isdigit():
                return int(last)
    return None


def _find_listener_posix(port: int) -> Optional[int]:
    pattern = re.compile(r":%d(?!\d)" % port)
    try:
        ss = subprocess.run(
            ["ss", "-ltnp"], capture_output=True, text=True,
            errors="replace", check=False, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        ss = None
    if ss is not None:
        for line in ss.stdout.splitlines():
            if pattern.search(line):
                m = re.search(r"pid=(\d+)", line)
                if m:
                    return int(m.group(1))
    try:
        lsof = subprocess.run(
            ["lsof", "-iTCP:%d" % port, "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, check=False, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in lsof.stdout.splitlines():
        if line.strip().isdigit():
            return int(line.strip())
    return None


def probe_ownership(
    port: int, expected: ProcessIdentity
) -> Tuple[bool, Optional[int], Optional[ProcessIdentity], List[str]]:
    """Verify that the process listening on *port* matches *expected* (65A.2).

    Returns (owned, listener_pid, listener_identity_if_readable, reasons).
    A foreign listener is never claimed as owned nor terminated.
    """
    listener_pid = find_listener_pid(port)
    if listener_pid is None:
        return (False, None, None, ["no listener"])
    snapshot = snapshot_process(listener_pid)
    if snapshot is None:
        return (False, listener_pid, None, ["listener identity unreadable"])
    owned, reasons = identity_matches(expected, snapshot)
    return (owned, listener_pid, snapshot, list(reasons))


# ---------------------------------------------------------------------------
# Process enumeration / descendants
# ---------------------------------------------------------------------------
def iter_processes() -> Iterator[dict]:
    """Yield {"pid","ppid","name"} for every process. Exception-safe: empty on failure.

    Windows prefers ToolHelp (CreateToolhelp32Snapshot); when the environment
    hides it (e.g. sandboxed hosts return an empty snapshot), falls back to
    `wmic process get ... /format:csv`.
    """
    if _WIN:
        seen = 0
        for entry in _iter_toolhelp():
            seen += 1
            yield entry
        if seen == 0:
            yield from _iter_wmic()
        return
    yield from _iter_proc_posix()


if _WIN:

    def _iter_toolhelp() -> Iterator[dict]:
        snapshot = _kernel32.CreateToolhelp32Snapshot(0x8, 0)  # TH32CS_SNAPPROCESS
        if not snapshot:
            return
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            ok = _kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                yield {
                    "pid": int(entry.dwProcessId),
                    "ppid": int(entry.dwParentProcessId),
                    "name": entry.szExeFile,
                }
                ok = _kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            try:
                _kernel32.CloseHandle(snapshot)
            except Exception:
                pass

    def _wmic_column_index(header, needle=None, skip_contains=None, exact=None, fallback=None):
        if exact is not None:
            for i, h in enumerate(header):
                if h == exact:
                    return i
        if needle is not None:
            for i, h in enumerate(header):
                # tolerate abbreviated/localized spellings (e.g. Prozess -> process)
                hh = h.replace("_", "").replace("prozess", "process")
                if needle in hh and (skip_contains is None or skip_contains not in hh):
                    return i
            for i, h in enumerate(header):
                hh = h.replace("_", "").replace("prozess", "process")
                if "pid" in hh and (skip_contains is None or skip_contains not in hh):
                    return i
        return fallback

    def _iter_wmic() -> Iterator[dict]:
        try:
            result = subprocess.run(
                ["wmic", "process", "get", "ProcessId,ParentProcessId,Name", "/format:csv"],
                capture_output=True, text=True, errors="replace", check=False, timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            return
        import csv as _csv

        def _clean(cell: str) -> str:
            # wmic quotes every field with single quotes once a Name column is
            # requested (`'8172'`); some locales add a BOM on the first cell.
            return cell.strip(" '\"\ufeff").strip()

        header = None
        i_ppid = 1
        i_pid = 2
        i_name = 3
        for row in _csv.reader(result.stdout.splitlines()):
            if not row:
                continue
            cells = [_clean(c) for c in row]
            if header is None:
                header = [c.lower() for c in cells]
                ncols = max(len(header), 4)
                i_ppid = _wmic_column_index(header, "parent", fallback=ncols - 2)
                i_pid = _wmic_column_index(
                    header, "processid", skip_contains="parent", fallback=ncols - 1
                )
                i_name = _wmic_column_index(header, exact="name", fallback=1)
                continue
            try:
                pid = int(cells[i_pid])
            except (ValueError, IndexError):
                continue
            try:
                ppid = int(cells[i_ppid])
            except (ValueError, IndexError):
                ppid = None
            name = cells[i_name] if i_name < len(cells) else None
            yield {"pid": pid, "ppid": ppid, "name": name}


def _iter_proc_posix() -> Iterator[dict]:
    import glob as _glob

    try:
        paths = sorted(_glob.glob("/proc/[0-9]*/stat"))
    except (OSError, ValueError):
        return
    for path in paths:
        try:
            with open(path, "r", encoding="ascii", errors="ignore") as f:
                stat = f.read()
            pid = int(path.rsplit("/", 2)[1])
            body = stat.rsplit(")", 1)[1].split()
            ppid = int(body[1])
            comm = stat[stat.find("(") + 1: stat.rfind(")")]
            yield {"pid": pid, "ppid": ppid, "name": comm}
        except (OSError, ValueError, IndexError):
            continue


def collect_descendants(root_pid: int) -> List[int]:
    """BFS over ppid chains. Never includes *root_pid*. Exception-safe -> [].

    Used to detect surviving children of a tracked process (65A.4).
    """
    try:
        children = {}
        for proc in iter_processes():
            ppid = proc.get("ppid")
            if ppid is None:
                continue
            children.setdefault(ppid, []).append(proc.get("pid"))
    except Exception:
        return []
    found: List[int] = []
    stack = list(children.get(root_pid, []))
    while stack:
        pid = stack.pop()
        if pid == root_pid or pid in found:
            continue
        found.append(pid)
        stack.extend(children.get(pid, []))
    return sorted(found)


# ---------------------------------------------------------------------------
# Termination
# ---------------------------------------------------------------------------
def _force_kill(pid: int) -> None:
    if _WIN:
        # SIGTERM IS TerminateProcess on Windows; documented (65A.1) that this
        # is only reached after the caller proved identity.
        os.kill(pid, signal.SIGTERM)
    else:
        os.kill(pid, signal.SIGKILL)


def _wait_gone(pid: int, timeout: float, sleep_fn=time.sleep, wait_fn=None) -> bool:
    if wait_fn is not None:
        return bool(wait_fn(pid, timeout))
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if not process_exists(pid):
            return True
        sleep_fn(_POLL_INTERVAL)
    return not process_exists(pid)


def terminate_process(
    pid: int,
    *,
    force_after_seconds: float = 5.0,
    terminate_fn=None,
    wait_fn=None,
    sleep_fn=time.sleep,
) -> Tuple[bool, Optional[str]]:
    """Terminate *pid*, returning (success, error_message).

    Safety contract (65A.1): callers MUST prove identity before calling — a PID
    alone is never sufficient.  A missing process is (True, None).  OSError with
    a surviving process is (False, str(e)).
    `terminate_fn(pid)` and `wait_fn(pid, timeout)->bool` are injectable seams
    for deterministic tests.
    """
    if not process_exists(pid):
        return (True, None)
    try:
        if terminate_fn is not None:
            terminate_fn(pid)
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        if process_exists(pid):
            return (False, str(exc))
        return (True, None)
    if not _wait_gone(pid, force_after_seconds, sleep_fn, wait_fn):
        try:
            _force_kill(pid)
        except OSError as exc:
            if process_exists(pid):
                return (False, str(exc))
            return (True, None)
        _wait_gone(pid, force_after_seconds, sleep_fn, wait_fn)
    if process_exists(pid):
        return (False, f"process {pid} still alive after termination")
    return (True, None)


def _terminate_spawned_tree(proc, snap) -> Tuple[List[int], Optional[str]]:
    """Terminate the process tree this CLI spawned after a failed launch (DEF-019).

    Ownership is established at spawn: ``proc`` is a Popen handle created in
    this very process, so its pid is the direct child we created (unambiguous).
    The direct child is only killed when its create time still matches the
    snapshot taken right after spawn — a mismatch means the PID was reused by a
    foreign process and NOTHING is touched (fail-closed, 65A.1). Descendants
    are equally ours: they descend (ppid chain) from the verified root we
    spawned, typically the real listener behind a .cmd/npm wrapper.

    Returns (surviving_pids, error_message). A launch must never return an
    error while its own spawned child is left running un-tracked.
    """
    descendants = collect_descendants(proc.pid)  # snapshot the tree while the root is alive
    survivors: List[int] = []
    error: Optional[str] = None

    current = snapshot_process(proc.pid)
    if current is not None and snap is not None:
        if abs(current.create_time - snap.create_time) > 5.0:
            survivors.append(int(proc.pid))
            error = (f"direct child pid {proc.pid} was reused by another process; "
                     f"refusing to terminate (65A.1)")
            # The root is foreign now, so its "descendants" are not ours either.
            return survivors, error

    if process_exists(proc.pid):
        ok, err = terminate_process(int(proc.pid))
        if not ok:
            survivors.append(int(proc.pid))
            error = err

    try:
        for _ in range(5):
            sweep = [p for p in set(descendants)
                     | set(collect_descendants(int(proc.pid)))
                     if process_exists(p)]
            if not sweep:
                break
            for pid in sorted(sweep):
                if not process_exists(pid):
                    continue
                ok, err = terminate_process(pid)
                if not ok:
                    survivors.append(pid)
                    error = error or err
            time.sleep(0.2)
    except Exception as exc:  # never let cleanup itself crash a launch failure
        error = error or str(exc)

    return sorted(set(survivors)), error


def _abort_launch(proc, snap, message) -> int:
    """Fail a launch after a successful Popen, terminating the spawned tree."""
    survivors, error = _terminate_spawned_tree(proc, snap)
    print(message, file=sys.stderr)
    if survivors:
        print(
            "error: cleanup incomplete; surviving process(es): %s%s"
            % (", ".join(map(str, survivors)), f" ({error})" if error else ""),
            file=sys.stderr,
        )
    return 1


# ---------------------------------------------------------------------------
# Safe shutdown (core algorithm: 65A.1 + 65A.4)
# ---------------------------------------------------------------------------
def _persist_metadata(
    metadata: ProcessMetadata,
    path: str,
    *,
    status: str,
    terminate_error: Optional[str] = None,
    surviving_descendants: Optional[List[int]] = None,
    extra_notes: Tuple[str, ...] = (),
    now_fn=time.time,
) -> None:
    updated = dataclasses.replace(
        metadata,
        status=status,
        terminate_error=terminate_error if terminate_error is not None else metadata.terminate_error,
        surviving_descendants=(list(surviving_descendants) if surviving_descendants is not None
                               else list(metadata.surviving_descendants)),
        notes=list(metadata.notes) + list(extra_notes),
    )
    try:
        ProcessMetadata.write(updated, path, now_fn=now_fn)
    except OSError:
        pass


def _erase_metadata(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _wait_port_free(port: int, sleep_fn, timeout: float) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if not port_in_use(port):
            return True
        sleep_fn(0.2)
    return not port_in_use(port)


def shutdown_process(
    metadata_path: str,
    *,
    grace_seconds: float = 10.0,
    terminate_fn=None,
    wait_fn=None,
    snapshot_fn=None,
    sleep_fn=time.sleep,
    now_fn=time.time,
) -> dict:
    """Safely stop the process described by *metadata_path* (65A.1/65A.4).

    Ordering guarantees:
      - identity verification ALWAYS precedes any kill;
      - on ANY uncertainty the metadata file remains on disk;
      - a foreign/mismatched PID is never terminated;
      - metadata is erased only after verified, descendant-free, port-released
        cleanup.
    Returns {"status", "reasons", "metadata_retained", "surviving_descendants"}.
    """
    reasons: List[str] = []
    if not os.path.exists(metadata_path):
        return {
            "status": "NOT_RUNNING",
            "reasons": [f"no process metadata at {metadata_path}; nothing to shut down"],
            "metadata_retained": False,
            "surviving_descendants": [],
        }
    metadata = load_metadata(metadata_path)
    if metadata is None:
        # ADV-006: present-but-unreadable metadata is distinct from missing. The
        # file may still be the only trail to a live tracked process, so it must
        # be reported as RETAINED, never as discarded. Nothing is killed.
        return {
            "status": "UNREADABLE_METADATA",
            "reasons": [
                f"process metadata at {metadata_path} exists but cannot be read; "
                f"refusing to act and retaining the file (65A.4)"
            ],
            "metadata_retained": True,
            "surviving_descendants": [],
        }

    expected = ProcessIdentity(
        pid=metadata.pid,
        create_time=metadata.create_time,
        executable=metadata.executable,
        cmdline=None,
        parent_pid=metadata.parent_pid,
        token=metadata.run_id or None,
    )

    snap = (snapshot_fn or snapshot_process)(expected.pid)
    if snap is None:
        # Recorded pid is gone.  Only a live tracked descendant blocks completion.
        survivors = sorted(d for d in metadata.surviving_descendants if process_exists(d))
        if survivors:
            reasons.append(
                f"recorded process {expected.pid} is not running but tracked "
                f"descendant(s) {survivors} may still be alive (65A.4)"
            )
            _persist_metadata(metadata, metadata_path, status="CLEANUP_INCOMPLETE",
                              surviving_descendants=survivors, extra_notes=tuple(reasons), now_fn=now_fn)
            return {
                "status": "NOT_RUNNING_UNVERIFIED",
                "reasons": reasons,
                "metadata_retained": True,
                "surviving_descendants": survivors,
            }
        reasons.append(
            f"recorded process {expected.pid} is not running and no tracked descendant survives"
        )
        _erase_metadata(metadata_path)
        return {
            "status": "SHUTDOWN_OK",
            "reasons": reasons,
            "metadata_retained": False,
            "surviving_descendants": [],
        }

    ok, mismatches = _authorized_for_kill(expected, snap)
    if not ok:
        # ADV-003: the KILL authority is the fail-closed gate (pid + known
        # create-time within tolerance + >=1 confirmed extra attribute), not the
        # tolerant `identity_matches` primitive.
        reasons = [f"identity verification failed; refusing to terminate (65A.1): {m}" for m in mismatches]
        _persist_metadata(metadata, metadata_path, status="UNVERIFIED",
                          extra_notes=tuple(reasons), now_fn=now_fn)
        return {
            "status": "UNVERIFIED",
            "reasons": reasons,
            "metadata_retained": True,
            "surviving_descendants": [],
        }

    live_descendants = collect_descendants(expected.pid)
    tracked_descendants = list(metadata.surviving_descendants)
    descendants = sorted(set(live_descendants) | set(tracked_descendants))

    terminated_ok, error = terminate_process(
        expected.pid,
        force_after_seconds=grace_seconds,
        terminate_fn=terminate_fn,
        wait_fn=wait_fn,
        sleep_fn=sleep_fn,
    )

    # Identity was proven: the root must never be left alive when the DEFAULT
    # termination path left it running — make one last forced-kill attempt before
    # concluding failure.  An injected terminate_fn fully owns termination
    # behavior (deterministic tests), so its outcome is respected as-is.
    if process_exists(expected.pid) and terminate_fn is None:
        try:
            _force_kill(expected.pid)
        except OSError as exc:
            error = error or str(exc)
        _wait_gone(expected.pid, min(3.0, grace_seconds), sleep_fn)

    survivors = sorted(d for d in descendants if process_exists(d))
    for d in collect_descendants(expected.pid):
        if d not in survivors:
            survivors.append(d)
    survivors = sorted(survivors)

    if not terminated_ok or process_exists(expected.pid):
        reasons.append(f"termination failed: {error or 'process still running'}")
        _persist_metadata(metadata, metadata_path, status="CLEANUP_INCOMPLETE",
                          terminate_error=error, surviving_descendants=survivors,
                          extra_notes=tuple(reasons), now_fn=now_fn)
        return {
            "status": "CLEANUP_INCOMPLETE",
            "reasons": reasons,
            "metadata_retained": True,
            "surviving_descendants": survivors,
        }

    if survivors:
        reasons.append(
            f"surviving descendant(s) {survivors} still alive after shutdown (65A.4)"
        )
        _persist_metadata(metadata, metadata_path, status="CLEANUP_INCOMPLETE",
                          surviving_descendants=survivors, extra_notes=tuple(reasons), now_fn=now_fn)
        return {
            "status": "CLEANUP_INCOMPLETE",
            "reasons": reasons,
            "metadata_retained": True,
            "surviving_descendants": survivors,
        }

    if metadata.port is not None:
        if _wait_port_free(metadata.port, sleep_fn, 3.0):
            reasons.append(f"port {metadata.port} released")
        else:
            reasons.append(f"port {metadata.port} still in use after shutdown")
            _persist_metadata(metadata, metadata_path, status="CLEANUP_INCOMPLETE",
                              extra_notes=tuple(reasons), now_fn=now_fn)
            return {
                "status": "CLEANUP_INCOMPLETE",
                "reasons": reasons,
                "metadata_retained": True,
                "surviving_descendants": [],
            }

    reasons.append(
        f"shutdown verified: process {expected.pid} terminated, no surviving descendants"
    )
    _erase_metadata(metadata_path)
    return {
        "status": "SHUTDOWN_OK",
        "reasons": reasons,
        "metadata_retained": False,
        "surviving_descendants": [],
    }


# ---------------------------------------------------------------------------
# CLI (thin; all safety logic lives in the functions above)
# ---------------------------------------------------------------------------
def _wait_for(predicate, timeout: float, interval: float = 0.2):
    deadline = time.monotonic() + max(0.0, timeout)
    value = None
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


def _cmd_launch(args) -> int:
    if args.port is not None and not _valid_port(args.port):
        print(f"error: invalid port {args.port} (must be 1-65535)", file=sys.stderr)
        return 2
    run_id = generate_run_id()
    if args.port is not None:
        verify_port_available(args.port)  # 65A.2 pre-launch check
    env = os.environ.copy()
    env[ENV_VAR] = run_id  # 65A.3
    # DEVNULL: the spawned server must never hold the caller's stdout/stderr
    # pipes open (that would hang `launch | ...` until the child exits).
    log_handle = None
    stdout_target = subprocess.DEVNULL
    stderr_target = subprocess.DEVNULL
    try:
        log_file = getattr(args, "log_file", None)
        if log_file:
            log_path = os.path.abspath(log_file)
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            log_handle = open(log_path, "a", encoding="utf-8", buffering=1)
            stdout_target = log_handle
            stderr_target = log_handle
        proc = subprocess.Popen(
            [args.cmd] + list(args.args), env=env,
            stdout=stdout_target, stderr=stderr_target,
        )
    except OSError as exc:  # FileNotFoundError and friends (DEF-002)
        if log_handle is not None:
            log_handle.close()
        print(f"error: cannot launch {args.cmd} ({exc})", file=sys.stderr)
        return 2
    finally:
        if log_handle is not None:
            log_handle.close()

    def _snapped():
        return snapshot_process(proc.pid)

    snap = _wait_for(_snapped, args.wait)
    if snap is None:
        return _abort_launch(
            proc, None, f"error: could not snapshot launched process {proc.pid}"
        )
    if snap.executable is None and snap.token is None:
        # ADV-003: without a readable executable (and no token in the live
        # snapshot) no multi-attribute identity can ever be proven at stop time;
        # writing VERIFIED metadata would authorize a stop that can never verify.
        return _abort_launch(
            proc, snap,
            "error: cannot establish required process identity: executable unreadable",
        )

    expected = ProcessIdentity(
        pid=snap.pid, create_time=snap.create_time, executable=snap.executable,
        cmdline=snap.cmdline, parent_pid=snap.parent_pid, token=run_id,
    )

    if args.port is not None:
        def _ownership_settled():
            owned, listener_pid, _identity, reasons = probe_ownership(args.port, expected)
            if listener_pid not in (None, 0):
                # DEF-019: a transient netstat row can report PID 0 before the
                # real listener PID appears; only an explicitly non-zero
                # listener may settle — 0 is NOT settled (skipped/retried), so
                # a healthy just-spawned child is never terminated based on it.
                return (owned, reasons)
            return None

        outcome = _wait_for(_ownership_settled, args.wait)
        if outcome is None:
            return _abort_launch(
                proc, snap, f"error: no verified listener appeared on port {args.port}"
            )
        owned, reasons = outcome
        if not owned:
            # DEF-019: the listener does not match the spawned process (e.g. the
            # real listener is a descendant behind a .cmd/npm wrapper). The
            # launched tree is OURS and is terminated so no orphan remains.
            survivors, cleanup_error = _terminate_spawned_tree(proc, snap)
            if survivors:
                print(
                    "error: cleanup incomplete; surviving process(es): %s%s"
                    % (", ".join(map(str, survivors)),
                       f" ({cleanup_error})" if cleanup_error else ""),
                    file=sys.stderr,
                )
            raise PortConflictError(
                args.port,
                "listener on port %d does not match the launched process: %s"
                % (args.port, "; ".join(reasons)),
            )

    metadata = ProcessMetadata(
        run_id=run_id,
        pid=snap.pid,
        create_time=snap.create_time,
        executable=snap.executable,
        cmdline=[args.cmd] + list(args.args),
        # ADV-012: the launching CLI is short-lived and exits immediately after
        # writing metadata; on POSIX the child is reparented at that point, so the
        # CLI's pid is NOT a stable identity anchor. parent_pid is deliberately
        # persisted as None — 65A.1's tuple is satisfied by pid + create_time +
        # executable + run token, and the kill-authorization gate (ADV-003) never
        # depends on parent equality alone.
        parent_pid=None,
        port=args.port,
        launched_at=_iso_timestamp(time.time()),
        status="VERIFIED",
        terminate_error=None,
        surviving_descendants=[],
        notes=[],
    )
    record_metadata(metadata, args.meta)  # written ONLY after verification (65A.2)
    print(f"{args.meta}\t{run_id}")
    return 0


def _cmd_verify(args) -> int:
    metadata = load_metadata(args.meta)
    if metadata is None:
        print(f"error: no readable metadata at {args.meta}", file=sys.stderr)
        return 2
    expected = ProcessIdentity(
        pid=metadata.pid, create_time=metadata.create_time, executable=metadata.executable,
        cmdline=None, parent_pid=metadata.parent_pid, token=metadata.run_id or None,
    )
    owned, listener_pid, _identity, reasons = probe_ownership(args.port, expected)
    print(json.dumps({"owned": owned, "listener_pid": listener_pid, "reasons": reasons}, indent=2))
    return 0 if owned else 1


def _cmd_stop(args) -> int:
    result = shutdown_process(args.meta)
    print(json.dumps(result, indent=2, sort_keys=True))
    # ADV-011: a retried stop of an already-stopped app (NOT_RUNNING) is a
    # successful no-op, not a failure. Everything else (UNVERIFIED,
    # CLEANUP_INCOMPLETE, UNREADABLE_METADATA, ...) stays non-zero.
    return 0 if result["status"] in ("SHUTDOWN_OK", "NOT_RUNNING") else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.process_guard")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("generate-run-id", help="print a fresh unique run id (65A.3)")
    p.set_defaults(func=lambda args: print(generate_run_id()))

    p = sub.add_parser("launch", help="spawn, verify ownership, then write metadata (65A.2/65A.3)")
    p.add_argument("--cmd", required=True, help="executable to launch")
    p.add_argument("--args", nargs=argparse.REMAINDER, default=[],
                   help="program arguments; must be the LAST option (consumes the rest of the line)")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--meta", required=True)
    p.add_argument("--wait", type=float, default=15.0)
    p.add_argument(
        "--log-file",
        default=None,
        help="append child stdout/stderr to this file instead of suppressing it",
    )
    p.set_defaults(func=_cmd_launch)

    p = sub.add_parser("verify", help="probe ownership of the listener on --port (65A.2)")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--meta", required=True)
    p.set_defaults(func=_cmd_verify)

    p = sub.add_parser("stop", help="stop the process described by --meta (65A.1/65A.4)")
    p.add_argument("--meta", required=True)
    p.set_defaults(func=_cmd_stop)

    args = parser.parse_args(argv)
    try:
        code = args.func(args)
    except PortConflictError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return int(code or 0)


if __name__ == "__main__":
    sys.exit(main())
