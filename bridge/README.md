# pd-ollama-bridge — Procedural Detective local AI bridge

A small, auditable Python CLI that connects your **local Ollama** to a hosted
Procedural Detective server over an **outbound** WebSocket (WSS) session.

```text
Browser
   │  HTTPS
   ▼
Procedural Detective Server
   │  authenticated outbound WSS
   ▼
Your machine: pd-ollama-bridge
   │  localhost only
   ▼
Your local Ollama :11434
```

The bridge exposes **exactly one** capability to the server —
`STRUCTURED_MODEL_INFERENCE`. It is not a remote-execution agent: the server can
never tell it to fetch a URL, read a file, run a command, touch Docker, open a
browser or reach any local service other than the operator-configured Ollama.

`backend/app`, `backend/tests` and `frontend/` are **not touched** by this
directory — they are another track's in-flight work.

---

## Install

Requires Python ≥ 3.11 with `websockets` and `httpx`.

```bash
# from this directory
python -m pip install -e .
# or run without installing:
python -m pd_ollama_bridge --help
```

The command line binary is `pd-ollama-bridge`.

## Day-to-day workflow (Phase 27)

**One-time setup** — write your stable non-secret bridge settings to
`bridge.toml` (the only command that writes the config file):

```powershell
pd-ollama-bridge configure ^
  --server wss://your-permanent-server.example
```

**First pairing** — the pairing code is a single-use secret, entered once:

```powershell
pd-ollama-bridge connect PD-XXXX-XXXX
```

Expected output:

```text
✓ Connected to your-permanent-server.example
✓ Pairing successful
Waiting for generation jobs...
```

The bridge session token is persisted to the secure token file (separate from
the config; 0600 on POSIX, best-effort owner-only ACL on Windows) **bound to
the server origin it was issued for**.

**Normal reconnects afterwards** — stop the bridge, then just:

```powershell
pd-ollama-bridge connect
```

The zero-argument form uses the stored server-bound token. No pairing code is
required. **If the configured server changes, the stored token is never sent
to the new server** — you must pair again against the new origin.

Inspect the effective configuration (the token itself is never printed):

```powershell
pd-ollama-bridge config
```

```text
Config file: C:\Users\you\AppData\Local\ProceduralDetective\bridge.toml
Server: wss://your-permanent-server.example
Ollama: http://127.0.0.1:11434
Timeout: 5
Max retries: 12
Model: hermes3:8b
LAN: no
Debug: no
Token present: yes
```

## Config file location and format

The non-secret settings live in a single TOML file:

| OS      | Path |
| ------- | ---- |
| Windows | `%LOCALAPPDATA%\ProceduralDetective\bridge.toml` (fallback `%USERPROFILE%\.procedural-detective\bridge.toml`) |
| Linux   | `$XDG_CONFIG_HOME/procedural-detective/bridge.toml` or `~/.config/procedural-detective/bridge.toml` |

(Point at a different file with `--config PATH` on any command.)

Example:

```toml
server = "wss://your-permanent-server.example"
ollama = "http://127.0.0.1:11434"
timeout = 120
max_retries = 10
lan = false
debug = false
```

Optional legacy/fallback model (per-job selection from the job frame remains
**authoritative**):

```toml
model = "hermes3:8b"
```

### Secrets stay separate

The following are **never** stored in `bridge.toml`:

- pairing code
- Bridge token
- API keys
- server credentials
- CaseTruth
- raw prompts

The Bridge token lives in its own secure token file — by default next to the
config (`bridge_token`), configurable with `--token-file PATH` /
`PD_BRIDGE_TOKEN_FILE` / `token_file` in TOML. The TOML may hold **only the
path**, never the token contents.

## Configuration precedence

```text
CLI argument  >  environment variable  >  bridge.toml  >  safe built-in default
```

Environment variable names (set them the usual way, e.g. in PowerShell
`$env:PD_BRIDGE_SERVER = "wss://..."`):

| Setting       | Environment variable |
| ------------- | -------------------- |
| server        | `PD_BRIDGE_SERVER`   |
| ollama        | `PD_BRIDGE_OLLAMA`   |
| timeout       | `PD_BRIDGE_TIMEOUT`  |
| max retries   | `PD_BRIDGE_MAX_RETRIES` |
| token file    | `PD_BRIDGE_TOKEN_FILE` |
| lan           | `PD_BRIDGE_LAN` (`true`/`false`) |
| debug         | `PD_BRIDGE_DEBUG` (`true`/`false`) |

Safe built-in defaults (config directory absent or nothing configured):
`ollama = http://127.0.0.1:11434`, `timeout = 5.0`, `max_retries = 12`,
`lan = false`, `debug = false`. No production hostname is hard-coded.

### Temporary overrides

CLI overrides are **runtime-only** and are never written back to
`bridge.toml`. Only `configure` modifies the file, and only the fields you
explicitly pass:

```powershell
pd-ollama-bridge connect ^
  --server wss://other.example ^
  --ollama http://127.0.0.1:11435
```

## `list-models`

```powershell
pd-ollama-bridge list-models
```

Resolves the local Ollama endpoint through the same precedence chain
(CLI → env → config → `http://127.0.0.1:11434`). No server configuration is
required to list local Ollama models.

## Legacy explicit form (Phase 22 syntax)

All explicit flags remain valid and work **with no config file at all**:

```powershell
pd-ollama-bridge connect ^
  PD-X7K4-92QP ^
  --server https://detective.example.com ^
  --ollama http://127.0.0.1:11434 ^
  --model hermes3:8b ^
  --timeout 120 ^
  --token-file C:\path\to\token
```

Note: a Phase 22 token file holds a bare token with **no server binding**.
For safety it is treated as unusable; the next pairing rewrites it in the new
bound format.

## The LOCAL_ONLY Ollama rule

- The default Ollama endpoint validation accepts **only** `localhost`,
  `127.0.0.1` and `::1`.
- Plain `ws://` to the Procedural Detective server is accepted **only** for
  loopback/development hosts; any remote server requires `https/wss`.
- TLS is enabled with default certificate + hostname validation. There is no
  `--insecure` / certificate-bypass flag.
- `--lan` is an advanced opt-in for a non-loopback Ollama endpoint. Passing it
  prints a loud warning; it is never enabled silently and never implied by a
  remote server.

## Security model

- One narrow capability; a strict closed-schema protocol (any frame with
  unknown/extra fields is rejected and the connection closed).
- Client-side size and depth limits: frames ≤ 256 KiB,
  prompt ≤ 120 000 bytes, response ≤ 256 KiB, nesting depth ≤ 32 and
  collection length ≤ 10 000, enforced with a depth-guarded JSON parser
  (nesting bombs are rejected before deep recursion).
- The bridge replies to at most one job at a time; a second concurrent job gets
  the typed `BRIDGE_BUSY` failure. `job_cancel` aborts the in-flight local
  request. A cancelled job never returns a result.
- Typed failure codes on the wire are a closed vocabulary:
  `LOCAL_OLLAMA_UNAVAILABLE`, `LOCAL_MODEL_UNAVAILABLE`,
  `LOCAL_PROVIDER_TIMEOUT`, `LOCAL_PROVIDER_INVALID_OUTPUT`, `BRIDGE_BUSY`,
  `BRIDGE_PROTOCOL_ERROR`.
- The pairing code is single-use and never persisted. The bridge session token
  is the only reconnect credential; it is persisted with the strongest
  practical permissions (0600 on POSIX; best-effort owner-only ACL on Windows)
  **and bound to the server origin it was issued for**. A token is never sent
  to a changed server — the CLI fails closed and asks for a new pairing code.
  `--memory-only` keeps the token in memory only if you prefer not to persist
  it at all. A rejected/expired session stops the bridge — re-pair.
- Per-job model selection from the job frame is always **authoritative**; the
  configured/local model is only a legacy fallback.

## Server-change behavior

The stored token carries its server binding (`server`, `protocolVersion`,
`createdAt`). If the effective server origin (CLI → env → config) differs from
the bound origin:

```text
pd-ollama-bridge connect
error: the stored bridge session token is bound to wss://old-server.example,
but the configured server is wss://new-server.example. A token is NEVER reused
on a changed server - pair again with a new pairing code.
```

Supply a new pairing code against the new server:

```powershell
pd-ollama-bridge connect PD-NEW1-CODE
```

## Troubleshooting

| Symptom | Cause → Fix |
| ------- | ----------- |
| `connect` with no code prints "no bridge session token is stored" | First use (or `--memory-only` previously): pair once with `connect PD-XXXX-XXXX` |
| `connect` prints a "bound to ... changed server" error after editing `--server` | The token belongs to a different origin. Pairing again with a new code against the new server is required by design |
| `configure` refuses a URL | Server must be `wss://`/`https://` (plain `ws://` is localhost-only); Ollama must be loopback unless `--lan` is also set |
| `configure`/`config` report "malformed TOML" | Edit only with `configure` (or fix the TOML). Security-sensitive fields are never silently ignored |
| "Ollama not reachable" | Start Ollama, verify the endpoint with `pd-ollama-bridge list-models` |
| Config file path is a surprise | Run `pd-ollama-bridge config` — it prints the resolved path first |

## Privacy note

This mode does **not** keep "everything" on your machine. Model inference runs
locally on your computer, and the hosted Procedural Detective server still
coordinates the generation workflow and receives the structured model result
for validation and publication. Prompts and case metadata may still be stored
according to the server's existing retention policy. Do not assume a secret
enclave:

```text
Model inference runs locally on your computer.
The hosted Procedural Detective server still coordinates the generation workflow
and receives the structured model result for validation and publication.
```

## Tests

Hermetic only — a fake WSS server and an in-process MockOllama, no real network:

```bash
cd bridge
python -m pytest tests -q
```

The server-track integration harness (`backend/tests/bridge_harness.py`) is not
importable from this directory (it depends on the in-flight backend app), so
the bridge tests are fully standalone.

## Layout

```text
bridge/
  pd_ollama_bridge/
    protocol.py        strict v1 protocol (mirrors the server's definition)
    bounded_json.py    depth/collection-bounded JSON parser
    urls.py            server WSS + loopback-only Ollama URL validation
    config.py          operator config + server-bound token file store
    bridge_config.py   bridge.toml (PG path/TOML/precedence) — Phase 27
    ollama_client.py   bounded local Ollama client (typed failures)
    bridge_client.py   asyncio WSS client: handshake, jobs, cancel, reconnect
    cli.py             pd-ollama-bridge CLI (configure/connect/config/list-models)
  tests/               pytest suite (fake WSS server + MockOllama)
  README.md
```