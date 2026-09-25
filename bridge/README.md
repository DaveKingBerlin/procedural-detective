# pd-ollama-bridge — Procedural Detective Phase 22 local AI bridge

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

## Run

Get a pairing code from the Procedural Detective web UI, then:

```bash
pd-ollama-bridge connect PD-X7K4-92QP \
  --server https://detective.example.com \
  --ollama http://127.0.0.1:11434 \
  --model hermes3:8b
```

Progress output:

```text
Procedural Detective Local AI Bridge

Checking Ollama...
✓ Ollama reachable
✓ hermes3:8b available

Connecting...
✓ Connected to detective.example.com
✓ Pairing successful

Waiting for generation jobs...
```

Reconnect: after a link drop the bridge reconnects automatically with bounded
backoff (1s → 2s → 4s cap), presenting the persisted bridge session token.
Start the bridge after pairing once with:

```bash
pd-ollama-bridge connect PD-X7K4-92QP --memory-only
```

`--memory-only` never writes the session token to disk (default writes it under
the user's temp/home directory as a 0600 file; override with
`--token-file PATH`).

Operator helpers:

```bash
pd-ollama-bridge list-models --ollama http://127.0.0.1:11434
```

The model is chosen **locally by the operator** (default `hermes3:8b`). The
browser/server can display it but can never free-text-select an arbitrary local
model: the bridge verifies the selected tag against the local `/api/tags` at
startup, and the protocol only ever carries the operator-selected model label.

## Config

Example operator config:

```text
server_url=https://detective.example.com
ollama_url=http://127.0.0.1:11434
model=hermes3:8b
```

There is no config file loader: the values are `--server`, `--ollama` and
`--model` on the command line. The bridge always owns these values itself; a
job frame from the server never contains a URL, a model selection change or
any instruction besides the prompt to send to the already-configured Ollama.

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
  is kept in memory (optionally 0600 token file) and is the only reconnect
  credential. A rejected/expired session stops the bridge — re-pair.

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
    config.py          operator config + token file store
    ollama_client.py   bounded local Ollama client (typed failures)
    bridge_client.py   asyncio WSS client: handshake, jobs, cancel, reconnect
    cli.py             pd-ollama-bridge CLI
  tests/               pytest suite (fake WSS server + MockOllama)
  README.md
```