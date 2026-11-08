"""Unified Faster Mac Use Runtime: Codex-style stateful cua_repl + 1-Trip JSON Batch + Context Window Compactor + Warm Unix Daemon."""

from __future__ import annotations
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from typing import Any

from .browser_engine import BrowserEngine
from .mac_engine import MacNativeEngine

try:
    import tiktoken as _tiktoken

    _ENC = _tiktoken.get_encoding("o200k_base")
except Exception:
    _ENC = None

DEFAULT_RUNTIME_SOCK = os.environ.get("FMU_RUNTIME_SOCK", "/tmp/fmu-runtime.sock")


def estimate_tokens(text: str) -> int:
    """Exact OpenAI o200k_base BPE token count."""
    if not text:
        return 0
    if _ENC is not None:
        return len(_ENC.encode(text))
    return max(1, int(round(len(text) / 3.85)))


@dataclass
class TurnRecord:
    turn_id: int
    surface: str
    is_full_snapshot: bool
    raw_output: str
    masked_output: str | None = None


@dataclass
class ContextWindowTracker:
    schema_tokens: int = 268
    turns: list[TurnRecord] = field(default_factory=list)
    mask_stale_snapshots: bool = True

    def record_turn(self, surface: str, output: str) -> TurnRecord:
        lines = output.splitlines()
        is_full = any(l.startswith("@u") or l.startswith("@a") or l.startswith("[") for l in lines[1:])
        if self.mask_stale_snapshots and len(self.turns) >= 1:
            for prev in self.turns:
                if prev.surface == surface and prev.is_full_snapshot and prev.masked_output is None:
                    hdr = prev.raw_output.splitlines()[0] if prev.raw_output else "# snapshot"
                    prev.masked_output = f"{hdr} [stale snapshot masked]"
        rec = TurnRecord(
            turn_id=len(self.turns) + 1,
            surface=surface,
            is_full_snapshot=is_full,
            raw_output=output,
        )
        self.turns.append(rec)
        return rec

    def metrics(self) -> dict[str, int]:
        unmasked: list[int] = []
        masked: list[int] = []
        running_u = self.schema_tokens
        for idx, t in enumerate(self.turns):
            running_u += estimate_tokens(t.raw_output)
            unmasked.append(running_u)
            cur_m = self.schema_tokens
            for j in range(idx + 1):
                if j < idx and self.turns[j].is_full_snapshot:
                    hdr = self.turns[j].raw_output.splitlines()[0] if self.turns[j].raw_output else ""
                    cur_m += estimate_tokens(f"{hdr} [masked]")
                else:
                    cur_m += estimate_tokens(self.turns[j].raw_output)
            masked.append(cur_m)
        return {
            "turns": len(self.turns),
            "schema_tokens": self.schema_tokens,
            "observation_tokens_raw": sum(estimate_tokens(t.raw_output) for t in self.turns),
            "peak_context_unmasked": max(unmasked, default=self.schema_tokens),
            "peak_context_masked": max(masked, default=self.schema_tokens),
            "cumulative_input_tokens_unmasked": sum(unmasked),
            "cumulative_input_tokens_masked": sum(masked),
        }


class AppHandle:
    def __init__(
        self,
        runtime: FasterMacUseRuntime,
        app_name: str,
        window: str | None = None,
        agent_id: str = "default",
    ) -> None:
        self.runtime = runtime
        self.app_name = app_name
        self.window = window
        self.agent_id = agent_id
        self._pending_steps: list[dict[str, Any]] = []
        self.initial_state = self.runtime.mac.execute(
            {"app": app_name, "window": window, "agent": agent_id}
        )
        self._fresh = True

    def has_pending(self) -> bool:
        return bool(self._pending_steps)

    def click(self, target: str | int | dict[str, Any], y: int | None = None) -> AppHandle:
        self._fresh = False
        if y is not None and isinstance(target, (int, float)):
            self._pending_steps.append({"op": "click", "x": int(target), "y": int(y)})
        elif isinstance(target, dict) and "x" in target and "y" in target:
            self._pending_steps.append({"op": "click", "x": int(target["x"]), "y": int(target["y"])})
        elif isinstance(target, int):
            self._pending_steps.append({"op": "click", "index": target})
        elif str(target).startswith(("@a", "@u", "@e")):
            self._pending_steps.append({"op": "click", "uid": str(target)})
        else:
            self._pending_steps.append({"op": "click", "name": str(target)})
        return self

    def moveCursor(self, target: str | int | dict[str, Any], y: int | None = None) -> AppHandle:
        self._fresh = False
        if y is not None and isinstance(target, (int, float)):
            self._pending_steps.append({"op": "move", "x": int(target), "y": int(y)})
        elif isinstance(target, dict) and "x" in target and "y" in target:
            self._pending_steps.append({"op": "move", "x": int(target["x"]), "y": int(target["y"])})
        elif str(target).startswith(("@a", "@u", "@e")):
            self._pending_steps.append({"op": "move", "uid": str(target)})
        else:
            self._pending_steps.append({"op": "move", "name": str(target)})
        return self

    move = moveCursor
    hover = moveCursor

    def set(self, target: str, value: str) -> AppHandle:
        self._fresh = False
        if str(target).startswith(("@a", "@u", "@e")):
            self._pending_steps.append({"op": "set", "uid": str(target), "value": str(value)})
        else:
            self._pending_steps.append({"op": "set", "name": str(target), "value": str(value)})
        return self

    def typeText(self, text: str) -> AppHandle:
        self._fresh = False
        self._pending_steps.append({"op": "type", "value": str(text)})
        return self

    def pressKey(self, chord: str) -> AppHandle:
        self._fresh = False
        self._pending_steps.append({"op": "key", "name": str(chord)})
        return self

    def menu(self, path: list[str]) -> AppHandle:
        self._fresh = False
        self._pending_steps.append({"op": "menu", "path": list(path)})
        return self

    def ocr(self) -> AppHandle:
        self._fresh = False
        self._pending_steps.append({"op": "ocr"})
        return self

    def diff(self) -> str:
        return self.flush(diff=True)

    def snapshot(self) -> str:
        if self._fresh and not self._pending_steps:
            self._fresh = False
            return self.initial_state
        return self.flush(diff=False)

    def flush(self, diff: bool = True) -> str:
        self._fresh = False
        if not self._pending_steps:
            return self.runtime.mac.execute(
                {"app": self.app_name, "window": self.window, "agent": self.agent_id, "diff": diff}
            )
        steps = list(self._pending_steps)
        self._pending_steps.clear()
        return self.runtime.mac.execute(
            {
                "app": self.app_name,
                "window": self.window,
                "agent": self.agent_id,
                "steps": steps,
                "diff": diff,
            }
        )


class TabHandle:
    def __init__(
        self,
        runtime: FasterMacUseRuntime,
        tab_id: str,
        url: str | None = None,
        mode: str = "chrome",
        session_name: str = "🤖 Agent",
        agent_id: str = "default",
        window: str | None = None,
        window_id: int | None = None,
        reuse_existing: bool = False,
        require_extension: bool = False,
    ) -> None:
        self.runtime = runtime
        self.tab_id = tab_id
        self.mode = mode
        self.session_name = session_name
        self.agent_id = agent_id
        self.window = window
        self.window_id = window_id
        self.reuse_existing = reuse_existing
        self.require_extension = require_extension
        self._pending_steps: list[dict[str, Any]] = []
        self._pending_macros: list[dict[str, Any]] = []
        self._force_ocr: bool = False
        init_req: dict[str, Any] = {
            "agent": agent_id,
            "session": tab_id,
            "url": url,
            "mode": mode,
            "sessionName": session_name,
        }
        if window:
            init_req["window"] = window
        if window_id is not None:
            init_req["windowId"] = window_id
        if reuse_existing:
            init_req["reuseExisting"] = True
        if require_extension:
            init_req["requireExtension"] = True
        self.initial_state = self.runtime.browser.execute_batch(init_req)
        self._fresh = True

    def has_pending(self) -> bool:
        return bool(self._pending_steps or self._pending_macros or self._force_ocr)

    def goto(self, url: str) -> TabHandle:
        self._fresh = False
        self._pending_steps.append({"op": "goto", "url": str(url)})
        return self

    navigate = goto

    def click(self, target: str) -> TabHandle:
        self._fresh = False
        self._pending_steps.append({"op": "click", "uid": str(target)})
        return self

    def moveCursor(self, target: str | int, y: int | None = None) -> TabHandle:
        self._fresh = False
        if y is not None and isinstance(target, (int, float)):
            self._pending_steps.append({"op": "move", "x": int(target), "y": int(y)})
        else:
            self._pending_steps.append({"op": "move", "uid": str(target)})
        return self

    move = moveCursor
    hover = moveCursor

    def fill(self, target: str, value: str, append: bool = False) -> TabHandle:
        self._fresh = False
        step: dict[str, Any] = {"op": "fill", "uid": str(target), "value": str(value)}
        if append:
            step["append"] = True
        self._pending_steps.append(step)
        return self

    set = fill

    def select(self, target: str, value: str) -> TabHandle:
        self._fresh = False
        self._pending_steps.append({"op": "select", "uid": str(target), "value": str(value)})
        return self

    def press(self, key: str = "Enter") -> TabHandle:
        self._fresh = False
        self._pending_steps.append({"op": "press", "key": str(key)})
        return self

    def eval(self, code: str) -> TabHandle:
        self._fresh = False
        self._pending_steps.append({"op": "eval", "code": str(code)})
        return self

    evaluate = eval

    def callTool(self, name: str, args: dict[str, Any] | None = None) -> TabHandle:
        self._fresh = False
        self._pending_steps.append({"op": "webmcp", "name": str(name), "args": args or {}})
        return self

    def saveMacro(
        self,
        name: str,
        steps: list[dict[str, Any]] | None = None,
        description: str = "",
    ) -> TabHandle:
        self._fresh = False
        if steps is not None:
            self.runtime.browser.save_macro(
                name=name,
                steps=steps,
                description=description,
                tab_id=self.tab_id,
            )
        else:
            self._pending_macros.append(
                {"name": name, "steps": None, "description": description}
            )
        return self

    def ocr(self) -> TabHandle:
        self._fresh = False
        self._force_ocr = True
        return self

    def upload(self, target: str, files: str | list[str]) -> TabHandle:
        self._fresh = False
        self._pending_steps.append({"op": "click", "uid": str(target), "files": files})
        return self

    def close(self) -> str:
        self._fresh = False
        self._pending_steps.clear()
        self._pending_macros.clear()
        sess = self.runtime._get_agent_session(self.agent_id)
        sess.cua.handles.pop(self.tab_id, None)
        return self.runtime.browser.close_tab(self.tab_id)

    def diff(self) -> str:
        return self.flush(diff=True)

    def snapshot(
        self, full_table: bool | dict[str, Any] = False, query: str = ""
    ) -> str:
        ft = False
        q = query
        if isinstance(full_table, dict):
            ft = bool(full_table.get("fullTable") or full_table.get("full_table"))
            q = str(full_table.get("query") or query or "")
        else:
            ft = bool(full_table)
        if self._fresh and not self.has_pending() and not ft and not q:
            self._fresh = False
            return self.initial_state
        return self.flush(diff=False, full_table=ft, query=q)

    def flush(
        self, diff: bool = True, full_table: bool = False, query: str = ""
    ) -> str:
        self._fresh = False
        payload: dict[str, Any] = {
            "agent": self.agent_id,
            "session": self.tab_id,
            "mode": self.mode,
            "sessionName": self.session_name,
            "diff": diff,
        }
        if self.window:
            payload["window"] = self.window
        if self.window_id is not None:
            payload["windowId"] = self.window_id
        if self.require_extension:
            payload["requireExtension"] = True
        if self._force_ocr:
            payload["ocr"] = True
            self._force_ocr = False
        if full_table:
            payload["fullTable"] = True
        if query:
            payload["query"] = query
        if self._pending_steps:
            payload["steps"] = list(self._pending_steps)
            self._pending_steps.clear()
        out = self.runtime.browser.execute_batch(payload)
        if self._pending_macros:
            macros = list(self._pending_macros)
            self._pending_macros.clear()
            for m in macros:
                entry = self.runtime.browser.save_macro(
                    name=m["name"],
                    steps=m["steps"],
                    description=m["description"],
                    tab_id=self.tab_id,
                )
                out += f'\n# macro saved name="{entry["name"]}" domain="{entry["domain"]}" steps={len(entry["steps"])}'
        return out


class CuaNamespace:
    def __init__(self, runtime: FasterMacUseRuntime, agent_id: str = "default") -> None:
        self.runtime = runtime
        self.agent_id = agent_id
        self.handles: dict[str, Any] = {}
        self._tab_seq: int = 1

    def createBrowserTab(
        self,
        browser_id: str | dict[str, Any] = "chrome",
        url: str = "about:blank",
        options: dict[str, Any] | None = None,
    ) -> TabHandle:
        if isinstance(browser_id, dict):
            opts = browser_id
            actual_url = str(opts.get("url") or url or "about:blank")
            b_id = str(opts.get("browser") or opts.get("mode") or "chrome")
        elif isinstance(browser_id, str) and browser_id.startswith(("http://", "https://", "file://", "about:")):
            opts = options if isinstance(options, dict) else {}
            actual_url = browser_id
            b_id = str((options or {}).get("mode") or "chrome")
        else:
            opts = options or {}
            actual_url = url
            b_id = str(browser_id or "chrome")

        default_group = "🤖 Agent" if self.agent_id == "default" else f"🤖 {self.agent_id}"
        session_name = str(opts.get("sessionName") or opts.get("group") or default_group)
        explicit_tid = opts.get("tabId") or opts.get("session")
        prefix = "" if self.agent_id == "default" else f"{self.agent_id}:"
        if explicit_tid:
            tab_id = str(explicit_tid)
        else:
            while f"{prefix}t{self._tab_seq}" in self.handles:
                self._tab_seq += 1
            tab_id = f"{prefix}t{self._tab_seq}"
            self._tab_seq += 1
        if b_id in ("extension", "chrome-extension", "live"):
            mode = "extension"
        elif b_id == "chrome":
            mode = "chrome"
        else:
            mode = "headless"
        win_filter = str(opts["window"]) if opts.get("window") else None
        win_id = int(opts["windowId"]) if opts.get("windowId") is not None else None
        reuse_existing = bool(opts.get("reuseExisting"))
        require_ext = bool(opts.get("requireExtension")) or mode == "extension"
        h = TabHandle(
            self.runtime,
            tab_id=tab_id,
            url=actual_url,
            mode=mode,
            session_name=session_name,
            agent_id=self.agent_id,
            window=win_filter,
            window_id=win_id,
            reuse_existing=reuse_existing,
            require_extension=require_ext,
        )
        self.handles[tab_id] = h
        return h

    def getTab(self, criteria: dict[str, Any] | str | None = None) -> TabHandle:
        if isinstance(criteria, str) and criteria in self.handles:
            return self.handles[criteria]
        if isinstance(criteria, dict):
            tid = criteria.get("tabId") or criteria.get("session")
            if tid and tid in self.handles:
                return self.handles[tid]
            if any(k in criteria for k in ("url", "window", "windowId", "reuseExisting", "mode")):
                opts = dict(criteria)
                opts.setdefault("reuseExisting", True)
                return self.createBrowserTab(opts)
        for h in self.handles.values():
            if isinstance(h, TabHandle):
                return h
        return self.createBrowserTab("chrome", "about:blank")

    def getBridgeStatus(self) -> str:
        return self.runtime.browser.execute_batch({"bridgeStatus": True})

    def getApp(self, identifier: str, window: str | None = None) -> AppHandle:
        key = f"app:{identifier}:{window or ''}"
        if key not in self.handles:
            self.handles[key] = AppHandle(
                self.runtime, identifier, window=window, agent_id=self.agent_id
            )
        else:
            h: AppHandle = self.handles[key]
            h.initial_state = self.runtime.mac.execute(
                {"app": identifier, "window": window, "agent": self.agent_id}
            )
            h._fresh = True
        return self.handles[key]

    def saveMacro(
        self,
        name: str,
        steps: list[dict[str, Any]] | None = None,
        domain: str | None = None,
        description: str = "",
    ) -> str:
        entry = self.runtime.browser.save_macro(
            name=name, steps=steps, domain=domain, description=description
        )
        return f'# macro saved name="{entry["name"]}" domain="{entry["domain"]}" steps={len(entry["steps"])}'

    def listMacros(self, domain: str | None = None) -> str:
        items = self.runtime.browser.list_macros(domain=domain)
        lines = [f"# macros count={len(items)}"]
        for m in items:
            lines.append(
                f'@macro name="{m.get("name")}" domain="{m.get("domain")}" steps={len(m.get("steps") or [])}'
            )
        return "\n".join(lines)

    def deleteMacro(self, name: str, domain: str | None = None) -> str:
        ok = self.runtime.browser.delete_macro(name=name, domain=domain)
        return f'# macro deleted name="{name}" ok={str(ok).lower()}'

    def getCursors(self) -> str:
        return self.runtime.get_cursors()

    def getState(self) -> str:
        items = [f"{k}:{type(v).__name__}" for k, v in self.handles.items()]
        return f"# cua agent={self.agent_id} handles=[{', '.join(items)}]"


class _ConsoleProxy:
    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    def log(self, *args: Any) -> None:
        parts = []
        for a in args:
            if isinstance(a, (AppHandle, TabHandle)):
                parts.append(a.flush())
            else:
                parts.append(str(a))
        self._sink.append(" ".join(parts))


@dataclass
class AgentSession:
    agent_id: str
    cua: CuaNamespace
    context_tracker: ContextWindowTracker
    repl_env: dict[str, Any]
    lock: Any


class FasterMacUseRuntime:
    def __init__(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        import threading

        self._is_mcp_stdio = len(sys.argv) >= 2 and sys.argv[1] in ("mcp", "--mcp")
        self._threading = threading
        self._global_lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="fmu-agent")
        self.mac = MacNativeEngine()
        self.browser = BrowserEngine()
        self._agents: dict[str, AgentSession] = {}
        default_sess = self._get_agent_session("default")
        self.cua = default_sess.cua
        self.context_tracker = default_sess.context_tracker
        self._repl_env = default_sess.repl_env

    def _get_agent_session(self, agent_id: str = "default") -> AgentSession:
        key = str(agent_id or "default").strip() or "default"
        with self._global_lock:
            if key not in self._agents:
                cua_ns = CuaNamespace(self, agent_id=key)
                tracker = ContextWindowTracker()
                env: dict[str, Any] = {"cua": cua_ns}
                self._agents[key] = AgentSession(
                    agent_id=key,
                    cua=cua_ns,
                    context_tracker=tracker,
                    repl_env=env,
                    lock=self._threading.RLock(),
                )
            return self._agents[key]

    def get_cursors(self) -> str:
        web_cursors = dict(self.browser.cursors)
        mac_lines: list[str] = []
        try:
            mac_raw = self.mac.execute({"getCursors": True})
            mac_lines = [l for l in mac_raw.splitlines() if l.startswith("@cursor ")]
        except Exception:
            pass
        out_lines: list[str] = []
        for aid in sorted(web_cursors.keys()):
            c = web_cursors[aid]
            out_lines.append(
                f'@cursor agent="{c["agent"]}" surface=web tab="{c["target"]}" '
                f'group="{c["group"]}" uid={c["uid"]} pos=({c["x"]},{c["y"]}) op={c["op"]}'
            )
        out_lines.extend(mac_lines)
        return f"# cursors count={len(out_lines)}\n" + "\n".join(out_lines)

    def _split_statements(self, code: str) -> list[str]:
        stmts: list[str] = []
        buf: list[str] = []
        in_str: str | None = None
        escape = False
        depth = 0
        for ch in code:
            if escape:
                buf.append(ch)
                escape = False
                continue
            if ch == "\\":
                buf.append(ch)
                escape = True
                continue
            if in_str:
                if ch == in_str:
                    in_str = None
                buf.append(ch)
                continue
            if ch in ('"', "'", "`"):
                in_str = ch
                buf.append(ch)
                continue
            if ch in ("(", "{", "["):
                depth += 1
            elif ch in (")", "}", "]"):
                depth = max(0, depth - 1)
            if (ch == ";" or ch == "\n") and depth == 0:
                s = "".join(buf).strip()
                if s:
                    stmts.append(s)
                buf = []
            else:
                buf.append(ch)
        tail = "".join(buf).strip()
        if tail:
            stmts.append(tail)
        return stmts

    def _normalize_js_stmt(self, stmt: str) -> str:
        strings: list[str] = []

        def _save_str(m: re.Match[str]) -> str:
            raw_s = m.group(0)
            if raw_s.startswith("`") and raw_s.endswith("`"):
                raw_s = json.dumps(raw_s[1:-1])
            strings.append(raw_s)
            return f"__STR_{len(strings) - 1}__"

        masked = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`', _save_str, stmt)
        masked = re.sub(r"//.*$", "", masked).strip()
        if not masked:
            return ""
        masked = re.sub(r"^(?:let|const|var)\s+", "", masked)
        masked = re.sub(r"\bawait\s+", "", masked)
        masked = re.sub(r"\btrue\b", "True", masked)
        masked = re.sub(r"\bfalse\b", "False", masked)
        masked = re.sub(r"\bnull\b", "None", masked)
        masked = re.sub(r"(\{|,\s*)([a-zA-Z_]\w*)\s*:", r'\1"\2":', masked)
        for idx, s in enumerate(strings):
            masked = masked.replace(f"__STR_{idx}__", s)
        return masked.strip()

    def execute_repl_code(self, code: str, agent_id: str = "default") -> str:
        sess = self._get_agent_session(agent_id)
        with sess.lock:
            raw_stmts = self._split_statements(code)
            lines = [
                norm
                for s in raw_stmts
                if s.strip() and not s.strip().startswith("//")
                and (norm := self._normalize_js_stmt(s))
            ]

            outputs: list[str] = []
            last_handle: AppHandle | TabHandle | None = None
            console = _ConsoleProxy(outputs)
            sess.repl_env["print"] = console.log
            sess.repl_env["console"] = console
            sess.repl_env["JSON"] = json

            for stmt in lines:
                assign_m = re.match(r"^([a-zA-Z_]\w*)\s*=\s*(?!=)(.+)$", stmt, re.DOTALL)
                if assign_m and not stmt.startswith(("if ", "assert ", "return ")):
                    var_name = assign_m.group(1).strip()
                    rhs = assign_m.group(2).strip()
                    if last_handle is not None and last_handle.has_pending():
                        outputs.append(last_handle.flush())
                    val = eval(rhs, {"__builtins__": {}}, sess.repl_env)
                    sess.repl_env[var_name] = val
                    if isinstance(val, (AppHandle, TabHandle)):
                        last_handle = val
                        if len(lines) == 1:
                            val._fresh = False
                            outputs.append(val.initial_state)
                elif stmt.startswith("return "):
                    val = eval(stmt[7:].strip(), {"__builtins__": {}}, sess.repl_env)
                    if isinstance(val, (AppHandle, TabHandle)):
                        outputs.append(val.flush())
                        last_handle = None
                    elif val is not None:
                        outputs.append(str(val))
                else:
                    val = eval(stmt, {"__builtins__": {}}, sess.repl_env)
                    if isinstance(val, (AppHandle, TabHandle)):
                        if last_handle is not None and last_handle is not val and last_handle.has_pending():
                            outputs.append(last_handle.flush())
                        last_handle = val
                    elif isinstance(val, str):
                        outputs.append(val)

            if last_handle is not None and last_handle.has_pending():
                outputs.append(last_handle.flush())
            for h in list(sess.cua.handles.values()):
                if isinstance(h, (AppHandle, TabHandle)) and h.has_pending():
                    outputs.append(h.flush())
            if last_handle is not None and not outputs and last_handle._fresh:
                last_handle._fresh = False
                outputs.append(last_handle.initial_state)

            result = "\n".join(outputs).strip()
            sess.context_tracker.record_turn("repl", result)
            return result

    def execute_parallel(self, tasks: list[dict[str, Any]]) -> str:
        if not tasks:
            return "# parallel tasks=0"
        futures = []
        for idx, sub_req in enumerate(tasks):
            req_copy = dict(sub_req)
            if not req_copy.get("agent") and not req_copy.get("cursor"):
                req_copy["agent"] = f"agent-{idx + 1}"
            futures.append((req_copy["agent"], self._pool.submit(self.execute, req_copy)))
        parts: list[str] = []
        for aid, fut in futures:
            try:
                res = fut.result()
            except Exception as exc:
                res = f"! error: {type(exc).__name__}: {exc}"
            parts.append(f"=== [{aid}] ===\n{res}")
        return "\n".join(parts)

    def execute(self, req: dict[str, Any]) -> str:
        if getattr(self, "_is_mcp_stdio", False):
            return execute_via_daemon(req, Path(__file__).resolve().parent / "cli.py")

        if req.get("getCursors"):
            return self.get_cursors()

        if req.get("bridgeStatus"):
            return self.browser.execute_batch({"bridgeStatus": True})

        if "parallel" in req and isinstance(req["parallel"], list):
            return self.execute_parallel(req["parallel"])

        explicit_agent = req.get("agent") or req.get("cursor")
        agent_id = str(explicit_agent or "default").strip() or "default"

        if req.get("reset"):
            if explicit_agent and agent_id not in ("default", "all"):
                self.mac.execute({"reset": True, "agent": agent_id})
                self.browser.reset(agent=agent_id)
                with self._global_lock:
                    self._agents.pop(agent_id, None)
                return f"# fmu runtime reset agent={agent_id}"
            self.mac.reset()
            self.browser.reset()
            with self._global_lock:
                self._agents.clear()
                default_sess = self._get_agent_session("default")
                self.cua = default_sess.cua
                self.context_tracker = default_sess.context_tracker
                self._repl_env = default_sess.repl_env
            return "# fmu runtime reset"

        if "code" in req and req["code"]:
            return self.execute_repl_code(str(req["code"]), agent_id=agent_id)

        sess = self._get_agent_session(agent_id)
        with sess.lock:
            raw_surface = str(req.get("surface") or ("app" if "app" in req else "web")).lower()
            surface = "web" if raw_surface in ("web", "browser") else raw_surface
            if surface == "app":
                out = self.mac.execute(req)
                sess.context_tracker.record_turn(f"app:{req.get('app', '')}", out)
                return out

            out = self.browser.execute_batch(req)
            if req.get("fallbackApp") and ("! errors:" in out or req.get("escalateToMac")):
                mac_out = self.mac.execute(
                    {
                        "agent": agent_id,
                        "app": req["fallbackApp"],
                        "steps": req.get("fallbackSteps") or [],
                    }
                )
                out = f"{out}\n--- mac-ax-fallback ---\n{mac_out}"
            sess.context_tracker.record_turn(f"web:{req.get('session', 't1')}", out)
            return out

    def shutdown(self) -> None:
        self.browser.shutdown()
        self.mac.shutdown()


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


def serve_runtime_daemon(socket_path: str = DEFAULT_RUNTIME_SOCK) -> None:
    import fcntl
    import threading

    lock_path = Path(f"{socket_path}.lock")
    lock_fp = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_fp.close()
        return

    sock_file = Path(socket_path)
    sock_file.unlink(missing_ok=True)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(socket_path)
    srv.listen(32)
    runtime = FasterMacUseRuntime()
    runtime._is_mcp_stdio = False
    runtime.browser.ensure_bridge()
    stop_ev = threading.Event()

    def _handle_client(conn: socket.socket) -> None:
        try:
            chunks: list[bytes] = []
            while True:
                buf = conn.recv(16384)
                if not buf:
                    break
                chunks.append(buf)
                if buf.endswith(b"\n"):
                    break
            raw = b"".join(chunks).decode("utf-8", errors="replace").strip()
            if not raw:
                return
            req = json.loads(raw)
            if req.get("shutdown_daemon"):
                conn.sendall(b"# fmu daemon stopped\n")
                stop_ev.set()
                try:
                    s_wake = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s_wake.connect(socket_path)
                    s_wake.close()
                except Exception:
                    pass
                return
            resp = runtime.execute(req)
            conn.sendall((resp + "\n").encode("utf-8"))
        except Exception as exc:
            try:
                conn.sendall(f"Error: {exc}\n".encode("utf-8"))
            except Exception:
                pass
        finally:
            conn.close()

    try:
        while not stop_ev.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                break
            if stop_ev.is_set():
                conn.close()
                break
            threading.Thread(target=_handle_client, args=(conn,), daemon=True).start()
    finally:
        srv.close()
        sock_file.unlink(missing_ok=True)
        runtime.shutdown()
        try:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock_fp.close()
        lock_path.unlink(missing_ok=True)


def execute_via_daemon(
    req: dict[str, Any],
    fmu_bin_path: str | Path,
    socket_path: str = DEFAULT_RUNTIME_SOCK,
) -> str:
    if not can_connect_runtime_daemon(socket_path):
        if req.get("shutdown_daemon"):
            return "# fmu daemon stopped"
        if req.get("reset") and len(req) == 1:
            MacNativeEngine().reset()
            return "# fmu runtime reset"
        import fcntl

        spawn_lock_path = Path(f"{socket_path}.spawn.lock")
        with open(spawn_lock_path, "a+", encoding="utf-8") as spawn_lock_fp:
            fcntl.flock(spawn_lock_fp.fileno(), fcntl.LOCK_EX)
            try:
                if not can_connect_runtime_daemon(socket_path):
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
                        time.sleep(0.015)
            finally:
                try:
                    fcntl.flock(spawn_lock_fp.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass

    payload = (json.dumps(req) + "\n").encode("utf-8")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(30.0)
        s.connect(socket_path)
        s.sendall(payload)
        s.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while True:
            buf = s.recv(16384)
            if not buf:
                break
            chunks.append(buf)
        return b"".join(chunks).decode("utf-8", errors="replace").strip()
    except OSError:
        rt = FasterMacUseRuntime()
        rt._is_mcp_stdio = False
        try:
            return rt.execute(req)
        finally:
            rt.shutdown()
    finally:
        s.close()


MCP_TOOL_SCHEMA = {
    "name": "cua",
    "description": (
        "Background Chrome ('🤖 Agent' Tab Group) & macOS AX control with multi-cursor isolation, stateful REPL (`code`), "
        "or `steps` batching and `+/~/-` UID diffs. Zero focus/cursor theft. "
        "Pass `code` (`tab=cua.createBrowserTab('chrome',url); tab.click('@u1')` or `app=cua.getApp('Calculator'); app.click('@a6')`), "
        "`{app|url, steps, agent}`, or `{parallel:[...]}`."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "agent": {"type": "string"},
            "parallel": {"type": "array", "items": {"type": "object"}},
            "surface": {"type": "string", "enum": ["web", "browser", "app"]},
            "app": {"type": "string"},
            "window": {"type": "string"},
            "windowId": {"type": "integer"},
            "url": {"type": "string"},
            "mode": {"type": "string", "enum": ["chrome", "extension", "headless"]},
            "requireExtension": {"type": "boolean"},
            "reuseExisting": {"type": "boolean"},
            "bridgeStatus": {"type": "boolean"},
            "session": {"type": "string"},
            "query": {"type": "string"},
            "diff": {"type": "boolean"},
            "steps": {"type": "array", "items": {"type": "object"}},
            "reset": {"type": "boolean"},
        },
    },
}

