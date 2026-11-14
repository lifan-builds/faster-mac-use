"""Persistent Unix-socket client for the native macOS Accessibility engine (fmu-ax)."""

from __future__ import annotations
import json
import os
from pathlib import Path
import queue
import re
import shutil
import socket
import subprocess
import threading
import time
from typing import Any

from fmu.telemetry import record_perf_event, sanitize_error

PKG_DIR = Path(__file__).resolve().parent
ROOT = PKG_DIR.parents[1]
DEFAULT_SOCK = "/tmp/fmu-ax.sock"
DEFAULT_STOP_FILE = "/tmp/fmu-stop.json"

USER_STOPPED_ERROR = (
    "Error: [USER_STOPPED] User clicked Stop in the macOS menu bar. "
    "Do NOT retry or call fmu again; stop immediately and report to the user. "
    "(Click '🛑 fmu Stopped ▶ Resume' in the macOS menu bar or run 'fmu --resume' to re-enable.)"
)


def get_stop_file() -> Path:
    return Path(os.environ.get("FMU_STOP_FILE", DEFAULT_STOP_FILE))


def is_user_stopped() -> bool:
    return get_stop_file().exists()


def interruptible_sleep(seconds: float) -> bool:
    """Sleep up to ``seconds`` in 20ms slices; returns False immediately if user clicked Stop."""
    deadline = time.perf_counter() + max(0.0, float(seconds))
    while time.perf_counter() < deadline:
        if is_user_stopped():
            return False
        rem = deadline - time.perf_counter()
        if rem <= 0:
            break
        time.sleep(min(0.02, rem))
    return not is_user_stopped()


_menubar_queue: queue.Queue[dict[str, Any]] = queue.Queue()
_menubar_worker_started = False
_menubar_worker_lock = threading.Lock()
_daemon_boot_lock = threading.Lock()


def _menubar_worker_loop() -> None:
    engine = MacNativeEngine(in_daemon=True)
    pending_next: dict[str, Any] | None = None
    while True:
        if pending_next is not None:
            item = pending_next
            pending_next = None
        else:
            item = _menubar_queue.get()
        if item.get("state") == "frame":
            while True:
                try:
                    nxt = _menubar_queue.get_nowait()
                except queue.Empty:
                    break
                if nxt.get("state") == "frame":
                    item = nxt
                else:
                    pending_next = nxt
                    break
        try:
            engine.execute({"statusUpdate": item})
        except Exception:
            pass


def notify_menubar_status(
    state: str,
    *,
    surface: str = "web",
    target: str = "",
    op: str = "",
    agent: str = "default",
    step_count: int = 0,
    cursor_x: int | None = None,
    cursor_y: int | None = None,
    cursor_label: str | None = None,
    win_w: int | None = None,
    win_h: int | None = None,
    preview_path: str | None = None,
    preview_nodes: list[dict[str, Any]] | None = None,
    is_headless: bool | None = None,
    sync: bool = False,
) -> None:
    """Publish real-time working/idle status & live PiP window preview to the macOS menu bar item in fmu-ax."""
    global _menubar_worker_started
    if os.environ.get("FMU_MENUBAR") == "0":
        return
    payload: dict[str, Any] = {
        "state": state,
        "surface": surface,
        "target": target,
        "op": op,
        "agent": agent,
        "stepCount": step_count,
    }
    if cursor_x is not None:
        payload["cursorX"] = cursor_x
    if cursor_y is not None:
        payload["cursorY"] = cursor_y
    if cursor_label:
        payload["cursorLabel"] = cursor_label
    if win_w is not None:
        payload["winW"] = win_w
    if win_h is not None:
        payload["winH"] = win_h
    if preview_path:
        payload["previewPath"] = preview_path
    if preview_nodes:
        payload["previewNodes"] = preview_nodes
    if is_headless is not None:
        payload["isHeadless"] = is_headless
    if sync:
        try:
            MacNativeEngine(in_daemon=True).execute({"statusUpdate": payload})
        except Exception:
            pass
        return
    with _menubar_worker_lock:
        if not _menubar_worker_started:
            t = threading.Thread(
                target=_menubar_worker_loop,
                name="fmu-menubar-status",
                daemon=True,
            )
            t.start()
            _menubar_worker_started = True
    _menubar_queue.put_nowait(payload)


def highlight_target_window(socket_path: str = DEFAULT_SOCK) -> str:
    try:
        return MacNativeEngine(socket_path=socket_path).execute({"highlightWindow": True})
    except Exception:
        return '# fmu-menubar state="idle" title="⚪ fmu Idle" stopped=false highlight=false'


def engage_user_stop(
    reason: str = "User stopped fmu",
    socket_path: str = DEFAULT_SOCK,
) -> str:
    stop_file = get_stop_file()
    try:
        stop_file.write_text(
            json.dumps({"stopped": True, "ts": time.time(), "reason": reason}) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    try:
        return MacNativeEngine(socket_path=socket_path).execute({"stop": True})
    except Exception:
        return '# fmu-menubar state="stopped" title="🛑 fmu Stopped  ▶ Resume" stopped=true'


def clear_user_stop(socket_path: str = DEFAULT_SOCK) -> str:
    try:
        get_stop_file().unlink(missing_ok=True)
    except OSError:
        pass
    try:
        return MacNativeEngine(socket_path=socket_path).execute({"resume": True})
    except Exception:
        return '# fmu-menubar state="idle" title="🤖 fmu" stopped=false'


def get_menubar_status(socket_path: str = DEFAULT_SOCK) -> str:
    try:
        return MacNativeEngine(socket_path=socket_path).execute({"statusBar": True})
    except Exception:
        stopped = is_user_stopped()
        state = "stopped" if stopped else "idle"
        title = "🛑 fmu Stopped  ▶ Resume" if stopped else "🤖 fmu"
        return f'# fmu-menubar state="{state}" title="{title}" stopped={str(stopped).lower()}'


PRIMARY_FMU_ROOT = Path("/Users/lfan/Project/faster-mac-use")
AGENT_FMU_SWIFT = Path("/Users/lfan/Project/agent/scripts/fmu/native/fmu_ax.swift")
AGENT_AGY_SWIFT = Path("/Users/lfan/Project/agent/scripts/agy-ax.swift")


def _sync_swift_sources(repo_swift: Path, pkg_swift: Path) -> Path:
    """Ensure repo_swift, pkg_swift, agent/scripts/fmu/native/fmu_ax.swift, and agy-ax.swift never drift."""
    has_repo = repo_swift.is_file()
    has_pkg = pkg_swift.is_file()
    if has_pkg or has_repo:
        try:
            all_paths = [
                pkg_swift,
                repo_swift,
                PRIMARY_FMU_ROOT / "src" / "fmu" / "native" / "fmu_ax.swift",
                PRIMARY_FMU_ROOT / "src" / "native" / "fmu_ax.swift",
                AGENT_FMU_SWIFT,
                AGENT_AGY_SWIFT,
            ]
            seen_inodes: set[tuple[int, int]] = set()
            candidates: list[tuple[Path, os.stat_result]] = []
            for p in all_paths:
                if p.is_file():
                    st = p.stat()
                    if st.st_size > 1024:
                        inode_key = (st.st_dev, st.st_ino)
                        if inode_key not in seen_inodes:
                            seen_inodes.add(inode_key)
                            candidates.append((p, st))
            if len(candidates) > 1:
                mtimes = {st.st_mtime_ns for _, st in candidates}
                sizes = {st.st_size for _, st in candidates}
                if len(mtimes) > 1 or len(sizes) > 1:
                    newest_path, _ = max(candidates, key=lambda item: item[1].st_mtime_ns)
                    newest_bytes = newest_path.read_bytes()
                    for target, _ in candidates:
                        if target == newest_path:
                            continue
                        try:
                            if target.read_bytes() != newest_bytes:
                                shutil.copy2(newest_path, target)
                            else:
                                os.utime(
                                    target,
                                    ns=(newest_path.stat().st_atime_ns, newest_path.stat().st_mtime_ns),
                                )
                        except OSError:
                            pass
            if has_pkg:
                return pkg_swift
        except OSError:
            pass
    if has_repo:
        return repo_swift
    return pkg_swift


def _resolve_ax_binary(
    root_dir: Path | None = None,
    cache_dir: Path | None = None,
) -> Path:
    if root_dir is None and (PRIMARY_FMU_ROOT / "src" / "fmu" / "native" / "fmu_ax.swift").is_file():
        effective_root = PRIMARY_FMU_ROOT
    else:
        effective_root = root_dir or ROOT
    repo_bin = effective_root / "bin" / "fmu-ax"
    repo_swift = effective_root / "src" / "native" / "fmu_ax.swift"
    pkg_swift = PKG_DIR / "native" / "fmu_ax.swift"
    swift_src = (
        _sync_swift_sources(repo_swift, pkg_swift)
        if root_dir is None
        else (repo_swift if repo_swift.is_file() else pkg_swift)
    )

    user_cache_bin = (
        cache_dir
        or Path(os.environ.get("FMU_HOME", str(Path.home() / ".fmu"))).expanduser() / "bin"
    ) / "fmu-ax"

    if repo_bin.is_file():
        if not swift_src.is_file() or repo_bin.stat().st_mtime >= swift_src.stat().st_mtime:
            if root_dir is None and user_cache_bin.is_file():
                try:
                    if user_cache_bin.stat().st_mtime < repo_bin.stat().st_mtime:
                        shutil.copy2(repo_bin, user_cache_bin)
                except OSError:
                    pass
            return repo_bin

    if root_dir is None and not repo_swift.is_file():
        which_bin = shutil.which("fmu-ax")
        if which_bin and Path(which_bin).is_file():
            return Path(which_bin)

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
            if root_dir is None and target_bin == repo_bin and user_cache_bin.parent.is_dir():
                try:
                    shutil.copy2(repo_bin, user_cache_bin)
                except OSError:
                    pass
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
        self._cached_ax_bin: Path | None = None
        self._last_bin_check_mono: float = 0.0

    def ensure_daemon(self) -> bool:
        now_mono = time.monotonic()
        if (
            self._cached_ax_bin is not None
            and (now_mono - self._last_bin_check_mono) < 2.0
            and self._can_connect()
        ):
            return False
        with _daemon_boot_lock:
            now_mono = time.monotonic()
            ax_bin = _resolve_ax_binary()
            self._cached_ax_bin = ax_bin
            self._last_bin_check_mono = now_mono
            if not ax_bin.is_file():
                raise FileNotFoundError(f"Native binary not found at {ax_bin}")
            sock_path = Path(self.socket_path)
            if self._can_connect():
                try:
                    if ax_bin.stat().st_mtime <= sock_path.stat().st_mtime + 0.5:
                        return False
                except OSError:
                    return False
            if self._daemon_proc and self._daemon_proc.poll() is None:
                self._daemon_proc.terminate()
            subprocess.run(
                ["pkill", "-f", f"fmu-ax --daemon {self.socket_path}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            sock_path.unlink(missing_ok=True)
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
            for _ in range(80):
                if self._can_connect():
                    return True
                time.sleep(0.02)
            return True

    def _can_connect(self) -> bool:
        if not Path(self.socket_path).exists():
            return False
        for attempt in range(2):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.settimeout(0.35)
                s.connect(self.socket_path)
                s.close()
                return True
            except OSError:
                s.close()
                if attempt == 0:
                    time.sleep(0.015)
        return False

    def execute(self, req: dict[str, Any]) -> str:
        t0 = time.perf_counter()
        is_control = any(
            req.get(k) is not None and req.get(k) is not False
            for k in (
                "reset",
                "stop",
                "resume",
                "statusBar",
                "clickStatusBar",
                "highlightWindow",
                "togglePreview",
                "toggleZoom",
                "dumpUiSnapshots",
                "statusUpdate",
                "getCursors",
            )
        )
        if not is_control and is_user_stopped():
            return USER_STOPPED_ERROR
        if "steps" in req and req["steps"] is not None:
            if not isinstance(req["steps"], list):
                return "Error: 'steps' must be a JSON array of step objects"
            for idx, st in enumerate(req["steps"]):
                if not isinstance(st, dict):
                    return f"Error: step {idx + 1} must be a JSON object"
        was_cold = not Path(self.socket_path).exists()
        t_boot0 = time.perf_counter()
        restarted = bool(self.ensure_daemon())
        was_cold = was_cold or restarted
        daemon_boot_ms = (time.perf_counter() - t_boot0) * 1000.0
        payload = (json.dumps(req) + "\n").encode("utf-8")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(1.5)
            try:
                s.connect(self.socket_path)
            except OSError:
                s.close()
                self._last_bin_check_mono = 0.0
                self.ensure_daemon()
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(1.5)
                s.connect(self.socket_path)
            s.sendall(payload)
            try:
                s.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            chunks: list[bytes] = []
            s.settimeout(0.05)
            deadline = time.perf_counter() + 15.0
            while True:
                if not is_control and is_user_stopped():
                    return USER_STOPPED_ERROR
                try:
                    buf = s.recv(16384)
                except socket.timeout:
                    if time.perf_counter() >= deadline:
                        raise TimeoutError("Timed out waiting for fmu-ax response")
                    continue
                if not buf:
                    break
                chunks.append(buf)
            out = b"".join(chunks).decode("utf-8", errors="replace").strip()
        finally:
            s.close()

        if not is_control:
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

