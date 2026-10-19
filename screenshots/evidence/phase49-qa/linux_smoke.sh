#!/usr/bin/env bash
# QA Phase 49 Linux clean-runtime-container smoke.
# Runs inside a pristine python:3.12-slim-bookworm with ONLY the artifact mounted.
set -u

echo "=== runtime container identity ==="
cat /etc/os-release | grep -E '^(PRETTY_NAME|VERSION)=' || true
echo "=== extract artifact ==="
mkdir -p /clean
tar -xzf /artifact.tar.gz -C /clean
ls -l /clean

echo "=== exec bit preserved in archive ==="
if [ -x /clean/pd-ollama-bridge ]; then
  echo "OK: pd-ollama-bridge extracted executable"
else
  echo "FAIL: pd-ollama-bridge not executable"
  exit 1
fi

echo "=== build-toolchain absence (not required at runtime) ==="
for tool in python python3 pip pyinstaller gcc cc make; do
  if command -v "$tool" >/dev/null 2>&1; then
    echo "present: $tool -> $(command -v "$tool")"
  else
    echo "absent : $tool"
  fi
done

echo "=== --version ==="
/clean/pd-ollama-bridge --version
echo "version_exit=$?"

echo "=== --help ==="
/clean/pd-ollama-bridge --help > /tmp/help.txt 2>&1
echo "help_exit=$?"
head -n 6 /tmp/help.txt

echo "=== list-models with NO Ollama present (must be truthful, non-zero) ==="
/clean/pd-ollama-bridge list-models --ollama http://127.0.0.1:11434
rc=$?
echo "list_models_exit=$rc"
if [ "$rc" -ne 0 ]; then
  echo "OK: list-models exited non-zero and reported Ollama unavailable"
else
  echo "FAIL: list-models unexpectedly succeeded without Ollama"
  exit 1
fi

echo "=== DONE ==="
