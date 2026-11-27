# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Faster Mac Use; Section 7 attribution terms in ATTRIBUTION.md.
"""CLI, Warm Daemon & MCP Server entrypoint for Faster Mac Use (fmu)."""

from __future__ import annotations
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from typing import Any

_SRC_DIR = str(Path(__file__).resolve().parents[1])
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

DEFAULT_RUNTIME_SOCK = os.environ.get("FMU_RUNTIME_SOCK", "/tmp/fmu-runtime.sock")
_CODE_WAIT_MS_RE = re.compile(
    r"(?:waitFor|wait_for|waitUntil|wait)\s*\([^)]*?(\b\d{3,6}\b)|timeoutMs\s*[:=]\s*(\d{3,6})"
)


def _estimate_request_wait_s(req: dict[str, Any]) -> float:
    extra_s = 0.0
    for st_item in req.get("steps") or []:
        if isinstance(st_item, dict):
            extra_s += float(
                st_item.get("timeoutMs") or st_item.get("timeout") or st_item.get("ms") or st_item.get("wait") or 0
            ) / 1000.0
    code_str = str(req.get("code") or "")
    if code_str:
        for m in _CODE_WAIT_MS_RE.finditer(code_str):
            raw_ms = m.group(1) or m.group(2)
            if raw_ms:
                extra_s += min(30.0, float(raw_ms) / 1000.0)
    par = req.get("parallel")
    if isinstance(par, list) and par:
        sub_waits = [_estimate_request_wait_s(sub) for sub in par if isinstance(sub, dict)]
        extra_s = max(extra_s, 30.0, (max(sub_waits) + 15.0) if sub_waits else 30.0)
    return extra_s


def can_connect_runtime_daemon(socket_path: str = DEFAULT_RUNTIME_SOCK) -> bool:
    if not Path(socket_path).exists():
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(0.15)
        s.connect(socket_path)
        s.close()
        return True
    except OSError:
        s.close()
        return False


def execute_via_daemon(
    req: dict[str, Any],
    fmu_bin_path: str | Path,
    socket_path: str = DEFAULT_RUNTIME_SOCK,
) -> str:
    from fmu.mac_engine import (
        USER_STOPPED_ERROR,
        MacNativeEngine,
        clear_user_stop,
        engage_user_stop,
        get_menubar_status,
        highlight_target_window,
        is_standalone_stop_control_code,
        is_standalone_stop_control_request,
        is_user_stopped,
    )

    has_action_payload = bool(
        req.get("steps")
        or req.get("parallel")
        or req.get("app")
        or req.get("url")
        or (req.get("code") and not is_standalone_stop_control_code(str(req.get("code"))))
    )
    is_control = (not has_action_payload) and (
        "liveView" in req
        or "menuAction" in req
        or "menuOutline" in req
        or req.get("listTabs") not in (None, False)
        or req.get("listExtensions") not in (None, False)
        or any(
            req.get(k)
            for k in (
                "stop",
                "resume",
                "statusBar",
                "getStatus",
                "clickStatusBar",
                "highlightWindow",
                "togglePreview",
                "dumpUiSnapshots",
                "shutdown_daemon",
                "perfReport",
                "bridgeStatus",
                "closeAgentTabs",
                "closeMyTabs",
                "agentSessions",
            )
        )
    )
    if not is_control and is_user_stopped():
        if not is_standalone_stop_control_request(req):
            return USER_STOPPED_ERROR

    if req.get("clickStatusBar"):
        return MacNativeEngine().execute({"clickStatusBar": True})
    if req.get("highlightWindow"):
        return highlight_target_window()
    if "liveView" in req:
        return MacNativeEngine().execute({"liveView": bool(req["liveView"])})
    if req.get("menuOutline") or req.get("menuAction"):
        return MacNativeEngine().execute({k: req[k] for k in ("menuOutline", "menuAction") if req.get(k)})
    if req.get("togglePreview"):
        return MacNativeEngine().execute({"togglePreview": True})
    if req.get("dumpUiSnapshots"):
        return MacNativeEngine().execute({"dumpUiSnapshots": str(req["dumpUiSnapshots"])})
    if req.get("stop"):
        return engage_user_stop()
    if (
        req.get("resume")
        and not req.get("reset")
        and not req.get("app")
        and not req.get("url")
        and not req.get("code")
        and not req.get("steps")
        and not req.get("parallel")
    ):
        return clear_user_stop()
    if req.get("statusBar") or req.get("getStatus"):
        return get_menubar_status()

    if not can_connect_runtime_daemon(socket_path):
        if req.get("shutdown_daemon"):
            return "# fmu daemon stopped"
        if req.get("reset") and len(req) == 1 or (req.get("reset") and req.get("resume") and len(req) == 2):
            if req.get("resume"):
                clear_user_stop()
            MacNativeEngine().reset()
            return "# fmu runtime reset"
        import fcntl

        spawn_lock_path = Path(f"{socket_path}.spawn.lock")
        with open(spawn_lock_path, "a+", encoding="utf-8") as spawn_lock_fp:
            fcntl.flock(spawn_lock_fp.fileno(), fcntl.LOCK_EX)
            try:
                if not can_connect_runtime_daemon(socket_path):
                    t_spawn0 = time.perf_counter()
                    proc = subprocess.Popen(
                        [sys.executable, str(fmu_bin_path), "--daemon", socket_path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        stdin=subprocess.DEVNULL,
                        start_new_session=True,
                        close_fds=True,
                    )
                    proc._child_created = False
                    for _ in range(80):
                        if can_connect_runtime_daemon(socket_path):
                            break
                        exit_code = proc.poll()
                        if exit_code is not None:
                            raise RuntimeError(f"DAEMON_START_EXITED: fmu daemon exited with code {exit_code} before readiness")
                        time.sleep(0.015)
                    else:
                        raise TimeoutError("DAEMON_START_TIMEOUT: fmu daemon did not become ready")
                    from fmu.telemetry import record_perf_event

                    record_perf_event(
                        {
                            "surface": "daemon",
                            "bridge": "unix_sock",
                            "target": Path(socket_path).name,
                            "cold_start": True,
                            "timings_ms": {"total": round((time.perf_counter() - t_spawn0) * 1000.0, 2)},
                        },
                        sync=True,
                    )
            finally:
                try:
                    fcntl.flock(spawn_lock_fp.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass

    payload = (json.dumps(req) + "\n").encode("utf-8")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connected_to_daemon = False
    try:
        s.settimeout(1.5)
        s.connect(socket_path)
        connected_to_daemon = True
        s.sendall(payload)
        s.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        s.settimeout(0.05)
        extra_wait_s = _estimate_request_wait_s(req)
        timeout_budget_s = min(180.0, max(45.0, 25.0 + extra_wait_s))
        deadline = time.perf_counter() + timeout_budget_s
        while True:
            if not is_control and is_user_stopped():
                if not is_standalone_stop_control_request(req):
                    return USER_STOPPED_ERROR
            try:
                buf = s.recv(16384)
            except socket.timeout:
                if time.perf_counter() >= deadline:
                    raise TimeoutError(
                        f"Timed out after {timeout_budget_s:.0f}s waiting for fmu runtime daemon"
                    )
                continue
            if not buf:
                break
            chunks.append(buf)
        return b"".join(chunks).decode("utf-8", errors="replace").strip()
    except TimeoutError:
        raise
    except OSError:
        if connected_to_daemon:
            raise
        from fmu.cua_runtime import FasterMacUseRuntime

        rt = FasterMacUseRuntime()
        rt._is_mcp_stdio = False
        try:
            return rt.execute(req)
        finally:
            rt.shutdown()
    finally:
        s.close()


def execute_traced(req: dict[str, Any], runner: Any) -> str:
    trace_path = os.environ.get("FMU_TRACE", "").strip()
    if not trace_path:
        return runner(req)
    from fmu.replay import record_trace_step, with_trace_bounds

    traced = with_trace_bounds(req)
    t0 = time.perf_counter()
    out = runner(traced)
    record_trace_step(trace_path, traced, out, (time.perf_counter() - t0) * 1000.0)
    return out


def serve_mcp(bin_path: Path | None = None) -> None:
    entry = bin_path if (bin_path and bin_path.is_file()) else Path(__file__).resolve()
    while True:
        raw = sys.stdin.readline()
        if not raw:
            break
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            from fmu import ATTRIBUTION, __version__

            resp = {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "faster-mac-use", "title": ATTRIBUTION, "version": __version__},
                },
            }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
        elif method == "tools/list":
            from fmu.cua_runtime import MCP_TOOL_SCHEMA

            resp = {"jsonrpc": "2.0", "id": mid, "result": {"tools": [MCP_TOOL_SCHEMA]}}
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
        elif method == "ping":
            resp = {"jsonrpc": "2.0", "id": mid, "result": {}}
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
        elif method == "tools/call":
            params = msg.get("params", {})
            args = params.get("arguments", {})
            try:
                text = execute_traced(args, lambda r: execute_via_daemon(r, entry))
                is_err = text.startswith("Error: [USER_STOPPED]")
            except Exception as exc:
                text = f"Error: {exc}"
                is_err = True
            resp = {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {"content": [{"type": "text", "text": text}], "isError": is_err},
            }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
        elif mid is not None:
            resp = {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


USAGE_TEXT = """Usage:
  fmu '<json>'            Execute a web, macOS AX, REPL, or parallel CUA request
  fmu mcp                 Run as an MCP stdio server
  fmu setup [flags]       Install the bundled browser-routing skill & Antigravity plugin (--antigravity|--claude|--codex|--cursor|--skill-only|--all)
  fmu --status            Show macOS menu bar & agent working/stopped status
  fmu --highlight         Flash neon highlight border around active target window on screen
  fmu --stop              Immediately stop active fmu agent work (same as menu bar Stop)
  fmu --resume            Clear user stop latch and resume fmu (same as menu bar Resume)
  fmu --live-view on|off  Show or permanently hide the floating Live View monitor (saved in ~/.fmu/prefs.json)
  fmu --perf [days]       Summarize recent fmu performance & error logs (~/.fmu/logs)
  fmu --perf --clear      Remove all recorded fmu session/perf logs (~/.fmu/logs/perf-*.jsonl)
  fmu replay <trace.jsonl> [-o out.html|out.svg]
                          Render an FMU_TRACE=<file> (or perf log) run as an HTML/animated-SVG replay
                          with @uid bounds, +/~/- diff counts, and per-step ms HUD
  fmu --reset             Reset warm browser & macOS AX daemon state
  fmu --shutdown          Stop background runtime daemon
  fmu --daemon [sock]     Run the warm runtime socket daemon
  fmu --version           Show version
  fmu --help              Show this help message
"""


def main(bin_path: Path | None = None) -> None:
    if len(sys.argv) >= 2 and sys.argv[1] in ("-h", "--help", "help"):
        sys.stdout.write(USAGE_TEXT)
        return
    if len(sys.argv) >= 2 and sys.argv[1] in ("-v", "--version", "version"):
        from fmu import ATTRIBUTION, __version__

        sys.stdout.write(f"faster-mac-use {__version__}\n{ATTRIBUTION}\nLicense: AGPL-3.0-only with Section 7 attribution terms (ATTRIBUTION.md)\n")
        return
    if len(sys.argv) >= 2 and sys.argv[1] in ("--status", "status", "--menubar"):
        from fmu.mac_engine import get_menubar_status

        print(get_menubar_status())
        return
    if len(sys.argv) >= 2 and sys.argv[1] in ("--highlight", "highlight"):
        from fmu.mac_engine import highlight_target_window

        print(highlight_target_window())
        return
    if len(sys.argv) >= 2 and sys.argv[1] in ("--stop", "stop"):
        from fmu.mac_engine import engage_user_stop

        print(engage_user_stop())
        return
    if len(sys.argv) >= 2 and sys.argv[1] in ("--resume", "resume"):
        from fmu.mac_engine import clear_user_stop

        print(clear_user_stop())
        return
    if len(sys.argv) >= 2 and sys.argv[1] in ("--live-view", "live-view"):
        if len(sys.argv) == 3 and sys.argv[2] in ("on", "off"):
            from fmu.mac_engine import MacNativeEngine

            print(MacNativeEngine().execute({"liveView": sys.argv[2] == "on"}))
            return
        if len(sys.argv) < 3:
            sys.stderr.write("Error: --live-view requires a value: expected 'on' or 'off'.\nUsage: fmu --live-view on|off\n")
        else:
            bad_val = " ".join(sys.argv[2:])
            sys.stderr.write(
                f"Error: Invalid --live-view value {bad_val!r}: expected 'on' or 'off'.\nUsage: fmu --live-view on|off\n"
            )
        sys.exit(2)
    if len(sys.argv) >= 2 and sys.argv[1] in ("--clear-logs", "clear-logs"):
        from fmu.telemetry import clear_perf_logs, get_log_dir

        removed = clear_perf_logs()
        sys.stdout.write(f'# fmu-perf cleared={removed} logs="{get_log_dir()}"\n')
        return
    if len(sys.argv) >= 2 and sys.argv[1] in ("--perf", "--stats", "perf", "stats"):
        from fmu.telemetry import build_perf_report, clear_perf_logs, get_log_dir

        if "--clear" in sys.argv[2:] or "clear" in sys.argv[2:]:
            removed = clear_perf_logs()
            sys.stdout.write(f'# fmu-perf cleared={removed} logs="{get_log_dir()}"\n')
            return
        days = 7
        fmt = "json" if "--json" in sys.argv[2:] else "markdown"
        for arg in sys.argv[2:]:
            if arg.isdigit():
                days = max(1, int(arg))
        sys.stdout.write(build_perf_report(days=days, fmt=fmt) + "\n")
        return
    if len(sys.argv) >= 2 and sys.argv[1] in ("replay", "--replay", "trace-html"):
        args = sys.argv[2:]
        if any(a in ("-h", "--help", "help") for a in args):
            sys.stdout.write(
                "Usage: fmu replay <trace.jsonl> [-o out.html|out.svg]\n"
                "  Render an FMU_TRACE=<file> (or perf log) run as an HTML/animated-SVG replay\n"
                "  with @uid bounds, +/~/- diff counts, and per-step ms HUD.\n"
            )
            return
        from fmu.replay import render_trace

        out_path = None
        if "-o" in args:
            i = args.index("-o")
            out_path = args[i + 1] if i + 1 < len(args) else None
            args = args[:i] + args[i + 2 :]
        if len(args) != 1 or (("-o" in sys.argv[2:]) and not out_path):
            sys.stderr.write("Usage: fmu replay <trace.jsonl> [-o out.html|out.svg]\n")
            sys.exit(2)
        try:
            dest = render_trace(args[0], out_path)
        except (OSError, ValueError) as exc:
            sys.stderr.write(f"Error: {exc}\n")
            sys.exit(1)
        sys.stdout.write(f'# fmu-replay out="{dest}"\n')
        return
    if len(sys.argv) >= 2 and sys.argv[1] in ("setup", "install-skill", "--install-skill"):
        from fmu.setup_cmd import run_setup

        sys.exit(run_setup(sys.argv[2:]))
    if len(sys.argv) >= 2 and sys.argv[1] in ("mcp", "--mcp"):
        serve_mcp(bin_path)
        return
    if len(sys.argv) >= 2 and sys.argv[1] in ("--daemon", "daemon"):
        from fmu.cua_runtime import serve_runtime_daemon

        sock = sys.argv[2] if len(sys.argv) >= 3 else DEFAULT_RUNTIME_SOCK
        serve_runtime_daemon(sock)
        return
    if len(sys.argv) >= 2 and sys.argv[1] in ("--reset", "reset"):
        from fmu.mac_engine import clear_user_stop

        clear_user_stop()
        req: dict[str, object] = {"reset": True, "resume": True}
    elif len(sys.argv) >= 2 and sys.argv[1] in ("--shutdown", "shutdown"):
        req = {"shutdown_daemon": True}
    elif len(sys.argv) >= 2:
        try:
            req = json.loads(sys.argv[1])
        except ValueError as exc:
            sys.stderr.write(f"Error: Invalid JSON argument: {exc}\n{USAGE_TEXT}")
            sys.exit(2)
    elif not sys.stdin.isatty():
        raw_stdin = sys.stdin.read().strip()
        if not raw_stdin:
            sys.stderr.write(USAGE_TEXT)
            sys.exit(2)
        try:
            req = json.loads(raw_stdin)
        except ValueError as exc:
            sys.stderr.write(f"Error: Invalid JSON on stdin: {exc}\n{USAGE_TEXT}")
            sys.exit(2)
    else:
        sys.stderr.write(USAGE_TEXT)
        sys.exit(2)

    if not isinstance(req, dict):
        sys.stderr.write("Error: Request payload must be a JSON object.\n")
        sys.exit(2)

    surface = req.get("surface") or ("app" if "app" in req else "web")
    if (
        surface == "app"
        and "code" not in req
        and "parallel" not in req
        and not req.get("getCursors")
        and not req.get("reset")
        and not req.get("stop")
        and not req.get("resume")
        and not req.get("statusBar")
        and not req.get("getStatus")
        and not req.get("clickStatusBar")
        and not req.get("shutdown_daemon")
    ):
        from fmu.mac_engine import MacNativeEngine

        print(execute_traced(req, MacNativeEngine().execute))
        return

    entry = bin_path if (bin_path and bin_path.is_file()) else Path(__file__).resolve()
    print(execute_traced(req, lambda r: execute_via_daemon(r, entry)))


if __name__ == "__main__":
    main()

