"""Hermetic MockOllama: an in-process ASGI app serving canned /api/tags and
/api/chat responses, wired through ``httpx.ASGITransport`` so no socket or
external service is ever touched.

Behavior knobs (all optional, set by the test):

- ``tags``: model tags returned by GET /api/tags.
- ``outputs``: mapping schemaId -> structured output dict used by /api/chat.
- ``hang``: when True, /api/chat waits forever (the bridge must time it out or
  cancel it). Cancellation is recorded in ``cancelled``.
- ``status`` / ``body`` / ``content`` / ``raise_exc``: force the /api/chat
  response shape for malformed/unavailable tests.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Mapping, Optional


class MockOllama:
    def __init__(
        self,
        *,
        tags: tuple[str, ...] = ("hermes3:8b", "hermes3:8b-q4_K_M"),
        tags_status: int = 200,
        outputs: Optional[Dict[str, Mapping[str, Any]]] = None,
        hang: bool = False,
        status: int = 200,
        content: Optional[str] = None,
        body: Optional[bytes] = None,
        raise_exc: Optional[Exception] = None,
    ) -> None:
        self.tags = list(tags)
        self.tags_status = tags_status
        self.outputs: Dict[str, Mapping[str, Any]] = outputs or {
            "ASSET_SPEC_v1": {"victim": "Sarah Miller", "murderer": "Thomas Reed"}
        }
        self.hang = hang
        self.status = status
        self.content = content
        self.body = body
        self.raise_exc = raise_exc
        self.requests: list[Dict[str, Any]] = []
        self.inflight = 0
        self.max_inflight = 0
        self.cancelled = 0
        self._hang_event = asyncio.Event()

    def release_hang(self) -> None:
        self._hang_event.set()

    async def _hang_until_cancelled(self) -> None:
        if not self.hang:
            return
        try:
            await self._hang_event.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise

    async def app(self, scope: Mapping[str, Any], receive: Any, send: Any) -> None:
        assert scope["type"] == "http"
        method = scope["method"]
        path = scope["path"]
        if method == "GET" and path == "/api/tags":
            body = json.dumps({"models": self.tags}).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": self.tags_status,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body, "more_body": False})
            return
        if method == "POST" and path == "/api/chat":
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            try:
                request_body = b""
                while True:
                    event = await receive()
                    request_body += event.get("body", b"")
                    if not event.get("more_body", False):
                        break
                try:
                    parsed = json.loads(request_body.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    parsed = {}
                self.requests.append(parsed)
                if self.raise_exc is not None:
                    raise self.raise_exc
                await self._hang_until_cancelled()
                if self.raise_exc is not None:
                    raise self.raise_exc
                if self.body is not None:
                    response_body = self.body
                elif self.content is not None:
                    response_body = self.content.encode("utf-8")
                elif self.status == 404:
                    response_body = b'{"error":"NotFound: model not found"}'
                elif self.hang:
                    response_body = b""  # unreachable: hang waits until cancelled
                else:
                    schema = parsed.get("schemaId") or "ASSET_SPEC_v1"
                    output = json.dumps(self.outputs.get(schema, {"result": "ok"}))
                    payload = {"message": {"role": "assistant", "content": output}}
                    response_body = json.dumps(payload).encode("utf-8")
                await send(
                    {
                        "type": "http.response.start",
                        "status": self.status,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send(
                    {"type": "http.response.body", "body": response_body, "more_body": False}
                )
                return
            finally:
                self.inflight -= 1
        await send(
            {
                "type": "http.response.start",
                "status": 404,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send(
            {"type": "http.response.body", "body": b'{"error":"not found"}', "more_body": False}
        )