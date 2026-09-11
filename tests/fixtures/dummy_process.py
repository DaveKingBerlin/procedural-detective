"""Isolated dummy process fixture for process-lifecycle tests (REQUIREMENTS 65A.7).

Spawned via `[sys.executable, <this file>, "<mode>", ...]`.  Stdlib only.
Modes:
  sleep --seconds N [--child-duration N] [--pidfile PATH] [--marker PATH] [--tag TAG]
      waits N seconds then exits.  With --child-duration it spawns a child
      `python -c "import time;time.sleep(DUR)"`, writes the child's pid to
      --pidfile, and registers an atexit handler writing "<tag>:<child_pid>"
      to --marker on graceful exit.  Prints "READY <tag> <pid>" after startup.
  bind --port N --seconds N [--tag TAG]
      binds 127.0.0.1:N (SO_REUSEADDR), listens, prints "READY BOUND <port> <pid>".

The script is killable normally; on graceful shutdown it reaps its own child.
Under forced termination the child is orphaned and the TEST (never the tool)
is responsible for cleaning it up.
"""

import atexit
import os
import socket
import subprocess
import sys
import time


def _arg_after(argv, name, default=None):
    for i, token in enumerate(argv):
        if token == name and i + 1 < len(argv):
            return argv[i + 1]
    return default


def _run_sleep(argv):
    seconds = float(_arg_after(argv, "--seconds", "5"))
    child_duration = _arg_after(argv, "--child-duration")
    pidfile = _arg_after(argv, "--pidfile")
    marker = _arg_after(argv, "--marker")
    tag = _arg_after(argv, "--tag", "dummy")
    child = None
    if child_duration is not None:
        code = "import time;time.sleep(%r)" % float(child_duration)
        child = subprocess.Popen([sys.executable, "-c", code])
        if pidfile:
            with open(pidfile, "w", encoding="utf-8", newline="\n") as f:
                f.write(f"{child.pid}\n")
        print(f"CHILD {child.pid}", flush=True)
        if marker:

            def _write_marker():
                try:
                    with open(marker, "w", encoding="utf-8", newline="\n") as f:
                        f.write(f"{tag}:{child.pid}")
                except OSError:
                    pass

            atexit.register(_write_marker)
    try:
        print(f"READY {tag} {os.getpid()}", flush=True)
        time.sleep(seconds)
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


def _run_bind(argv):
    port = int(_arg_after(argv, "--port"))
    seconds = float(_arg_after(argv, "--seconds", "5"))
    tag = _arg_after(argv, "--tag", "bound")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(1)
    print(f"READY BOUND {port} {os.getpid()}", flush=True)
    try:
        time.sleep(seconds)
    finally:
        sock.close()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "sleep":
        _run_sleep(sys.argv[2:])
    elif mode == "bind":
        _run_bind(sys.argv[2:])
    else:
        print(f"usage: {sys.argv[0]} <sleep|bind> ...", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())