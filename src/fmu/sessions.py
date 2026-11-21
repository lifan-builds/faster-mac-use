"""Agent session identity: which harness conversation is calling fmu, and how its Chrome tabs are labeled."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
from dataclasses import dataclass, field
from pathlib import Path
import re
import struct
import threading
import time
import zlib

HARNESS_NAMES = {
    "devin": "Devin",
    "codex": "Codex",
    "claude": "Claude",
    "language_server": "Antigravity",
    "agy": "Antigravity",
    "gemini": "Gemini",
    "cursor-agent": "Cursor",
    "opencode": "OpenCode",
    "goose": "Goose",
    "amp": "Amp",
}

CHROME_GROUP_COLORS = ["blue", "purple", "cyan", "green", "orange", "pink", "yellow", "red"]

_PROC_PIDTBSDINFO = 3
_PROC_PIDVNODEPATHINFO = 9
_BSDINFO_SIZE = 136
_VNODEPATHINFO_SIZE = 2352
_libproc = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    ppid: int
    name: str
    start: int


def proc_info(pid: int) -> ProcInfo | None:
    buf = ctypes.create_string_buffer(_BSDINFO_SIZE)
    if _libproc.proc_pidinfo(pid, _PROC_PIDTBSDINFO, ctypes.c_uint64(0), buf, _BSDINFO_SIZE) != _BSDINFO_SIZE:
        return None
    raw = buf.raw
    return ProcInfo(
        pid=pid,
        ppid=struct.unpack_from("I", raw, 16)[0],
        name=raw[64:96].split(b"\0", 1)[0].decode("utf-8", "replace"),
        start=struct.unpack_from("Q", raw, 120)[0],
    )


def proc_cwd(pid: int) -> str:
    buf = ctypes.create_string_buffer(_VNODEPATHINFO_SIZE)
    if _libproc.proc_pidinfo(pid, _PROC_PIDVNODEPATHINFO, ctypes.c_uint64(0), buf, _VNODEPATHINFO_SIZE) != _VNODEPATHINFO_SIZE:
        return ""
    return buf.raw[152 : 152 + 1024].split(b"\0", 1)[0].decode("utf-8", "replace")


def peer_pid(conn: object) -> int:
    """PID of the process on the other end of a connected AF_UNIX socket (macOS LOCAL_PEERPID)."""
    return struct.unpack("i", conn.getsockopt(0, 0x002, 4))[0]


def project_name(cwd: str) -> str | None:
    if not cwd or cwd in ("/", str(Path.home())):
        return None
    p = Path(cwd)
    for cand in (p, *p.parents):
        if (cand / ".git").exists():
            return cand.name
    return p.name


@dataclass(frozen=True)
class CallerIdentity:
    harness: str
    host_pid: int
    host_start: int
    project: str | None

    @property
    def owner(self) -> str:
        return f"{self.harness.lower()}:{self.host_pid}:{self.host_start}:{self.project or ''}"


def resolve_caller(pid: int, label: str | None = None) -> CallerIdentity | None:
    """Walk up from the calling process to the nearest agent harness process.

    Commands an agent runs through its terminal tool execute in that session's workspace, so their cwd names the
    project. Tool servers the harness spawns directly (MCP stdio) inherit the harness's own cwd, so they only
    identify the harness unless the caller passes an explicit `sessionLabel`.
    """
    cur = proc_info(pid)
    for _ in range(32):
        if cur is None or (cur.pid == os.getpid() and cur.pid != pid):
            return None
        harness = HARNESS_NAMES.get(cur.name.lower())
        if harness:
            if label:
                project: str | None = label
            else:
                peer_cwd = proc_cwd(pid)
                project = project_name(peer_cwd) if peer_cwd != proc_cwd(cur.pid) else None
            return CallerIdentity(harness, cur.pid, cur.start, project)
        if cur.ppid <= 1:
            return None
        cur = proc_info(cur.ppid)
    return None


def is_alive(ident: CallerIdentity) -> bool:
    info = proc_info(ident.host_pid)
    return info is not None and info.start == ident.host_start


@dataclass
class AgentClient:
    short_id: str
    ident: CallerIdentity
    label: str
    color: str
    last_active: float = field(default_factory=time.time)

    @property
    def owner(self) -> str:
        return self.ident.owner


class SessionRegistry:
    """Maps caller identities to short agent-namespace ids (`devin1`), unique tab group titles, and stable colors."""

    _SHORT_RE = re.compile(r"[^a-z0-9]+")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_owner: dict[str, AgentClient] = {}
        self._by_short: dict[str, AgentClient] = {}
        self._counters: dict[str, int] = {}
        self._last_prune = 0.0

    def client_for(self, ident: CallerIdentity) -> AgentClient:
        with self._lock:
            client = self._by_owner.get(ident.owner)
            if client is None:
                stem = self._SHORT_RE.sub("", ident.harness.lower()) or "agent"
                n = self._counters.get(stem, 0) + 1
                self._counters[stem] = n
                base_label = f"{ident.harness} · {ident.project}" if ident.project else ident.harness
                taken = {c.label for c in self._by_owner.values()}
                label = base_label
                suffix = 2
                while label in taken:
                    label = f"{base_label} {suffix}"
                    suffix += 1
                used = {c.color for c in self._by_owner.values()}
                start = zlib.crc32(base_label.encode("utf-8")) % len(CHROME_GROUP_COLORS)
                ring = CHROME_GROUP_COLORS[start:] + CHROME_GROUP_COLORS[:start]
                color = next((c for c in ring if c not in used), "grey")
                client = AgentClient(short_id=f"{stem}{n}", ident=ident, label=label, color=color)
                self._by_owner[ident.owner] = client
                self._by_short[client.short_id] = client
            client.last_active = time.time()
            return client

    def lookup(self, agent_id: str | None) -> tuple[AgentClient | None, str]:
        """Resolve a scoped agent id (`devin1` / `devin1.audit`) or tab id (`devin1.audit:t3`) to (client, sub-agent)."""
        scope = str(agent_id or "").split(":", 1)[0]
        short, _, sub = scope.partition(".")
        with self._lock:
            return self._by_short.get(short), sub

    def by_short(self, short_id: str) -> AgentClient | None:
        with self._lock:
            return self._by_short.get(short_id)

    def live(self) -> list[AgentClient]:
        with self._lock:
            return list(self._by_owner.values())

    def take_dead(self, min_interval_s: float = 5.0) -> list[AgentClient]:
        now = time.time()
        with self._lock:
            if now - self._last_prune < min_interval_s:
                return []
            self._last_prune = now
            dead = [c for c in self._by_owner.values() if not is_alive(c.ident)]
            for c in dead:
                self._by_owner.pop(c.owner, None)
                self._by_short.pop(c.short_id, None)
            return dead
