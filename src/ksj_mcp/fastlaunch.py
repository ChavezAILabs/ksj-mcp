"""
Fast-handshake launcher for the KSJ MCP server (the `ksj-mcp` entry point).

Why: importing the MCP SDK takes ~7 s on an idle low-power laptop and 45–60 s
when the machine is busy (measured on a Celeron N4120, 2026-09-28/10-01),
because the SDK builds hundreds of pydantic models at import time. MCP
clients give a server about 30–60 s to answer `initialize`, so a busy machine
meant ksj never connected — no change inside ksj's own code can speed up the
SDK import.

How: this module imports only the standard library, so it starts in about a
second. It launches the real server (ksj_mcp.server) as a child process and
relays newline-delimited JSON-RPC between the client and the child, with one
addition: the answers to `initialize` and the list requests (`tools/list`,
`prompts/list`, `resources/list`, ...) are cached on disk from the previous
run. When a cached answer exists the client gets it immediately, while the
request is still forwarded to the child — the child must be initialized
itself, and its fresh answer replaces the cache entry (it is not forwarded,
since the client already has one). Everything else is relayed untouched; a
tool call made before the child is ready simply waits in the pipe.

The cache is keyed by ksj-mcp version, MCP SDK version, and Python version,
and by the request's method and parameters (for `initialize`, the requested
protocol version). No cache entry means plain relaying: the first run after
an install or upgrade behaves exactly like the server without this launcher.

Set KSJ_FAST_HANDSHAKE=0 to bypass the launcher and run the server in-process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from importlib import metadata
from pathlib import Path

from . import __version__

# Requests whose answers depend only on the server build, never on journal
# contents or on time — safe to answer from a cache written by a previous run.
CACHEABLE_METHODS = frozenset({
    "initialize",
    "tools/list",
    "prompts/list",
    "resources/list",
    "resources/templates/list",
})

CACHE_FILE = "handshake-cache.json"


def _data_dir() -> Path:
    """Same resolution as ksj_mcp.server._data_dir (kept import-free)."""
    env = os.environ.get("KSJ_DATA_DIR")
    return Path(env) if env else Path.home() / ".ksj-mcp"


def build_key() -> str:
    """Identifies the server build whose answers the cache holds."""
    try:
        sdk = metadata.version("mcp")
    except metadata.PackageNotFoundError:
        sdk = "?"
    py = ".".join(str(p) for p in sys.version_info[:3])
    return f"ksj-mcp {__version__} | mcp {sdk} | python {py}"


def request_key(method: str, params: object) -> str:
    """Cache key for one request. `initialize` answers depend only on the
    requested protocol version (clientInfo and capabilities don't change
    what this server replies); list requests on their params minus _meta."""
    if method == "initialize":
        version = params.get("protocolVersion") if isinstance(params, dict) else None
        return f"initialize|{version}"
    if isinstance(params, dict):
        params = {k: v for k, v in params.items() if k != "_meta"}
    return f"{method}|{json.dumps(params or {}, sort_keys=True)}"


class HandshakeCache:
    """Persisted request-key → result map for one server build."""

    def __init__(self, path: Path, build: str):
        self.path = path
        self.build = build
        self._lock = threading.Lock()
        self.entries: dict[str, object] = {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("build") == build and isinstance(data.get("entries"), dict):
                self.entries = data["entries"]
        except (OSError, ValueError, AttributeError):
            pass  # missing, unreadable, or from another build — start empty

    def get(self, key: str):
        with self._lock:
            return self.entries.get(key)

    def put(self, key: str, result: object) -> None:
        with self._lock:
            if self.entries.get(key) == result:
                return
            self.entries[key] = result
            snapshot = {"build": self.build, "entries": dict(self.entries)}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(snapshot), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass  # a cache that can't be written only costs speed next time


class Relay:
    """
    Relays JSON-RPC lines between the client (*client_in*/*client_out*) and
    a child server process, answering cacheable requests from *cache*.
    """

    def __init__(self, cache: HandshakeCache, child: subprocess.Popen,
                 client_in, client_out):
        self.cache = cache
        self.child = child
        self.client_in = client_in
        self.client_out = client_out
        self._out_lock = threading.Lock()
        # id (JSON-encoded) → cache key, for requests whose answer the client
        # already got from the cache (child's reply refreshes the cache only)
        self._answered: dict[str, str] = {}
        # id → cache key, for cacheable requests relayed normally (child's
        # reply is forwarded AND stored)
        self._pending: dict[str, str] = {}
        self._ids_lock = threading.Lock()

    # ── client → child ──────────────────────────────────────────────────────

    def _send_client(self, line: bytes) -> None:
        with self._out_lock:
            self.client_out.write(line if line.endswith(b"\n") else line + b"\n")
            self.client_out.flush()

    def _handle_client_line(self, line: bytes) -> None:
        msg = None
        try:
            msg = json.loads(line)
        except ValueError:
            pass
        if (isinstance(msg, dict) and msg.get("method") in CACHEABLE_METHODS
                and "id" in msg):
            key = request_key(msg["method"], msg.get("params"))
            rid = json.dumps(msg["id"])
            cached = self.cache.get(key)
            with self._ids_lock:
                if cached is not None:
                    self._answered[rid] = key
                else:
                    self._pending[rid] = key
            if cached is not None:
                reply = {"jsonrpc": "2.0", "id": msg["id"], "result": cached}
                self._send_client(json.dumps(reply).encode("utf-8"))
        try:
            self.child.stdin.write(line if line.endswith(b"\n") else line + b"\n")
            self.child.stdin.flush()
        except (BrokenPipeError, OSError):
            pass  # child gone; the child-reader side ends the session

    def pump_client(self) -> None:
        for line in self.client_in:
            if line.strip():
                self._handle_client_line(line)
        try:
            self.child.stdin.close()  # client hung up: let the child exit
        except OSError:
            pass

    # ── child → client ──────────────────────────────────────────────────────

    def _handle_child_line(self, line: bytes) -> None:
        msg = None
        try:
            msg = json.loads(line)
        except ValueError:
            pass
        if isinstance(msg, dict) and "id" in msg and "method" not in msg:
            rid = json.dumps(msg["id"])
            with self._ids_lock:
                answered = self._answered.pop(rid, None)
                pending = self._pending.pop(rid, None)
            key = answered or pending
            if key is not None and "result" in msg:
                self.cache.put(key, msg["result"])
            if answered is not None:
                return  # the client already has its answer
        self._send_client(line)

    def pump_child(self) -> None:
        for line in self.child.stdout:
            if line.strip():
                self._handle_child_line(line)


def _child_command() -> list[str]:
    return [sys.executable, "-c", "from ksj_mcp.server import main; main()"]


def main() -> None:
    if os.environ.get("KSJ_FAST_HANDSHAKE", "1").strip() == "0":
        from .server import main as server_main
        server_main()
        return

    cache = HandshakeCache(_data_dir() / CACHE_FILE, build_key())
    child = subprocess.Popen(
        _child_command(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,  # the child's diagnostics go straight to the client log
    )
    relay = Relay(cache, child, sys.stdin.buffer, sys.stdout.buffer)
    reader = threading.Thread(target=relay.pump_client, name="ksj-client-in", daemon=True)
    reader.start()
    relay.pump_child()  # returns when the child exits
    sys.exit(child.wait())


if __name__ == "__main__":
    main()
