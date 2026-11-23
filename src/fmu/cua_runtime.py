# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Faster Mac Use; Section 7 attribution terms in ATTRIBUTION.md.
"""Unified Faster Mac Use Runtime: Codex-style stateful cua_repl + 1-Trip JSON Batch + Context Window Compactor + Warm Unix Daemon."""

from __future__ import annotations
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import socket
import sys
import time
from typing import Any

from .cli import DEFAULT_RUNTIME_SOCK, execute_via_daemon
from .browser_engine import BrowserEngine, _HAS_SCHEME_RE, _normalize_nav_url, _url_matches_target
from .sessions import AgentClient, CallerIdentity, peer_pid, resolve_caller
from .mac_engine import (
    USER_STOPPED_ERROR,
    MacNativeEngine,
    clear_user_stop,
    engage_user_stop,
    get_menubar_status,
    highlight_target_window,
    is_user_stopped,
    notify_menubar_status,
)

try:
    import tiktoken as _tiktoken

    _ENC = _tiktoken.get_encoding("o200k_base")
except Exception:
    _ENC = None


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

    @staticmethod
    def _format_masked_output(raw_output: str) -> str:
        hdr = raw_output.splitlines()[0] if raw_output else "# snapshot"
        return f"{hdr} [stale snapshot masked]"

    def _should_mask_turn(self, turn_idx: int, up_to_idx: int) -> bool:
        if not self.mask_stale_snapshots or turn_idx >= up_to_idx:
            return False
        target = self.turns[turn_idx]
        if not target.is_full_snapshot:
            return False
        return any(
            self.turns[later_idx].surface == target.surface
            for later_idx in range(turn_idx + 1, up_to_idx + 1)
        )

    def record_turn(self, surface: str, output: str) -> TurnRecord:
        lines = output.splitlines()
        is_full = any(l.startswith("@u") or l.startswith("@a") or l.startswith("[") for l in lines[1:])
        rec = TurnRecord(
            turn_id=len(self.turns) + 1,
            surface=surface,
            is_full_snapshot=is_full,
            raw_output=output,
        )
        self.turns.append(rec)
        last_idx = len(self.turns) - 1
        for prev_idx in range(last_idx):
            prev = self.turns[prev_idx]
            if prev.masked_output is None and self._should_mask_turn(prev_idx, last_idx):
                prev.masked_output = self._format_masked_output(prev.raw_output)
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
                turn_j = self.turns[j]
                if self._should_mask_turn(j, idx):
                    rendered = turn_j.masked_output or self._format_masked_output(turn_j.raw_output)
                else:
                    rendered = turn_j.raw_output
                cur_m += estimate_tokens(rendered)
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
        elif str(target).startswith(("@a", "@u", "@e", "@o")):
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
        elif str(target).startswith(("@a", "@u", "@e", "@o")):
            self._pending_steps.append({"op": "move", "uid": str(target)})
        else:
            self._pending_steps.append({"op": "move", "name": str(target)})
        return self

    move = moveCursor
    hover = moveCursor

    def scroll(
        self,
        dy: int = -320,
        dx: int = 0,
        x: int | None = None,
        y: int | None = None,
    ) -> AppHandle:
        self._fresh = False
        step: dict[str, Any] = {"op": "scroll", "dy": int(dy), "dx": int(dx)}
        if x is not None:
            step["x"] = int(x)
        if y is not None:
            step["y"] = int(y)
        self._pending_steps.append(step)
        return self

    def swipe(
        self,
        from_x: int,
        from_y: int,
        to_x: int,
        to_y: int,
    ) -> AppHandle:
        self._fresh = False
        self._pending_steps.append(
            {"op": "drag", "x": int(from_x), "y": int(from_y), "toX": int(to_x), "toY": int(to_y)}
        )
        return self

    drag = swipe

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

    def screenshot(self, path: str | dict[str, Any] | None = None) -> AppHandle:
        self._fresh = False
        shot_path = ""
        if isinstance(path, dict):
            shot_path = str(path.get("path") or path.get("file") or "")
        elif path is not None:
            shot_path = str(path)
        step: dict[str, Any] = {"op": "screenshot"}
        if shot_path:
            step["value"] = shot_path
        self._pending_steps.append(step)
        return self

    def screenshotValue(self, path: str | dict[str, Any] | None = None) -> str:
        self._fresh = False
        if self.has_pending():
            flushed = self.flush(diff=True)
            sess = self.runtime._get_agent_session(self.agent_id)
            if sess.active_outputs is not None and flushed:
                sess.active_outputs.append(flushed)
        shot_path = ""
        if isinstance(path, dict):
            shot_path = str(path.get("path") or path.get("file") or "")
        elif path is not None:
            shot_path = str(path)
        step: dict[str, Any] = {"op": "screenshot"}
        if shot_path:
            step["value"] = shot_path
        out = self.runtime.mac.execute(
            {
                "app": self.app_name,
                "window": self.window,
                "agent": self.agent_id,
                "steps": [step],
                "diff": True,
            }
        )
        m = re.search(r'^# screenshot path="([^"]+)"', out, re.MULTILINE)
        if m:
            return m.group(1)
        err_m = re.search(r"^! errors:\s*(.+)$", out, re.MULTILINE)
        if err_m:
            raise RuntimeError(err_m.group(1).strip())
        raise RuntimeError(f"Screenshot failed on app {self.app_name}: {out}")

    def wait(self, ms: int | float = 250) -> AppHandle:
        self._fresh = False
        self._pending_steps.append({"op": "wait", "ms": max(0, int(round(float(ms))))})
        return self

    sleep = wait

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


class _CallableStr(str):
    def __call__(self) -> str:
        return str(self)


class TabHandle:
    def __init__(
        self,
        runtime: FasterMacUseRuntime,
        tab_id: str,
        url: str | None = None,
        mode: str = "chrome",
        session_name: str = "Agent",
        agent_id: str = "default",
        window: str | None = None,
        window_id: int | None = None,
        chrome_tab_id: int | None = None,
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
        self.chrome_tab_id = chrome_tab_id
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
        if chrome_tab_id is not None:
            init_req["chromeTabId"] = chrome_tab_id
        if reuse_existing:
            init_req["reuseExisting"] = True
        if require_extension:
            init_req["requireExtension"] = True
        self.initial_state = self.runtime.browser.execute_batch(init_req)
        self._fresh = True

    def has_pending(self) -> bool:
        return bool(self._pending_steps or self._pending_macros or self._force_ocr)

    @property
    def url(self) -> str:
        sess = self.runtime.browser._tabs.get(self.tab_id)
        if sess is not None and sess.last_url:
            return _CallableStr(sess.last_url)
        return _CallableStr(self.runtime.browser._tab_last_urls.get(self.tab_id, ""))

    @property
    def title(self) -> str:
        sess = self.runtime.browser._tabs.get(self.tab_id)
        if sess is not None and sess.last_title:
            return _CallableStr(sess.last_title)
        return _CallableStr(self.runtime.browser._tab_last_titles.get(self.tab_id, ""))

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

    def wait(self, ms: int | float = 250) -> TabHandle:
        self._fresh = False
        self._pending_steps.append({"op": "wait", "ms": max(0, int(round(float(ms))))})
        return self

    sleep = wait

    def waitFor(
        self,
        condition: str | list[str] | dict[str, Any] | None = None,
        timeoutMs: int | float | None = None,
        **kwargs: Any,
    ) -> TabHandle:
        self._fresh = False
        step: dict[str, Any] = {"op": "waitFor"}
        if isinstance(condition, dict):
            for k, v in condition.items():
                if v is not None:
                    step[k] = v
        elif isinstance(condition, (list, tuple)):
            step["anyText"] = [str(x) for x in condition if x is not None]
        elif isinstance(condition, str) and condition:
            step["target"] = condition
        if timeoutMs is not None:
            step["timeoutMs"] = max(0, int(round(float(timeoutMs))))
        for k, v in kwargs.items():
            if v is not None:
                if k == "timeout_ms":
                    step["timeoutMs"] = max(0, int(round(float(v))))
                elif k == "any_text":
                    step["anyText"] = list(v) if isinstance(v, (list, tuple)) else [str(v)]
                elif k == "text_gone":
                    step["textGone"] = str(v)
                elif k == "selector_gone":
                    step["selectorGone"] = str(v)
                else:
                    step[k] = v
        self._pending_steps.append(step)
        return self

    wait_for = waitFor
    waitUntil = waitFor

    def text(self, selector: str | dict[str, Any] = "", maxChars: int = 4000, **kwargs: Any) -> TabHandle:
        self._fresh = False
        step: dict[str, Any] = {"op": "text"}
        if isinstance(selector, dict):
            if selector.get("selector") or selector.get("target") or selector.get("uid"):
                step["selector"] = str(selector.get("selector") or selector.get("target") or selector.get("uid"))
            step["maxChars"] = int(selector.get("maxChars") or selector.get("max_chars") or maxChars or 4000)
        else:
            if selector:
                step["selector"] = str(selector)
            step["maxChars"] = int(kwargs.get("max_chars") or kwargs.get("maxChars") or maxChars or 4000)
        self._pending_steps.append(step)
        return self

    readText = text
    readMain = text

    def textValue(self, selector: str | dict[str, Any] = "", maxChars: int = 4000, **kwargs: Any) -> str:
        self._fresh = False
        if self.has_pending():
            flushed = self.flush(diff=True)
            sess = self.runtime._get_agent_session(self.agent_id)
            if sess.active_outputs is not None and flushed:
                sess.active_outputs.append(flushed)
        sel_str = ""
        cap = int(kwargs.get("max_chars") or kwargs.get("maxChars") or maxChars or 4000)
        if isinstance(selector, dict):
            sel_str = str(selector.get("selector") or selector.get("target") or selector.get("uid") or "")
            cap = int(selector.get("maxChars") or selector.get("max_chars") or cap)
        elif selector:
            sel_str = str(selector)
        val = self.runtime.browser.evaluate_value(
            code="",
            agent=self.agent_id,
            session=self.tab_id,
            mode=self.mode,
            session_name=self.session_name,
            window=self.window,
            window_id=self.window_id,
            chrome_tab_id=self.chrome_tab_id,
            require_extension=self.require_extension,
            step_override={"op": "text", "selector": sel_str, "maxChars": cap},
        )
        return "" if val is None else str(val)

    def eval(self, code: str, options: int | dict[str, Any] | None = None) -> TabHandle:
        self._fresh = False
        step_obj: dict[str, Any] = {"op": "eval", "code": str(code)}
        if isinstance(options, int):
            step_obj["maxChars"] = options
        elif isinstance(options, dict) and options.get("maxChars") is not None:
            step_obj["maxChars"] = int(options["maxChars"])
        self._pending_steps.append(step_obj)
        return self

    evaluate = eval

    def evalValue(self, code: str) -> Any:
        self._fresh = False
        if self.has_pending():
            flushed = self.flush(diff=True)
            sess = self.runtime._get_agent_session(self.agent_id)
            if sess.active_outputs is not None and flushed:
                sess.active_outputs.append(flushed)
        return self.runtime.browser.evaluate_value(
            code=str(code),
            agent=self.agent_id,
            session=self.tab_id,
            mode=self.mode,
            session_name=self.session_name,
            window=self.window,
            window_id=self.window_id,
            chrome_tab_id=self.chrome_tab_id,
            require_extension=self.require_extension,
        )

    evaluateValue = evalValue
    evaluate_value = evalValue

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

    def screenshot(
        self,
        path: str | dict[str, Any] | None = None,
        options: dict[str, Any] | bool | None = None,
        *,
        fullPage: bool = False,
        full_page: bool = False,
    ) -> TabHandle:
        self._fresh = False
        shot_path = ""
        fp = bool(fullPage or full_page)
        if isinstance(path, dict):
            shot_path = str(path.get("path") or path.get("file") or "")
            fp = bool(fp or path.get("fullPage") or path.get("full_page"))
        elif path is not None:
            shot_path = str(path)
        if isinstance(options, dict):
            if not shot_path and (options.get("path") or options.get("file")):
                shot_path = str(options.get("path") or options.get("file"))
            fp = bool(fp or options.get("fullPage") or options.get("full_page"))
        elif isinstance(options, bool):
            fp = bool(fp or options)
        step: dict[str, Any] = {"op": "screenshot"}
        if shot_path:
            step["path"] = shot_path
        if fp:
            step["fullPage"] = True
        self._pending_steps.append(step)
        return self

    def screenshotValue(
        self,
        path: str | dict[str, Any] | None = None,
        options: dict[str, Any] | bool | None = None,
        *,
        fullPage: bool = False,
        full_page: bool = False,
    ) -> str:
        self._fresh = False
        if self.has_pending():
            flushed = self.flush(diff=True)
            sess = self.runtime._get_agent_session(self.agent_id)
            if sess.active_outputs is not None and flushed:
                sess.active_outputs.append(flushed)
        shot_path: str | None = None
        fp = bool(fullPage or full_page)
        if isinstance(path, dict):
            raw_p = path.get("path") or path.get("file")
            shot_path = str(raw_p) if raw_p else None
            fp = bool(fp or path.get("fullPage") or path.get("full_page"))
        elif path is not None and str(path).strip():
            shot_path = str(path)
        if isinstance(options, dict):
            if not shot_path and (options.get("path") or options.get("file")):
                shot_path = str(options.get("path") or options.get("file"))
            fp = bool(fp or options.get("fullPage") or options.get("full_page"))
        elif isinstance(options, bool):
            fp = bool(fp or options)
        return self.runtime.browser.screenshot_tab(
            path=shot_path,
            full_page=fp,
            agent=self.agent_id,
            session=self.tab_id,
            mode=self.mode,
            session_name=self.session_name,
            window=self.window,
            window_id=self.window_id,
            chrome_tab_id=self.chrome_tab_id,
            require_extension=self.require_extension,
        )

    def setViewport(
        self,
        width: int | float | dict[str, Any] | None = None,
        height: int | float | None = None,
        options: dict[str, Any] | None = None,
        *,
        scale: float = 1.0,
        deviceScaleFactor: float | None = None,
        mobile: bool | None = None,
        reset: bool = False,
    ) -> TabHandle:
        self._fresh = False
        w = width
        h = height
        sc = float(deviceScaleFactor if deviceScaleFactor is not None else scale)
        mob = mobile
        do_reset = reset
        if isinstance(width, dict):
            do_reset = bool(do_reset or width.get("reset"))
            w = width.get("width")
            h = width.get("height")
            if width.get("scale") is not None or width.get("deviceScaleFactor") is not None:
                sc = float(width.get("scale") or width.get("deviceScaleFactor") or 1.0)
            if "mobile" in width:
                mob = bool(width["mobile"])
        if isinstance(options, dict):
            if options.get("scale") is not None or options.get("deviceScaleFactor") is not None:
                sc = float(options.get("scale") or options.get("deviceScaleFactor") or 1.0)
            if "mobile" in options:
                mob = bool(options["mobile"])
            if options.get("reset"):
                do_reset = True
        if do_reset or w is None or h is None:
            self._pending_steps.append({"op": "viewport", "reset": True})
            return self
        step: dict[str, Any] = {
            "op": "viewport",
            "width": int(round(float(w))),
            "height": int(round(float(h))),
            "scale": sc,
        }
        if mob is not None:
            step["mobile"] = bool(mob)
        self._pending_steps.append(step)
        return self

    viewport = setViewport
    set_viewport = setViewport

    def resetViewport(self) -> TabHandle:
        return self.setViewport(reset=True)

    reset_viewport = resetViewport

    def layoutAudit(
        self,
        widths: list[int] | tuple[int, ...] | dict[str, Any] | None = None,
        height: int = 812,
    ) -> dict[str, Any]:
        self._fresh = False
        if self.has_pending():
            flushed = self.flush(diff=True)
            sess = self.runtime._get_agent_session(self.agent_id)
            if sess.active_outputs is not None and flushed:
                sess.active_outputs.append(flushed)
        if isinstance(widths, dict):
            h = int(widths.get("height") or height)
            w_raw = widths.get("widths")
            w_list = [int(x) for x in w_raw] if w_raw is not None else None
        else:
            h = int(height)
            w_list = [int(x) for x in widths] if widths is not None else None
        return self.runtime.browser.audit_tab_layout(self.tab_id, widths=w_list, height=h)

    layout_audit = layoutAudit

    def errors(self, clear: bool | dict[str, Any] = False) -> list[dict[str, Any]]:
        self._fresh = False
        if self.has_pending():
            flushed = self.flush(diff=True)
            sess = self.runtime._get_agent_session(self.agent_id)
            if sess.active_outputs is not None and flushed:
                sess.active_outputs.append(flushed)
        do_clear = bool(clear.get("clear")) if isinstance(clear, dict) else bool(clear)
        return self.runtime.browser.get_tab_errors(self.tab_id, clear=do_clear)

    def clearErrors(self) -> TabHandle:
        self._fresh = False
        self._pending_steps.append({"op": "clearerrors"})
        return self

    clear_errors = clearErrors

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
        self,
        full_table: bool | dict[str, Any] = False,
        query: str = "",
        *,
        full: bool = False,
        fullPage: bool = False,
        full_page: bool = False,
    ) -> str:
        ft = False
        fp = bool(fullPage or full_page)
        force_full = bool(full)
        q = query
        if isinstance(full_table, dict):
            ft = bool(full_table.get("fullTable") or full_table.get("full_table"))
            fp = bool(fp or full_table.get("fullPage") or full_table.get("full_page"))
            force_full = bool(
                force_full
                or full_table.get("full")
                or (full_table.get("diff") is False)
            )
            q = str(full_table.get("query") or query or "")
        else:
            ft = bool(full_table)
        if self._fresh and not self.has_pending() and not ft and not fp and not q and not force_full:
            self._fresh = False
            return self.initial_state
        use_smart_diff = bool(self.has_pending() and not force_full and not ft and not fp and not q)
        return self.flush(diff=use_smart_diff, full_table=ft, full_page=fp, query=q)

    def flush(
        self,
        diff: bool = True,
        full_table: bool = False,
        query: str = "",
        full_page: bool = False,
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
        if self.chrome_tab_id is not None:
            payload["chromeTabId"] = self.chrome_tab_id
        if self.require_extension:
            payload["requireExtension"] = True
        if self._force_ocr:
            payload["ocr"] = True
            self._force_ocr = False
        if full_table:
            payload["fullTable"] = True
        if full_page:
            payload["fullPage"] = True
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
    _AGENT_TAB_ID_RE = re.compile(r"^(?:[a-zA-Z0-9_.-]+:)?t\d+$")

    def __init__(self, runtime: FasterMacUseRuntime, agent_id: str = "default") -> None:
        self.runtime = runtime
        self.agent_id = agent_id
        self.handles: dict[str, Any] = {}
        self._tab_seq: int = 1

    @staticmethod
    def _looks_like_url_or_domain(val: str) -> bool:
        s = val.strip()
        if not s or " " in s or CuaNamespace._AGENT_TAB_ID_RE.fullmatch(s):
            return False
        if _HAS_SCHEME_RE.match(s) or s.startswith("//"):
            return True
        if s.lower().startswith(("localhost:", "localhost/", "127.0.0.1")):
            return True
        host_part = s.split("/", 1)[0].split("?", 1)[0]
        return "." in host_part and not host_part.startswith(".") and not host_part.endswith(".")

    @property
    def _session_scope(self) -> str | None:
        """This namespace's agent id when it belongs to an identified harness session (`devin1`, `devin1.audit`)."""
        client, _ = self.runtime.browser.clients.lookup(self.agent_id)
        return self.agent_id if client is not None else None

    def _shares_session(self, tab_id: str) -> bool:
        client, _ = self.runtime.browser.clients.lookup(self.agent_id)
        return self.runtime.browser.in_scope(tab_id, client.short_id if client is not None else None)

    def _bind_existing_session(self, sess: Any) -> TabHandle:
        tid = str(sess.tab_id)
        want_mode = "extension" if sess.is_extension else "headless"
        existing = self.handles.get(tid)
        if (
            isinstance(existing, TabHandle)
            and self.runtime.browser._tabs.get(tid) is sess
            and self.runtime.browser._is_session_alive(sess)
            and (existing.mode == want_mode or (existing.mode == "chrome" and sess.is_extension))
        ):
            return existing
        chrome_tid = getattr(sess._raw_page, "chrome_tab_id", None) if sess.is_extension else None
        win_id = getattr(sess._raw_page, "window_id", None) if sess.is_extension else None
        h = TabHandle(
            self.runtime,
            tab_id=tid,
            url=None,
            mode="extension" if sess.is_extension else "headless",
            session_name=str(sess.group_name or self.runtime.browser.group_title(self.agent_id)),
            agent_id=self.agent_id,
            window_id=int(win_id) if win_id is not None else None,
            chrome_tab_id=int(chrome_tid) if chrome_tid is not None else None,
            reuse_existing=True,
            require_extension=bool(sess.is_extension),
        )
        self.handles[tid] = h
        return h

    def _lookup_handle_by_id(self, raw_id: str) -> TabHandle | None:
        key = str(raw_id or "").strip()
        if not key:
            return None
        candidates: list[str] = [key]
        if ":" not in key and self.agent_id != "default":
            candidates.append(f"{self.agent_id}:{key}")
        elif ":" in key:
            bare = key.split(":", 1)[1]
            if bare and bare not in candidates:
                candidates.append(bare)

        # 1. Exact/normalized match in current agent handles
        for cand in candidates:
            h = self.handles.get(cand)
            if isinstance(h, TabHandle):
                b_sess = self.runtime.browser._tabs.get(h.tab_id)
                if b_sess is not None and self.runtime.browser._is_session_alive(b_sess):
                    return h

        # 2. Cross-agent lookup in BrowserEngine._tabs (e.g. default agent inspecting `audit:t1`)
        for cand in candidates:
            # Only match bare `t1` fallback inside BrowserEngine._tabs if exact `key` wasn't agent-qualified
            if cand != key and ":" in key:
                continue
            b_sess = self.runtime.browser._tabs.get(cand)
            if b_sess is not None and self.runtime.browser._is_session_alive(b_sess):
                return self._bind_existing_session(b_sess)

        # 3. Cross-agent handles dict check
        for other_agent in list(self.runtime._agents.values()):
            if other_agent.cua is self:
                continue
            h_other = other_agent.cua.handles.get(key)
            if isinstance(h_other, TabHandle):
                b_sess = self.runtime.browser._tabs.get(h_other.tab_id)
                if b_sess is not None and self.runtime.browser._is_session_alive(b_sess):
                    return self._bind_existing_session(b_sess)
        return None

    def createBrowserTab(
        self,
        browser_id: str | dict[str, Any] = "chrome",
        url: str = "about:blank",
        options: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> TabHandle:
        if isinstance(browser_id, dict):
            opts: dict[str, Any] = dict(browser_id)
            if isinstance(options, dict):
                opts.update(options)
            if kwargs:
                opts.update(kwargs)
            actual_url = str(opts.get("url") or url or "about:blank")
            b_id = str(opts.get("browser") or opts.get("mode") or "chrome")
        elif isinstance(browser_id, str) and (
            browser_id.startswith(("http://", "https://", "file://", "about:"))
            or (url == "about:blank" and self._looks_like_url_or_domain(browser_id) and browser_id not in ("chrome", "headless", "extension", "chrome-extension", "live"))
        ):
            opts = dict(options) if isinstance(options, dict) else {}
            if kwargs:
                opts.update(kwargs)
            actual_url = browser_id
            b_id = str(opts.get("browser") or opts.get("mode") or "chrome")
        else:
            opts = dict(options) if isinstance(options, dict) else {}
            if kwargs:
                opts.update(kwargs)
            actual_url = str(opts.get("url") or url or "about:blank") if url == "about:blank" and opts.get("url") else url
            b_id = str(opts.get("browser") or opts.get("mode") or browser_id or "chrome")

        default_group = self.runtime.browser.group_title(self.agent_id)
        session_name = str(
            opts.get("sessionName")
            or opts.get("session_name")
            or opts.get("group")
            or default_group
        )
        explicit_tid = opts.get("tabId") or opts.get("tab_id") or opts.get("session")
        raw_chrome_tid = opts.get("chromeTabId") if opts.get("chromeTabId") is not None else opts.get("chrome_tab_id")
        chrome_tid = int(raw_chrome_tid) if raw_chrome_tid is not None else None
        reuse_existing = bool(
            opts.get("reuseExisting")
            if opts.get("reuseExisting") is not None
            else opts.get("reuse_existing")
        )
        if reuse_existing and not explicit_tid:
            browser = self.runtime.browser
            for b_tid, b_s in reversed(list(browser._tabs.items())):
                if not self._shares_session(b_tid):
                    continue
                if b_s.is_extension and b_id == "headless":
                    continue
                if not b_s.is_extension and b_id in ("extension", "chrome-extension", "live"):
                    continue
                if not browser._is_session_alive(b_s):
                    continue
                live_ctid = getattr(b_s._raw_page, "chrome_tab_id", None) if b_s.is_extension else None
                if (chrome_tid is not None and live_ctid == chrome_tid) or (
                    chrome_tid is None and _url_matches_target(b_s.last_url, actual_url)
                ):
                    return self._bind_existing_session(b_s)
        prefix = "" if self.agent_id == "default" else f"{self.agent_id}:"
        if explicit_tid:
            tab_id = str(explicit_tid)
        else:
            while f"{prefix}t{self._tab_seq}" in self.handles or f"{prefix}t{self._tab_seq}" in self.runtime.browser._tabs:
                self._tab_seq += 1
            tab_id = f"{prefix}t{self._tab_seq}"
            self._tab_seq += 1
        if b_id in ("extension", "chrome-extension", "live"):
            mode = "extension"
        elif b_id == "chrome":
            mode = "chrome"
        else:
            mode = "headless"
        win_raw = opts.get("window")
        win_filter = str(win_raw) if win_raw else None
        raw_win_id = opts.get("windowId") if opts.get("windowId") is not None else opts.get("window_id")
        win_id = int(raw_win_id) if raw_win_id is not None else None
        req_ext_opt = (
            opts.get("requireExtension")
            if opts.get("requireExtension") is not None
            else opts.get("require_extension")
        )
        require_ext = bool(req_ext_opt) or mode == "extension" or chrome_tid is not None
        # When chromeTabId is explicitly supplied without a URL, preserve its live URL (url=None)
        eff_init_url: str | None = actual_url
        if chrome_tid is not None and (not actual_url or actual_url == "about:blank") and "url" not in opts:
            eff_init_url = None
        h = TabHandle(
            self.runtime,
            tab_id=tab_id,
            url=eff_init_url,
            mode=mode,
            session_name=session_name,
            agent_id=self.agent_id,
            window=win_filter,
            window_id=win_id,
            chrome_tab_id=chrome_tid,
            reuse_existing=reuse_existing,
            require_extension=require_ext,
        )
        self.handles[tab_id] = h
        return h

    def listTabs(self, query: str | dict[str, Any] = "", **kwargs: Any) -> str:
        opts: dict[str, Any] = {}
        q_str = ""
        if isinstance(query, dict):
            opts.update(query)
            q_str = str(opts.get("query") or opts.get("url") or "")
        elif query is not None:
            q_str = str(query)
        if kwargs:
            opts.update(kwargs)
            if not q_str and (opts.get("query") or opts.get("url")):
                q_str = str(opts.get("query") or opts.get("url") or "")
        raw_win_id = opts.get("windowId") if opts.get("windowId") is not None else opts.get("window_id")
        return self.runtime.browser.list_tabs(
            query=q_str,
            window=str(opts["window"]) if opts.get("window") else None,
            window_id=int(raw_win_id) if raw_win_id is not None else None,
            session_name=str(opts.get("sessionName") or opts.get("session_name") or opts.get("group") or "") or None,
            include_internal=bool(opts.get("includeInternal") or opts.get("include_internal")),
            mode=str(opts.get("mode") or "chrome"),
        )

    list_tabs = listTabs

    def getTab(self, criteria: dict[str, Any] | str | int | None = None, **kwargs: Any) -> TabHandle:
        # Direct Chrome integer tabId (`cua.getTab(501)`)
        if isinstance(criteria, int) and not isinstance(criteria, bool) and not kwargs:
            sess = self.runtime.browser.attach_existing_tab(
                chrome_tab_id=int(criteria),
                group_color=self.runtime.browser._get_agent_color(self.agent_id),
                scope=self._session_scope,
            )
            if sess is not None:
                return self._bind_existing_session(sess)
            return self.createBrowserTab({"chromeTabId": int(criteria), "reuseExisting": True})

        if kwargs:
            merged: dict[str, Any] = {}
            if isinstance(criteria, dict):
                merged.update(criteria)
            elif isinstance(criteria, int) and not isinstance(criteria, bool):
                merged["chromeTabId"] = int(criteria)
            elif isinstance(criteria, str) and criteria:
                found_h = self._lookup_handle_by_id(criteria)
                if found_h is not None and not any(k in kwargs for k in ("url", "window", "windowId", "chromeTabId")):
                    return found_h
                if self._looks_like_url_or_domain(criteria):
                    merged["url"] = criteria
                else:
                    merged["window"] = criteria
            merged.update(kwargs)
            criteria = merged

        if isinstance(criteria, str) and criteria:
            stripped = criteria.strip()
            if stripped.isdigit():
                sess = self.runtime.browser.attach_existing_tab(
                    chrome_tab_id=int(stripped),
                    group_color=self.runtime.browser._get_agent_color(self.agent_id),
                    scope=self._session_scope,
                )
                if sess is not None:
                    return self._bind_existing_session(sess)
            found_h = self._lookup_handle_by_id(stripped)
            if found_h is not None:
                return found_h
            # Never spawn an `about:blank` tab with window="<agent>:t1" when a handle ID doesn't exist
            if self._AGENT_TAB_ID_RE.fullmatch(stripped):
                raise RuntimeError(
                    f"Tab handle '{stripped}' not found; call cua.listTabs() to see active tabs"
                )
            needle = stripped.lower()
            ext_connected = self.runtime.browser.extension_connected
            live_candidates: list[TabHandle] = []
            for h in reversed(list(self.handles.values())):
                if isinstance(h, TabHandle):
                    b_s = self.runtime.browser._tabs.get(h.tab_id)
                    if b_s is not None and self.runtime.browser._is_session_alive(b_s):
                        if needle in h.url.lower() or needle in h.title.lower():
                            live_candidates.append(h)
            if live_candidates:
                if ext_connected:
                    for h in live_candidates:
                        b_s = self.runtime.browser._tabs.get(h.tab_id)
                        if b_s is not None and b_s.is_extension:
                            return h
                return live_candidates[0]

            # Check other agent sessions' live tabs before spawning a new tab
            for b_tid, b_s in reversed(list(self.runtime.browser._tabs.items())):
                if self._shares_session(b_tid) and self.runtime.browser._is_session_alive(b_s):
                    if needle in (b_s.last_url or "").lower() or needle in (b_s.last_title or "").lower():
                        if not ext_connected or b_s.is_extension:
                            return self._bind_existing_session(b_s)

            # Attach to matching live Chrome tab via MV3 bridge without navigating/clobbering its URL
            attached = self.runtime.browser.attach_existing_tab(
                url_pattern=stripped,
                window=stripped,
                group_color=self.runtime.browser._get_agent_color(self.agent_id),
                scope=self._session_scope,
            )
            if attached is not None:
                return self._bind_existing_session(attached)

            if self._looks_like_url_or_domain(stripped):
                return self.createBrowserTab({"url": _normalize_nav_url(stripped), "reuseExisting": True})
            return self.createBrowserTab({"window": stripped, "reuseExisting": True})

        if isinstance(criteria, dict):
            raw_ctid = (
                criteria.get("chromeTabId")
                if criteria.get("chromeTabId") is not None
                else criteria.get("chrome_tab_id")
            )
            if raw_ctid is not None:
                sess = self.runtime.browser.attach_existing_tab(
                    chrome_tab_id=int(raw_ctid),
                    group_color=self.runtime.browser._get_agent_color(self.agent_id),
                    scope=self._session_scope,
                )
                if sess is not None:
                    return self._bind_existing_session(sess)
                return self.createBrowserTab({**criteria, "chromeTabId": int(raw_ctid), "reuseExisting": True})

            tid = criteria.get("tabId") or criteria.get("tab_id") or criteria.get("session")
            if tid is not None:
                tid_str = str(tid).strip()
                if tid_str.isdigit():
                    sess = self.runtime.browser.attach_existing_tab(
                        chrome_tab_id=int(tid_str),
                        group_color=self.runtime.browser._get_agent_color(self.agent_id),
                        scope=self._session_scope,
                    )
                    if sess is not None:
                        return self._bind_existing_session(sess)
                found_h = self._lookup_handle_by_id(tid_str)
                if found_h is not None:
                    return found_h
                if self._AGENT_TAB_ID_RE.fullmatch(tid_str) and not criteria.get("url"):
                    raise RuntimeError(
                        f"Tab handle '{tid_str}' not found; call cua.listTabs() to see active tabs"
                    )

            filter_keys = (
                "url",
                "window",
                "windowId",
                "window_id",
                "reuseExisting",
                "reuse_existing",
                "mode",
                "browser",
                "sessionName",
                "session_name",
                "group",
            )
            if any(k in criteria for k in filter_keys):
                want_session = (
                    criteria.get("sessionName")
                    or criteria.get("session_name")
                    or criteria.get("group")
                )
                want_url = criteria.get("url")
                want_win = criteria.get("window")
                want_win_id = (
                    criteria.get("windowId")
                    if criteria.get("windowId") is not None
                    else criteria.get("window_id")
                )
                want_mode_raw = criteria.get("mode") or criteria.get("browser")
                want_mode: str | None = None
                if want_mode_raw:
                    bm = str(want_mode_raw)
                    if bm in ("extension", "chrome-extension", "live"):
                        want_mode = "extension"
                    elif bm == "chrome":
                        want_mode = "chrome"
                    else:
                        want_mode = "headless"

                matched_handles: list[TabHandle] = []
                for h in reversed(list(self.handles.values())):
                    if not isinstance(h, TabHandle):
                        continue
                    b_s = self.runtime.browser._tabs.get(h.tab_id)
                    if b_s is None or not self.runtime.browser._is_session_alive(b_s):
                        continue
                    if want_session and h.session_name != str(want_session):
                        continue
                    if want_mode == "headless" and b_s.is_extension:
                        continue
                    if want_mode == "extension" and not b_s.is_extension:
                        continue
                    if want_win_id is not None and h.window_id != int(want_win_id):
                        continue
                    if want_url and str(want_url) != "about:blank":
                        u_needle = str(want_url).lower()
                        u_stripped = re.sub(r"^[a-z]+://(www\.)?", "", u_needle).rstrip("/")
                        if u_needle not in h.url.lower() and (not u_stripped or u_stripped not in h.url.lower()):
                            continue
                    if want_win:
                        wl = str(want_win).lower()
                        if (
                            wl not in h.url.lower()
                            and wl not in h.title.lower()
                            and not (h.window and wl == h.window.lower())
                        ):
                            continue
                    matched_handles.append(h)

                if matched_handles and want_mode is None and self.runtime.browser.extension_connected:
                    matched_handles.sort(
                        key=lambda item: (
                            0
                            if (
                                self.runtime.browser._tabs.get(item.tab_id) is not None
                                and self.runtime.browser._tabs[item.tab_id].is_extension
                            )
                            else 1
                        )
                    )

                for h in matched_handles:
                    if h.url and h.url != "about:blank":
                        return h
                if matched_handles and not want_url and not want_win:
                    return matched_handles[0]

                # Try non-destructive MV3 attach before creating/navigating a tab
                if want_mode != "headless":
                    attached = self.runtime.browser.attach_existing_tab(
                        url_pattern=str(want_url) if want_url and str(want_url) != "about:blank" else None,
                        window=str(want_win) if want_win else None,
                        window_id=int(want_win_id) if want_win_id is not None else None,
                        session_name=str(want_session) if want_session else None,
                        group_color=self.runtime.browser._get_agent_color(self.agent_id),
                        scope=self._session_scope,
                    )
                    if attached is not None:
                        return self._bind_existing_session(attached)

                opts = dict(criteria)
                if opts.get("url") and str(opts["url"]) != "about:blank":
                    opts["url"] = _normalize_nav_url(str(opts["url"]))
                if "reuseExisting" not in opts and "reuse_existing" not in opts:
                    opts["reuseExisting"] = True
                return self.createBrowserTab(opts)

        for h in reversed(list(self.handles.values())):
            if isinstance(h, TabHandle):
                b_s = self.runtime.browser._tabs.get(h.tab_id)
                if b_s is not None and self.runtime.browser._is_session_alive(b_s) and h.url and h.url != "about:blank":
                    return h
        for h in reversed(list(self.handles.values())):
            if isinstance(h, TabHandle):
                b_s = self.runtime.browser._tabs.get(h.tab_id)
                if b_s is not None and self.runtime.browser._is_session_alive(b_s):
                    return h
        if self.runtime.browser.extension_connected:
            attached = self.runtime.browser.attach_existing_tab(
                group_color=self.runtime.browser._get_agent_color(self.agent_id),
                scope=self._session_scope,
            )
            if attached is not None:
                return self._bind_existing_session(attached)
        return self.createBrowserTab("chrome", "about:blank")

    def getBridgeStatus(self) -> str:
        return self.runtime.browser.execute_batch({"bridgeStatus": True})

    def reloadExtension(self) -> str:
        return self.runtime.browser.execute_batch({"reloadExtension": True})

    def getPerfReport(self, options: dict[str, Any] | int | None = None) -> str:
        from fmu.telemetry import build_perf_report, clear_perf_logs, get_log_dir

        if isinstance(options, int):
            return build_perf_report(days=options)
        if isinstance(options, dict):
            if options.get("clear"):
                removed = clear_perf_logs()
                return f'# fmu-perf cleared={removed} logs="{get_log_dir()}"'
            days = int(options.get("days", 7))
            fmt = str(options.get("format", "markdown"))
            return build_perf_report(days=days, fmt=fmt)
        return build_perf_report()

    def clearPerfLogs(self) -> str:
        from fmu.telemetry import clear_perf_logs, get_log_dir

        removed = clear_perf_logs()
        return f'# fmu-perf cleared={removed} logs="{get_log_dir()}"'

    def getApp(
        self,
        identifier: str,
        window: str | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AppHandle:
        win_str: str | None = None
        if isinstance(window, dict):
            w_val = window.get("window")
            win_str = str(w_val) if w_val else None
        elif window is not None:
            win_str = str(window)
        if kwargs.get("window"):
            win_str = str(kwargs["window"])
        key = f"app:{identifier}:{win_str or ''}"
        if key not in self.handles:
            self.handles[key] = AppHandle(
                self.runtime, identifier, window=win_str, agent_id=self.agent_id
            )
        else:
            h: AppHandle = self.handles[key]
            h.initial_state = self.runtime.mac.execute(
                {"app": identifier, "window": win_str, "agent": self.agent_id}
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

    def getStatus(self) -> str:
        return get_menubar_status(socket_path=self.runtime.mac.socket_path)

    def highlightWindow(self) -> str:
        return highlight_target_window(socket_path=self.runtime.mac.socket_path)

    def toggleZoom(self) -> str:
        return self.runtime.mac.execute({"toggleZoom": True})

    def stop(self, reason: str = "User stopped fmu") -> str:
        return engage_user_stop(reason=reason, socket_path=self.runtime.mac.socket_path)

    def resume(self) -> str:
        return clear_user_stop(socket_path=self.runtime.mac.socket_path)

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


class _JsonProxy:
    """Exposes both JS (JSON.stringify / JSON.parse) and Python (json.dumps / json.loads) APIs."""

    @staticmethod
    def stringify(obj: Any, _replacer: Any = None, indent: int | None = None) -> str:
        return json.dumps(obj, indent=indent if isinstance(indent, int) else None, ensure_ascii=False)

    @staticmethod
    def parse(text: str) -> Any:
        return json.loads(text)

    @staticmethod
    def dumps(*args: Any, **kwargs: Any) -> str:
        return json.dumps(*args, **kwargs)

    @staticmethod
    def loads(*args: Any, **kwargs: Any) -> Any:
        return json.loads(*args, **kwargs)


_JSON_PROXY = _JsonProxy()


def _fmu_len(obj: Any) -> Any:
    if hasattr(obj, "length") and not isinstance(obj, (str, bytes, list, tuple, dict, set)):
        return getattr(obj, "length")
    return len(obj)


def _fmu_includes(obj: Any, sub: Any) -> bool:
    return sub in obj


def _fmu_trim(obj: Any) -> Any:
    return obj.strip() if isinstance(obj, str) else obj


def _fmu_lower(obj: Any) -> str:
    return obj.lower() if isinstance(obj, str) else str(obj).lower()


def _fmu_upper(obj: Any) -> str:
    return obj.upper() if isinstance(obj, str) else str(obj).upper()


def _fmu_startswith(obj: Any, prefix: Any, *rest: Any) -> bool:
    return str(obj).startswith(prefix, *rest)


def _fmu_endswith(obj: Any, suffix: Any, *rest: Any) -> bool:
    return str(obj).endswith(suffix, *rest)


def _fmu_slice(obj: Any, start: Any = None, end: Any = None) -> Any:
    return obj[start:end]


def _fmu_join(recv: Any, sep: Any = ",") -> str:
    if isinstance(recv, str):
        return recv.join(sep)
    return str(sep).join(str(x) for x in recv)


_REPL_SAFE_BUILTINS: dict[str, Any] = {
    "len": len,
    "_fmu_len": _fmu_len,
    "_fmu_includes": _fmu_includes,
    "_fmu_trim": _fmu_trim,
    "_fmu_lower": _fmu_lower,
    "_fmu_upper": _fmu_upper,
    "_fmu_startswith": _fmu_startswith,
    "_fmu_endswith": _fmu_endswith,
    "_fmu_slice": _fmu_slice,
    "_fmu_join": _fmu_join,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "min": min,
    "max": max,
    "sum": sum,
    "sorted": sorted,
    "round": round,
    "abs": abs,
    "any": any,
    "all": all,
    "zip": zip,
    "map": map,
    "filter": filter,
    "reversed": reversed,
    "next": next,
    "repr": repr,
    "enumerate": enumerate,
    "range": range,
    "isinstance": isinstance,
    "type": type,
}

_TAB_CHAIN_METHODS: frozenset[str] = frozenset(
    {
        "goto",
        "navigate",
        "click",
        "moveCursor",
        "move",
        "hover",
        "fill",
        "set",
        "select",
        "press",
        "wait",
        "sleep",
        "waitFor",
        "wait_for",
        "waitUntil",
        "text",
        "readText",
        "readMain",
        "textValue",
        "eval",
        "evaluate",
        "evalValue",
        "evaluateValue",
        "evaluate_value",
        "callTool",
        "saveMacro",
        "ocr",
        "scroll",
        "swipe",
        "drag",
        "screenshot",
        "screenshotValue",
        "setViewport",
        "viewport",
        "set_viewport",
        "resetViewport",
        "reset_viewport",
        "layoutAudit",
        "layout_audit",
        "errors",
        "clearErrors",
        "clear_errors",
        "upload",
        "close",
        "diff",
        "snapshot",
        "flush",
    }
)


def _split_top_level_commas(inner: str) -> list[str]:
    parts: list[str] = []
    cur: list[str] = []
    depth = 0
    for ch in inner:
        if ch in ("(", "[", "{"):
            depth += 1
            cur.append(ch)
        elif ch in (")", "]", "}"):
            depth = max(0, depth - 1)
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def _find_top_level_colon(entry: str) -> int:
    depth = 0
    for idx, ch in enumerate(entry):
        if ch in ("(", "[", "{"):
            depth += 1
        elif ch in (")", "]", "}"):
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0:
            return idx
    return -1


def _rewrite_braces(expr: str) -> str:
    """Recursively transpile JS object literals, shorthand `{ fields, iframes }`, and `...spread` into Python dicts."""
    out: list[str] = []
    i = 0
    n = len(expr)
    while i < n:
        if expr[i] != "{":
            out.append(expr[i])
            i += 1
            continue
        depth = 1
        j = i + 1
        while j < n and depth > 0:
            if expr[j] == "{":
                depth += 1
            elif expr[j] == "}":
                depth -= 1
            j += 1
        if depth != 0:
            out.append(expr[i:])
            break
        inner = expr[i + 1 : j - 1]
        if not inner.strip():
            out.append("{}")
            i = j
            continue
        raw_parts = _split_top_level_commas(inner)
        new_parts: list[str] = []
        for raw_p in raw_parts:
            p = raw_p.strip()
            if not p:
                continue
            if p.startswith("..."):
                new_parts.append(f"**({_rewrite_braces(p[3:].strip())})")
                continue
            if p.startswith("**"):
                new_parts.append(f"**{_rewrite_braces(p[2:].strip())}")
                continue
            colon_idx = _find_top_level_colon(p)
            if colon_idx >= 0:
                k = p[:colon_idx].strip()
                v = _rewrite_braces(p[colon_idx + 1 :].strip())
                if re.fullmatch(r"[a-zA-Z_$][a-zA-Z0-9_$]*", k) and not re.fullmatch(r"__STR_\d+__", k):
                    if k not in ("True", "False", "None"):
                        k = f'"{k}"'
                elif k.startswith("[") and k.endswith("]"):
                    k = f"({_rewrite_braces(k[1:-1].strip())})"
                new_parts.append(f"{k}: {v}")
            else:
                if (
                    re.fullmatch(r"[a-zA-Z_]\w*", p)
                    and not re.fullmatch(r"__STR_\d+__", p)
                    and p not in ("True", "False", "None")
                ):
                    new_parts.append(f'"{p}": {p}')
                else:
                    new_parts.append(_rewrite_braces(p))
        out.append("{" + ", ".join(new_parts) + "}")
        i = j
    return "".join(out)


def _rewrite_value_evals(stmt: str) -> str:
    """Rewrite `.eval(...)` / `.text(...)` to `.evalValue(...)` / `.textValue(...)` when used as a value rather than a chained/standalone step."""
    is_return = stmt.startswith("return ")
    is_assign = bool(
        not is_return
        and not stmt.startswith(("if ", "assert "))
        and re.match(
            r"^(?:[\[\(]?[a-zA-Z_]\w*(?:\s*,\s*[a-zA-Z_]\w*)*[\]\)]?|__OBJ_DESTR__[^=]+)\s*=\s*(?!=)",
            stmt,
        )
    )
    eval_re = re.compile(r"\.(eval|evaluate|text|readText|readMain|screenshot)\s*\(")
    pos = 0
    while True:
        m = eval_re.search(stmt, pos)
        if not m:
            break
        m_name = m.group(1)
        dot_idx = m.start()
        open_paren = m.end() - 1
        outer_depth = 0
        for ch in stmt[:dot_idx]:
            if ch in ("(", "[", "{"):
                outer_depth += 1
            elif ch in (")", "]", "}"):
                outer_depth = max(0, outer_depth - 1)
        depth = 1
        k = open_paren + 1
        while k < len(stmt) and depth > 0:
            if stmt[k] in ("(", "[", "{"):
                depth += 1
            elif stmt[k] in (")", "]", "}"):
                depth -= 1
            k += 1
        if depth != 0:
            break
        after = stmt[k:]
        chain_m = re.match(r"^\s*\.\s*([a-zA-Z_]\w*)", after)
        if chain_m and chain_m.group(1) in _TAB_CHAIN_METHODS:
            pos = open_paren + 1
            continue
        has_trailing_op = bool(after.strip())
        if is_return or is_assign or outer_depth > 0 or has_trailing_op:
            if m_name in ("text", "readText", "readMain"):
                repl = ".textValue("
            elif m_name == "screenshot":
                repl = ".screenshotValue("
            else:
                repl = ".evalValue("
            stmt = stmt[:dot_idx] + repl + stmt[open_paren + 1 :]
            pos = dot_idx + len(repl)
        else:
            pos = open_paren + 1
    return stmt


@dataclass
class AgentSession:
    agent_id: str
    cua: CuaNamespace
    context_tracker: ContextWindowTracker
    repl_env: dict[str, Any]
    lock: Any
    active_outputs: list[str] | None = None


_JS_METHOD_HELPERS: dict[str, str] = {
    "includes": "_fmu_includes",
    "trim": "_fmu_trim",
    "toLowerCase": "_fmu_lower",
    "toUpperCase": "_fmu_upper",
    "startsWith": "_fmu_startswith",
    "endsWith": "_fmu_endswith",
    "slice": "_fmu_slice",
    "join": "_fmu_join",
}
_JS_METHOD_RE = re.compile(r"\.(includes|trim|toLowerCase|toUpperCase|startsWith|endsWith|slice|join)\s*\(")


def _extract_postfix_receiver(expr: str, dot_pos: int) -> tuple[int, int, str]:
    k = dot_pos - 1
    while k >= 0 and expr[k] == " ":
        k -= 1
    atom_end = k + 1
    stepped_bracket = False
    while k >= 0:
        ch = expr[k]
        if ch in (")", "]"):
            close_ch = ch
            open_ch = "(" if close_ch == ")" else "["
            depth = 1
            k -= 1
            while k >= 0 and depth > 0:
                if expr[k] == close_ch:
                    depth += 1
                elif expr[k] == open_ch:
                    depth -= 1
                k -= 1
            stepped_bracket = True
            # Only continue backward if immediately attached to an identifier, `.`, `)`, or `]` (postfix call/index)
            if k >= 0 and (expr[k].isalnum() or expr[k] in ("_", ".", ")", "]")):
                continue
            break
        if ch.isalnum() or ch in ("_", "."):
            k -= 1
            continue
        break
    atom_start = k + 1
    atom = expr[atom_start:atom_end].strip()
    if not stepped_bracket and atom in ("return", "elif", "else", "and", "or", "not", "in", "is", "if", "assert"):
        return atom_start, atom_end, ""
    return atom_start, atom_end, atom


def _rewrite_js_methods(expr: str) -> str:
    """Rewrite common JS String/Array methods (`.includes()`, `.slice()`, `.join()`, `.trim()`, `.startsWith()`) into Python helpers."""
    pos = 0
    while True:
        m = _JS_METHOD_RE.search(expr, pos)
        if not m:
            return expr
        method_name = m.group(1)
        helper_fn = _JS_METHOD_HELPERS[method_name]
        dot_pos = m.start()
        open_paren = m.end() - 1
        atom_start, _atom_end, atom = _extract_postfix_receiver(expr, dot_pos)
        if not atom:
            pos = m.end()
            continue
        depth = 1
        p = open_paren + 1
        while p < len(expr) and depth > 0:
            if expr[p] in ("(", "[", "{"):
                depth += 1
            elif expr[p] in (")", "]", "}"):
                depth -= 1
            p += 1
        if depth != 0:
            return expr
        args_inner = _rewrite_js_methods(expr[open_paren + 1 : p - 1]).strip()
        if args_inner:
            repl = f"{helper_fn}({atom}, {args_inner})"
        else:
            repl = f"{helper_fn}({atom})"
        expr = f"{expr[:atom_start]}{repl}{expr[p:]}"
        pos = 0


def _rewrite_js_length(expr: str) -> str:
    """Rewrite JS `.length` property reads (`s.length`, `arr[0].length`) to `_fmu_len(...)`."""
    pos = 0
    while True:
        m = re.search(r"\.length\b(?!\s*(?:\(|=(?!=)))", expr[pos:])
        if not m:
            return expr
        dot_pos = pos + m.start()
        end_pos = pos + m.end()
        atom_start, _atom_end, atom = _extract_postfix_receiver(expr, dot_pos)
        if not atom:
            pos = end_pos
            continue
        expr = f"{expr[:atom_start]}_fmu_len({atom}){expr[end_pos:]}"
        pos = 0


_RAW_EVAL_CALL_RE = re.compile(r"\.(?:eval|evaluate|evalValue|evaluateValue|evaluate_value)\s*\(")
_RAW_JS_FN_START_RE = re.compile(
    r"^(?:async\s+)?(?:function\b|\([^()]*\)\s*=>|[a-zA-Z_$][a-zA-Z0-9_$]*\s*=>)"
)
_JS_REGEX_PREV_CHARS = frozenset("=([,:!&|?{};+-*%^~<>")


def _scan_js_callback_end(src: str, start: int) -> int:
    """Scan from right after `.eval(` to the end of the callback arg (`)` at depth 0 or `,` at depth 1)."""
    n = len(src)
    i = start
    depth = 1
    in_str: str | None = None
    in_regex = False
    in_regex_cls = False
    in_line_comment = False
    in_block_comment = False
    escape = False
    last_sig = "("
    last_word = ""

    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue
        if escape:
            escape = False
            i += 1
            continue
        if in_str is not None:
            if ch == "\\":
                escape = True
            elif ch == in_str:
                in_str = None
                last_sig = '"'
                last_word = ""
            i += 1
            continue
        if in_regex:
            if ch == "\\":
                escape = True
            elif ch == "[":
                in_regex_cls = True
            elif ch == "]" and in_regex_cls:
                in_regex_cls = False
            elif ch == "/" and not in_regex_cls:
                in_regex = False
                last_sig = "/"
                last_word = ""
            i += 1
            continue
        if ch == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if ch in ("'", '"', "`"):
            in_str = ch
            i += 1
            continue
        if ch == "/" and (
            last_sig in _JS_REGEX_PREV_CHARS
            or last_word in ("return", "typeof", "void", "delete", "throw", "case", "in", "of")
        ):
            in_regex = True
            in_regex_cls = False
            i += 1
            continue
        if ch in ("(", "[", "{"):
            depth += 1
            last_sig = ch
            last_word = ""
            i += 1
            continue
        if ch in (")", "]", "}"):
            depth -= 1
            if depth == 0 and ch == ")":
                return i
            last_sig = ch
            last_word = ""
            i += 1
            continue
        if ch == "," and depth == 1:
            return i
        if ch.isalpha() or ch in ("_", "$"):
            w_start = i
            while i < n and (src[i].isalnum() or src[i] in ("_", "$")):
                i += 1
            last_word = src[w_start:i]
            last_sig = last_word[-1]
            continue
        if not ch.isspace():
            last_sig = ch
            last_word = ""
        i += 1
    return -1


def _quote_raw_eval_callbacks(code: str) -> str:
    """Wrap unquoted Playwright-style JS callbacks in `.eval(() => ...)` as JSON code strings before REPL parsing."""
    if "eval" not in code:
        return code
    out: list[str] = []
    n = len(code)
    i = 0
    in_str: str | None = None
    in_line_comment = False
    in_block_comment = False
    escape = False

    while i < n:
        ch = code[i]
        nxt = code[i + 1] if i + 1 < n else ""
        if in_line_comment:
            out.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                out.append("*/")
                in_block_comment = False
                i += 2
            else:
                out.append(ch)
                i += 1
            continue
        if escape:
            out.append(ch)
            escape = False
            i += 1
            continue
        if in_str is not None:
            if len(in_str) == 3 and code[i : i + 3] == in_str:
                out.append(in_str)
                in_str = None
                i += 3
                continue
            out.append(ch)
            if ch == "\\":
                escape = True
            elif len(in_str) == 1 and ch == in_str:
                in_str = None
            i += 1
            continue
        if (ch == "/" and nxt == "/") or ch == "#":
            in_line_comment = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            out.append("/*")
            i += 2
            continue
        if code[i : i + 3] in ('"""', "'''"):
            in_str = code[i : i + 3]
            out.append(in_str)
            i += 3
            continue
        if ch in ("'", '"', "`"):
            in_str = ch
            out.append(ch)
            i += 1
            continue
        if ch == ".":
            m = _RAW_EVAL_CALL_RE.match(code, i)
            if m:
                open_paren = m.end() - 1
                arg_start = open_paren + 1
                ws_end = arg_start
                while ws_end < n and code[ws_end].isspace():
                    ws_end += 1
                if _RAW_JS_FN_START_RE.match(code[ws_end:]):
                    arg_end = _scan_js_callback_end(code, arg_start)
                    if arg_end > ws_end:
                        cb_src = code[ws_end:arg_end].strip()
                        out.append(code[i : open_paren + 1])
                        out.append(json.dumps(f"({cb_src})()", ensure_ascii=False))
                        i = arg_end
                        continue
        out.append(ch)
        i += 1
    return "".join(out)


class FasterMacUseRuntime:
    def __init__(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        import threading

        self._is_mcp_stdio = len(sys.argv) >= 2 and sys.argv[1] in ("mcp", "--mcp")
        self._threading = threading
        self._global_lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="fmu-agent")
        self.mac = MacNativeEngine(in_daemon=True)
        self.browser = BrowserEngine()
        self.bounds_agents: set[str] = set()
        self.mac.bounds_agents = self.browser.bounds_agents = self.bounds_agents
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
        except (OSError, RuntimeError, TimeoutError):
            mac_lines = []
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
        in_line_comment = False
        in_block_comment = False
        escape = False
        depth = 0
        i = 0
        n = len(code)
        while i < n:
            ch = code[i]
            nxt = code[i + 1] if i + 1 < n else ""
            if in_line_comment:
                if ch == "\n":
                    in_line_comment = False
                    if depth == 0:
                        s = "".join(buf).strip()
                        if s:
                            stmts.append(s)
                        buf = []
                    else:
                        buf.append(ch)
                i += 1
                continue
            if in_block_comment:
                if ch == "*" and nxt == "/":
                    in_block_comment = False
                    i += 2
                else:
                    i += 1
                continue
            if escape:
                buf.append(ch)
                escape = False
                i += 1
                continue
            if in_str:
                if ch == "\\":
                    buf.append(ch)
                    escape = True
                    i += 1
                    continue
                if ch == in_str:
                    in_str = None
                buf.append(ch)
                i += 1
                continue
            if ch == "/" and nxt == "/":
                in_line_comment = True
                i += 2
                continue
            if ch == "/" and nxt == "*":
                in_block_comment = True
                i += 2
                continue
            if ch in ('"', "'", "`"):
                in_str = ch
                buf.append(ch)
                i += 1
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
            i += 1
        tail = "".join(buf).strip()
        if tail:
            stmts.append(tail)
        return stmts

    def _transpile_template_literal(self, raw_bt: str) -> str:
        content = raw_bt[1:-1]
        parts: list[str] = []
        lit_buf: list[str] = []
        has_interp = False
        i = 0
        n = len(content)
        while i < n:
            ch = content[i]
            if ch == "\\" and i + 1 < n:
                nxt = content[i + 1]
                esc_map = {"`": "`", "$": "$", "\\": "\\", "n": "\n", "r": "\r", "t": "\t", "'": "'", '"': '"'}
                lit_buf.append(esc_map.get(nxt, "\\" + nxt))
                i += 2
                continue
            if ch == "$" and i + 1 < n and content[i + 1] == "{":
                depth = 1
                k = i + 2
                in_q: str | None = None
                esc = False
                while k < n and depth > 0:
                    c2 = content[k]
                    if esc:
                        esc = False
                        k += 1
                        continue
                    if in_q:
                        if c2 == "\\":
                            esc = True
                        elif c2 == in_q:
                            in_q = None
                        k += 1
                        continue
                    if c2 in ("'", '"', "`"):
                        in_q = c2
                    elif c2 == "{":
                        depth += 1
                    elif c2 == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    k += 1
                if depth == 0:
                    has_interp = True
                    if lit_buf:
                        parts.append(json.dumps("".join(lit_buf), ensure_ascii=False))
                        lit_buf = []
                    expr_py = self._normalize_js_stmt(content[i + 2 : k])
                    parts.append(f"str({expr_py})")
                    i = k + 1
                    continue
            lit_buf.append(ch)
            i += 1
        if not has_interp:
            return json.dumps("".join(lit_buf), ensure_ascii=False)
        if lit_buf:
            parts.append(json.dumps("".join(lit_buf), ensure_ascii=False))
        if not parts:
            return '""'
        if len(parts) == 1:
            return parts[0]
        return "(" + " + ".join(parts) + ")"

    def _normalize_js_stmt(self, stmt: str) -> str:
        strings: list[str] = []

        def _save_str(m: re.Match[str]) -> str:
            raw_s = m.group(0)
            if raw_s.startswith("`") and raw_s.endswith("`"):
                raw_s = self._transpile_template_literal(raw_s)
            elif not raw_s.startswith(('"""', "'''")) and ("\n" in raw_s or "\r" in raw_s):
                raw_s = re.sub(r"\\\r?\n", "", raw_s)
                raw_s = raw_s.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\r")
            strings.append(raw_s)
            return f"__STR_{len(strings) - 1}__"

        masked = re.sub(
            r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:\\[\s\S]|[^"\\])*"|\'(?:\\[\s\S]|[^\'\\])*\'|`(?:\\[\s\S]|[^`\\])*`',
            _save_str,
            stmt,
        )
        masked = re.sub(r"/\*[\s\S]*?\*/", " ", masked)
        masked = re.sub(r"//.*$", "", masked, flags=re.MULTILINE).strip()
        if not masked:
            return ""
        masked = re.sub(r"\s+", " ", masked).strip()
        masked = re.sub(r"^(?:let|const|var)\s+", "", masked)
        masked = re.sub(r"\bawait\s+", "", masked)
        masked = masked.replace("!==", "!=").replace("===", "==")
        masked = re.sub(r"!(?!=)\s*", " not ", masked)
        masked = masked.replace("&&", " and ").replace("||", " or ")
        masked = re.sub(r"\btrue\b", "True", masked)
        masked = re.sub(r"\bfalse\b", "False", masked)
        masked = re.sub(r"\bnull\b", "None", masked)
        masked = re.sub(r"\bundefined\b", "None", masked)
        obj_destr_m = re.match(
            r"^\{\s*([a-zA-Z_]\w*(?:\s*,\s*[a-zA-Z_]\w*)*)\s*\}\s*=\s*(?!=)(.+)$",
            masked,
        )
        if obj_destr_m:
            keys_csv = ",".join(k.strip() for k in obj_destr_m.group(1).split(",") if k.strip())
            rhs_norm = _rewrite_braces(obj_destr_m.group(2).strip())
            masked = f"__OBJ_DESTR__{keys_csv}__ = {rhs_norm}"
        else:
            masked = _rewrite_braces(masked)
        masked = _rewrite_value_evals(masked)
        masked = _rewrite_js_methods(masked)
        masked = _rewrite_js_length(masked)
        for idx, s in enumerate(strings):
            masked = masked.replace(f"__STR_{idx}__", s)
        return masked.strip()

    def execute_repl_code(self, code: str, agent_id: str = "default") -> str:
        from fmu.telemetry import _telemetry_ctx, reset_telemetry_context, set_telemetry_context

        sess = self._get_agent_session(agent_id)
        with sess.lock:
            outer_ep = getattr(_telemetry_ctx, "entrypoint", None)
            outer_bid = getattr(_telemetry_ctx, "batch_id", None)
            bid = outer_bid or f"repl-{int(time.time() * 1000) % 1_000_000_000:x}"
            prev_ctx = set_telemetry_context(entrypoint=outer_ep or "repl", batch_id=bid)
            raw_stmts = self._split_statements(_quote_raw_eval_callbacks(code))
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
            sess.repl_env["JSON"] = _JSON_PROXY
            sess.active_outputs = outputs
            eval_globals = {"__builtins__": _REPL_SAFE_BUILTINS}

            try:
                for stmt in lines:
                    stmt_allows_stopped = "cua.resume" in stmt or "cua.getStatus" in stmt or "cua.stop" in stmt
                    if not stmt_allows_stopped and is_user_stopped():
                        return USER_STOPPED_ERROR
                    obj_destr_exec = re.match(r"^__OBJ_DESTR__([a-zA-Z0-9_,]+)__\s*=\s*(.+)$", stmt, re.DOTALL)
                    tuple_assign_m = (
                        None
                        if obj_destr_exec
                        else re.match(
                            r"^([\[\(]?[a-zA-Z_]\w*(?:\s*,\s*[a-zA-Z_]\w*)+[\]\)]?)\s*=\s*(?!=)(.+)$",
                            stmt,
                            re.DOTALL,
                        )
                    )
                    assign_m = (
                        None
                        if (obj_destr_exec or tuple_assign_m)
                        else re.match(r"^([a-zA-Z_]\w*)\s*=\s*(?!=)(.+)$", stmt, re.DOTALL)
                    )
                    if obj_destr_exec:
                        keys = [k for k in obj_destr_exec.group(1).split(",") if k]
                        rhs = obj_destr_exec.group(2).strip()
                        if last_handle is not None and last_handle.has_pending():
                            outputs.append(last_handle.flush())
                            if is_user_stopped():
                                return USER_STOPPED_ERROR
                        val = eval(rhs, eval_globals, sess.repl_env)
                        for k in keys:
                            sess.repl_env[k] = val.get(k) if isinstance(val, dict) else getattr(val, k, None)
                    elif tuple_assign_m and not stmt.startswith(("if ", "assert ", "return ")):
                        raw_lhs = tuple_assign_m.group(1).strip().lstrip("[(").rstrip("])")
                        names = [n.strip() for n in raw_lhs.split(",") if n.strip()]
                        rhs = tuple_assign_m.group(2).strip()
                        if last_handle is not None and last_handle.has_pending():
                            outputs.append(last_handle.flush())
                            if is_user_stopped():
                                return USER_STOPPED_ERROR
                        val = list(eval(rhs, eval_globals, sess.repl_env))
                        for idx_n, nm in enumerate(names):
                            sess.repl_env[nm] = val[idx_n] if idx_n < len(val) else None
                    elif assign_m and not stmt.startswith(("if ", "assert ", "return ")):
                        var_name = assign_m.group(1).strip()
                        rhs = assign_m.group(2).strip()
                        if last_handle is not None and last_handle.has_pending():
                            outputs.append(last_handle.flush())
                            if is_user_stopped():
                                return USER_STOPPED_ERROR
                        val = eval(rhs, eval_globals, sess.repl_env)
                        sess.repl_env[var_name] = val
                        if isinstance(val, (AppHandle, TabHandle)):
                            last_handle = val
                            if len(lines) == 1:
                                val._fresh = False
                                outputs.append(val.initial_state)
                    elif stmt.startswith("return "):
                        if last_handle is not None and last_handle.has_pending():
                            outputs.append(last_handle.flush())
                            if is_user_stopped():
                                return USER_STOPPED_ERROR
                        val = eval(stmt[7:].strip(), eval_globals, sess.repl_env)
                        if isinstance(val, (AppHandle, TabHandle)):
                            outputs.append(val.flush())
                            last_handle = None
                        elif val is not None:
                            outputs.append(str(val))
                        break
                    else:
                        val = eval(stmt, eval_globals, sess.repl_env)
                        if isinstance(val, (AppHandle, TabHandle)):
                            if last_handle is not None and last_handle is not val and last_handle.has_pending():
                                outputs.append(last_handle.flush())
                            last_handle = val
                        elif isinstance(val, str):
                            outputs.append(val)
                        elif val is not None:
                            try:
                                outputs.append(json.dumps(val, ensure_ascii=False))
                            except Exception:
                                outputs.append(str(val))
                    if not stmt_allows_stopped and is_user_stopped():
                        return USER_STOPPED_ERROR

                if last_handle is not None and last_handle.has_pending():
                    if is_user_stopped():
                        return USER_STOPPED_ERROR
                    outputs.append(last_handle.flush())
                for h in list(sess.cua.handles.values()):
                    if isinstance(h, (AppHandle, TabHandle)) and h.has_pending():
                        if is_user_stopped():
                            return USER_STOPPED_ERROR
                        outputs.append(h.flush())
                if last_handle is not None and not outputs and last_handle._fresh:
                    last_handle._fresh = False
                    outputs.append(last_handle.initial_state)
            finally:
                sess.active_outputs = None
                reset_telemetry_context(prev_ctx)

            result = "\n".join(outputs).strip()
            sess.context_tracker.record_turn("repl", result)
            return result

    def execute_parallel(self, tasks: list[dict[str, Any]]) -> str:
        from fmu.telemetry import reset_telemetry_context, set_telemetry_context

        if is_user_stopped():
            return USER_STOPPED_ERROR
        if not tasks:
            return "# parallel tasks=0"
        par_bid = f"par-{int(time.time() * 1000) % 1_000_000_000:x}"
        notify_menubar_status(
            "working",
            surface="parallel",
            target=f"{len(tasks)} agents",
            op="swarm",
            agent="parallel",
            step_count=len(tasks),
        )

        def _run_subtask(sub_payload: dict[str, Any]) -> str:
            prev_c = set_telemetry_context(entrypoint="parallel", batch_id=par_bid)
            try:
                return self.execute(sub_payload)
            finally:
                reset_telemetry_context(prev_c)

        try:
            futures = []
            for idx, sub_req in enumerate(tasks):
                req_copy = dict(sub_req)
                if not req_copy.get("agent") and not req_copy.get("cursor"):
                    req_copy["agent"] = f"agent-{idx + 1}"
                futures.append((req_copy["agent"], self._pool.submit(_run_subtask, req_copy)))
            parts: list[str] = []
            for aid, fut in futures:
                try:
                    res = fut.result()
                except Exception as exc:
                    res = f"! error: {type(exc).__name__}: {exc}"
                parts.append(f"=== [{aid}] ===\n{res}")
            return "\n".join(parts)
        finally:
            notify_menubar_status(
                "done",
                surface="parallel",
                target=f"{len(tasks)} agents",
                op="swarm",
                agent="parallel",
            )

    def execute_for_client(self, req: dict[str, Any], ident: CallerIdentity | None) -> str:
        """Daemon entry: give each harness session its own agent namespace, tab group, and color."""
        for dead in self.browser.clients.take_dead():
            self._pool.submit(self._retire_client, dead)
        if ident is None:
            return self.execute(req)
        client = self.browser.clients.client_for(ident)
        return self.execute(self._scope_request(req, client.short_id))

    def _retire_client(self, client: AgentClient) -> None:
        with self._global_lock:
            for key in [k for k in self._agents if k == client.short_id or k.startswith(f"{client.short_id}.")]:
                self._agents.pop(key, None)
        self.browser.retire_client(client)

    @classmethod
    def _scope_request(cls, req: dict[str, Any], base: str) -> dict[str, Any]:
        out = dict(req)
        raw = str(out.pop("cursor", None) or out.get("agent") or "").strip()
        if raw in ("", "default", base) or raw.startswith(f"{base}."):
            out["agent"] = raw if raw.startswith(f"{base}.") else base
        else:
            out["agent"] = f"{base}.{raw}"
        for key in ("session", "tab"):
            tid = out.get(key)
            if isinstance(tid, str) and tid and ":" not in tid and not tid.isdigit():
                out[key] = f"{out['agent']}:{tid}"
        if isinstance(out.get("parallel"), list):
            out["parallel"] = [
                cls._scope_request({**t, "agent": t.get("agent") or t.get("cursor") or f"agent-{i + 1}"}, base)
                for i, t in enumerate(out["parallel"])
                if isinstance(t, dict)
            ]
        return out

    def execute(self, req: dict[str, Any]) -> str:
        if getattr(self, "_is_mcp_stdio", False):
            return execute_via_daemon(req, Path(__file__).resolve().parent / "cli.py")

        if req.get("clickStatusBar"):
            return self.mac.execute({"clickStatusBar": True})

        if req.get("stop"):
            return engage_user_stop(socket_path=self.mac.socket_path)

        if req.get("resume"):
            res_out = clear_user_stop(socket_path=self.mac.socket_path)
            if not req.get("reset") and not req.get("app") and not req.get("url") and not req.get("code"):
                return res_out

        if req.get("statusBar") or req.get("getStatus"):
            return get_menubar_status(socket_path=self.mac.socket_path)

        if req.get("highlightWindow"):
            return highlight_target_window(socket_path=self.mac.socket_path)

        if "liveView" in req:
            return self.mac.execute({"liveView": bool(req["liveView"])})

        if req.get("menuOutline") or req.get("menuAction"):
            return self.mac.execute({k: req[k] for k in ("menuOutline", "menuAction") if req.get(k)})

        if req.get("togglePreview"):
            return self.mac.execute({"togglePreview": True})

        if req.get("toggleZoom"):
            return self.mac.execute({"toggleZoom": True})

        if req.get("dumpUiSnapshots"):
            return self.mac.execute({"dumpUiSnapshots": str(req["dumpUiSnapshots"])})

        if req.get("bridgeStatus"):
            return self.browser.execute_batch({"bridgeStatus": True})

        if req.get("agentSessions"):
            sessions = self.browser.agent_sessions()
            if isinstance(req["agentSessions"], dict) and req["agentSessions"].get("format") == "json":
                return json.dumps(sessions, ensure_ascii=False)
            lines = [f"# agent sessions count={len(sessions)}"]
            for ses in sessions:
                lines.append(
                    f'@session id={ses["id"]} label="{ses["label"]}" color={ses["color"]} '
                    f'tabs={ses["tabs"]} idle={ses["idleSec"]}s'
                )
            return "\n".join(lines)

        if req.get("closeAgentTabs"):
            if req.get("all"):
                scope = None
            elif req.get("owner"):
                client, _ = self.browser.clients.lookup(str(req["owner"]))
                if client is None:
                    return "# web agent tabs closed chrome=0 playwright=0"
                scope = client.short_id
            else:
                client, _ = self.browser.clients.lookup(str(req.get("agent") or ""))
                scope = client.short_id if client is not None else None
            out = self.browser.close_agent_tabs(scope=scope)
            with self._global_lock:
                for a_sess in self._agents.values():
                    for hid, h in list(a_sess.cua.handles.items()):
                        if isinstance(h, TabHandle) and h.tab_id not in self.browser._tabs:
                            a_sess.cua.handles.pop(hid, None)
            return out

        if req.get("listTabs"):
            return self.browser.execute_batch(req)

        if "perfReport" in req and req["perfReport"]:
            from fmu.telemetry import build_perf_report, clear_perf_logs, get_log_dir

            pr = req["perfReport"]
            if isinstance(pr, dict):
                if pr.get("clear"):
                    removed = clear_perf_logs()
                    return f'# fmu-perf cleared={removed} logs="{get_log_dir()}"'
                return build_perf_report(
                    days=int(pr.get("days", 7)),
                    fmt=str(pr.get("format", "markdown")),
                )
            if isinstance(pr, int) and not isinstance(pr, bool):
                return build_perf_report(days=pr)
            return build_perf_report()

        if is_user_stopped():
            code_str = str(req.get("code") or "")
            if not (code_str and ("cua.resume" in code_str or "cua.getStatus" in code_str)):
                return USER_STOPPED_ERROR

        if req.get("getCursors"):
            return self.get_cursors()

        if req.get("reloadExtension"):
            return self.browser.execute_batch({"reloadExtension": True})

        if "parallel" in req and isinstance(req["parallel"], list):
            return self.execute_parallel(req["parallel"])

        explicit_agent = req.get("agent") or req.get("cursor")
        agent_id = str(explicit_agent or "default").strip() or "default"

        if req.get("reset"):
            if explicit_agent and agent_id not in ("default", "all"):
                self.mac.execute({"reset": True, "agent": agent_id})
                self.browser.reset(agent=agent_id)
                with self._global_lock:
                    for key in [k for k in self._agents if k == agent_id or k.startswith(f"{agent_id}.")]:
                        self._agents.pop(key, None)
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
            if not req.get("bounds"):
                return self.execute_repl_code(str(req["code"]), agent_id=agent_id)
            self.bounds_agents.add(agent_id)
            try:
                return self.execute_repl_code(str(req["code"]), agent_id=agent_id)
            finally:
                self.bounds_agents.discard(agent_id)

        sess = self._get_agent_session(agent_id)
        with sess.lock:
            raw_surface = str(req.get("surface") or ("app" if "app" in req else "web")).lower()
            surface = "web" if raw_surface in ("web", "browser") else raw_surface
            if surface == "app":
                out = self.mac.execute({**req, "agentLabel": self.browser.group_title(agent_id)})
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
        from fmu.telemetry import flush_telemetry

        self.browser.shutdown()
        self.mac.shutdown()
        flush_telemetry()


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
                except OSError:
                    pass
                return
            ident = resolve_caller(peer_pid(conn), label=str(req.get("sessionLabel") or "").strip() or None)
            resp = runtime.execute_for_client(req, ident)
            conn.sendall((resp + "\n").encode("utf-8"))
        except Exception as exc:
            try:
                conn.sendall(f"Error: {exc}\n".encode("utf-8"))
            except OSError:
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
        except (OSError, ValueError):
            pass
        lock_fp.close()
        lock_path.unlink(missing_ok=True)


MCP_TOOL_SCHEMA = {
    "name": "cua",
    "description": (
        "Background Chrome ('Agent' Tab Group) & macOS AX control with multi-cursor isolation, stateful REPL (`code`), "
        "or `steps` batching and `+/~/-` UID diffs. Zero focus/cursor theft. "
        "Pass `code` (`tab=cua.createBrowserTab('chrome',url); tab.click('@u1').waitFor('Confirmed')`, `cua.listTabs()`, `tab.text()`, or `app=cua.getApp('Calculator'); app.click('@a6')`), "
        "`{app|url, steps, agent}`, or `{parallel:[...]}`. "
        "Each agent session gets its own Chrome tab group; pass `sessionLabel` (task/project name) to title it."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "agent": {"type": "string"},
            "sessionLabel": {"type": "string"},
            "parallel": {"type": "array", "items": {"type": "object"}},
            "surface": {"type": "string", "enum": ["web", "browser", "app"]},
            "app": {"type": "string"},
            "window": {"type": "string"},
            "windowId": {"type": "integer"},
            "chromeTabId": {"type": "integer"},
            "url": {"type": "string"},
            "mode": {"type": "string", "enum": ["chrome", "extension", "headless"]},
            "requireExtension": {"type": "boolean"},
            "reuseExisting": {"type": "boolean"},
            "listTabs": {"type": ["boolean", "string", "object"]},
            "bridgeStatus": {"type": "boolean"},
            "reloadExtension": {"type": "boolean"},
            "perfReport": {"type": ["boolean", "object"]},
            "statusBar": {"type": "boolean"},
            "highlightWindow": {"type": "boolean"},
            "toggleZoom": {"type": "boolean"},
            "stop": {"type": "boolean"},
            "resume": {"type": "boolean"},
            "session": {"type": "string"},
            "query": {"type": "string"},
            "diff": {"type": "boolean"},
            "fullTable": {"type": "boolean"},
            "fullPage": {"type": "boolean"},
            "steps": {"type": "array", "items": {"type": "object"}},
            "reset": {"type": "boolean"},
        },
    },
}

