"""The IPC deadline terminates only the child owned by its connection."""

from __future__ import annotations

import os
import pickle
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from pcb_world.engine import router_client
from pcb_world.engine.router_client import EngineServerCrashed, _ServerConn
from pcb_world.agent.runner import runner_run_owned_process


def _connection(child: subprocess.Popen, peer: socket.socket, timeout):
    conn = _ServerConn.__new__(_ServerConn)
    conn.sock, conn.proc, conn.pid = peer, child, child.pid
    conn.call_timeout_s = timeout
    conn.tmpdir = tempfile.mkdtemp(prefix="owned-engine-test-")
    conn._stderr_f = open(os.path.join(conn.tmpdir, "stderr"), "a+b")
    return conn


def test_hung_owned_child_is_reaped_and_unrelated_process_survives():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    local, remote = socket.socketpair()
    conn = _connection(child, local, 0.05)
    try:
        with pytest.raises(EngineServerCrashed, match="deadline"):
            conn.request("hung-native-op", None)
        assert child.poll() is not None
        assert sentinel.poll() is None
    finally:
        remote.close()
        conn.kill()
        sentinel.terminate()
        sentinel.wait(timeout=5)


def test_dead_owned_child_reports_crash_and_is_reaped():
    child = subprocess.Popen([sys.executable, "-c", "raise SystemExit(9)"])
    child.wait(timeout=5)
    local, remote = socket.socketpair()
    conn = _connection(child, local, 0.5)
    remote.close()
    try:
        with pytest.raises(EngineServerCrashed, match="died"):
            conn.request("crashed-native-op", None)
        assert child.poll() == 9
    finally:
        conn.kill()


def _read_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        data.extend(sock.recv(size - len(data)))
    return bytes(data)


def _reply_peer(sock: socket.socket, *, drip: bool) -> None:
    try:
        size = struct.unpack(">Q", _read_exact(sock, 8))[0]
        _read_exact(sock, size)
        reply = pickle.dumps({"ok": True, "value": 42}, protocol=pickle.HIGHEST_PROTOCOL)
        frame = struct.pack(">Q", len(reply)) + reply
        for offset in range(0, len(frame), 4 if drip else len(frame)):
            sock.sendall(frame[offset:offset + (4 if drip else len(frame))])
            if drip:
                time.sleep(0.02)
    except OSError:
        pass


def test_slow_dribble_reply_uses_one_absolute_deadline():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    local, remote = socket.socketpair()
    conn = _connection(child, local, 0.05)
    writer = threading.Thread(target=_reply_peer, args=(remote,), kwargs={"drip": True}, daemon=True)
    writer.start()
    started = time.monotonic()
    try:
        with pytest.raises(EngineServerCrashed, match="deadline"):
            conn.request("slow-dribble", None)
        assert time.monotonic() - started < 0.3
        assert child.poll() is not None
    finally:
        remote.close()
        writer.join(timeout=1)
        conn.kill()


def test_late_request_resolves_a_fresh_remaining_budget():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    local, remote = socket.socketpair()
    budget = {"value": 1.0}
    conn = _connection(child, local, lambda: budget["value"])

    def serve_twice():
        _reply_peer(remote, drip=False)
        _reply_peer(remote, drip=True)

    writer = threading.Thread(target=serve_twice, daemon=True)
    writer.start()
    try:
        assert conn.request("early-request", None) == 42
        budget["value"] = 0.05
        with pytest.raises(EngineServerCrashed, match="deadline"):
            conn.request("late-candidate", None)
        assert child.poll() is not None
    finally:
        remote.close()
        writer.join(timeout=1)
        conn.kill()


def test_startup_obeys_short_caller_deadline(monkeypatch, tmp_path):
    server = tmp_path / "sleep_server.py"
    server.write_text("import time; time.sleep(60)\n", encoding="utf-8")
    monkeypatch.setattr(router_client, "_SERVER_SCRIPT", str(server))
    import pcb_world.engine as engine_module
    monkeypatch.setattr(engine_module, "ensure_router_provenance", lambda: None)
    captured = []
    original_popen = subprocess.Popen

    def capture(*args, **kwargs):
        proc = original_popen(*args, **kwargs)
        captured.append(proc)
        return proc

    monkeypatch.setattr(router_client.subprocess, "Popen", capture)
    started = time.monotonic()
    with pytest.raises(EngineServerCrashed, match="startup timed out"):
        _ServerConn(call_timeout_s=0.05)
    assert time.monotonic() - started < 0.5
    assert captured and captured[0].poll() is not None


def _pid_is_gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def test_verifier_timeout_kills_only_its_process_tree(tmp_path):
    parent_pid_file = tmp_path / "parent.pid"
    child_pid_file = tmp_path / "grandchild.pid"
    sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    grandchild_code = (
        "import os,sys,time,pathlib; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
        "time.sleep(60)"
    )
    parent_code = (
        "import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,'-c',"
        "sys.argv[1],sys.argv[2]]); "
        "pathlib.Path(sys.argv[3]).write_text(str(__import__('os').getpid())); time.sleep(60)"
    )
    # The helper gives each verifier a new process group, inherited by its engine child.
    proc_pid = None
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            runner_run_owned_process(
                [sys.executable, "-c", parent_code, grandchild_code,
                 str(child_pid_file), str(parent_pid_file)],
                timeout_s=0.2,
            )
        assert parent_pid_file.exists() and child_pid_file.exists()
        proc_pid = int(parent_pid_file.read_text())
        child_pid = int(child_pid_file.read_text())
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not (
            _pid_is_gone(proc_pid) and _pid_is_gone(child_pid)
        ):
            time.sleep(0.02)
        assert _pid_is_gone(proc_pid)
        assert _pid_is_gone(child_pid)
        assert sentinel.poll() is None
    finally:
        sentinel.terminate()
        sentinel.wait(timeout=5)
