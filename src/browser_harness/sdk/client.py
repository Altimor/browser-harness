"""Async IPC client for the browser-harness daemon.

All cross-call state lives in the daemon; this is a thin newline-JSON request
layer over the same socket the CLI uses. The daemon name is instance state,
not a module global, so one process can drive several browsers.
"""
import asyncio
import json

from .. import _ipc as ipc
from .. import admin


class HarnessError(RuntimeError):
    """Error reported by the daemon."""


class HarnessClient:
    def __init__(self, name: str = "default", *, request_timeout: float = 30.0, env: dict[str, str] | None = None):
        ipc._check(name)
        self.name = name
        self.request_timeout = request_timeout
        # extra env for daemon spawn (BU_CDP_WS etc) -- only used if we have to start one
        self.env = dict(env or {})

    # --- daemon lifecycle ---

    async def ensure_daemon(self, wait: float = 60.0) -> None:
        """Idempotent bootstrap (self-heal paths in admin.ensure_daemon); blocking, so threaded."""
        await asyncio.to_thread(admin.ensure_daemon, wait, self.name, self.env or None)

    async def alive(self) -> bool:
        return await asyncio.to_thread(ipc.ping, self.name, 1.0)

    async def shutdown_daemon(self) -> None:
        """Stop the daemon; for cloud browsers this also ends billing."""
        await asyncio.to_thread(admin.restart_daemon, self.name)

    # --- transport ---

    async def send(self, req: dict) -> dict:
        """One request/response round-trip; raises HarnessError on daemon errors."""
        r = await self._request(req)
        if isinstance(r, dict) and "error" in r:
            raise HarnessError(r["error"])
        if not isinstance(r, dict):
            raise HarnessError(f"malformed daemon response: {r!r}")
        return r

    # a single AX-tree response can be tens of MB on heavy pages; asyncio's
    # default 64KiB stream limit would fail readline() on them
    _STREAM_LIMIT = 256 * 1024 * 1024

    async def _request(self, req: dict):
        if not ipc.IS_WINDOWS:
            token = None
            connector = asyncio.open_unix_connection(str(ipc._sock_path(self.name)), limit=self._STREAM_LIMIT)
        else:
            port, token = ipc._read_port_file(self.name)
            if port is None:
                raise FileNotFoundError(str(ipc.port_path(self.name)))
            connector = asyncio.open_connection("127.0.0.1", port, limit=self._STREAM_LIMIT)
        reader, writer = await asyncio.wait_for(connector, timeout=5.0)
        try:
            if token:
                req = {**req, "token": token}
            writer.write((json.dumps(req) + "\n").encode())
            await writer.drain()
            data = await asyncio.wait_for(reader.readline(), timeout=self.request_timeout)
            return json.loads(data or b"{}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    # --- request shapes ---

    async def cdp(self, method: str, session_id: str | None = None, **params):
        """Raw CDP: `await client.cdp('Page.navigate', url='...')`."""
        r = await self.send({"method": method, "params": params, "session_id": session_id})
        return r.get("result", {})

    async def meta(self, command: str, **fields) -> dict:
        """Daemon meta command (ping, drain_events, current_tab, set_session, ...)."""
        return await self.send({"meta": command, **fields})
