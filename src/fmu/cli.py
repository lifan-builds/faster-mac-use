"""CLI, Warm Daemon & MCP Server entrypoint for Faster Mac Use (fmu)."""

from __future__ import annotations
import json
from pathlib import Path
import sys

_SRC_DIR = str(Path(__file__).resolve().parents[1])
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from fmu.cua_runtime import (
    DEFAULT_RUNTIME_SOCK,
    FasterMacUseRuntime,
    MCP_TOOL_SCHEMA,
    execute_via_daemon,
    serve_runtime_daemon,
)
from fmu.mac_engine import MacNativeEngine


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
                text = execute_via_daemon(args, entry)
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


USAGE_TEXT = """Usage:
  fmu '<json>'            Execute a web, macOS AX, REPL, or parallel CUA request
  fmu mcp                 Run as an MCP stdio server
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
        from fmu import __version__

        sys.stdout.write(f"faster-mac-use {__version__}\n")
        return
    if len(sys.argv) >= 2 and sys.argv[1] in ("mcp", "--mcp"):
        serve_mcp(bin_path)
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
        and not req.get("shutdown_daemon")
    ):
        print(MacNativeEngine().execute(req))
        return

    entry = bin_path if (bin_path and bin_path.is_file()) else Path(__file__).resolve()
    print(execute_via_daemon(req, entry))


if __name__ == "__main__":
    main()

