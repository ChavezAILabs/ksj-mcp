"""
Tests for ksj_mcp.fastlaunch — the stdlib-only launcher that answers
`initialize` and list requests from a cache written by the previous run,
so a busy machine no longer makes ksj miss the client's startup timeout (F7).
"""

import io
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from ksj_mcp import fastlaunch
from ksj_mcp.fastlaunch import HandshakeCache, Relay, build_key, request_key

# A stand-in server: answers every request with {"from": "child", ...} after
# an optional delay, echoes nothing for notifications, exits on EOF.
FAKE_CHILD = textwrap.dedent("""
    import json, sys, time
    delay = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue  # the relay passes bad lines through; the server decides
        if "id" in msg and "method" in msg:
            time.sleep(delay)
            out = {"jsonrpc": "2.0", "id": msg["id"],
                   "result": {"from": "child", "method": msg["method"]}}
            sys.stdout.write(json.dumps(out) + "\\n")
            sys.stdout.flush()
""")


def _req(rid, method, params=None):
    return json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                       "params": params or {}}).encode() + b"\n"


INIT = {"protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"}}


def _run(tmp_path, lines, cache=None, delay=0.0):
    script = tmp_path / "fake_child.py"
    script.write_text(FAKE_CHILD)
    child = subprocess.Popen([sys.executable, str(script), str(delay)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    cache = cache or HandshakeCache(tmp_path / "cache.json", "build-1")
    out = io.BytesIO()
    relay = Relay(cache, child, io.BytesIO(b"".join(lines)), out)
    relay.pump_client()   # sends everything, then closes the child's stdin
    relay.pump_child()    # drains the child until it exits
    child.wait()
    replies = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
    return replies, cache


class TestRequestKey:
    def test_initialize_keyed_by_protocol_version_only(self):
        a = request_key("initialize", {**INIT, "clientInfo": {"name": "x"}})
        b = request_key("initialize", INIT)
        assert a == b
        assert a != request_key("initialize", {**INIT, "protocolVersion": "2024-11-05"})

    def test_list_key_ignores_meta(self):
        assert (request_key("tools/list", {"_meta": {"progressToken": 1}})
                == request_key("tools/list", {}))

    def test_list_key_includes_cursor(self):
        assert request_key("tools/list", {"cursor": "a"}) != request_key("tools/list", {})


class TestRelay:
    def test_no_cache_relays_and_records(self, tmp_path):
        replies, cache = _run(tmp_path, [_req(1, "initialize", INIT), _req(2, "tools/list")])
        assert [r["id"] for r in replies] == [1, 2]
        assert all(r["result"]["from"] == "child" for r in replies)
        saved = json.loads((tmp_path / "cache.json").read_text())
        assert saved["build"] == "build-1"
        assert saved["entries"][request_key("initialize", INIT)]["method"] == "initialize"

    def test_cached_answer_sent_once_and_refreshed(self, tmp_path):
        cache = HandshakeCache(tmp_path / "cache.json", "build-1")
        cache.put(request_key("initialize", INIT), {"from": "cache"})
        replies, cache = _run(tmp_path, [_req(1, "initialize", INIT)], cache=cache)
        assert len(replies) == 1                      # child's reply suppressed
        assert replies[0]["result"] == {"from": "cache"}
        assert cache.get(request_key("initialize", INIT))["from"] == "child"

    def test_cached_answer_does_not_wait_for_child(self, tmp_path):
        """The whole point: the client is answered while the child is still
        busy (here: 3 s per request, standing in for the SDK import)."""
        script = tmp_path / "fake_child.py"
        script.write_text(FAKE_CHILD)
        child = subprocess.Popen([sys.executable, str(script), "3"],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        cache = HandshakeCache(tmp_path / "cache.json", "build-1")
        cache.put(request_key("initialize", INIT), {"from": "cache"})
        out = io.BytesIO()
        relay = Relay(cache, child, io.BytesIO(b""), out)
        t = time.monotonic()
        relay._handle_client_line(_req(1, "initialize", INIT))
        elapsed = time.monotonic() - t
        assert json.loads(out.getvalue())["result"] == {"from": "cache"}
        assert elapsed < 1.0
        child.stdin.close()
        relay.pump_child()
        child.wait()

    def test_uncacheable_requests_relayed(self, tmp_path):
        cache = HandshakeCache(tmp_path / "cache.json", "build-1")
        cache.put(request_key("initialize", INIT), {"from": "cache"})
        replies, _ = _run(tmp_path, [_req(1, "initialize", INIT),
                                     _req(2, "tools/call", {"name": "get_stats"})],
                          cache=cache)
        by_id = {r["id"]: r for r in replies}
        assert by_id[2]["result"] == {"from": "child", "method": "tools/call"}

    def test_other_protocol_version_is_a_miss(self, tmp_path):
        cache = HandshakeCache(tmp_path / "cache.json", "build-1")
        cache.put(request_key("initialize", INIT), {"from": "cache"})
        other = {**INIT, "protocolVersion": "2024-11-05"}
        replies, _ = _run(tmp_path, [_req(1, "initialize", other)], cache=cache)
        assert replies[0]["result"]["from"] == "child"

    def test_notifications_and_bad_lines_pass_through(self, tmp_path):
        note = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode() + b"\n"
        replies, _ = _run(tmp_path, [_req(1, "initialize", INIT), note, b"not json\n",
                                     _req(2, "ping")])
        assert [r["id"] for r in replies] == [1, 2]

    def test_string_ids(self, tmp_path):
        cache = HandshakeCache(tmp_path / "cache.json", "build-1")
        cache.put(request_key("tools/list", {}), {"tools": []})
        replies, _ = _run(tmp_path, [_req("abc", "tools/list")], cache=cache)
        assert replies == [{"jsonrpc": "2.0", "id": "abc", "result": {"tools": []}}]


class TestHandshakeCache:
    def test_other_build_ignored(self, tmp_path):
        old = HandshakeCache(tmp_path / "c.json", "ksj-mcp 3.7.0")
        old.put("k", {"v": 1})
        assert HandshakeCache(tmp_path / "c.json", "ksj-mcp 3.8.1").get("k") is None

    def test_corrupt_file_ignored(self, tmp_path):
        (tmp_path / "c.json").write_text("{not json")
        assert HandshakeCache(tmp_path / "c.json", "b").get("k") is None

    def test_build_key_names_versions(self):
        key = build_key()
        assert "ksj-mcp" in key and "mcp" in key and "python" in key


# ── End to end: the installed entry point against the real server ─────────────

def _handshake(env):
    """Run the launcher, do initialize + tools/list, return (seconds to the
    initialize reply, tool count)."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "from ksj_mcp.fastlaunch import main; main()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env,
    )
    t = time.monotonic()
    proc.stdin.write(_req(1, "initialize", INIT))
    proc.stdin.flush()
    first = json.loads(proc.stdout.readline())
    t_init = time.monotonic() - t
    proc.stdin.write(json.dumps({"jsonrpc": "2.0",
                                 "method": "notifications/initialized"}).encode() + b"\n")
    proc.stdin.write(_req(2, "tools/list"))
    proc.stdin.flush()
    tools = json.loads(proc.stdout.readline())
    proc.stdin.close()
    proc.wait(timeout=120)
    assert first["id"] == 1 and "serverInfo" in first["result"]
    return t_init, len(tools["result"]["tools"])


def test_end_to_end_second_start_served_from_cache(tmp_path):
    env = {**os.environ, "KSJ_DATA_DIR": str(tmp_path), "KSJ_FAST_HANDSHAKE": "1"}
    _, n_cold = _handshake(env)
    assert (tmp_path / fastlaunch.CACHE_FILE).exists()
    t_warm, n_warm = _handshake(env)
    assert n_warm == n_cold >= 30
    # Answered before the child could have imported the MCP SDK.
    assert t_warm < 5.0, f"cached initialize took {t_warm:.1f}s"
