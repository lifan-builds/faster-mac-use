"""CLI, Warm Daemon & MCP Server entrypoint for Faster Mac Use (fmu)."""

from __future__ import annotations
import json
from pathlib import Path
import sys

from fmu.cua_runtime import (
    DEFAULT_RUNTIME_SOCK,
    FasterMacUseRuntime,
    MCP_TOOL_SCHEMA,
    execute_via_daemon,
    serve_runtime_daemon,
)
from fmu.mac_engine import MacNativeEngine


def serve_mcp() -> None:
    runtime = FasterMacUseRuntime()
    try:
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
                resp = {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "faster-mac-use", "version": "0.1.0"},
                    },
                }
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
            elif method == "tools/list":
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
                    text = runtime.execute(args)
                    is_err = False
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
    finally:
        runtime.shutdown()


def main(bin_path: Path | None = None) -> None:
    if len(sys.argv) >= 2 and sys.argv[1] in ("mcp", "--mcp"):
        serve_mcp()
        return
    if len(sys.argv) >= 2 and sys.argv[1] in ("--daemon", "daemon"):
        sock = sys.argv[2] if len(sys.argv) >= 3 else DEFAULT_RUNTIME_SOCK
        serve_runtime_daemon(sock)
        return
    if len(sys.argv) >= 2 and sys.argv[1] in ("--reset", "reset"):
        req: dict[str, object] = {"reset": True}
    elif len(sys.argv) >= 2 and sys.argv[1] in ("--shutdown", "shutdown"):
        req = {"shutdown_daemon": True}
    elif len(sys.argv) >= 2:
        req = json.loads(sys.argv[1])
    elif not sys.stdin.isatty():
        raw_stdin = sys.stdin.read().strip()
        if not raw_stdin:
            sys.stderr.write("Usage: fmu '<json>' | fmu mcp | fmu --reset | fmu --daemon [sock]\n")
            sys.exit(2)
        req = json.loads(raw_stdin)
    else:
        sys.stderr.write("Usage: fmu '<json>' | fmu mcp | fmu --reset | fmu --daemon [sock]\n")
        sys.exit(2)

    surface = req.get("surface") or ("app" if "app" in req else "web")
    if (
        surface == "app"
        and "code" not in req
        and "parallel" not in req
        and not req.get("getCursors")
        and not req.get("reset")
        and not req.get("shutdown_daemon")
    ):
        print(MacNativeEngine().execute(req))
        return

    entry = bin_path or (Path(__file__).resolve().parents[2] / "bin" / "fmu")
    print(execute_via_daemon(req, entry))


if __name__ == "__main__":
    main()
