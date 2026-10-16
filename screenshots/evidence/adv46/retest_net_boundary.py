"""Phase 46 ADV-46-03 retest: merged/standalone production network boundary.

Hermetic: canned Compose renders (no daemon) + real client-side
`docker compose config --format json` renders of temp chains when the CLI is
present. Prints PASS/FAIL per scenario. No configured value is printed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
while not (REPO / "tools" / "release_check.py").is_file():
    if REPO.parent == REPO:
        raise SystemExit("repo root not found")
    REPO = REPO.parent
sys.path.insert(0, str(REPO))
from tools import release_check  # noqa: E402


def _render(body: dict):
    return subprocess.CompletedProcess(["docker", "compose"], 0, json.dumps(body), "")


def runner(merged: dict, prod: dict):
    def _r(command, **_kwargs):
        count = [str(a) for a in command].count("-f")
        return _render(merged if count >= 2 else prod)
    return _r


def merged_body(*, backend_ports=None, caddy_ports=None, extra=None):
    backend = {
        "expose": ["8000"],
        "ports": ([{"target": 8000, "published": "8000"}]
                  if backend_ports is None else backend_ports),
    }
    caddy = {"ports": ([{"target": 80, "published": "80"},
                        {"target": 443, "published": "443"}]
                       if caddy_ports is None else caddy_ports)}
    services = {"procedural-detective": backend, "caddy": caddy}
    if extra:
        services.update(extra)
    return {"services": services}


def prod_body(*, backend_ports=None, extra=None):
    services = {
        "procedural-detective": {
            "expose": ["8000"],
            "ports": [] if backend_ports is None else backend_ports,
        },
        "caddy": {"ports": [{"target": 80, "published": "80"},
                            {"target": 443, "published": "443"}]},
    }
    if extra:
        services.update(extra)
    return {"services": services}


def run(merged: dict, prod: dict) -> tuple[bool, list[str]]:
    findings = release_check.check_prod_merge_effective_config(
        REPO, compose_runner=runner(merged, prod)
    )
    fails = [f.message for f in findings if f.severity == "fail"]
    return (not fails), fails


def main() -> None:
    scenarios = [
        ("S0 shipped shape (expect PASS + report)",
         merged_body(), prod_body(), True),
        ("S1 prod backend publishes 8000:8000 (expect FAIL)",
         merged_body(), prod_body(backend_ports=[{"target": 8000, "published": "8000"}]), False),
        ("S2a MERGED new svc host port 9000 (expect FAIL)",
         merged_body(extra={"monitoring": {"ports": [{"target": 9000, "published": "9000"}]}}),
         prod_body(), False),
        ("S2b STANDALONE new svc host port 9090 (expect FAIL)",
         merged_body(),
         prod_body(extra={"monitoring": {"ports": [{"target": 9090, "published": "9090"}]}}), False),
        ("S3a MERGED backend network_mode host (expect FAIL)",
         merged_body(extra={"procedural-detective": {
             "expose": ["8000"], "network_mode": "host"}}),
         prod_body(), False),
        ("S3b STANDALONE backend network_mode host (expect FAIL)",
         merged_body(),
         prod_body(extra={"procedural-detective": {
             "expose": ["8000"], "network_mode": "host"}}), False),
        ("S4 backend renamed in prod (expect FAIL closed)",
         merged_body(),
         {"services": {"backend": {"expose": ["8000"]},
                       "caddy": {"ports": [{"target": 80, "published": "80"},
                                           {"target": 443, "published": "443"}]}}}, False),
        ("S5 caddy extra 8000:8000 (expect FAIL)",
         merged_body(caddy_ports=[{"target": 80, "published": "80"},
                                  {"target": 443, "published": "443"},
                                  {"target": 8000, "published": "8000"}]),
         prod_body(), False),
        ("S6 caddy network_mode host (expect FAIL)",
         merged_body(extra={"caddy": {"network_mode": "host",
                                      "ports": [{"target": 80, "published": "80"},
                                                {"target": 443, "published": "443"}]}}),
         prod_body(), False),
        ("S7 new svc publishes caddy ports 80/443 (expect FAIL)",
         merged_body(extra={"edge2": {"ports": [{"target": 80, "published": "80"},
                                                {"target": 443, "published": "443"}]}}),
         prod_body(), False),
        ("S8 new svc string-form host port (expect FAIL)",
         merged_body(extra={"db": {"ports": ["5432:5432"]}}),
         prod_body(), False),
        ("S9 network_mode: \"host\" quoted (expect FAIL)",
         merged_body(extra={"monitoring": {"network_mode": "host"}}),
         prod_body(), False),
        ("S10 prod backend 127.0.0.1:8000:8000 (expect FAIL standalone)",
         merged_body(),
         prod_body(backend_ports=[{"target": 8000, "published": "8000",
                                   "host_ip": "127.0.0.1"}]), False),
        ("S11 MISSING caddy service (expect FAIL closed)",
         {"services": {"procedural-detective": {"expose": ["8000"],
                                                "ports": [{"target": 8000, "published": "8000"}]}}},
         {"services": {"procedural-detective": {"expose": ["8000"]}}}, False),
    ]
    print("== canned-render merge-guard scenarios ==")
    ok = True
    for label, m, p, expect_pass in scenarios:
        passed, fails = run(m, p)
        good = (passed == expect_pass)
        ok = ok and good
        print(("  PASS" if good else "  MISS"), label,
              "| gate_pass =", passed)
        if not good:
            for f in fails:
                print("       ", f)
    print("canned scenarios all as expected:", ok)

    # ---- real client-side renders ----
    if shutil.which("docker") is None:
        print("\n(no docker CLI; skipping real renders)")
        return
    tmp = Path(tempfile.mkdtemp(prefix="net-", dir=Path(__file__).parent))
    base = (tmp / "docker-compose.yml")
    prod = (tmp / "docker-compose.prod.yml")
    base.write_text(
        "services:\n"
        "  procedural-detective:\n"
        "    image: busybox\n"
        "    ports:\n      - \"8000:8000\"\n"
        "    expose:\n      - \"8000\"\n"
        "  caddy:\n"
        "    image: busybox\n"
        "    ports:\n      - \"80:80\"\n      - \"443:443\"\n",
        encoding="utf-8")
    prod.write_text(
        "services:\n"
        "  procedural-detective:\n"
        "    image: busybox\n"
        "    expose:\n      - \"8000\"\n"
        "  caddy:\n"
        "    image: busybox\n"
        "    ports:\n      - \"80:80\"\n      - \"443:443\"\n",
        encoding="utf-8")

    def real(spec_files, env_files=None):
        if isinstance(spec_files, (str, Path)):
            spec_files = [spec_files]
        cmd = ["docker", "compose"]
        for f in spec_files:
            cmd += ["-f", str(f)]
        cmd += ["config", "--format", "json"]
        r = subprocess.run(cmd, cwd=str(tmp), capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr

    print("\n== real renders (temp chain) ==")
    scenarios_real = [
        ("R0 shipped temp chain (expect PASS+report)", [base, prod]),
        ("R1 prod backend :8000 regression (expect FAIL)",
         [base, prod], "regress_backend"),
        ("R2 new svc host port in prod (expect FAIL)",
         [base, prod], "new_service"),
        ("R3 backend network_mode host in prod (expect FAIL)",
         [base, prod], "host_mode"),
        ("R4 prod backend env-interpolated ${PD_EXTRA_PORT} (expect FAIL)",
         [base, prod], "env_port"),
    ]

    def write_variant(kind: str) -> None:
        extra_backend = ""
        extra_services = ""
        if kind == "regress_backend":
            extra_backend = "    ports:\n      - \"8000:8000\"\n"
        elif kind == "new_service":
            extra_services = ("  monitoring:\n    image: busybox\n"
                              "    ports:\n      - \"9000:9000\"\n")
        elif kind == "host_mode":
            extra_backend = "    network_mode: host\n"
        elif kind == "env_port":
            extra_backend = "    ports:\n      - \"${PD_EXTRA_PORT:-8000}:8000\"\n"
        prod.write_text(
            "services:\n"
            "  procedural-detective:\n"
            "    image: busybox\n"
            "    expose:\n      - \"8000\"\n"
            + extra_backend
            + "  caddy:\n"
            "    image: busybox\n"
            "    ports:\n      - \"80:80\"\n      - \"443:443\"\n"
            + extra_services,
            encoding="utf-8")

    for item in scenarios_real:
        label, files = item[0], item[1]
        kind = item[2] if len(item) > 2 else None
        if kind:
            write_variant(kind)
        else:
            write_variant("clean")
        rc, out, err = real(files)
        if rc != 0:
            print("  render-error", label, err.splitlines()[:1])
            continue
        merged_doc = json.loads(out)
        # standalone render = the prod file alone
        rc2, out2, err2 = real([prod])
        prod_doc = json.loads(out2) if rc2 == 0 else {}
        findings = release_check.check_prod_merge_effective_config(
            tmp,
            compose_paths=(base, prod),
            compose_runner=lambda command, **kw: subprocess.CompletedProcess(
                command, 0,
                json.dumps(merged_doc if [str(a) for a in command].count("-f") >= 2
                           else prod_doc), ""),
        )
        fails = [f.message for f in findings if f.severity == "fail"]
        print("  gate_pass =", not fails, "|", label)
        for f in fails:
            print("       ", f.splitlines()[0])

    # cleanup
    prod.write_text("services: {}\n", encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
