#!/bin/sh
mkdir -p /x
tar xzf /pkg.tar.gz -C /x
echo "--- extracted modes ---"
ls -la /x
echo "--- no python/toolchain ---"
command -v python3 || echo NO_PYTHON3
command -v pip || echo NO_PIP
echo "--- version ---"
/x/pd-ollama-bridge --version
echo "EXIT_VERSION=$?"
echo "--- help (stdout suppressed) ---"
/x/pd-ollama-bridge --help >/dev/null
echo "EXIT_HELP=$?"
echo "--- list-models with no Ollama present (truthful failure expected) ---"
/x/pd-ollama-bridge list-models
echo "EXIT_LISTMODELS=$?"
echo "--- connect smoke to unreachable server (bounded, must not fake health) ---"
timeout 20 /x/pd-ollama-bridge connect PD-TEST-0000 --server wss://127.0.0.1:1
echo "EXIT_CONNECT=$?"
echo "DONE"
