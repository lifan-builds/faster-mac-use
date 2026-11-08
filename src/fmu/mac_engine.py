"""Persistent Unix-socket client for the native macOS Accessibility engine (fmu-ax)."""

from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time
from typing import Any

from fmu.telemetry import record_perf_event, sanitize_error

PKG_DIR = Path(__file__).resolve().parent
ROOT = PKG_DIR.parents[1]
DEFAULT_SOCK = "/tmp/fmu-ax.sock"


AGENT_AGY_SWIFT = Path("/Users/lfan/Project/agent/scripts/agy-ax.swift")


def _sync_swift_sources(repo_swift: Path, pkg_swift: Path) -> Path:
    """Ensure repo_swift (src/native/fmu_ax.swift), pkg_swift (src/fmu/native/fmu_ax.swift), and agy-ax.swift never drift."""
    has_repo = repo_swift.is_file()
    has_pkg = pkg_swift.is_file()
    if has_repo and has_pkg:
        try:
            # Only auto-sync when both paths live under the same `src/` tree (not when ROOT is mocked in tests)
            if repo_swift.resolve().parent.parent == pkg_swift.resolve().parent.parent:
                candidates = [p for p in (pkg_swift, repo_swift, AGENT_AGY_SWIFT) if p.is_file() and p.stat().st_size > 1024]
                if candidates:
                    newest = max(candidates, key=lambda p: p.stat().st_mtime)
                    newest_bytes = newest.read_bytes()
                    for target in candidates:
                        try:
                            if not os.path.samefile(newest, target) and target.read_bytes() != newest_bytes:
                                shutil.copy2(newest, target)
                        except OSError:
                            pass
                    return pkg_swift
        except OSError:
            return repo_swift
    if has_repo:
        return repo_swift
    return pkg_swift


def _resolve_ax_binary(
    root_dir: Path | None = None,
    cache_dir: Path | None = None,
) -> Path:
    effective_root = root_dir or ROOT
    repo_bin = effective_root / "bin" / "fmu-ax"
    repo_swift = effective_root / "src" / "native" / "fmu_ax.swift"
    pkg_swift = PKG_DIR / "native" / "fmu_ax.swift"
    swift_src = (
        _sync_swift_sources(repo_swift, pkg_swift)
        if root_dir is None
        else (repo_swift if repo_swift.is_file() else pkg_swift)
    )

    if repo_bin.is_file():
        if not swift_src.is_file() or repo_bin.stat().st_mtime >= swift_src.stat().st_mtime:
            return repo_bin

    if root_dir is None and not repo_swift.is_file():
        which_bin = shutil.which("fmu-ax")
        if which_bin and Path(which_bin).is_file():
            return Path(which_bin)

    user_cache_bin = (
        cache_dir
        or Path(os.environ.get("FMU_HOME", str(Path.home() / ".fmu"))).expanduser() / "bin"
    ) / "fmu-ax"

    # Decide where to compile if swift_src is available
    target_bin = repo_bin if repo_swift.is_file() else user_cache_bin
    if target_bin.is_file() and (
        not swift_src.is_file() or target_bin.stat().st_mtime >= swift_src.stat().st_mtime
    ):
        return target_bin

    if swift_src.is_file():
        try:
            target_bin.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            target_bin = user_cache_bin
            target_bin.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                ["swiftc", "-O", str(swift_src), "-o", str(target_bin)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError as exc:
            if target_bin.is_file():
                return target_bin
            if repo_bin.is_file():
                return repo_bin
            raise RuntimeError(
                f"Cannot compile {swift_src}: 'swiftc' not found. "
                "Install Xcode Command Line Tools with: xcode-select --install"
            ) from exc
        except subprocess.CalledProcessError as exc:
            if target_bin.is_file():
                return target_bin
            if repo_bin.is_file():
                return repo_bin
            err_detail = (exc.stderr or "").strip()
            raise RuntimeError(
                f"Failed to compile {swift_src} with swiftc: {err_detail}"
            ) from exc
        if target_bin.is_file():
            return target_bin
    return repo_bin if repo_bin.is_file() else target_bin


class MacNativeEngine:
    """Drives macOS apps, menus, and dialogs in the background without moving the cursor or stealing focus."""

    def __init__(self, socket_path: str = DEFAULT_SOCK, *, in_daemon: bool = False) -> None:
        self.socket_path = socket_path
        self._in_daemon = in_daemon
        self._daemon_proc: subprocess.Popen[bytes] | None = None

    def ensure_daemon(self) -> None:
        ax_bin = _resolve_ax_binary()
        if not ax_bin.is_file():
            raise FileNotFoundError(f"Native binary not found at {ax_bin}")
        sock_path = Path(self.socket_path)
        if self._can_connect():
            try:
                if ax_bin.stat().st_mtime <= sock_path.stat().st_mtime + 0.5:
                    return
                # Binary was recompiled after the daemon started; restart daemon cleanly
                if self._daemon_proc and self._daemon_proc.poll() is None:
                    self._daemon_proc.terminate()
                else:
                    subprocess.run(
                        ["pkill", "-f", f"fmu-ax --daemon {self.socket_path}"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                sock_path.unlink(missing_ok=True)
            except OSError:
                return
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
        t0 = time.perf_counter()
        if "steps" in req and req["steps"] is not None:
            if not isinstance(req["steps"], list):
                return "Error: 'steps' must be a JSON array of step objects"
            for idx, st in enumerate(req["steps"]):
                if not isinstance(st, dict):
                    return f"Error: step {idx + 1} must be a JSON object"
        was_cold = not self._can_connect()
        t_boot0 = time.perf_counter()
        self.ensure_daemon()
        daemon_boot_ms = (time.perf_counter() - t_boot0) * 1000.0
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
            out = b"".join(chunks).decode("utf-8", errors="replace").strip()
        finally:
            s.close()

        if not req.get("reset"):
            total_ms = (time.perf_counter() - t0) * 1000.0
            first_line = out.splitlines()[0] if out else ""
            ms_m = re.search(r"\bms=([\d.]+)", first_line)
            els_m = re.search(r"\bels=(\d+)", first_line)
            native_ax_ms = float(ms_m.group(1)) if ms_m else 0.0
            els_count = int(els_m.group(1)) if els_m else 0
            ipc_overhead_ms = max(0.0, total_ms - daemon_boot_ms - native_ax_ms)
            steps_list = req.get("steps") or []
            if "= unchanged" in out:
                mode_out = "unchanged"
            elif req.get("diff") is True or any(l.startswith(("+ ", "~ ", "- ")) for l in out.splitlines()[1:6]):
                mode_out = "diff"
            else:
                mode_out = "snapshot"
            errs = [
                sanitize_error(l[len("! errors: "):] if l.startswith("! errors: ") else l)
                for l in out.splitlines()
                if l.startswith(("! errors:", "Error:"))
            ]
            record_perf_event(
                {
                    "surface": "app",
                    "bridge": "ax",
                    "agent": str(req.get("agent") or "default"),
                    "target": str(req.get("app") or ""),
                    "ops": [
                        str(st.get("op") or st.get("action") or "").lower()
                        for st in steps_list
                        if isinstance(st, dict) and (st.get("op") or st.get("action"))
                    ],
                    "step_count": len(steps_list),
                    "els": els_count,
                    "mode_out": mode_out,
                    "out_chars": len(out),
                    "est_tokens": max(1, (len(out) + 3) // 4),
                    "cold_start": was_cold,
                    "error_count": len(errs),
                    "errors": errs[:3],
                    "timings_ms": {
                        "total": round(total_ms, 2),
                        "daemon_boot": round(daemon_boot_ms, 2),
                        "native_ax": round(native_ax_ms, 2),
                        "ipc_overhead": round(ipc_overhead_ms, 2),
                    },
                },
                sync=not self._in_daemon,
            )
        return out

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

