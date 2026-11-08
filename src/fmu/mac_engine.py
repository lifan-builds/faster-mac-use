"""Persistent Unix-socket client for the native macOS Accessibility engine (fmu-ax)."""

from __future__ import annotations
import json
from pathlib import Path
import socket
import subprocess
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOCK = "/tmp/fmu-ax.sock"


def _resolve_ax_binary() -> Path:
    candidates = [
        ROOT / "bin" / "fmu-ax",
        ROOT / "scripts" / "fmu-ax",
        ROOT / "scripts" / "agy-ax",
        Path.home() / "Project" / "faster-mac-use" / "bin" / "fmu-ax",
        Path.home() / "Project" / "agent" / "scripts" / "agy-ax",
    ]
    for c in candidates:
        if c.is_file():
            return c
    swift_src = ROOT / "src" / "native" / "fmu_ax.swift"
    target_bin = ROOT / "bin" / "fmu-ax"
    if swift_src.is_file():
        target_bin.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["swiftc", "-O", str(swift_src), "-o", str(target_bin)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if target_bin.is_file():
            return target_bin
    return candidates[0]


class MacNativeEngine:
    """Drives macOS apps, menus, and dialogs in the background without moving the cursor or stealing focus."""

    def __init__(self, socket_path: str = DEFAULT_SOCK) -> None:
        self.socket_path = socket_path
        self._daemon_proc: subprocess.Popen[bytes] | None = None

    def ensure_daemon(self) -> None:
        if self._can_connect():
            return
        ax_bin = _resolve_ax_binary()
        if not ax_bin.is_file():
            raise FileNotFoundError(f"Native binary not found at {ax_bin}")
        proc = subprocess.Popen(
            [str(ax_bin), "--daemon", self.socket_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        proc._child_created = False  # Detach intentional background daemon from Popen.__del__ warning
        self._daemon_proc = proc
        for _ in range(60):
            if self._can_connect():
                return
            time.sleep(0.02)

    def _can_connect(self) -> bool:
        if not Path(self.socket_path).exists():
            return False
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(0.15)
            s.connect(self.socket_path)
            s.close()
            return True
        except OSError:
            s.close()
            return False

    def execute(self, req: dict[str, Any]) -> str:
        self.ensure_daemon()
        payload = (json.dumps(req) + "\n").encode("utf-8")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(15.0)
            s.connect(self.socket_path)
            s.sendall(payload)
            s.shutdown(socket.SHUT_WR)
            chunks: list[bytes] = []
            while True:
                buf = s.recv(16384)
                if not buf:
                    break
                chunks.append(buf)
            return b"".join(chunks).decode("utf-8", errors="replace").strip()
        finally:
            s.close()

    def reset(self) -> str:
        if not self._can_connect():
            return "# fmu-ax state reset"
        return self.execute({"reset": True})

    def shutdown(self, stop_daemon: bool = False) -> None:
        if stop_daemon:
            if self._daemon_proc and self._daemon_proc.poll() is None:
                self._daemon_proc.terminate()
            try:
                Path(self.socket_path).unlink(missing_ok=True)
            except OSError:
                pass
