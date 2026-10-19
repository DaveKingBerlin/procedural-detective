# pd-ollama-bridge — Quick Start

`pd-ollama-bridge` is the small companion program that lets Procedural Detective
use an AI model running in **Ollama on your own computer**.

It is an **outbound-only client**. It connects to the Procedural Detective
server over an authenticated WebSocket (WSS) session and calls your local
Ollama HTTP API on `http://127.0.0.1:11434`. It does **not** open a local
network port, does **not** run a local web server and does **not** expose a
health endpoint or CORS surface on your machine.

```text
Browser  ── HTTPS ──▶  Procedural Detective server
                              │  authenticated outbound WSS
                              ▼
                    pd-ollama-bridge (on your machine)
                              │  localhost only
                              ▼
                    Ollama (on your machine)
```

---

## 1. Before you start

1. **Install Ollama** for your operating system: <https://ollama.com/download>
2. **Pull at least one model**, for example:

   ```bash
   ollama pull llama3.2:3b
   ```

   The bridge does not bundle Ollama or any model. Any model that Ollama can
   run may be used — pick one that fits your machine. (`llama3.2:3b` is only an
   example; it is not required.)

> **This binary is not code-signed.** Windows may show a SmartScreen reputation
> warning the first time you run it. Verify the SHA-256 checksum (below) and
> only then choose **More info → Run anyway**. There is no "verified publisher"
> or "digitally signed" claim for this build.

---

## 2. Download and verify

Download the archive for your platform from the project's release page:

<https://github.com/DaveKingBerlin/procedural-detective/releases>

Version `1.0.0` artifacts:

| Platform | File |
| --- | --- |
| Windows 10/11 x64 | `pd-ollama-bridge-v1.0.0-windows-x64.zip` |
| Linux x86_64 (amd64) | `pd-ollama-bridge-v1.0.0-linux-x64.tar.gz` |

Each archive contains the bridge binary, this `QUICKSTART.md`, `LICENSE` and
`THIRD_PARTY_NOTICES.txt`. The same release page carries `SHA256SUMS.txt`.

> The direct download links only resolve **after** the `v1.0.0` release is
> published. If the release page is not live yet, build from source instead —
> see the repository `bridge/README.md`.

### Verify the SHA-256 checksum

**Windows (PowerShell):**

```powershell
Get-FileHash .\pd-ollama-bridge-v1.0.0-windows-x64.zip -Algorithm SHA256
```

**Linux:**

```bash
sha256sum pd-ollama-bridge-v1.0.0-linux-x64.tar.gz
```

Compare the printed hash with the matching line in `SHA256SUMS.txt` from the
same release. Do not run the binary if the hash does not match.

---

## 3. Windows — install and run

1. Extract `pd-ollama-bridge-v1.0.0-windows-x64.zip` to a folder of your
   choice.
2. Start Ollama (the Ollama desktop app, or `ollama serve`).
3. Open a terminal in the extracted folder and confirm the bridge starts:

   ```powershell
   .\pd-ollama-bridge.exe --version
   .\pd-ollama-bridge.exe list-models
   ```

   `list-models` prints the model tags Ollama has installed. If it prints
   `✗ Ollama not reachable`, start Ollama and try again.

4. **Point the bridge at your Procedural Detective server.** The shipped default
   server is only a placeholder (`https://detective.example.com`), so a fresh
   download cannot connect until you set the server you actually use. Set it
   once:

   ```powershell
   .\pd-ollama-bridge.exe configure --server wss://<your-pd-server>
   ```

   (Run `.\pd-ollama-bridge.exe config` to see the effective server.)

5. Open Procedural Detective in your browser, choose **Local Ollama** and click
   **Connect local Ollama**. The page shows a short **pairing code**
   (`PD-XXXX-XXXX`) and the exact command to run — including the server origin,
   so you can copy it directly.

6. Run the command the page shows (first pairing). It passes the server origin
   explicitly, which overrides whatever is configured:

   ```powershell
   .\pd-ollama-bridge.exe connect --server wss://<your-pd-server> PD-XXXX-XXXX
   ```

   If you already ran `configure --server ...` in step 4, the shorter form works
   too:

   ```powershell
   .\pd-ollama-bridge.exe connect PD-XXXX-XXXX
   ```

   On later runs you can reconnect without a code:

   ```powershell
   .\pd-ollama-bridge.exe connect
   ```

   Wait until it prints `Waiting for generation jobs...`. Leave this window
   open while you play.

7. Back on the website, select a detected model and generate a case.

To stop the bridge cleanly, press **Ctrl+C** in its terminal window.

---

## 4. Linux — install and run

1. Extract the archive:

   ```bash
   tar -xzf pd-ollama-bridge-v1.0.0-linux-x64.tar.gz
   cd pd-ollama-bridge-v1.0.0-linux-x64
   ```

2. Make the binary executable:

   ```bash
   chmod +x pd-ollama-bridge
   ```

3. Start Ollama (`ollama serve`, or your distribution's service), then confirm
   the bridge starts:

   ```bash
   ./pd-ollama-bridge --version
   ./pd-ollama-bridge list-models
   ```

4. **Point the bridge at your Procedural Detective server.** The shipped default
   server is only a placeholder (`https://detective.example.com`), so a fresh
   download cannot connect until you set the server you actually use. Set it
   once:

   ```bash
   ./pd-ollama-bridge configure --server wss://<your-pd-server>
   ```

   (Run `./pd-ollama-bridge config` to see the effective server.)

5. Open Procedural Detective, choose **Local Ollama**, click **Connect local
   Ollama**, then run the command the page shows. It includes the server origin
   and the pairing code, and the `--server` flag overrides the configured value:

   ```bash
   ./pd-ollama-bridge connect --server wss://<your-pd-server> PD-XXXX-XXXX
   ```

   Later reconnects need no code:

   ```bash
   ./pd-ollama-bridge connect
   ```

6. Select a detected model on the website and generate a case.

Press **Ctrl+C** to stop the bridge cleanly.

---

## 5. How pairing works

The browser never talks to the bridge directly. Pairing is relayed by the
Procedural Detective server:

1. On the website you click **Connect local Ollama**.
2. The **server** mints a short-lived, single-use pairing code and shows it on
   the page, together with the command
   `pd-ollama-bridge connect --server <origin> <code>` — the origin is the
   server the page is served from.
3. You run that command locally. The bridge opens an **outbound** WSS session to
   the server and sends the code.
4. On success the server returns a **session token**, which the bridge stores
   (owner-only file permissions) and binds to that server's origin. The pairing
   code itself is single-use and is never stored.
5. Later runs use the stored token: `pd-ollama-bridge connect` (no code).

The pairing code expires quickly (about three minutes by default). If it
expires, click **Request a new code** on the website and run `connect` again
with the new code. If you change the configured server, the stored token is
**never** sent to the new server — you must pair again.

---

## 6. Command reference

```text
pd-ollama-bridge --help                 show usage
pd-ollama-bridge --version              print the bridge version
pd-ollama-bridge configure [options]    write stable settings to bridge.toml
pd-ollama-bridge connect [--server URL] [CODE]
                                        connect (pairing with CODE, else reconnect)
pd-ollama-bridge config                 show effective settings (never the token)
pd-ollama-bridge list-models            list local Ollama model tags
```

The shipped default server is a **placeholder** — you must point the bridge at
the Procedural Detective server you use, either with
`configure --server wss://<your-pd-server>` (persisted) or with
`connect --server wss://<your-pd-server> <CODE>` (one-off, overrides the
configured value). The bridge's local Ollama endpoint defaults to
`http://127.0.0.1:11434` and is **loopback-only** unless you explicitly opt in
with `--lan` (advanced; prints a warning). Debug logging is off by default and is
opt-in with `--debug`.

---

## 7. Troubleshooting

| Symptom | What to do |
| --- | --- |
| `✗ Ollama not reachable` | Start Ollama (`ollama serve` or the desktop app), then run `pd-ollama-bridge list-models` to confirm the endpoint. |
| No models installed | Pull one, e.g. `ollama pull llama3.2:3b`. The bridge needs at least one installed model before it will connect. |
| Cannot connect / tries `detective.example.com` | The shipped default server is a placeholder. Set your server: `pd-ollama-bridge configure --server wss://<your-pd-server>`, or pass `connect --server wss://<your-pd-server> <CODE>`. |
| Pairing fails / "no bridge session token is stored" | The first connection needs a code from the website: click **Connect local Ollama**, then run the command it shows — `pd-ollama-bridge connect --server wss://<your-pd-server> PD-XXXX-XXXX`. |
| Pairing code expired | Click **Request a new code** on the website and run `connect` with the fresh code. |
| "bound to ... changed server" | The stored token belongs to a different server origin. Pair again against the new server with a new code. |
| Browser seems unable to reach the bridge | Expected — the browser never contacts the bridge. Pairing is server-relayed: the website shows the code and you run `pd-ollama-bridge connect --server <origin> <CODE>`. Make sure the bridge is pointed at the **same** server the website uses. |
| Windows SmartScreen warning | The binary is unsigned. Verify the SHA-256 first, then **More info → Run anyway**. |
| Linux `Permission denied` | Run `chmod +x pd-ollama-bridge` before `./pd-ollama-bridge`. |
| TLS certificate verification failed | Check that your system CA store is present/current; the bridge falls back to its bundled `certifi` roots and fails closed if no usable trust store exists. |

---

## 8. Privacy (what actually happens)

Selecting **Local Ollama** runs the model inference on your machine, but this is
**not** a fully offline mode. The Procedural Detective server coordinates the
generation workflow and receives the prompts and structured results it needs to
validate and publish the case:

```text
Model inference runs locally in your Ollama instance.
The Procedural Detective server still receives the generation requests
(prompts/jobs) and the structured model result for validation and publication.
```

The bridge sends nothing to Ollama except the inference job it was given, and
the browser never receives your local Ollama endpoint or your bridge session
token. For the full policy see `docs/PRIVACY.md` in the repository.

---

## 9. Licenses

The packaged bridge bundles the CPython runtime, OpenSSL and a set of Python
libraries (including `websockets`, `httpx`, `httpcore`, `certifi`, `anyio`,
`idna`, `h11` and `typing_extensions`, plus `click`, `pygments` and `packaging`
on Windows) and the PyInstaller bootloader. The complete, versioned notices and
licenses ship next to this file in `THIRD_PARTY_NOTICES.txt`. The bridge itself
is MIT-licensed (`LICENSE`).
