# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Faster Mac Use; Section 7 attribution terms in ATTRIBUTION.md.
"""Browser Interaction Engine: Unfocused 'Agent' tabs, Outbound MV3 WebSocket Bridge, Multi-Cursor Isolation, Focus Emulation, WeakRef UIDs, Native + Virtual WebMCP Macros, <canvas> Vision OCR Fusion, and CRC-32 Delta Reader."""

from __future__ import annotations
import base64
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import queue
import re
import socket
import struct
import sys
import threading
import time
from urllib.parse import urlparse
from typing import Any, Callable

from fmu.prefs import load_prefs
from fmu.sessions import SessionRegistry
from fmu.mac_engine import (
    USER_STOPPED_ERROR,
    interruptible_sleep,
    is_user_stopped,
    notify_menubar_status,
)
from fmu.telemetry import (
    _telemetry_ctx,
    flush_telemetry,
    record_perf_event,
    reset_telemetry_context,
    sanitize_error,
    sanitize_url_target,
    set_telemetry_context,
)


_HAS_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_LINE_UID_RE = re.compile(r"^(?:[+~-] )?(@u\d+) ")
_UID_BOXES_JS = """uids => {
  const out = {};
  const store = window.__fmuStore;
  if (!store) return out;
  for (const uid of uids) {
    const ref = store.uidToRef.get(uid);
    const el = ref && ref.deref();
    if (!el || !el.isConnected || !el.getBoundingClientRect) continue;
    const r = el.getBoundingClientRect();
    out[uid] = [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)];
  }
  return out;
}"""

try:
    from playwright.sync_api import Error as _PlaywrightError
except ImportError:
    _PlaywrightError = None  # type: ignore[assignment]

_BROWSER_OPS_ERRORS: tuple[type[BaseException], ...] = (
    (OSError, RuntimeError, TimeoutError, ValueError, KeyError, AttributeError, TypeError, _PlaywrightError)
    if _PlaywrightError is not None
    else (OSError, RuntimeError, TimeoutError, ValueError, KeyError, AttributeError, TypeError)
)


def _normalize_nav_url(raw_url: str | None) -> str:
    s = str(raw_url or "").strip()
    if not s or s in ("about:blank", "*"):
        return s or "about:blank"
    if _HAS_SCHEME_RE.match(s):
        return s
    if s.startswith("//"):
        return "https:" + s
    low = s.lower()
    if low.startswith(("localhost", "127.0.0.1", "[::1]")):
        return "http://" + s
    return "https://" + s


def _url_matches_target(live_url: str | None, requested_url: str | None) -> bool:
    live = str(live_url or "").strip()
    req = str(requested_url or "").strip()
    if not live or not req or req in ("about:blank", "*"):
        return False
    if live == req:
        return True
    live_l = live.lower()
    req_l = req.lower()
    if not _HAS_SCHEME_RE.match(req):
        stripped = re.sub(r"^/+|/+$", "", req_l)
        return bool(stripped) and stripped in live_l
    if req.endswith("/") and live == req[:-1]:
        return True
    if live.endswith("/") and req == live[:-1]:
        return True
    try:
        lp = urlparse(live)
        rp = urlparse(req)
        lh = (lp.hostname or "").lower().removeprefix("www.")
        rh = (rp.hostname or "").lower().removeprefix("www.")
        if lh and lh == rh and (rp.path in ("", "/")) and not rp.query and not rp.fragment:
            return True
    except ValueError:
        pass
    return False


def _same_origin_and_path(u1: str | None, u2: str | None) -> bool:
    if not u1 or not u2:
        return False
    try:
        p1 = urlparse(str(u1))
        p2 = urlparse(str(u2))
        if not p1.scheme or not p1.netloc:
            return False
        return (
            p1.scheme.lower() == p2.scheme.lower()
            and p1.netloc.lower() == p2.netloc.lower()
            and (p1.path.rstrip("/") or "/") == (p2.path.rstrip("/") or "/")
        )
    except Exception:
        return False


def _fnv1a32(text: str) -> str:
    """Compute unsigned 32-bit FNV-1a hex digest matching JS charCodeAt hashes."""
    h = 0x811C9DC5
    for ch in text:
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


def _normalize_field_value(text: str) -> str:
    """Strip spaces, dashes, and slashes for formatted PAN/expiry comparison."""
    return re.sub(r"[\s\-/]+", "", str(text or ""))


_CDP_SPECIAL_KEYS: dict[str, tuple[str, str, int, str | None]] = {
    "backspace": ("Backspace", "Backspace", 8, None),
    "tab": ("Tab", "Tab", 9, None),
    "enter": ("Enter", "Enter", 13, "\r"),
    "return": ("Enter", "Enter", 13, "\r"),
    "escape": ("Escape", "Escape", 27, None),
    "esc": ("Escape", "Escape", 27, None),
    "space": (" ", "Space", 32, " "),
    "end": ("End", "End", 35, None),
    "home": ("Home", "Home", 36, None),
    "arrowleft": ("ArrowLeft", "ArrowLeft", 37, None),
    "left": ("ArrowLeft", "ArrowLeft", 37, None),
    "arrowup": ("ArrowUp", "ArrowUp", 38, None),
    "up": ("ArrowUp", "ArrowUp", 38, None),
    "arrowright": ("ArrowRight", "ArrowRight", 39, None),
    "right": ("ArrowRight", "ArrowRight", 39, None),
    "arrowdown": ("ArrowDown", "ArrowDown", 40, None),
    "down": ("ArrowDown", "ArrowDown", 40, None),
    "delete": ("Delete", "Delete", 46, None),
    "del": ("Delete", "Delete", 46, None),
}

_CDP_CHAR_KEYS: dict[str, tuple[str, int, bool]] = {
    " ": ("Space", 32, False),
    "-": ("Minus", 189, False),
    "_": ("Minus", 189, True),
    "=": ("Equal", 187, False),
    "+": ("Equal", 187, True),
    "[": ("BracketLeft", 219, False),
    "{": ("BracketLeft", 219, True),
    "]": ("BracketRight", 221, False),
    "}": ("BracketRight", 221, True),
    "\\": ("Backslash", 220, False),
    "|": ("Backslash", 220, True),
    ";": ("Semicolon", 186, False),
    ":": ("Semicolon", 186, True),
    "'": ("Quote", 222, False),
    '"': ("Quote", 222, True),
    ",": ("Comma", 188, False),
    "<": ("Comma", 188, True),
    ".": ("Period", 190, False),
    ">": ("Period", 190, True),
    "/": ("Slash", 191, False),
    "?": ("Slash", 191, True),
    "`": ("Backquote", 192, False),
    "~": ("Backquote", 192, True),
    "!": ("Digit1", 49, True),
    "@": ("Digit2", 50, True),
    "#": ("Digit3", 51, True),
    "$": ("Digit4", 52, True),
    "%": ("Digit5", 53, True),
    "^": ("Digit6", 54, True),
    "&": ("Digit7", 55, True),
    "*": ("Digit8", 56, True),
    "(": ("Digit9", 57, True),
    ")": ("Digit0", 48, True),
}


def _parse_cdp_key_spec(raw_key: str) -> dict[str, Any]:
    """Translate a key or modifier chord into CDP Input.dispatchKeyEvent fields."""
    key_str = str(raw_key or "Enter")
    modifiers = 0
    if len(key_str) > 1 and "+" in key_str:
        parts = [p.strip() for p in key_str.split("+") if p.strip()]
        if key_str.endswith("+") and parts:
            parts.append("+")
        main = parts[-1] if parts else key_str
        for mod in parts[:-1]:
            m = mod.lower()
            if m in ("alt", "opt", "option"):
                modifiers |= 1
            elif m in ("ctrl", "control"):
                modifiers |= 2
            elif m in ("meta", "cmd", "command", "super"):
                modifiers |= 4
            elif m == "shift":
                modifiers |= 8
            elif m == "cmdorctrl":
                modifiers |= 4 if sys.platform == "darwin" else 2
    else:
        main = key_str

    low = main.lower()
    commands: list[str] = []
    if low in _CDP_SPECIAL_KEYS:
        key_val, code, vk, txt = _CDP_SPECIAL_KEYS[low]
        if modifiers == 0 and low == "backspace":
            commands = ["deleteBackward"]
        elif modifiers == 0 and low in ("delete", "del"):
            commands = ["deleteForward"]
        return {
            "key": key_val,
            "code": code,
            "vk": vk,
            "modifiers": modifiers,
            "text": txt if (modifiers & 7) == 0 else None,
            "unmodifiedText": txt if (modifiers & 7) == 0 else None,
            "commands": commands,
        }

    if len(main) == 1:
        ch = main
        if "0" <= ch <= "9":
            code = f"Digit{ch}"
            vk = ord(ch)
        elif "a" <= ch.lower() <= "z":
            code = f"Key{ch.upper()}"
            vk = ord(ch.upper())
            if ch.isupper():
                modifiers |= 8
            if (modifiers & (4 | 2)) and not (modifiers & (1 | 8)) and ch.lower() == "a":
                commands = ["selectAll"]
        elif ch in _CDP_CHAR_KEYS:
            code, vk, need_shift = _CDP_CHAR_KEYS[ch]
            if need_shift:
                modifiers |= 8
        else:
            code = ""
            vk = ord(ch.upper()) if ord(ch) < 128 else 0
        no_cmd = (modifiers & (1 | 2 | 4)) == 0
        txt = ch if no_cmd and ord(ch) >= 32 else None
        unmod = (ch.lower() if "A" <= ch <= "Z" else ch) if no_cmd and ord(ch) >= 32 else None
        return {
            "key": ch,
            "code": code,
            "vk": vk,
            "modifiers": modifiers,
            "text": txt,
            "unmodifiedText": unmod,
            "commands": commands,
        }

    return {
        "key": main,
        "code": main,
        "vk": 0,
        "modifiers": modifiers,
        "text": None,
        "unmodifiedText": None,
        "commands": commands,
    }


_IFRAME_PROBE_JS = r"""(arg) => {
  const isEditable = (el) => {
    if (!el || el.disabled) return false;
    const tag = (el.tagName || "").toLowerCase();
    if (tag === "textarea") return true;
    if (tag === "input") {
      const tp = String(el.type || "text").toLowerCase();
      return !["hidden", "checkbox", "radio", "button", "submit", "reset", "file", "image"].includes(tp);
    }
    const ce = el.getAttribute && el.getAttribute("contenteditable");
    return Boolean(el.isContentEditable || ce === "true" || ce === "" || ce === "plaintext-only");
  };
  const isVisibleEditable = (el) => {
    if (!isEditable(el)) return false;
    try {
      const r = el.getBoundingClientRect();
      const st = window.getComputedStyle(el);
      return r.width > 0 && r.height > 0 && st.display !== "none" && st.visibility !== "hidden";
    } catch (_) {
      return true;
    }
  };
  let a = document.activeElement;
  while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;
  const findFirstEditableDeep = (root) => {
    const sel = "input:not([type='hidden']):not([type='checkbox']):not([type='radio']):not([type='button']):not([type='submit']):not([type='reset']):not([type='file']):not([type='image']), textarea, [contenteditable='true'], [contenteditable=''], [contenteditable='plaintext-only']";
    const list = root.querySelectorAll ? Array.from(root.querySelectorAll(sel)) : [];
    let fallback = null;
    for (const cand of list) {
      if (isVisibleEditable(cand)) return cand;
      if (!fallback && isEditable(cand)) fallback = cand;
    }
    const all = root.querySelectorAll ? root.querySelectorAll("*") : [];
    for (let i = 0; i < all.length; i++) {
      if (all[i].shadowRoot) {
        const inner = findFirstEditableDeep(all[i].shadowRoot);
        if (inner) return inner;
      }
    }
    return fallback;
  };
  let frameIndex = -1;
  try {
    if (window.parent === window.top) {
      for (let k = 0; k < window.top.length; k++) {
        if (window.top[k] === window) { frameIndex = k; break; }
      }
    }
  } catch (_) {}
  const hasFocus = Boolean(document.hasFocus && document.hasFocus());
  const activeIsEditable = isEditable(a);
  const target = activeIsEditable ? a : findFirstEditableDeep(document);
  if (!target) {
    return { matched: false, frameIndex, hasFocus, activeIsEditable };
  }
  const readVal = () => (
    "value" in target && target.value !== undefined && typeof target.value === "string"
      ? target.value
      : String(target.innerText || target.textContent || "")
  );
  if (arg.action === "probe") {
    return {
      matched: true,
      frameIndex,
      hasFocus,
      activeIsEditable,
      curLen: readVal().length
    };
  }
  if (arg.action === "focus_and_select") {
    try { target.focus({ preventScroll: true }); } catch (_) { try { target.focus(); } catch (_) {} }
    try {
      if (typeof target.select === "function") {
        target.select();
      } else if (window.getSelection && document.createRange) {
        const range = document.createRange();
        range.selectNodeContents(target);
        const selObj = window.getSelection();
        if (selObj) { selObj.removeAllRanges(); selObj.addRange(range); }
      }
    } catch (_) {}
    return {
      matched: true,
      frameIndex,
      hasFocus: Boolean(document.hasFocus && document.hasFocus()),
      activeIsEditable: isEditable(target),
      curLen: readVal().length
    };
  }
  if (arg.action === "force_clear") {
    try { target.focus({ preventScroll: true }); } catch (_) {}
    const tag = (target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") {
      const proto = tag === "textarea" ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
      const desc = proto ? Object.getOwnPropertyDescriptor(proto, "value") : null;
      try {
        if (desc && desc.set) desc.set.call(target, "");
        else target.value = "";
      } catch (_) { target.value = ""; }
    } else {
      target.textContent = "";
    }
    target.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "deleteContentBackward", data: null }));
    target.dispatchEvent(new Event("change", { bubbles: true }));
    return {
      matched: true,
      frameIndex,
      hasFocus: Boolean(document.hasFocus && document.hasFocus()),
      activeIsEditable: isEditable(target),
      curLen: readVal().length
    };
  }
  const actual = readVal();
  const normActual = actual.replace(/[\s\-/]+/g, "");
  const fnv = (s) => {
    let h = 0x811c9dc5;
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 0x01000193) >>> 0;
    }
    return h.toString(16).padStart(8, "0");
  };
  const exactMatch = fnv(actual) === arg.expectedHash && actual.length === arg.expectedLen;
  const normMatch = fnv(normActual) === arg.expectedNormHash && normActual.length === arg.expectedNormLen;
  const bulletMasked = normActual.length > 0 && /^[•*]+$/.test(normActual) && arg.expectedNormLen >= 8 && normActual.length === arg.expectedNormLen;
  return {
    matched: true,
    frameIndex,
    hasFocus,
    activeIsEditable,
    curLen: actual.length,
    actualNormLen: normActual.length,
    verified: Boolean(exactMatch || normMatch || bulletMasked)
  };
}"""


def _short_menubar_target(raw_url: str | None) -> str:
    if not raw_url or raw_url in ("about:blank", "*"):
        return "Chrome"
    try:
        parsed = urlparse(raw_url if "://" in raw_url else f"https://{raw_url}")
        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if not host:
            return "Chrome"
        if host in ("127.0.0.1", "localhost") and parsed.path and parsed.path != "/":
            return f"{host}{parsed.path}"[:18]
        return host[:18]
    except Exception:
        return "Chrome"

_PREVIEW_FILE_LOCK = threading.Lock()
_PREVIEW_SEQ = 0
_SCREENSHOT_SEQ = 0


def _preview_enabled() -> bool:
    if os.environ.get("FMU_PREVIEW") == "0" or os.environ.get("FMU_MENUBAR") == "0":
        return False
    prefs = load_prefs()
    return prefs.get("liveView", True) is not False and prefs.get("livePreview", True) is not False


def _write_web_preview_b64(b64_data: str | None) -> str | None:
    global _PREVIEW_SEQ
    if not b64_data or len(b64_data) < 64 or not _preview_enabled():
        return None
    try:
        raw = base64.b64decode(b64_data)
        if len(raw) < 48:
            return None
        with _PREVIEW_FILE_LOCK:
            _PREVIEW_SEQ = (_PREVIEW_SEQ + 1) % 1_000_000
            tmp_path = Path(f"/tmp/fmu-preview-web-{os.getpid()}-{_PREVIEW_SEQ & 3}.tmp.jpg")
            final_path = Path("/tmp/fmu-preview-web.jpg")
            tmp_path.write_bytes(raw)
            os.replace(tmp_path, final_path)
            return str(final_path)
    except Exception:
        return None


DEFAULT_MAX_EVAL_CHARS = 16000
MAX_INLINE_UPLOAD_BYTES = 25 * 1024 * 1024

_SHORTHAND_STEP_VERBS: tuple[tuple[str, str], ...] = (
    ("click", "target"),
    ("tap", "target"),
    ("fill", "value"),
    ("set", "value"),
    ("type", "text"),
    ("select", "value"),
    ("upload", "files"),
    ("move", "target"),
    ("hover", "target"),
    ("press", "key"),
    ("key", "key"),
    ("menu", "menu"),
    ("waitFor", "target"),
    ("wait_for", "target"),
    ("waitUntil", "target"),
    ("wait", "ms"),
    ("sleep", "ms"),
    ("delay", "ms"),
    ("settle", "ms"),
    ("eval", "code"),
    ("evaluate", "code"),
    ("screenshot", "path"),
    ("ocr", ""),
    ("scroll", "dy"),
    ("drag", ""),
    ("errors", ""),
    ("clearErrors", ""),
    ("goto", "url"),
    ("navigate", "url"),
    ("open", "url"),
)


def _normalize_waitfor_timeout_fields(step: dict[str, Any]) -> dict[str, Any]:
    sop = str(step.get("op") or step.get("action") or "").lower()
    if sop not in ("waitfor", "wait_for", "waituntil"):
        return step
    if "timeoutMs" in step:
        return step
    if "timeout_ms" in step and step["timeout_ms"] is not None:
        out = dict(step)
        out["timeoutMs"] = max(0, int(round(float(out.pop("timeout_ms")))))
        return out
    if "timeout_s" in step or "timeoutSeconds" in step:
        out = dict(step)
        raw_s = out.pop("timeout_s", None)
        if raw_s is None:
            raw_s = out.pop("timeoutSeconds", None)
        if raw_s is not None:
            out["timeoutMs"] = max(0, int(round(float(raw_s) * 1000.0)))
        return out
    if "timeout" in step and step["timeout"] is not None:
        try:
            tv = float(step["timeout"])
        except (TypeError, ValueError):
            return step
        out = dict(step)
        out.pop("timeout", None)
        out["timeoutMs"] = max(0, int(round(tv * 1000.0 if 0 < tv <= 30 else tv)))
        return out
    return step


def normalize_step_dict(step: dict[str, Any]) -> dict[str, Any]:
    """Expand single-verb shorthand steps (e.g. {'click': '@u1'}, {'wait': 300}, {'key': 'Enter'})."""
    if not isinstance(step, dict):
        return step
    if step.get("op") or step.get("action"):
        return _normalize_waitfor_timeout_fields(step)
    for verb, param_key in _SHORTHAND_STEP_VERBS:
        matched_key = verb if verb in step else (verb.lower() if verb.lower() in step else None)
        if matched_key is None:
            continue
        raw_val = step[matched_key]
        out = {k: v for k, v in step.items() if k != matched_key}
        out["op"] = verb
        if isinstance(raw_val, dict):
            for rk, rv in raw_val.items():
                out.setdefault(rk, rv)
        elif param_key and not isinstance(raw_val, bool) and raw_val is not None:
            out.setdefault(param_key, raw_val)
        return _normalize_waitfor_timeout_fields(out)
    return step


def _build_inline_files_payload(
    file_list: list[str],
    max_total_bytes: int = MAX_INLINE_UPLOAD_BYTES,
) -> tuple[list[dict[str, str]], str]:
    """Read local files into base64 File payloads; return (items, skip_reason) where skip_reason explains an empty result."""
    paths = [Path(str(f)).expanduser() for f in file_list]
    try:
        missing = next((p for p in paths if not p.is_file()), None)
        if missing is not None:
            return [], f"file not found: {missing}"
        total_bytes = sum(p.stat().st_size for p in paths)
        if total_bytes > max_total_bytes:
            return [], (
                f"{total_bytes / 1048576:.1f} MB across {len(paths)} file(s) exceeds the "
                f"{max_total_bytes / 1048576:.0f} MB inline cap; upload in smaller batches"
            )
        return [
            {
                "name": p.name,
                "type": mimetypes.guess_type(p.name)[0] or "application/octet-stream",
                "dataB64": base64.b64encode(p.read_bytes()).decode("ascii"),
            }
            for p in paths
        ], ""
    except OSError as exc:
        return [], f"cannot read upload file: {exc}"


_JS_NON_EXPR_STMT_RE = re.compile(
    r"^(?:const|let|var|if|for|while|do|switch|try|throw|return|break|continue|function|class|debugger)\b"
)


def _format_eval_output_with_flag(val: Any, max_chars_override: int | None = None) -> tuple[str, bool]:
    s = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
    if max_chars_override is not None:
        max_chars = int(max_chars_override)
    else:
        try:
            max_chars = int(os.environ.get("FMU_MAX_EVAL_CHARS", str(DEFAULT_MAX_EVAL_CHARS)))
        except ValueError:
            max_chars = DEFAULT_MAX_EVAL_CHARS
    if max_chars > 0 and len(s) > max_chars:
        return (
            f"{s[:max_chars]}... "
            f"[truncated: showing {max_chars} of {len(s)} chars; set FMU_MAX_EVAL_CHARS to increase]",
            True,
        )
    return s, False


def _format_eval_output(val: Any, max_chars_override: int | None = None) -> str:
    return _format_eval_output_with_flag(val, max_chars_override=max_chars_override)[0]


def _is_navigation_error(exc_or_msg: Any) -> bool:
    msg = str(exc_or_msg or "")
    low = msg.lower()
    if any(
        tok in low
        for tok in (
            "inspected target navigated or closed",
            "inspected target navigated",
            "execution context was destroyed",
            "most likely because of a navigation",
            "cannot find context with specified id",
            "frame with the given id was not found",
            "frame was detached",
            "navigating frame was detached",
        )
    ):
        return True
    return "-32000" in low and "navigat" in low


def _scan_js_top_level_py(code: str) -> tuple[str, bool, int, bool]:
    """Scan JS code for top-level `return`, trailing semicolons, last `;` split, and top-level assignment."""
    src = code.strip()
    n = len(src)
    in_str: str | None = None
    in_line_comment = False
    in_block_comment = False
    escape = False
    depth = 0
    has_top_return = False
    semi_positions: list[int] = []
    i = 0
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
        if ch in ("(", "[", "{"):
            depth += 1
            i += 1
            continue
        if ch in (")", "]", "}"):
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth == 0:
            if ch == ";":
                semi_positions.append(i)
            elif (
                ch == "r"
                and src.startswith("return", i)
                and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] in ("_", "$", ".")))
                and (i + 6 >= n or not (src[i + 6].isalnum() or src[i + 6] in ("_", "$")))
            ):
                has_top_return = True
        i += 1

    trimmed = src
    while trimmed.endswith(";"):
        trimmed = trimmed[:-1].rstrip()

    # Recompute non-trailing top-level semicolon split on trimmed
    last_semi = -1
    for pos in semi_positions:
        if pos < len(trimmed) and trimmed[pos + 1 :].strip():
            last_semi = pos

    tail_has_assign = False
    tail_stmt = trimmed[last_semi + 1 :].strip() if last_semi >= 0 else trimmed
    if tail_stmt:
        t_in_str: str | None = None
        t_esc = False
        t_depth = 0
        for idx, ch in enumerate(tail_stmt):
            if t_esc:
                t_esc = False
                continue
            if t_in_str is not None:
                if ch == "\\":
                    t_esc = True
                elif ch == t_in_str:
                    t_in_str = None
                continue
            if ch in ("'", '"', "`"):
                t_in_str = ch
                continue
            if ch in ("(", "[", "{"):
                t_depth += 1
            elif ch in (")", "]", "}"):
                t_depth = max(0, t_depth - 1)
            elif ch == "=" and t_depth == 0:
                prev_c = tail_stmt[idx - 1] if idx > 0 else ""
                next_c = tail_stmt[idx + 1] if idx + 1 < len(tail_stmt) else ""
                if prev_c not in ("=", "!", "<", ">") and next_c not in ("=", ">"):
                    tail_has_assign = True
                    break
    return trimmed, has_top_return, last_semi, tail_has_assign


def _build_js_eval_candidates(raw_code: str) -> list[str]:
    code = str(raw_code or "")
    trimmed, has_top_return, last_semi, tail_has_assign = _scan_js_top_level_py(code)
    if not trimmed:
        return [code]
    if has_top_return:
        return [code]
    candidates: list[str] = [f"return (\n{trimmed}\n)"]
    if last_semi >= 0:
        prefix = trimmed[: last_semi + 1]
        last_stmt = trimmed[last_semi + 1 :].strip()
        if last_stmt and not _JS_NON_EXPR_STMT_RE.match(last_stmt) and not tail_has_assign:
            candidates.append(f"{prefix}\nreturn (\n{last_stmt}\n);")
    candidates.append(code)
    return candidates


_FMU_RUNTIME_VERSION = "0.5.5"

IN_PAGE_RUNTIME_JS = r"""
(() => {
  const FMU_RUNTIME_VERSION = "0.5.5";
  if (window.__fmu && window.__fmu.version === FMU_RUNTIME_VERSION) return true;

  const store = window.__fmuStore || (window.__fmuStore = {
    elToUid: new WeakMap(),
    elToStableLabel: new WeakMap(),
    uidToRef: new Map(),
    uidToMeta: new Map(),
    agentStates: new Map(),
    virtualTools: new Map(),
    ocrTargets: new Map(),
    waiters: new Set(),
    nextUid: 1,
    strictCsp: false,
    mutationSeq: 1,
    observerAttached: false,
    waiterObserverAttached: false,
    navListenerAttached: false,
    navigating: false
  });
  const elToUid = store.elToUid;
  const elToStableLabel = store.elToStableLabel;
  const uidToRef = store.uidToRef;
  const uidToMeta = store.uidToMeta || (store.uidToMeta = new Map());
  const agentStates = store.agentStates;
  const virtualTools = store.virtualTools;
  const ocrTargets = store.ocrTargets;
  const waiters = store.waiters || (store.waiters = new Set());

  function notifyWaiters() {
    if (waiters.size === 0) return;
    for (const fn of Array.from(waiters)) {
      try { fn(); } catch (_) {}
    }
  }

  function tagOf(el) {
    return (el && typeof el.tagName === "string") ? el.tagName.toLowerCase() : "";
  }

  function getShadowRoot(el) {
    const sr = el && (el.shadowRoot || el._sr || el._shadowRoot || el.__shadowRoot);
    return (sr && typeof sr.querySelectorAll === "function" && (sr.nodeType === 11 || sr.nodeType === 1)) ? sr : null;
  }

  function queryAllDeep(sel, root = document) {
    const base = root.querySelectorAll(sel);
    const all = root.querySelectorAll("*");
    let extra = null;
    const ownSr = getShadowRoot(root);
    if (ownSr) {
      extra = Array.from(base).filter(n => n && n.nodeType === 1);
      const ownSub = queryAllDeep(sel, ownSr);
      for (let j = 0; j < ownSub.length; j++) {
        if (ownSub[j] && ownSub[j].nodeType === 1) extra.push(ownSub[j]);
      }
    }
    for (let i = 0; i < all.length; i++) {
      const sr = getShadowRoot(all[i]);
      if (sr) {
        if (!extra) extra = Array.from(base).filter(n => n && n.nodeType === 1);
        const sub = queryAllDeep(sel, sr);
        for (let j = 0; j < sub.length; j++) {
          if (sub[j] && sub[j].nodeType === 1) extra.push(sub[j]);
        }
      }
    }
    return extra || base;
  }

  function queryOneDeep(sel, root = document) {
    const hit = root.querySelector ? root.querySelector(sel) : null;
    if (hit) return hit;
    const ownSr = getShadowRoot(root);
    if (ownSr && ownSr.querySelector) {
      const ownHit = queryOneDeep(sel, ownSr);
      if (ownHit) return ownHit;
    }
    const all = root.querySelectorAll ? root.querySelectorAll("*") : [];
    for (let i = 0; i < all.length; i++) {
      const sr = getShadowRoot(all[i]);
      if (sr && sr.querySelector) {
        const sub = queryOneDeep(sel, sr);
        if (sub) return sub;
      }
    }
    return null;
  }

  function getAgentState(agentId = "default") {
    const key = String(agentId || "default");
    let st = agentStates.get(key);
    if (!st) {
      st = {
        agent: key,
        lastSnapSeq: -1,
        lastLinesByUid: new Map(),
        lastCrc: "00000000",
        lastQuery: "",
        lastFoldTables: true,
        lastFullPage: false,
        cursor: { agent: key, x: 0, y: 0, uid: "", label: "", op: "init" }
      };
      agentStates.set(key, st);
    }
    return st;
  }

  function resetAgent(agentId) {
    if (!agentId || agentId === "all") {
      agentStates.clear();
    } else {
      agentStates.delete(String(agentId));
    }
    return true;
  }

  function registerVirtualTools(tools) {
    if (!tools) return 0;
    const list = Array.isArray(tools) ? tools : Object.values(tools);
    for (const t of list) {
      if (t && t.name && Array.isArray(t.steps)) {
        virtualTools.set(String(t.name), {
          name: String(t.name),
          description: String(t.description || ""),
          steps: t.steps
        });
      }
    }
    return virtualTools.size;
  }

  function registerOcrTargets(items) {
    ocrTargets.clear();
    if (Array.isArray(items)) {
      let idx = 1;
      for (const item of items) {
        if (!item || !item.label) continue;
        const uid = item.uid || ("@ocr" + (idx++));
        ocrTargets.set(uid, {
          uid,
          label: String(item.label),
          x: Math.round(Number(item.x || 0)),
          y: Math.round(Number(item.y || 0)),
          w: Math.round(Number(item.w || 0)),
          h: Math.round(Number(item.h || 0)),
          canvasUid: String(item.canvasUid || "")
        });
      }
    }
    store.mutationSeq++;
    return ocrTargets.size;
  }

  function resolveOcrTarget(target) {
    if (!target || ocrTargets.size === 0) return null;
    const t = String(target).trim();
    if (t.startsWith("@ocr")) {
      return ocrTargets.get(t) || null;
    }
    const lower = t.toLowerCase();
    for (const item of ocrTargets.values()) {
      if (item.label.toLowerCase() === lower) return item;
    }
    for (const item of ocrTargets.values()) {
      if (item.label.toLowerCase().includes(lower)) return item;
    }
    return null;
  }

  function dispatchCanvasClick(ocrItem) {
    const x = ocrItem.x;
    const y = ocrItem.y;
    const targetEl = (ocrItem.canvasUid && resolveEl(ocrItem.canvasUid))
      || document.elementFromPoint(x, y)
      || document.querySelector("canvas")
      || document.body;
    if (!targetEl) return false;
    const evInit = {
      bubbles: true,
      cancelable: true,
      view: window,
      clientX: x,
      clientY: y,
      screenX: x,
      screenY: y,
      button: 0,
      buttons: 1
    };
    try { targetEl.dispatchEvent(new PointerEvent("pointerdown", evInit)); } catch (_) {}
    targetEl.dispatchEvent(new MouseEvent("mousedown", evInit));
    try { targetEl.dispatchEvent(new PointerEvent("pointerup", evInit)); } catch (_) {}
    targetEl.dispatchEvent(new MouseEvent("mouseup", evInit));
    targetEl.dispatchEvent(new MouseEvent("click", evInit));
    store.mutationSeq++;
    return true;
  }

  if (!store.observerAttached) {
    const observer = new MutationObserver(() => {
      store.mutationSeq++;
      notifyWaiters();
    });
    if (document.documentElement) {
      observer.observe(document.documentElement, {
        subtree: true,
        childList: true,
        attributes: true,
        characterData: true
      });
    }
    window.addEventListener("input", () => { store.mutationSeq++; notifyWaiters(); }, true);
    window.addEventListener("change", () => { store.mutationSeq++; notifyWaiters(); }, true);
    store.observerAttached = true;
    store.waiterObserverAttached = true;
  } else if (!store.waiterObserverAttached) {
    const wObs = new MutationObserver(() => { notifyWaiters(); });
    if (document.documentElement) {
      wObs.observe(document.documentElement, {
        subtree: true,
        childList: true,
        attributes: true,
        characterData: true
      });
    }
    window.addEventListener("popstate", () => { store.mutationSeq++; notifyWaiters(); }, true);
    window.addEventListener("hashchange", () => { store.mutationSeq++; notifyWaiters(); }, true);
    store.waiterObserverAttached = true;
  }

  if (!store.navListenerAttached) {
    const markNavigating = () => { store.navigating = true; notifyWaiters(); };
    window.addEventListener("beforeunload", markNavigating, true);
    window.addEventListener("pagehide", markNavigating, true);
    window.addEventListener("popstate", () => { store.mutationSeq++; notifyWaiters(); }, true);
    window.addEventListener("hashchange", () => { store.mutationSeq++; notifyWaiters(); }, true);
    store.navListenerAttached = true;
  }

  function crc32(str) {
    let crc = 0xFFFFFFFF;
    for (let i = 0; i < str.length; i++) {
      crc ^= str.charCodeAt(i) & 0xFF;
      for (let j = 0; j < 8; j++) {
        crc = (crc >>> 1) ^ (0xEDB88320 & -(crc & 1));
      }
    }
    return ((crc ^ 0xFFFFFFFF) >>> 0).toString(16).padStart(8, "0");
  }

  function getUid(el) {
    let uid = elToUid.get(el);
    if (!uid) {
      uid = "@u" + (store.nextUid++);
      elToUid.set(el, uid);
    }
    uidToRef.set(uid, new WeakRef(el));
    return uid;
  }

  function isContentEditableEl(el) {
    if (!el || !el.getAttribute) return false;
    const ce = el.getAttribute("contenteditable");
    return Boolean(
      el.isContentEditable
      || ce === "true"
      || ce === "plaintext-only"
      || (ce === "" && el.hasAttribute("contenteditable"))
    );
  }

  function getCandidateAliases(el) {
    const aliases = [getLabel(el)];
    if (el && el.getAttribute) {
      for (const attr of ["aria-label", "placeholder", "data-placeholder", "aria-placeholder", "data-testid", "name", "title", "id"]) {
        const v = el.getAttribute(attr);
        if (v) aliases.push(cleanStr(v));
      }
      if (el.parentElement && el.parentElement.closest) {
        const anc = el.parentElement.closest("[aria-label], [data-placeholder], [aria-placeholder], [data-testid]");
        if (anc) {
          for (const attr of ["aria-label", "data-placeholder", "aria-placeholder", "data-testid"]) {
            const v = anc.getAttribute(attr);
            if (v) aliases.push(cleanStr(v));
          }
        }
      }
    }
    return aliases.filter(Boolean).map(s => s.toLowerCase());
  }

  const CANDIDATE_SEL = "button, a, input, select, textarea, canvas, iframe, [contenteditable='true'], [contenteditable=''], [contenteditable='plaintext-only'], [role], label, h1, h2, h3, [data-testid], [data-placeholder], [aria-placeholder]";

  function getSearchCandidates() {
    const base = Array.from(queryAllDeep(CANDIDATE_SEL));
    const baseSet = new Set(base);
    const extras = collectInteractivePointerElements(baseSet, 60);
    for (let i = 0; i < extras.length; i++) base.push(extras[i]);
    return base;
  }

  function resolveEl(target) {
    if (!target) return null;
    const t = String(target).trim();
    if (t.startsWith("@ocr")) return null;
    if (t.startsWith("@u") || t.startsWith("@e") || t.startsWith("@a")) {
      const norm = "@u" + t.slice(2);
      const ref = uidToRef.get(norm);
      if (ref) {
        const el = ref.deref();
        if (el && el.isConnected) return el;
      }
      const meta = uidToMeta.get(norm);
      if (meta && meta.label) {
        const targetLbl = meta.label.toLowerCase();
        const candidates = getSearchCandidates();
        let fallbackMatch = null;
        for (const c of candidates) {
          if (!isVisible(c)) continue;
          const cRole = getRole(c);
          const cLbl = getLabel(c, cRole === "card" ? 140 : 80).toLowerCase();
          const aliases = getCandidateAliases(c);
          if (cLbl === targetLbl || aliases.includes(targetLbl)) {
            if (!meta.role || cRole === meta.role) {
              elToUid.set(c, norm);
              uidToRef.set(norm, new WeakRef(c));
              return c;
            }
            if (!fallbackMatch) fallbackMatch = c;
          }
        }
        if (fallbackMatch) {
          elToUid.set(fallbackMatch, norm);
          uidToRef.set(norm, new WeakRef(fallbackMatch));
          return fallbackMatch;
        }
      }
    }
    try {
      const bySel = queryOneDeep(t);
      if (bySel) return bySel;
    } catch (_) {}
    const byId = document.getElementById(t);
    if (byId) return byId;
    const lower = t.toLowerCase();
    const candidates = getSearchCandidates();
    for (const el of candidates) {
      const aliases = getCandidateAliases(el);
      if (aliases.includes(lower)) return el;
    }
    for (const el of candidates) {
      const aliases = getCandidateAliases(el);
      if (aliases.some(a => a.includes(lower))) return el;
    }
    return null;
  }

  function getEffectiveRect(el) {
    if (!el || !el.getBoundingClientRect) {
      return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 };
    }
    const rect = el.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) return rect;
    const style = window.getComputedStyle(el);
    if (style.display === "contents" && el.children && el.children.length > 0) {
      let minL = Infinity, minT = Infinity, maxR = -Infinity, maxB = -Infinity;
      const stack = Array.from(el.children);
      while (stack.length > 0) {
        const child = stack.shift();
        if (!child || !child.getBoundingClientRect) continue;
        const cs = window.getComputedStyle(child);
        if (cs.display === "none" || cs.visibility === "hidden" || cs.opacity === "0") continue;
        const cr = child.getBoundingClientRect();
        if (cr.width > 0 && cr.height > 0) {
          if (cr.left < minL) minL = cr.left;
          if (cr.top < minT) minT = cr.top;
          if (cr.right > maxR) maxR = cr.right;
          if (cr.bottom > maxB) maxB = cr.bottom;
        } else if (cs.display === "contents" && child.children && child.children.length > 0) {
          for (let k = 0; k < child.children.length; k++) stack.push(child.children[k]);
        }
      }
      if (minL < Infinity && maxR > minL && maxB > minT) {
        return {
          left: minL,
          top: minT,
          right: maxR,
          bottom: maxB,
          width: maxR - minL,
          height: maxB - minT,
          x: minL,
          y: minT
        };
      }
    }
    return rect;
  }

  function isVisible(el) {
    if (!el || !el.isConnected) return false;
    if (el.hasAttribute && el.hasAttribute("data-fmu-cursor")) return false;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
    const rect = getEffectiveRect(el);
    return rect.width > 0 && rect.height > 0;
  }

  function cleanStr(s, maxLen = 80) {
    const c = String(s || "").replace(/\s+/g, " ").trim();
    return c.length > maxLen ? c.slice(0, maxLen - 1) + "…" : c;
  }

  const CARD_CLASS_RE = /\b(MuiCard-root|MuiCardActionArea-root|[a-zA-Z0-9_-]*[Cc]ard(?:-root|-container|-item|-box|-body)?|[a-zA-Z0-9_-]*[Rr]ate-?(?:card|box|option|tile)|[a-zA-Z0-9_-]*[Vv]ehicle-?(?:card|item|tile))\b/;

  function isCardElement(el) {
    const tag = tagOf(el);
    if (!tag) return false;
    if (tag === "article") return true;
    const cls = typeof el.className === "string" ? el.className : "";
    if (cls && CARD_CLASS_RE.test(cls)) return true;
    return false;
  }

  function getRole(el) {
    const tag = tagOf(el);
    if (tag === "iframe") return "iframe";
    const explicit = el && el.getAttribute ? el.getAttribute("role") : "";
    if (typeof explicit === "string" && explicit) return explicit.toLowerCase();
    if (isContentEditableEl(el)) return "textbox";
    if (tag === "a") return "link";
    if (tag === "button" || tag === "summary") return "button";
    if (tag === "select") return "select";
    if (tag === "textarea") return "textbox";
    if (tag === "canvas") return "canvas";
    if (tag === "input") {
      const type = String((el.getAttribute && el.getAttribute("type")) || "text").toLowerCase();
      if (type === "submit" || type === "button" || type === "reset") return "button";
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      return "input:" + type;
    }
    if (/^h[1-6]$/.test(tag)) return "heading";
    if (isCardElement(el)) return "card";
    if (elToUid.has(el)) {
      const meta = uidToMeta.get(elToUid.get(el));
      if (meta && meta.role) return meta.role;
    }
    return tag;
  }

  function getLabel(el, maxLen = 80) {
    const aria = el.getAttribute("aria-label")
      || el.getAttribute("title")
      || el.getAttribute("placeholder")
      || el.getAttribute("data-placeholder")
      || el.getAttribute("aria-placeholder");
    if (aria) {
      const cleaned = cleanStr(aria, maxLen);
      if (isContentEditableEl(el)) elToStableLabel.set(el, cleaned);
      return cleaned;
    }
    if (el.id) {
      try {
        const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (lbl) return cleanStr(lbl.innerText || lbl.textContent, maxLen);
      } catch (_) {}
    }
    if (isContentEditableEl(el)) {
      const parent = el.parentElement;
      if (parent) {
        const ph = parent.querySelector(".public-DraftEditorPlaceholder-root, [data-placeholder], .placeholder");
        if (ph && ph !== el) {
          const phText = ph.getAttribute("data-placeholder") || ph.innerText || ph.textContent;
          if (phText) {
            const cleaned = cleanStr(phText, maxLen);
            elToStableLabel.set(el, cleaned);
            return cleaned;
          }
        }
        const ancestor = parent.closest && parent.closest("[aria-label], [data-placeholder], [aria-placeholder]");
        if (ancestor) {
          const ancLabel = ancestor.getAttribute("aria-label")
            || ancestor.getAttribute("data-placeholder")
            || ancestor.getAttribute("aria-placeholder");
          if (ancLabel) {
            const cleaned = cleanStr(ancLabel, maxLen);
            if (!elToStableLabel.has(el)) elToStableLabel.set(el, cleaned);
            return elToStableLabel.get(el) || cleaned;
          }
        }
      }
      if (elToStableLabel.has(el)) {
        return elToStableLabel.get(el);
      }
      const fallback = cleanStr(el.getAttribute("name") || el.getAttribute("data-testid") || el.id || "richtext", maxLen);
      elToStableLabel.set(el, fallback);
      return fallback;
    }
    const tag = tagOf(el);
    if (tag === "input" || tag === "textarea" || tag === "select" || tag === "canvas") {
      return cleanStr(el.name || el.id || tag, maxLen);
    }
    if (tag === "iframe") {
      return cleanStr(el.getAttribute("title") || el.id || el.getAttribute("name") || "iframe", maxLen);
    }
    return cleanStr(el.innerText || el.textContent || "", maxLen);
  }

  function isCrossOriginIframeEl(el) {
    if (!el || tagOf(el) !== "iframe") return false;
    try {
      return !(el.contentDocument && el.contentDocument.documentElement);
    } catch (_) {
      return true;
    }
  }

  function getCrossOriginIframeInfo(el) {
    if (tagOf(el) !== "iframe") return null;
    if (el.getAttribute("aria-hidden") === "true") return null;
    const style = window.getComputedStyle(el);
    if (style.pointerEvents === "none") return null;
    const rect = el.getBoundingClientRect();
    if (rect.width < 16 || rect.height < 16) return null;

    const rawSrc = String(el.getAttribute("src") || el.src || "").trim();
    let parsedOrigin = "";
    let parsedHost = "";
    if (rawSrc && rawSrc !== "about:blank" && !rawSrc.startsWith("javascript:")) {
      try {
        const u = new URL(rawSrc, location.href);
        parsedOrigin = u.origin || "";
        parsedHost = u.host || u.hostname || "";
      } catch (_) {}
    }
    let docAccessible = false;
    try {
      docAccessible = Boolean(el.contentDocument && el.contentDocument.documentElement);
    } catch (_) {
      docAccessible = false;
    }
    const hasCrossOriginUrl = Boolean(
      parsedOrigin && parsedOrigin !== "null" && parsedOrigin !== location.origin
    );
    const isIsolated = !docAccessible && Boolean(
      (rawSrc && rawSrc !== "about:blank") || el.hasAttribute("sandbox")
    );
    if (!hasCrossOriginUrl && !isIsolated) return null;

    const idAttr = cleanStr(el.id || "", 80);
    const nameAttr = cleanStr(el.getAttribute("name") || "", 80);
    const titleAttr = cleanStr(el.getAttribute("title") || el.getAttribute("aria-label") || "", 80);
    let assocLabel = "";
    if (el.id) {
      try {
        const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (lbl) assocLabel = cleanStr(lbl.innerText || lbl.textContent, 80);
      } catch (_) {}
    }
    if (!assocLabel && el.parentElement && el.parentElement.id) {
      try {
        const pLbl = document.querySelector(`label[for="${CSS.escape(el.parentElement.id)}"]`);
        if (pLbl) assocLabel = cleanStr(pLbl.innerText || pLbl.textContent, 80);
      } catch (_) {}
    }
    if (!assocLabel && el.closest) {
      const wrapLbl = el.closest("label");
      if (wrapLbl) assocLabel = cleanStr(wrapLbl.innerText || wrapLbl.textContent, 80);
    }
    const originStr = (parsedOrigin && parsedOrigin !== "null")
      ? parsedOrigin
      : (parsedHost || (rawSrc.startsWith("data:") ? "data:" : "cross-origin"));
    const label = titleAttr || assocLabel || idAttr || nameAttr || parsedHost || "hosted-field";
    const ix = Math.round(rect.left);
    const iy = Math.round(rect.top);
    const iw = Math.round(rect.width);
    const ih = Math.round(rect.height);

    let body = `iframe "${label}"`;
    if (idAttr) body += ` id="${idAttr}"`;
    if (nameAttr && nameAttr !== idAttr) body += ` name="${nameAttr}"`;
    else if (nameAttr && !idAttr) body += ` name="${nameAttr}"`;
    else if (nameAttr) body += ` name="${nameAttr}"`;
    if (titleAttr) body += ` title="${titleAttr}"`;
    body += ` origin="${originStr}" (${ix},${iy}) [${iw}x${ih}] [visible, cross-origin]`;
    const searchBlob = `${label} ${idAttr} ${nameAttr} ${titleAttr} ${originStr} iframe`.toLowerCase();
    return { label, body, searchBlob, x: ix, y: iy, w: iw, h: ih, origin: originStr };
  }

  const NON_POINTER_SCAN_TAGS = new Set([
    "html", "body", "head", "script", "style", "link", "meta", "noscript", "template",
    "svg", "path", "g", "circle", "rect", "polygon", "line", "polyline", "defs", "use", "symbol",
    "img", "picture", "source", "video", "audio", "br", "hr",
    "button", "a", "input", "select", "textarea", "label", "option", "optgroup",
    "canvas", "iframe", "table", "thead", "tbody", "tfoot", "tr", "th",
    "ul", "ol", "dl", "nav", "header", "footer", "main", "form", "fieldset",
    "h1", "h2", "h3", "h4", "h5", "h6", "p"
  ]);

  const SEMANTIC_INTERACTIVE_ANCESTOR_SEL = "button, a[href], label, select, textarea, input, summary, [role='button'], [role='link'], [role='tab'], [role='menuitem'], [role='checkbox'], [role='switch'], [role='option'], [role='textbox'], [role='searchbox'], [role='combobox'], [contenteditable='true'], [contenteditable=''], [contenteditable='plaintext-only']";

  function hasDirectClickBinding(el) {
    if (!el) return false;
    if (typeof el.onclick === "function" || (el.hasAttribute && el.hasAttribute("onclick"))) {
      return true;
    }
    if (el.hasAttribute) {
      if (
        el.hasAttribute("@click")
        || el.hasAttribute("x-on:click")
        || el.hasAttribute("(click)")
        || el.hasAttribute("ng-click")
      ) {
        return true;
      }
    }
    try {
      const ownKeys = Object.getOwnPropertyNames(el);
      for (let i = 0; i < ownKeys.length; i++) {
        const k = ownKeys[i];
        if (k.startsWith("__reactProps$") || k.startsWith("__reactEventHandlers$")) {
          const props = el[k];
          if (
            props
            && (
              typeof props.onClick === "function"
              || typeof props.onClickCapture === "function"
              || typeof props.onMouseUp === "function"
              || typeof props.onPointerUp === "function"
            )
          ) {
            return true;
          }
        } else if (k === "__vei" && el.__vei && (el.__vei.onClick || el.__vei.onclick)) {
          return true;
        }
      }
    } catch (_) {}
    return false;
  }

  function collectInteractivePointerElements(semanticSet, maxPointerItems = 60) {
    const root = document.body || document.documentElement;
    if (!root) return [];
    const all = queryAllDeep("*", root);
    const maxScan = Math.min(all.length, 4000);
    const vw = window.innerWidth || 1280;
    const vh = window.innerHeight || 800;
    const results = [];
    const acceptedTextByEl = new Map();

    for (let i = 0; i < maxScan && results.length < maxPointerItems; i++) {
      const el = all[i];
      if (!el || semanticSet.has(el)) continue;
      const tag = tagOf(el);
      if (!tag || NON_POINTER_SCAN_TAGS.has(tag)) continue;
      if (el.getAttribute && (el.getAttribute("aria-hidden") === "true" || el.hasAttribute("disabled"))) continue;
      if (el.getAttribute && el.getAttribute("role")) continue;
      if (el.closest && el.closest(SEMANTIC_INTERACTIVE_ANCESTOR_SEL)) continue;

      const directClick = hasDirectClickBinding(el);
      const tabIdxZero = el.getAttribute && el.getAttribute("tabindex") === "0";
      let style = null;
      if (!directClick) {
        style = window.getComputedStyle(el);
        if (style.cursor !== "pointer") continue;
        if (!tabIdxZero && !isCardElement(el) && el.parentElement) {
          const pStyle = window.getComputedStyle(el.parentElement);
          if (pStyle.cursor === "pointer") continue;
        }
      } else {
        style = window.getComputedStyle(el);
      }
      if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0" || style.pointerEvents === "none") {
        continue;
      }
      const rect = el.getBoundingClientRect();
      if (rect.width < 24 || rect.height < 18) continue;
      if (rect.width > vw * 0.96 && rect.height > vh * 0.85) continue;

      if (el.querySelectorAll) {
        const subCards = el.querySelectorAll(".MuiCard-root, article");
        if (subCards && subCards.length > 2) continue;
      }

      const rawText = String(
        el.getAttribute("aria-label")
        || el.getAttribute("title")
        || el.innerText
        || el.textContent
        || ""
      ).replace(/\s+/g, " ").trim();
      if (rawText.length < 2 || rawText.length > 900) continue;

      // Skip if an already-accepted interactive ancestor has identical normalized text
      let dupAncestor = false;
      let anc = el.parentElement;
      while (anc && anc !== root) {
        if (acceptedTextByEl.has(anc) && acceptedTextByEl.get(anc) === rawText) {
          dupAncestor = true;
          break;
        }
        anc = anc.parentElement;
      }
      if (dupAncestor) continue;

      // Skip if this is merely a styling wrapper around a single semantic link/button with identical text
      let wrapsIdenticalSemantic = false;
      if (el.querySelectorAll) {
        const semKids = el.querySelectorAll("a[href], button, input, select, textarea, [role='button'], [role='link']");
        if (semKids.length === 1 && semanticSet.has(semKids[0])) {
          const kidText = String(semKids[0].innerText || semKids[0].textContent || semKids[0].getAttribute("aria-label") || "").replace(/\s+/g, " ").trim();
          if (kidText === rawText) wrapsIdenticalSemantic = true;
        }
      }
      if (wrapsIdenticalSemantic) continue;

      acceptedTextByEl.set(el, rawText);
      results.push(el);
    }
    return results;
  }

  function classifyInteractiveElementRole(el) {
    if (isCardElement(el)) return "card";
    const rect = el.getBoundingClientRect();
    const txtLen = String(el.innerText || el.textContent || "").replace(/\s+/g, " ").trim().length;
    if (rect.width >= 140 && rect.height >= 72 && txtLen >= 20) return "card";
    return "button";
  }

  function getValue(el) {
    if (isContentEditableEl(el)) {
      return cleanStr(el.innerText || el.textContent || "", 80);
    }
    const tag = tagOf(el);
    if (tag === "input") {
      const type = String((el.getAttribute && el.getAttribute("type")) || "text").toLowerCase();
      if (type === "checkbox" || type === "radio") return el.checked ? "checked" : "unchecked";
      return cleanStr(el.value || "", 60);
    }
    if (tag === "textarea" || tag === "select") {
      return cleanStr(el.value || "", 60);
    }
    if (el.getAttribute("aria-checked")) {
      return el.getAttribute("aria-checked") === "true" ? "checked" : "unchecked";
    }
    return "";
  }

  function getPageFooterScope(el, footerLinkCache) {
    if (!el || !el.closest) return null;
    const footerEl = el.closest("footer, [role='contentinfo']");
    if (!footerEl) return null;
    if (footerEl.closest("dialog, [role='dialog'], [aria-modal='true'], form, main, [role='main']")) {
      return null;
    }
    let linkCount = footerLinkCache.get(footerEl);
    if (linkCount === undefined) {
      linkCount = footerEl.querySelectorAll ? footerEl.querySelectorAll("a[href]").length : 0;
      footerLinkCache.set(footerEl, linkCount);
    }
    return linkCount >= 8 ? footerEl : null;
  }

  function collectPrimaryStaticEntries(capturedSet, capturedLabels, footerLinkCache, foldFooter, maxStaticItems = 28) {
    const entries = [];
    if (maxStaticItems <= 0 || !document.body) return entries;
    const seenLabels = new Set();
    for (const lbl of capturedLabels) {
      if (lbl) seenLabels.add(lbl.toLowerCase());
    }
    const isCoveredByCaptured = (el, normTxt) => {
      if (!normTxt || normTxt.length < 2) return true;
      const lower = normTxt.toLowerCase();
      if (seenLabels.has(lower)) return true;
      let cur = el;
      while (cur && cur !== document.body) {
        if (capturedSet.has(cur)) return true;
        cur = cur.parentElement;
      }
      return false;
    };

    // 1. Static or partially-uncovered data table rows (`<tr>`)
    const allRows = document.querySelectorAll("tbody tr, table tr");
    for (let i = 0; i < allRows.length && entries.length < maxStaticItems; i++) {
      const tr = allRows[i];
      if (!isVisible(tr)) continue;
      if (foldFooter && getPageFooterScope(tr, footerLinkCache)) continue;
      if (tr.closest("nav, [role='navigation']")) continue;
      const cells = tr.cells ? Array.from(tr.cells) : Array.from(tr.querySelectorAll("td"));
      if (cells.length === 0) continue;
      let hasTd = false;
      const uncoveredParts = [];
      let rowHasInteractive = false;
      for (const cell of cells) {
        if (tagOf(cell) === "td") hasTd = true;
        const cellHasInteractive = Array.from(cell.querySelectorAll("*")).some(ch => capturedSet.has(ch)) || capturedSet.has(cell);
        if (cellHasInteractive) {
          rowHasInteractive = true;
          continue;
        }
        const ct = cleanStr(cell.innerText || cell.textContent || "", 56);
        if (ct) uncoveredParts.push(ct);
      }
      if (!hasTd || uncoveredParts.length === 0) continue;
      const rowSummary = cleanStr(uncoveredParts.join(" | "), 140);
      if (!rowSummary || rowSummary.length < 2) continue;
      if (rowHasInteractive) {
        // Skip if every uncovered cell is already a substring of an interactive label in this row
        const rowInteractiveText = Array.from(tr.querySelectorAll("*"))
          .filter(ch => capturedSet.has(ch))
          .map(ch => cleanStr(ch.innerText || ch.textContent || "", 120).toLowerCase())
          .join(" ");
        const allRedundant = uncoveredParts.every(p => rowInteractiveText.includes(p.toLowerCase()));
        if (allRedundant) continue;
      }
      const lowerRow = rowSummary.toLowerCase();
      if (seenLabels.has(lowerRow)) continue;
      seenLabels.add(lowerRow);
      entries.push({ el: tr, role: "row", label: rowSummary });
    }

    // 2. Concise leaf text blocks inside <main> / [role="main"] / article / open dialogs (or compact body)
    let roots = Array.from(document.querySelectorAll("dialog[open], [role='dialog'], [aria-modal='true'], main, [role='main'], article"))
      .filter(r => isVisible(r));
    const usingBodyFallback = roots.length === 0;
    if (usingBodyFallback) {
      roots = [document.body];
    }
    const INLINE_TAGS = new Set(["span", "strong", "b", "em", "i", "code", "small", "time", "mark", "abbr", "sup", "sub", "br", "wbr"]);
    const BLOCK_SKIP_SEL = "script, style, noscript, svg, template, table, nav, [role='navigation'], footer, [role='contentinfo'], [contenteditable='true'], [contenteditable=''], [contenteditable='plaintext-only']";
    const textSel = "p, li, dt, dd, h4, h5, h6, [data-testid], [class*='price'], [class*='total'], [class*='confirm'], [class*='amount'], [class*='summary'], [class*='error'], [class*='msg'], [class*='trip'], div, span";

    for (const root of roots) {
      if (entries.length >= maxStaticItems) break;
      const cands = root.querySelectorAll(textSel);
      for (let i = 0; i < cands.length && entries.length < maxStaticItems; i++) {
        const el = cands[i];
        if (capturedSet.has(el)) continue;
        if (el.closest(BLOCK_SKIP_SEL)) continue;
        if (usingBodyFallback && el.closest("header, [role='banner'], aside")) continue;
        const tag = tagOf(el);
        if (tag === "label" || el.closest("label")) continue;
        if (el.querySelector("button, a[href], input, select, textarea, div, p, ul, ol, dl, table, section, article, form, h1, h2, h3, h4")) {
          continue;
        }
        const childEls = el.children;
        if (childEls.length > 4) continue;
        let onlyInlineChildren = true;
        for (let c = 0; c < childEls.length; c++) {
          if (!INLINE_TAGS.has(tagOf(childEls[c]))) {
            onlyInlineChildren = false;
            break;
          }
        }
        if (!onlyInlineChildren) continue;
        // If parent is also an inline-only leaf with identical text, let parent emit once and skip child
        const par = el.parentElement;
        if (par && par !== root && par !== document.body && !par.querySelector("button, a[href], input, select, textarea, div, p, ul, ol, table")) {
          const parTxt = cleanStr(par.innerText || par.textContent || "", 140);
          const ownTxt0 = cleanStr(el.innerText || el.textContent || "", 140);
          if (parTxt && parTxt === ownTxt0 && (tag === "span" || INLINE_TAGS.has(tag))) {
            continue;
          }
        }
        if (!isVisible(el)) continue;
        const txt = cleanStr(el.innerText || el.textContent || "", 140);
        if (!txt || txt.length < 3) continue;
        if (isCoveredByCaptured(el, txt)) continue;
        seenLabels.add(txt.toLowerCase());
        capturedSet.add(el);
        entries.push({ el, role: "text", label: txt });
      }
    }
    return entries;
  }

  function collectSnapshot(query = "", maxItems = 180, foldTables = true, fullPage = false) {
    const q = String(query || "").toLowerCase();
    const effectiveMaxItems = (!foldTables || fullPage) ? Math.max(maxItems, 320) : maxItems;
    const foldFooter = Boolean(foldTables && !fullPage && !q);
    const footerLinkCache = new Map();
    const sel = "button, a[href], input, select, textarea, canvas, iframe, summary, [role='button'], [role='link'], [role='tab'], [role='menuitem'], [role='checkbox'], [role='switch'], [role='option'], [role='textbox'], [role='searchbox'], [role='combobox'], [contenteditable='true'], [contenteditable=''], [contenteditable='plaintext-only'], h1, h2, h3, [data-status], [role='status'], [role='alert'], output, .result, #result, #status";
    const semNodes = Array.from(queryAllDeep(sel));
    const semanticSet = new Set(semNodes);
    const pointerExtras = collectInteractivePointerElements(semanticSet, 60);
    const pointerSet = new Set(pointerExtras);
    let nodes = semNodes;
    if (pointerExtras.length > 0) {
      nodes = semNodes.concat(pointerExtras);
    }

    const capturedSet = new Set();
    const capturedLabels = [];
    const stagedItems = [];
    const canvases = [];

    for (const el of nodes) {
      if (stagedItems.length >= effectiveMaxItems) break;
      if (!isVisible(el)) continue;
      if (
        el.parentElement
        && el.parentElement.closest("[contenteditable='true'], [contenteditable=''], [contenteditable='plaintext-only']")
      ) {
        const tag = tagOf(el);
        if (tag !== "button" && tag !== "a" && tag !== "input" && tag !== "select") {
          continue;
        }
      }
      const isPointerExtra = pointerSet.has(el);
      const role = isPointerExtra ? classifyInteractiveElementRole(el) : getRole(el);
      if (role === "iframe") {
        const info = getCrossOriginIframeInfo(el);
        if (!info) continue;
        if (q && !info.searchBlob.includes(q)) continue;
        const uid = getUid(el);
        uidToMeta.set(uid, { role: "iframe", label: info.label });
        capturedSet.add(el);
        if (info.label) capturedLabels.push(info.label);
        const fullLine = `${uid} ${info.body}`;
        stagedItems.push({ el, uid, role: "iframe", label: info.label, val: "", body: info.body, fullLine });
        continue;
      }
      const label = getLabel(el, role === "card" ? 140 : 80);
      const val = getValue(el);
      if (role === "canvas") {
        const uid = getUid(el);
        uidToMeta.set(uid, { role: "canvas", label });
        capturedSet.add(el);
        const rect = el.getBoundingClientRect();
        const cx = Math.round(rect.left);
        const cy = Math.round(rect.top);
        const cw = Math.round(rect.width);
        const ch = Math.round(rect.height);
        let dataUrl = "";
        try { dataUrl = el.toDataURL("image/png"); } catch (_) {}
        canvases.push({ uid, label, x: cx, y: cy, w: cw, h: ch, dataUrl });
        const body = `canvas "${label}" (${cx},${cy}) [${cw}x${ch}]`;
        const fullLine = `${uid} ${body}`;
        if (!q || label.toLowerCase().includes(q) || "canvas".includes(q)) {
          stagedItems.push({ el, uid, role: "canvas", label, val: "", body, fullLine });
        }
        continue;
      }
      if (!label && !val && !role.startsWith("input") && role !== "textbox" && role !== "select") continue;
      capturedSet.add(el);
      if (label) capturedLabels.push(label);
      if (val) capturedLabels.push(val);
      if (q && !label.toLowerCase().includes(q) && !val.toLowerCase().includes(q) && !role.includes(q)) continue;

      const uid = getUid(el);
      uidToMeta.set(uid, { role, label });
      let body = role;
      if (label) body += ` "${label}"`;
      if (val && val !== label) body += ` val="${val}"`;
      if (el.disabled) body += " [disabled]";
      const fullLine = `${uid} ${body}`;
      stagedItems.push({ el, uid, role, label, val, body, fullLine });
    }

    // Collect concise primary static text & uncovered data-table rows within remaining budget
    const remainingBudget = Math.min(28, Math.max(0, effectiveMaxItems - stagedItems.length));
    if (remainingBudget > 0) {
      const staticEntries = collectPrimaryStaticEntries(capturedSet, capturedLabels, footerLinkCache, foldFooter, remainingBudget);
      for (const se of staticEntries) {
        if (q && !se.label.toLowerCase().includes(q) && !se.role.includes(q)) continue;
        const uid = getUid(se.el);
        uidToMeta.set(uid, { role: se.role, label: se.label });
        const body = `${se.role} "${se.label}"`;
        const fullLine = `${uid} ${body}`;
        stagedItems.push({ el: se.el, uid, role: se.role, label: se.label, val: "", body, fullLine });
      }
    }

    if (stagedItems.length > 1) {
      stagedItems.sort((a, b) => {
        if (a.el === b.el) return 0;
        try {
          const pos = a.el.compareDocumentPosition(b.el);
          if (pos & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
          if (pos & Node.DOCUMENT_POSITION_PRECEDING) return 1;
        } catch (_) {}
        return 0;
      });
    }

    const currentMap = new Map();
    const lines = [];
    const tbodyRows = new Map();
    const foldedStats = new Map();
    const footerStats = new Map();
    const maxVisibleTableRows = 6;

    for (const item of stagedItems) {
      const el = item.el;
      let isFolded = false;
      if (foldFooter && !item.role.startsWith("input") && item.role !== "textbox" && item.role !== "select") {
        const footerScope = getPageFooterScope(el, footerLinkCache);
        if (footerScope) {
          let fst = footerStats.get(footerScope);
          if (!fst) {
            fst = { count: 0, firstUid: item.uid, lastUid: item.uid };
            footerStats.set(footerScope, fst);
          }
          fst.count += 1;
          fst.lastUid = item.uid;
          isFolded = true;
        }
      }
      if (!isFolded && foldTables && !q) {
        const tr = tagOf(el) === "tr" ? el : (el.closest ? el.closest("tbody tr") : null);
        const tbody = tr ? tr.parentElement : null;
        if (tbody && tagOf(tbody) === "tbody" && tr) {
          let rowSet = tbodyRows.get(tbody);
          if (!rowSet) {
            rowSet = new Set();
            tbodyRows.set(tbody, rowSet);
          }
          rowSet.add(tr);
          if (rowSet.size > maxVisibleTableRows) {
            let st = foldedStats.get(tbody);
            if (!st) {
              st = { rows: new Set(), count: 0, firstUid: item.uid, lastUid: item.uid };
              foldedStats.set(tbody, st);
            }
            st.rows.add(tr);
            st.count += 1;
            st.lastUid = item.uid;
            isFolded = true;
          }
        }
      }
      currentMap.set(item.uid, {
        uid: item.uid,
        role: item.role,
        label: item.label,
        val: item.val,
        body: item.body,
        fullLine: item.fullLine,
        folded: isFolded
      });
      if (!isFolded) {
        lines.push(item.fullLine);
      }
    }

    for (const [oUid, oItem] of ocrTargets.entries()) {
      if (q && !oItem.label.toLowerCase().includes(q) && !oUid.toLowerCase().includes(q) && !"canvas-text".includes(q)) {
        continue;
      }
      const body = `canvas-text "${oItem.label}" (${oItem.x},${oItem.y})`;
      const fullLine = `${oUid} ${body}`;
      currentMap.set(oUid, { uid: oUid, role: "canvas-text", label: oItem.label, val: "", body, fullLine, folded: false });
      lines.push(fullLine);
    }

    for (const st of foldedStats.values()) {
      lines.push(`# ... +${st.rows.size} rows (${st.firstUid}..${st.lastUid}, ${st.count} items indexed; target by label/UID)`);
    }
    for (const fst of footerStats.values()) {
      lines.push(`# footer: +${fst.count} items folded (${fst.firstUid}..${fst.lastUid} indexed; pass fullPage=true or query=... to expand)`);
    }

    const allFullLines = [];
    for (const item of currentMap.values()) {
      allFullLines.push(item.fullLine);
    }
    const newCrc = crc32(allFullLines.join("\n"));
    const layoutIssues = query ? [] : collectLayoutIssues(stagedItems);
    return { currentMap, lines, canvases, newCrc, totalCount: currentMap.size, layoutIssues };
  }

  const UNIVERSAL_ICON_RE = /\b(logo|search|find|close|dismiss|cancel|menu|nav|navigation|hamburger|refresh|reload|sync|back|forward|previous|prev|next|more|options|settings|clear|reset|copy|share|star|favorite|pin|unpin|bookmark|play|pause|stop|mute|unmute|volume|expand|collapse|fullscreen|zoom|theme|dark|light|avatar|profile|account|notifications?|help|info|filter|sort|delete|remove|trash|edit|plus|minus)\b/i;

  function collectLayoutIssues(stagedItems) {
    const issues = [];
    const vw = (document.documentElement && document.documentElement.clientWidth) || window.innerWidth || 1280;
    const vh = (document.documentElement && document.documentElement.clientHeight) || window.innerHeight || 800;
    let modalRoot = null;
    const modals = document.querySelectorAll("[aria-modal='true'], dialog[open]");
    for (let i = modals.length - 1; i >= 0; i--) {
      if (isVisible(modals[i])) {
        modalRoot = modals[i];
        break;
      }
    }

    if (!modalRoot && document.documentElement) {
      const rootOx = window.getComputedStyle(document.documentElement).overflowX;
      const bodyOx = document.body ? window.getComputedStyle(document.body).overflowX : "visible";
      if (rootOx !== "hidden" && rootOx !== "clip" && bodyOx !== "hidden" && bodyOx !== "clip") {
        const docOverflow = Math.round(document.documentElement.scrollWidth - vw);
        if (docOverflow > 2) {
          issues.push(`doc_overflow(+${docOverflow}px at ${vw}x${vh})`);
        }
      }
    }

    const styleCache = new Map();
    const getStyle = (node) => {
      let s = styleCache.get(node);
      if (!s) {
        s = window.getComputedStyle(node);
        styleCache.set(node, s);
      }
      return s;
    };

    const controls = [];
    for (const item of stagedItems) {
      const el = item.el;
      if (!el || !el.getBoundingClientRect) continue;
      const rRole = item.role || "";
      const isAtomic = (
        rRole === "button"
        || rRole === "link"
        || rRole === "tab"
        || rRole === "checkbox"
        || rRole === "radio"
        || rRole === "switch"
        || rRole === "select"
        || rRole === "combobox"
        || rRole === "textbox"
        || rRole.startsWith("input")
      );
      if (!isAtomic) continue;
      if (modalRoot && !modalRoot.contains(el)) continue;

      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2 || r.right <= 0 || r.left >= vw) continue;

      let fixedRoot = null;
      let scrollRoot = null;
      let cur = el;
      while (cur && cur !== document.documentElement) {
        const cs = getStyle(cur);
        if (!fixedRoot && (cs.position === "fixed" || cs.position === "sticky")) {
          fixedRoot = cur;
        }
        if (
          !scrollRoot
          && cur !== document.body
          && (cs.overflowX === "auto" || cs.overflowX === "scroll" || cs.overflowY === "auto" || cs.overflowY === "scroll")
        ) {
          scrollRoot = cur;
        }
        cur = cur.parentElement;
      }

      if (vw < 768) {
        const tag = tagOf(el);
        const inpType = (el.getAttribute && el.getAttribute("type")) || "text";
        if (
          tag === "textarea"
          || tag === "select"
          || (tag === "input" && !/^(checkbox|radio|button|submit|reset|range|color|file|hidden|image)$/i.test(inpType))
        ) {
          const fs = parseFloat(getStyle(el).fontSize || "16");
          if (fs > 0 && fs < 15.5) {
            issues.push(`ios_input_zoom(${item.uid} "${cleanStr(item.label || tag, 22)}" ${Math.round(fs)}px<16px)`);
          }
        }
      }

      if (vw < 1024 && (rRole === "button" || rRole === "tab")) {
        const visTxt = String(el.innerText || "").replace(/\s+/g, " ").trim();
        const hasRasterImg = Boolean(el.querySelector && el.querySelector("img[src]"));
        if (!hasRasterImg && visTxt.length === 0 && item.label && item.label.length >= 4 && !UNIVERSAL_ICON_RE.test(item.label)) {
          issues.push(`unlabeled_touch_cta(${item.uid} "${cleanStr(item.label, 28)}")`);
        }
      }

      controls.push({ el, uid: item.uid, label: item.label || rRole, r, fixedRoot, scrollRoot });
    }

    for (let i = 0; i < controls.length && issues.length < 20; i++) {
      const a = controls[i];
      for (let j = i + 1; j < controls.length && issues.length < 20; j++) {
        const b = controls[j];
        if (a.fixedRoot !== b.fixedRoot || a.scrollRoot !== b.scrollRoot) continue;
        if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
        const ix = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left);
        const iy = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top);
        if (ix > 3 && iy > 3) {
          issues.push(`overlap(${a.uid} "${cleanStr(a.label, 20)}" <-> ${b.uid} "${cleanStr(b.label, 20)}", ${Math.round(ix)}x${Math.round(iy)}px)`);
        }
      }
    }
    return issues;
  }

  function computeDiff(prevMap, curMap) {
    const delta = [];
    let foldedAdded = null;
    for (const [uid, oldItem] of prevMap.entries()) {
      if (!curMap.has(uid)) {
        if (!oldItem.folded) {
          delta.push(`- ${oldItem.fullLine}`);
        }
      }
    }
    for (const [uid, curItem] of curMap.entries()) {
      const oldItem = prevMap.get(uid);
      if (!oldItem) {
        if (curItem.folded) {
          if (!foldedAdded) {
            foldedAdded = { count: 0, firstUid: uid, lastUid: uid };
          }
          foldedAdded.count += 1;
          foldedAdded.lastUid = uid;
        } else {
          delta.push(`+ ${curItem.fullLine}`);
        }
      } else if (oldItem.body !== curItem.body) {
        if (oldItem.val !== curItem.val) {
          delta.push(`~ ${uid} ${curItem.role} "${curItem.label}" val="${oldItem.val}" -> val="${curItem.val}"`);
        } else if (oldItem.label !== curItem.label) {
          delta.push(`~ ${uid} ${curItem.role} "${oldItem.label}" -> "${curItem.label}"`);
        } else {
          delta.push(`~ ${curItem.fullLine}`);
        }
      }
    }
    if (foldedAdded) {
      delta.push(`# ... +${foldedAdded.count} folded items (${foldedAdded.firstUid}..${foldedAdded.lastUid} indexed; target by label/UID)`);
    }
    return delta;
  }

  function setNativeValue(el, value, append = false) {
    let target = el;
    if (tagOf(target) === "label" && target.control) {
      target = target.control;
    }
    const tag0 = tagOf(target);
    if (tag0 !== "input" && tag0 !== "textarea" && tag0 !== "select" && !isContentEditableEl(target)) {
      const inner = queryOneDeep(
        "[contenteditable='true'], [contenteditable=''], [contenteditable='plaintext-only'], textarea, input:not([type='hidden']):not([type='submit']):not([type='button']):not([type='reset']):not([type='checkbox']):not([type='radio']):not([type='file']), select",
        target
      );
      if (inner) target = inner;
    }

    const strVal = String(value ?? "");
    if (isContentEditableEl(target)) {
      target.focus({ preventScroll: true });
      const selectContents = (collapseToEnd) => {
        const sel = window.getSelection && window.getSelection();
        if (sel) {
          const range = document.createRange();
          range.selectNodeContents(target);
          if (collapseToEnd) range.collapse(false);
          sel.removeAllRanges();
          sel.addRange(range);
        }
      };
      const normWs = (s) => String(s || "").replace(/\s+/g, " ").trim();
      const prevNorm = normWs(target.innerText || target.textContent || "");
      if (prevNorm.length > 0) {
        selectContents(append);
      }
      if (!append && prevNorm.length > 0) {
        try {
          const delBi = new InputEvent("beforeinput", { bubbles: true, cancelable: true, inputType: "deleteContentBackward", data: null });
          target.dispatchEvent(delBi);
        } catch (_) {}
        if (normWs(target.innerText || target.textContent || "").length > 0) {
          selectContents(false);
          try {
            document.execCommand("delete", false, null);
          } catch (_) {}
        }
        if (normWs(target.innerText || target.textContent || "").length > 0) {
          const leaves = target.querySelectorAll ? target.querySelectorAll("[data-text='true']") : [];
          if (leaves && leaves.length === 1) {
            leaves[0].textContent = "";
          } else {
            target.textContent = "";
          }
          target.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "deleteContentBackward", data: null }));
        }
      }
      if (strVal.length > 0) {
        const expNorm = normWs(strVal);
        const expectedFullNorm = append && prevNorm.length > 0 ? normWs(prevNorm + " " + expNorm) : expNorm;
        const baseLen = append ? prevNorm.length : 0;
        const isRichEditor = Boolean(
          (target.closest && target.closest("[data-contents], .DraftEditor-root, [data-lexical-editor], .ProseMirror"))
          || (target.querySelector && target.querySelector("[data-contents], [data-text='true'], [data-lexical-text]"))
        );
        if (append && prevNorm.length > 0) {
          selectContents(true);
        }
        if (strVal.includes("\n") || isRichEditor) {
          let pasteHandled = false;
          try {
            const dt = new DataTransfer();
            dt.setData("text/plain", strVal);
            const pasteEv = new ClipboardEvent("paste", {
              clipboardData: dt,
              bubbles: true,
              cancelable: true
            });
            pasteHandled = !target.dispatchEvent(pasteEv);
          } catch (_) {}
          const afterPasteNorm = normWs(target.innerText || target.textContent || "");
          if (pasteHandled || afterPasteNorm.length > baseLen) {
            target.dispatchEvent(new Event("change", { bubbles: true }));
            store.mutationSeq++;
            return;
          }
        }
        let biHandled = false;
        try {
          const bi = new InputEvent("beforeinput", { bubbles: true, cancelable: true, inputType: "insertText", data: strVal });
          biHandled = !target.dispatchEvent(bi);
        } catch (_) {}
        const afterBiNorm = normWs(target.innerText || target.textContent || "");
        const okAfterBi = biHandled || (
          append
            ? (afterBiNorm === expectedFullNorm || (afterBiNorm.endsWith(expNorm) && afterBiNorm.length > prevNorm.length))
            : (afterBiNorm === expNorm || afterBiNorm.length > 0)
        );
        let inserted = okAfterBi;
        if (!okAfterBi) {
          if (append && prevNorm.length > 0) {
            selectContents(true);
          }
          try {
            inserted = Boolean(document.execCommand("insertText", false, strVal));
          } catch (_) {}
        }
        const afterExecNorm = normWs(target.innerText || target.textContent || "");
        const okAfterExec = append ? afterExecNorm.endsWith(expNorm) : (afterExecNorm === expNorm || afterExecNorm.length > 0);
        if (!inserted && !okAfterExec) {
          const leaf = target.querySelector && target.querySelector("[data-text='true']");
          if (leaf) {
            leaf.textContent = append ? ((leaf.textContent || "") + strVal) : strVal;
          } else {
            target.textContent = append ? ((target.textContent || "") + strVal) : strVal;
          }
          target.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: strVal }));
        }
      }
      target.dispatchEvent(new Event("change", { bubbles: true }));
      store.mutationSeq++;
      return;
    }

    const tag = tagOf(target);
    const win = (target && target.ownerDocument && target.ownerDocument.defaultView) || window;
    if (tag === "select") {
      let matchedOptVal = strVal;
      if (target.options) {
        for (const opt of target.options) {
          if (opt.value === strVal || cleanStr(opt.text).toLowerCase() === strVal.toLowerCase()) {
            matchedOptVal = opt.value;
            break;
          }
        }
      }
      const selProto = win.HTMLSelectElement && win.HTMLSelectElement.prototype;
      const selDesc = selProto ? Object.getOwnPropertyDescriptor(selProto, "value") : null;
      try {
        if (selDesc && selDesc.set) selDesc.set.call(target, matchedOptVal);
        else target.value = matchedOptVal;
      } catch (_) {
        target.value = matchedOptVal;
      }
      target.dispatchEvent(new Event("input", { bubbles: true }));
      target.dispatchEvent(new Event("change", { bubbles: true }));
      store.mutationSeq++;
      return;
    }

    const finalVal = append ? (String(target.value || "") + strVal) : strVal;
    if (tag === "input" || tag === "textarea") {
      const proto = tag === "textarea"
        ? (win.HTMLTextAreaElement && win.HTMLTextAreaElement.prototype)
        : (win.HTMLInputElement && win.HTMLInputElement.prototype);
      const desc = proto ? Object.getOwnPropertyDescriptor(proto, "value") : null;
      try {
        if (desc && desc.set) {
          desc.set.call(target, finalVal);
        } else {
          target.value = finalVal;
        }
      } catch (_) {
        target.value = finalVal;
      }
      target.dispatchEvent(new InputEvent("input", { bubbles: true, data: finalVal }));
      target.dispatchEvent(new Event("change", { bubbles: true }));
      store.mutationSeq++;
      return;
    }

    try {
      target.focus({ preventScroll: true });
    } catch (_) {}
    try {
      target.dispatchEvent(new InputEvent("beforeinput", { bubbles: true, cancelable: true, inputType: "insertText", data: finalVal }));
    } catch (_) {}
    if ("value" in target) {
      try { target.value = finalVal; } catch (_) {}
    }
    target.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: finalVal }));
    target.dispatchEvent(new Event("change", { bubbles: true }));
    store.mutationSeq++;
  }

  async function waitQuietDom(quietMs = 15, maxWaitMs = 110) {
    const start = performance.now();
    let lastSeenSeq = store.mutationSeq;
    let lastChangeTime = start;
    while (performance.now() - start < maxWaitMs) {
      await new Promise(r => setTimeout(r, 6));
      if (store.mutationSeq !== lastSeenSeq) {
        lastSeenSeq = store.mutationSeq;
        lastChangeTime = performance.now();
      } else if (performance.now() - lastChangeTime >= quietMs) {
        break;
      }
    }
  }

  function getVisiblePageText() {
    if (!document.body) return "";
    return String(document.body.innerText || document.body.textContent || "");
  }

  function extractReadableText(selector = "", maxChars = 4000) {
    const cap = Math.max(120, Math.min(Number(maxChars || 4000), 32000));
    let root = null;
    const selStr = String(selector || "").trim();
    if (selStr) {
      root = resolveEl(selStr) || queryOneDeep(selStr);
      if (!root) throw new Error(`selector not found (${selStr})`);
    } else {
      const mainCands = document.querySelectorAll("dialog[open], [role='dialog'], [aria-modal='true'], main, [role='main'], article");
      for (const c of mainCands) {
        if (isVisible(c) && String(c.innerText || "").trim().length >= 20) {
          root = c;
          break;
        }
      }
      if (!root) root = document.body;
    }
    if (!root) return "";
    let raw = "";
    if (!selStr && root === document.body) {
      // Exclude boilerplate footer/nav when summarizing body without explicit selector
      const parts = [];
      for (const child of Array.from(root.children)) {
        const tag = tagOf(child);
        const role = child.getAttribute ? (child.getAttribute("role") || "") : "";
        if (tag === "script" || tag === "style" || tag === "noscript" || tag === "svg" || tag === "footer" || role === "contentinfo") {
          continue;
        }
        if (!isVisible(child)) continue;
        const ct = String(child.innerText || "").trim();
        if (ct) parts.push(ct);
      }
      raw = parts.length > 0 ? parts.join("\n") : String(root.innerText || "");
    } else {
      raw = String(root.innerText || root.textContent || "");
    }
    const cleaned = raw
      .split(/\r?\n/)
      .map(l => l.replace(/[ \t]+/g, " ").trim())
      .filter(Boolean)
      .join("\n");
    if (cleaned.length <= cap) return cleaned;
    return cleaned.slice(0, cap) + `\n[truncated: ${cap} of ${cleaned.length} chars]`;
  }

  function matchSingleSelectorOrUid(sel, requireVisible = true) {
    const s = String(sel || "").trim();
    if (!s) return false;
    if (s.startsWith("@")) {
      const el = resolveEl(s);
      return Boolean(el && (!requireVisible || isVisible(el)));
    }
    let el = null;
    try { el = queryOneDeep(s); } catch (_) {}
    if (!el) el = resolveEl(s);
    return Boolean(el && (!requireVisible || isVisible(el)));
  }

  function detectBotChallenge(descLower = "") {
    if (
      descLower.includes("sorry")
      || descLower.includes("captcha")
      || descLower.includes("challenge")
      || descLower.includes("bots")
    ) {
      return "";
    }
    const host = String(location.hostname || "").toLowerCase();
    const path = String(location.pathname || "");
    const title = String(document.title || "").trim();
    if ((host.startsWith("google.") || host.includes(".google.")) && path.startsWith("/sorry")) {
      return "google_sorry_captcha";
    }
    if (/\/help\/bots(?:\.html|\/|$)/i.test(path)) {
      return "bot_block_page";
    }
    if (/\/captcha(?:-v\d+)?(?:\/|\.html|$)/i.test(path) || /\/cdn-cgi\/challenge-platform/i.test(path)) {
      return "captcha_interstitial";
    }
    if (
      (title === "Just a moment..." || title === "Attention Required! | Cloudflare")
      && document.querySelector("#challenge-running, #challenge-stage, .cf-turnstile, iframe[src*='challenges.cloudflare.com']")
    ) {
      return "cloudflare_challenge";
    }
    return "";
  }

  function summarizeWaitTimeoutContext() {
    const parts = [];
    const title = cleanStr(document.title || "", 60);
    if (title) parts.push(`title=${JSON.stringify(title)}`);
    const headCands = document.querySelectorAll("h1, h2, [role='alert'], [role='heading']");
    for (const h of headCands) {
      if (!isVisible(h)) continue;
      const ht = cleanStr(h.innerText || h.textContent || "", 70);
      if (ht && ht !== title) {
        parts.push(`heading=${JSON.stringify(ht)}`);
        break;
      }
    }
    return parts.length > 0 ? ` [${parts.join(", ")}]` : "";
  }

  async function waitForCondition(s) {
    const timeoutMs = Math.max(0, Math.min(Number(s.timeoutMs ?? s.timeout ?? s.ms ?? 5000), 25000));
    const requireVisible = String(s.state || "visible").toLowerCase() !== "attached";
    const wantUrl = s.url !== undefined ? String(s.url) : "";
    const wantSel = s.selector !== undefined ? String(s.selector) : "";
    const wantJs = s.js !== undefined ? String(s.js) : (s.expr !== undefined ? String(s.expr) : "");
    const invertFlag = s.gone === true || String(s.state || "").toLowerCase() === "hidden" || String(s.state || "").toLowerCase() === "detached";
    const explicitGone = typeof s.gone === "string" ? s.gone : "";
    const wantTextGone = s.textGone !== undefined ? String(s.textGone) : "";
    const wantSelGone = s.selectorGone !== undefined ? String(s.selectorGone) : "";

    let textList = [];
    if (Array.isArray(s.anyText)) {
      textList = s.anyText.map(x => String(x)).filter(Boolean);
    } else if (Array.isArray(s.text)) {
      textList = s.text.map(x => String(x)).filter(Boolean);
    } else if (s.text !== undefined && s.text !== null && String(s.text) !== "") {
      textList = [String(s.text)];
    }

    const fallbackTarget = (!wantUrl && !wantSel && !wantJs && !explicitGone && !wantTextGone && !wantSelGone && textList.length === 0)
      ? String(s.target || s.uid || s.ref || s.name || s.value || "").trim()
      : "";

    const descParts = [];
    if (textList.length > 0) descParts.push(`${invertFlag ? "text gone" : "text"}=${JSON.stringify(textList.length === 1 ? textList[0] : textList)}`);
    if (wantSel) descParts.push(`${invertFlag ? "selector gone" : "selector"}=${JSON.stringify(wantSel)}`);
    if (wantUrl) descParts.push(`url=${JSON.stringify(wantUrl)}`);
    if (explicitGone) descParts.push(`gone=${JSON.stringify(explicitGone)}`);
    if (wantTextGone) descParts.push(`textGone=${JSON.stringify(wantTextGone)}`);
    if (wantSelGone) descParts.push(`selectorGone=${JSON.stringify(wantSelGone)}`);
    if (wantJs) descParts.push("js condition");
    if (fallbackTarget) descParts.push(JSON.stringify(fallbackTarget));
    const desc = descParts.join(", ") || "condition";

    const looksLikeCss = (str) => /^[#.@\[]|[>+~]/.test(str);

    const checkOnce = async () => {
      if (wantUrl) {
        const href = String(location.href || "");
        if (wantUrl.startsWith("/") && wantUrl.lastIndexOf("/") > 0) {
          const lastSlash = wantUrl.lastIndexOf("/");
          try {
            const re = new RegExp(wantUrl.slice(1, lastSlash), wantUrl.slice(lastSlash + 1) || "i");
            if (!re.test(href)) return false;
          } catch (_) {
            if (!href.toLowerCase().includes(wantUrl.toLowerCase())) return false;
          }
        } else if (!href.toLowerCase().includes(wantUrl.toLowerCase())) {
          return false;
        }
      }
      let pageTxtLower = null;
      const getPageLower = () => {
        if (pageTxtLower === null) pageTxtLower = getVisiblePageText().toLowerCase();
        return pageTxtLower;
      };
      if (textList.length > 0) {
        const matchedAny = textList.some(t => {
          const tl = t.toLowerCase();
          if (getPageLower().includes(tl)) return true;
          const el = resolveEl(t);
          return Boolean(el && isVisible(el));
        });
        if (invertFlag ? matchedAny : !matchedAny) return false;
      }
      if (wantSel) {
        const matchedSel = matchSingleSelectorOrUid(wantSel, requireVisible);
        if (invertFlag ? matchedSel : !matchedSel) return false;
      }
      if (explicitGone) {
        const present = looksLikeCss(explicitGone)
          ? matchSingleSelectorOrUid(explicitGone, true)
          : (getPageLower().includes(explicitGone.toLowerCase()) || Boolean(resolveEl(explicitGone)));
        if (present) return false;
      }
      if (wantTextGone) {
        if (getPageLower().includes(wantTextGone.toLowerCase())) return false;
      }
      if (wantSelGone) {
        if (matchSingleSelectorOrUid(wantSelGone, true)) return false;
      }
      if (fallbackTarget) {
        const matchedFb = looksLikeCss(fallbackTarget)
          ? (matchSingleSelectorOrUid(fallbackTarget, requireVisible) || getPageLower().includes(fallbackTarget.toLowerCase()))
          : (getPageLower().includes(fallbackTarget.toLowerCase()) || matchSingleSelectorOrUid(fallbackTarget, requireVisible));
        if (invertFlag ? matchedFb : !matchedFb) return false;
      }
      if (wantJs) {
        try {
          const jsRes = await runInPageEval(wantJs);
          if (!jsRes) return false;
        } catch (_) {
          return false;
        }
      }
      return true;
    };

    const t0 = performance.now();
    const descLower = desc.toLowerCase();
    if (await checkOnce()) {
      return { ok: true, elapsedMs: +(performance.now() - t0).toFixed(2) };
    }
    const initialChallenge = detectBotChallenge(descLower);
    if (initialChallenge) {
      return { ok: false, botChallenge: initialChallenge, elapsedMs: +(performance.now() - t0).toFixed(2), desc };
    }
    while (performance.now() - t0 < timeoutMs) {
      if (store.navigating) return { navigating: true };
      const rem = Math.max(1, timeoutMs - (performance.now() - t0));
      await new Promise(resolve => {
        let done = false;
        const finish = () => {
          if (done) return;
          done = true;
          waiters.delete(finish);
          clearTimeout(tid);
          resolve();
        };
        const tid = setTimeout(finish, Math.min(35, rem));
        waiters.add(finish);
      });
      if (store.navigating) return { navigating: true };
      if (await checkOnce()) {
        return { ok: true, elapsedMs: +(performance.now() - t0).toFixed(2) };
      }
      const midChallenge = detectBotChallenge(descLower);
      if (midChallenge) {
        return { ok: false, botChallenge: midChallenge, elapsedMs: +(performance.now() - t0).toFixed(2), desc };
      }
    }
    return { ok: false, timedOut: true, timeoutMs, desc, contextHint: summarizeWaitTimeoutContext() };
  }

  function interpolateTemplate(val, args) {
    if (typeof val === "string") {
      const exactMatch = val.match(/^(?:\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}|\$([a-zA-Z0-9_]+))$/);
      if (exactMatch) {
        const key = exactMatch[1] || exactMatch[2];
        if (args && Object.prototype.hasOwnProperty.call(args, key)) {
          return args[key];
        }
      }
      return val
        .replace(/\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}/g, (_, k) => (args && args[k] !== undefined ? String(args[k]) : `{{${k}}}`))
        .replace(/\$([a-zA-Z0-9_]+)/g, (m, k) => (args && args[k] !== undefined ? String(args[k]) : m));
    }
    if (Array.isArray(val)) {
      return val.map(v => interpolateTemplate(v, args));
    }
    if (val && typeof val === "object") {
      const out = {};
      for (const [k, v] of Object.entries(val)) {
        out[k] = interpolateTemplate(v, args);
      }
      return out;
    }
    return val;
  }

  async function invokeWebMcp(toolName, args) {
    const ctx = navigator.modelContext;
    if (ctx) {
      const tools = Array.isArray(ctx.tools) ? ctx.tools : [];
      const found = tools.find(t => t.name === toolName);
      if (found && typeof found.execute === "function") {
        return await found.execute(args || {});
      }
      if (typeof ctx.callTool === "function" && (!virtualTools.has(toolName) || found)) {
        try {
          return await ctx.callTool(toolName, args || {});
        } catch (err) {
          if (!virtualTools.has(toolName)) throw err;
        }
      }
    }
    const vTool = virtualTools.get(String(toolName));
    if (vTool) {
      const interpolatedSteps = interpolateTemplate(vTool.steps, args || {});
      const subRes = await executeBatch({
        agent: "macro",
        steps: interpolatedSteps,
        skipSnapshot: true,
        settle: true
      });
      if (subRes.errors && subRes.errors.length > 0) {
        throw new Error(`Virtual WebMCP macro '${toolName}' failed: ${subRes.errors.join("; ")}`);
      }
      return {
        ok: true,
        macro: toolName,
        stepsExecuted: interpolatedSteps.length,
        outputs: subRes.evalOutputs || []
      };
    }
    if (!ctx) throw new Error("navigator.modelContext (WebMCP) not available on page and no virtual macro registered");
    throw new Error(`WebMCP tool not found or not executable: ${toolName}`);
  }

  function updateAgentCursor(st, el, op, explicitX, explicitY) {
    if (typeof explicitX === "number" && typeof explicitY === "number") {
      st.cursor = {
        agent: st.agent,
        x: Math.round(explicitX),
        y: Math.round(explicitY),
        uid: el ? getUid(el) : "@xy",
        label: el ? getLabel(el) : "",
        op
      };
      return;
    }
    if (el) {
      const rect = getEffectiveRect(el);
      const uid = getUid(el);
      const label = getLabel(el);
      uidToMeta.set(uid, { role: getRole(el), label });
      st.cursor = {
        agent: st.agent,
        x: Math.round(rect.left + rect.width / 2),
        y: Math.round(rect.top + rect.height / 2),
        uid,
        label,
        op
      };
    }
  }

  const VALID_WEB_OPS = new Set([
    "click", "upload", "fill", "set", "type", "select", "move",
    "press", "key", "wait", "sleep", "delay", "settle",
    "waitfor", "wait_for", "waituntil",
    "text", "readtext", "readmain",
    "eval", "evaluate", "webmcp", "calltool",
    "goto", "navigate", "open", "close", "snapshot", "diff", "ocr",
    "screenshot", "viewport", "setviewport", "resetviewport", "errors", "clearerrors"
  ]);

  const NON_EXPR_STMT_RE = /^(?:const|let|var|if|for|while|do|switch|try|throw|return|break|continue|function|class|debugger)\b/;

  function scanJsTopLevel(rawCode) {
    const src = String(rawCode || "").trim();
    const n = src.length;
    let inStr = null;
    let inLineComment = false;
    let inBlockComment = false;
    let escape = false;
    let depth = 0;
    let hasTopReturn = false;
    const semiPositions = [];

    for (let i = 0; i < n; i++) {
      const ch = src[i];
      const nxt = i + 1 < n ? src[i + 1] : "";
      if (inLineComment) {
        if (ch === "\n") inLineComment = false;
        continue;
      }
      if (inBlockComment) {
        if (ch === "*" && nxt === "/") {
          inBlockComment = false;
          i++;
        }
        continue;
      }
      if (escape) {
        escape = false;
        continue;
      }
      if (inStr !== null) {
        if (ch === "\\") escape = true;
        else if (ch === inStr) inStr = null;
        continue;
      }
      if (ch === "/" && nxt === "/") {
        inLineComment = true;
        i++;
        continue;
      }
      if (ch === "/" && nxt === "*") {
        inBlockComment = true;
        i++;
        continue;
      }
      if (ch === "'" || ch === '"' || ch === "`") {
        inStr = ch;
        continue;
      }
      if (ch === "(" || ch === "[" || ch === "{") {
        depth++;
        continue;
      }
      if (ch === ")" || ch === "]" || ch === "}") {
        depth = Math.max(0, depth - 1);
        continue;
      }
      if (depth === 0) {
        if (ch === ";") {
          semiPositions.push(i);
        } else if (
          ch === "r"
          && src.slice(i, i + 6) === "return"
          && (i === 0 || !/[a-zA-Z0-9_$.]/.test(src[i - 1]))
          && (i + 6 >= n || !/[a-zA-Z0-9_$]/.test(src[i + 6]))
        ) {
          hasTopReturn = true;
        }
      }
    }

    let trimmed = src;
    while (trimmed.endsWith(";")) {
      trimmed = trimmed.slice(0, -1).trimEnd();
    }
    let lastSemi = -1;
    for (let i = 0; i < semiPositions.length; i++) {
      const pos = semiPositions[i];
      if (pos < trimmed.length && trimmed.slice(pos + 1).trim().length > 0) {
        lastSemi = pos;
      }
    }
    const tailStmt = lastSemi >= 0 ? trimmed.slice(lastSemi + 1).trim() : trimmed;
    let tailHasAssign = false;
    if (tailStmt) {
      let tStr = null;
      let tEsc = false;
      let tDepth = 0;
      for (let i = 0; i < tailStmt.length; i++) {
        const ch = tailStmt[i];
        if (tEsc) { tEsc = false; continue; }
        if (tStr !== null) {
          if (ch === "\\") tEsc = true;
          else if (ch === tStr) tStr = null;
          continue;
        }
        if (ch === "'" || ch === '"' || ch === "`") { tStr = ch; continue; }
        if (ch === "(" || ch === "[" || ch === "{") tDepth++;
        else if (ch === ")" || ch === "]" || ch === "}") tDepth = Math.max(0, tDepth - 1);
        else if (ch === "=" && tDepth === 0) {
          const prev = i > 0 ? tailStmt[i - 1] : "";
          const next = i + 1 < tailStmt.length ? tailStmt[i + 1] : "";
          if (prev !== "=" && prev !== "!" && prev !== "<" && prev !== ">" && next !== "=" && next !== ">") {
            tailHasAssign = true;
            break;
          }
        }
      }
    }
    return { code: String(rawCode || ""), trimmed, hasTopReturn, lastSemi, tailHasAssign };
  }

  function buildEvalCandidates(rawCode) {
    const info = scanJsTopLevel(rawCode);
    if (!info.trimmed || info.hasTopReturn) return [info.code];
    const out = [`return (\n${info.trimmed}\n)`];
    if (info.lastSemi >= 0) {
      const prefix = info.trimmed.slice(0, info.lastSemi + 1);
      const lastStmt = info.trimmed.slice(info.lastSemi + 1).trim();
      if (lastStmt && !NON_EXPR_STMT_RE.test(lastStmt) && !info.tailHasAssign) {
        out.push(`${prefix}\nreturn (\n${lastStmt}\n);`);
      }
    }
    out.push(info.code);
    return out;
  }

  function safeStringifyEval(val) {
    if (typeof val === "string") return val;
    if (val === null) return "null";
    if (typeof val === "number" || typeof val === "boolean" || typeof val === "bigint") {
      return String(val);
    }
    try {
      const seen = new WeakSet();
      const s = JSON.stringify(val, (_k, v) => {
        if (typeof v === "bigint") return String(v);
        if (v && typeof v === "object") {
          if (seen.has(v)) return "[Circular]";
          seen.add(v);
        }
        return v;
      });
      if (s !== undefined) return s;
    } catch (_) {}
    try {
      return String(val);
    } catch (_) {
      return "[Unserializable]";
    }
  }

  function toCloneableEval(val) {
    if (val === undefined || val === null) return null;
    if (typeof val === "string" || typeof val === "number" || typeof val === "boolean") return val;
    if (typeof val === "bigint") return String(val);
    try {
      return JSON.parse(safeStringifyEval(val));
    } catch (_) {
      return safeStringifyEval(val);
    }
  }

  async function runInPageEval(rawCode) {
    const AsyncFn = Object.getPrototypeOf(async function () {}).constructor;
    const candidates = buildEvalCandidates(rawCode);
    let fn = null;
    let lastSyntaxErr = null;
    for (let i = 0; i < candidates.length; i++) {
      try {
        fn = new AsyncFn(candidates[i]);
        break;
      } catch (synErr) {
        lastSyntaxErr = synErr;
        const msg = String((synErr && synErr.message) || synErr || "");
        if (
          (synErr && synErr.name === "EvalError")
          || msg.includes("Content Security Policy")
          || msg.includes("unsafe-eval")
        ) {
          throw synErr;
        }
      }
    }
    if (!fn) throw lastSyntaxErr || new SyntaxError("Invalid JavaScript in eval()");
    const out = await fn();
    return typeof out === "function" ? await out() : out;
  }

  async function executeBatch(req) {
    const tBatchStart = performance.now();
    store.navigating = false;
    if (req.strictCsp) {
      store.strictCsp = true;
    }
    if (req.virtualTools) {
      registerVirtualTools(req.virtualTools);
    }
    if (req.ocrTargets) {
      registerOcrTargets(req.ocrTargets);
    }
    const agentId = String(req.agent || "default");
    const st = getAgentState(agentId);
    const steps = req.steps || [];
    const query = String(req.query || "");
    const foldTables = req.fullTable !== true;
    const fullPage = req.fullPage === true;
    const viewChanged = (query !== st.lastQuery) || (foldTables !== st.lastFoldTables) || (fullPage !== st.lastFullPage);
    const forceFull = req.diff === false || req.fullTable === true || req.fullPage === true || viewChanged;
    const hadBaseline = st.lastLinesByUid.size > 0 && !viewChanged;

    if (steps.length === 0 && !req.hadPreSteps && req.diff === true && !forceFull && hadBaseline && store.mutationSeq === st.lastSnapSeq) {
      const fastMs = +(performance.now() - tBatchStart).toFixed(2);
      return {
        unchanged: true,
        crc: st.lastCrc,
        seq: store.mutationSeq,
        count: st.lastLinesByUid.size,
        cursor: st.cursor,
        canvases: [],
        timings: { execMs: 0, settleMs: 0, snapMs: fastMs, totalInPageMs: fastMs },
        lines: [`= unchanged (crc=${st.lastCrc}, seq=${store.mutationSeq})`]
      };
    }

    if (!hadBaseline && !forceFull && !query && steps.length > 0 && !req.skipSnapshot) {
      const base = collectSnapshot("", 180, foldTables, fullPage);
      st.lastLinesByUid = base.currentMap;
      st.lastCrc = base.newCrc;
      st.lastSnapSeq = store.mutationSeq;
      st.lastQuery = "";
      st.lastFoldTables = foldTables;
      st.lastFullPage = fullPage;
    }

    const errors = [];
    const evalOutputs = [];
    const rawEvalValues = [];
    const escalations = [];
    const stepOffset = Number(req.stepOffset || 0);
    const tExecStart = performance.now();

    const makeNavigatedEarlyReturn = (nextIdx) => ({
      navigated: true,
      nextStepIndex: nextIdx,
      url: location.href,
      title: document.title,
      errors,
      evalOutputs,
      rawEvalValues,
      escalations,
      cursor: st.cursor,
      canvases: [],
      timings: {
        execMs: +(performance.now() - tExecStart).toFixed(2),
        settleMs: 0,
        snapMs: 0,
        totalInPageMs: +(performance.now() - tBatchStart).toFixed(2)
      },
      lines: []
    });

    const makeCdpClickReturn = (nextIdx, x, y) => ({
      cdpClick: { x: Math.round(x * 100) / 100, y: Math.round(y * 100) / 100 },
      nextStepIndex: nextIdx,
      url: location.href,
      title: document.title,
      errors,
      evalOutputs,
      rawEvalValues,
      escalations,
      cursor: st.cursor,
      canvases: [],
      timings: {
        execMs: +(performance.now() - tExecStart).toFixed(2),
        settleMs: 0,
        snapMs: 0,
        totalInPageMs: +(performance.now() - tBatchStart).toFixed(2)
      },
      lines: []
    });

    const makeCdpInputReturn = (nextIdx, inputSpec) => ({
      cdpInput: inputSpec,
      nextStepIndex: nextIdx,
      url: location.href,
      title: document.title,
      errors,
      evalOutputs,
      rawEvalValues,
      escalations,
      cursor: st.cursor,
      canvases: [],
      timings: {
        execMs: +(performance.now() - tExecStart).toFixed(2),
        settleMs: 0,
        snapMs: 0,
        totalInPageMs: +(performance.now() - tBatchStart).toFixed(2)
      },
      lines: []
    });

    if (store.navigating) return makeNavigatedEarlyReturn(0);

    const SHORTHAND_VERBS = [
      ["click", "target"], ["tap", "target"], ["fill", "value"], ["set", "value"],
      ["type", "text"], ["select", "value"], ["upload", "files"], ["move", "target"],
      ["hover", "target"], ["press", "key"], ["key", "key"], ["waitFor", "target"],
      ["wait_for", "target"], ["waitUntil", "target"], ["wait", "ms"], ["sleep", "ms"],
      ["delay", "ms"], ["settle", "ms"], ["eval", "code"], ["evaluate", "code"],
      ["screenshot", "path"], ["ocr", ""], ["errors", ""], ["clearErrors", ""]
    ];

    function normalizeStepInPage(rawStep) {
      if (!rawStep || typeof rawStep !== "object" || rawStep.op || rawStep.action) return rawStep || {};
      for (let v = 0; v < SHORTHAND_VERBS.length; v++) {
        const [verb, paramKey] = SHORTHAND_VERBS[v];
        const mk = Object.prototype.hasOwnProperty.call(rawStep, verb)
          ? verb
          : (Object.prototype.hasOwnProperty.call(rawStep, verb.toLowerCase()) ? verb.toLowerCase() : null);
        if (!mk) continue;
        const val = rawStep[mk];
        const out = { ...rawStep, op: verb };
        delete out[mk];
        if (val && typeof val === "object" && !Array.isArray(val)) {
          for (const [rk, rv] of Object.entries(val)) {
            if (out[rk] === undefined) out[rk] = rv;
          }
        } else if (paramKey && typeof val !== "boolean" && val !== undefined && val !== null && out[paramKey] === undefined) {
          out[paramKey] = val;
        }
        return out;
      }
      return rawStep;
    }

    for (let i = 0; i < steps.length; i++) {
      const s = normalizeStepInPage(steps[i] || {});
      let op = String(s.op || s.action || "").trim().toLowerCase();
      const targetRef = s.uid || s.ref || s.target || s.selector || s.name;
      const stepValue = s.value !== undefined ? s.value : s.text;
        const stepNum = stepOffset + i + 1;
      if (!op) {
        if (!s.optional) {
          errors.push(`step ${stepNum}: missing 'op' (or 'action') field; valid ops: click, fill, set, type, select, upload, move, press, key, waitFor, wait, text, eval, callTool`);
        }
        continue;
      }
      if (op === "press" && targetRef && String(targetRef).startsWith("@") && !s.key) {
        op = "click";
      }
      if (op === "hover" || op === "cursor" || op === "point") {
        op = "move";
      }
      if (!VALID_WEB_OPS.has(op)) {
        if (!s.optional) {
          errors.push(`step ${stepNum}: unsupported op '${op}'; valid ops: click, fill, set, type, select, upload, move, press, key, waitFor, wait, text, eval, callTool`);
        }
        continue;
      }
      try {
        const hasWaitCond = (
          s.text !== undefined || s.anyText !== undefined || s.selector !== undefined
          || s.url !== undefined || s.gone !== undefined || s.textGone !== undefined
          || s.selectorGone !== undefined || s.js !== undefined || s.expr !== undefined
        );
        if (op === "waitfor" || op === "wait_for" || op === "waituntil" || (op === "wait" && hasWaitCond)) {
          const wRes = await waitForCondition(s);
          if (wRes.navigating || store.navigating) return makeNavigatedEarlyReturn(i + 1);
          if (wRes.botChallenge) {
            escalations.push({ step: stepNum, reason: "bot_challenge", kind: wRes.botChallenge });
            if (!s.optional) {
              errors.push(`step ${stepNum} (waitFor): aborted early after ${wRes.elapsedMs}ms — bot/CAPTCHA interstitial detected (${wRes.botChallenge} at ${location.host}${location.pathname}); use mode="chrome" MV3 tab instead of headless while waiting for ${wRes.desc}`);
            }
            continue;
          }
          if (!wRes.ok && !s.optional) {
            errors.push(`step ${stepNum} (waitFor): timed out after ${wRes.timeoutMs}ms waiting for ${wRes.desc}${wRes.contextHint || ""}`);
          }
          continue;
        }
        if (op === "wait" || op === "sleep" || op === "delay" || op === "settle") {
          await new Promise(r => setTimeout(r, Number(s.ms || 25)));
          if (store.navigating) return makeNavigatedEarlyReturn(i + 1);
          continue;
        }
        if (op === "text" || op === "readtext" || op === "readmain") {
          const selArg = String(s.selector || s.target || s.uid || "");
          const txtOut = extractReadableText(selArg, Number(s.maxChars || 4000));
          evalOutputs.push(txtOut);
          if (req.returnRawEval) rawEvalValues.push(txtOut);
          continue;
        }
        if (
          op === "snapshot" || op === "diff" || op === "ocr"
          || op === "screenshot" || op === "viewport" || op === "setviewport"
          || op === "resetviewport" || op === "errors" || op === "clearerrors"
        ) {
          continue;
        }
        if (op === "eval" || op === "evaluate") {
          const code = String(s.code || stepValue || "");
          if (store.strictCsp) {
            escalations.push({ step: stepNum, reason: "eval_csp", code, cached: true, maxChars: s.maxChars });
            continue;
          }
          try {
            const res = await runInPageEval(code);
            if (res !== undefined) evalOutputs.push(safeStringifyEval(res));
            if (req.returnRawEval) rawEvalValues.push(toCloneableEval(res));
          } catch (evalErr) {
            const msg = String((evalErr && evalErr.message) || evalErr || "");
            if (
              (evalErr && evalErr.name === "EvalError")
              || msg.includes("Content Security Policy")
              || msg.includes("unsafe-eval")
            ) {
              store.strictCsp = true;
              escalations.push({ step: stepNum, reason: "eval_csp", code, maxChars: s.maxChars });
            } else {
              throw evalErr;
            }
          }
          if (store.navigating) return makeNavigatedEarlyReturn(i + 1);
          continue;
        }
        if (op === "webmcp" || op === "calltool") {
          const tName = s.tool || s.name || s.target;
          const res = await invokeWebMcp(tName, s.args || stepValue || {});
          store.mutationSeq++;
          if (res !== undefined) {
            evalOutputs.push(`webmcp(${tName}): ${safeStringifyEval(res)}`);
          }
          if (store.navigating) return makeNavigatedEarlyReturn(i + 1);
          continue;
        }
        if (op === "press" || op === "key") {
          const key = String(s.key || s.name || stepValue || "Enter");
          const active = document.activeElement || document.body;
          if (req.cdpClick && active && isCrossOriginIframeEl(active)) {
            store.mutationSeq++;
            return makeCdpInputReturn(i + 1, { kind: "key", key });
          }
          active.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
          active.dispatchEvent(new KeyboardEvent("keyup", { key, bubbles: true }));
          if (key === "Enter" && active && active.form) {
            active.form.requestSubmit ? active.form.requestSubmit() : active.form.submit();
            if (!store.navigating) await new Promise(r => setTimeout(r, 0));
          }
          if (store.navigating) return makeNavigatedEarlyReturn(i + 1);
          continue;
        }

        if ((op === "move" || op === "click") && typeof s.x === "number" && typeof s.y === "number" && !targetRef) {
          const hitEl = document.elementFromPoint(s.x, s.y);
          updateAgentCursor(st, hitEl, op, s.x, s.y);
          if (op === "click") {
            const vw = window.innerWidth || document.documentElement.clientWidth || 0;
            const vh = window.innerHeight || document.documentElement.clientHeight || 0;
            if (req.cdpClick && s.x >= 0 && s.y >= 0 && s.x < vw && s.y < vh) {
              store.mutationSeq++;
              return makeCdpClickReturn(i + 1, s.x, s.y);
            }
            dispatchCanvasClick({ x: s.x, y: s.y, label: "@xy", uid: "@xy", canvasUid: "" });
            if (!store.navigating) await new Promise(r => setTimeout(r, 0));
            if (store.navigating) return makeNavigatedEarlyReturn(i + 1);
          }
          continue;
        }

        if (op === "type" && !targetRef && stepValue !== undefined && req.cdpClick && document.activeElement && isCrossOriginIframeEl(document.activeElement)) {
          store.mutationSeq++;
          return makeCdpInputReturn(i + 1, {
            kind: "type",
            value: String(stepValue ?? ""),
            clear: false
          });
        }

        if (!targetRef) {
          if (!s.optional) {
            errors.push(`step ${stepNum} (${op}): missing target (provide 'uid', 'name', 'target', 'selector', or 'x'/'y')`);
          }
          continue;
        }

        const el = resolveEl(targetRef);
        if (!el) {
          const ocrItem = resolveOcrTarget(targetRef);
          if (ocrItem) {
            st.cursor = {
              agent: st.agent,
              x: ocrItem.x,
              y: ocrItem.y,
              uid: ocrItem.uid,
              label: ocrItem.label,
              op
            };
            if (op === "move") {
              continue;
            } else if (op === "click") {
              const vw = window.innerWidth || document.documentElement.clientWidth || 0;
              const vh = window.innerHeight || document.documentElement.clientHeight || 0;
              if (req.cdpClick && ocrItem.x >= 0 && ocrItem.y >= 0 && ocrItem.x < vw && ocrItem.y < vh) {
                store.mutationSeq++;
                return makeCdpClickReturn(i + 1, ocrItem.x, ocrItem.y);
              }
              dispatchCanvasClick(ocrItem);
              if (!store.navigating) await new Promise(r => setTimeout(r, 0));
              if (store.navigating) return makeNavigatedEarlyReturn(i + 1);
              continue;
            } else if (op === "fill" || op === "set" || op === "type") {
              dispatchCanvasClick(ocrItem);
              const textVal = String(stepValue ?? "");
              const active = document.activeElement || document.body;
              for (const ch of textVal) {
                active.dispatchEvent(new KeyboardEvent("keydown", { key: ch, bubbles: true }));
                active.dispatchEvent(new KeyboardEvent("keyup", { key: ch, bubbles: true }));
              }
              continue;
            }
          }
          if (!s.optional) errors.push(`step ${stepNum} (${op}): target not found (${targetRef})`);
          continue;
        }

        const scrollEl = (window.getComputedStyle(el).display === "contents" && el.firstElementChild) ? el.firstElementChild : el;
        scrollEl.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
        updateAgentCursor(st, el, op);

        if (op === "move") {
          continue;
        } else if (op === "click" || op === "upload") {
          let fileEl = (tagOf(el) === "input" && String(el.type || "").toLowerCase() === "file") ? el : null;
          if (!fileEl && (s.files || op === "upload")) {
            fileEl = el.querySelector && el.querySelector("input[type='file']");
            if (!fileEl) {
              const lbl = tagOf(el) === "label" ? el : (el.closest && el.closest("label"));
              if (lbl) {
                fileEl = lbl.querySelector && lbl.querySelector("input[type='file']");
                if (!fileEl && lbl.htmlFor) {
                  const forEl = document.getElementById(lbl.htmlFor);
                  if (forEl && tagOf(forEl) === "input" && String(forEl.type || "").toLowerCase() === "file") {
                    fileEl = forEl;
                  }
                }
              }
            }
            if (!fileEl) {
              let anc = el.parentElement;
              for (let d = 0; d < 4 && anc && anc !== document.body; d++, anc = anc.parentElement) {
                const candidate = anc.querySelector && anc.querySelector("input[type='file']");
                if (candidate) {
                  fileEl = candidate;
                  break;
                }
              }
            }
            if (!fileEl) {
              const scope = el.closest && el.closest("[role='dialog'], [aria-modal='true'], form");
              if (scope) {
                fileEl = scope.querySelector && scope.querySelector("input[type='file']");
              }
            }
            if (!fileEl) {
              fileEl = document.querySelector("input[type='file']");
            }
          }
          if (fileEl || s.files || op === "upload") {
            const targetFileEl = fileEl || el;
            const fUid = getUid(targetFileEl);
            if (fileEl) {
              for (const old of document.querySelectorAll("[data-fmu-upload-uid]")) {
                old.removeAttribute("data-fmu-upload-uid");
              }
              fileEl.setAttribute("data-fmu-upload-uid", fUid);
            }
            escalations.push({ step: stepNum, reason: "file_input", uid: fileEl ? fUid : "" });
            continue;
          }
          if (req.cdpClick) {
            const pt = resolveTrustedClickPoint(el);
            if (pt) {
              return makeCdpClickReturn(i + 1, pt.x, pt.y);
            }
          }
          el.focus({ preventScroll: true });
          el.click();
          if (!store.navigating) await new Promise(r => setTimeout(r, 0));
          if (store.navigating) return makeNavigatedEarlyReturn(i + 1);
        } else if (op === "fill" || op === "set" || op === "type") {
          if (tagOf(el) === "iframe") {
            let innerDoc = null;
            try {
              innerDoc = (el.contentDocument && el.contentDocument.documentElement) ? el.contentDocument : null;
            } catch (_) {
              innerDoc = null;
            }
            if (innerDoc) {
              const innerSel = "input:not([type='hidden']):not([disabled]), textarea:not([disabled]), select:not([disabled]), [contenteditable='true'], [contenteditable=''], [contenteditable='plaintext-only']";
              const innerTarget = innerDoc.querySelector(innerSel);
              if (innerTarget) {
                try { innerTarget.focus({ preventScroll: true }); } catch (_) {}
                setNativeValue(innerTarget, stepValue ?? "", Boolean(s.append));
                continue;
              }
            }
            const measured = measureIframeCdpTarget(el);
            const eff = getEffectiveRect(el);
            if (req.cdpClick && (measured.inViewport || (eff.width >= 1 && eff.height >= 1))) {
              store.mutationSeq++;
              return makeCdpInputReturn(i + 1, {
                kind: "fill",
                x: measured.inViewport ? measured.x : Math.round((eff.left + eff.width * 0.5) * 100) / 100,
                y: measured.inViewport ? measured.y : Math.round((eff.top + eff.height * 0.5) * 100) / 100,
                needsScroll: !measured.inViewport,
                value: String(stepValue ?? ""),
                clear: !Boolean(s.append),
                frameSrc: measured.frameSrc,
                frameName: measured.frameName,
                frameTitle: measured.frameTitle,
                frameIndex: measured.frameIndex,
                uid: measured.uid,
                label: measured.label
              });
            }
            if (!s.optional) {
              errors.push(`step ${stepNum} (${op}): cannot fill cross-origin iframe ${getUid(el)} without CDP input support`);
            }
            continue;
          }
          el.focus({ preventScroll: true });
          setNativeValue(el, stepValue ?? "", Boolean(s.append));
        } else if (op === "select") {
          el.focus({ preventScroll: true });
          const desired = String(stepValue ?? "");
          let matched = false;
          if (el.options) {
            for (const opt of el.options) {
              if (opt.value === desired || cleanStr(opt.text).toLowerCase() === desired.toLowerCase()) {
                el.value = opt.value;
                matched = true;
                break;
              }
            }
          }
          if (!matched) el.value = desired;
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
        }
      } catch (err) {
        errors.push(`step ${stepNum} (${op}): ${err.message || err}`);
      }
    }

    const execMs = +(performance.now() - tExecStart).toFixed(2);
    if (store.navigating) {
      return makeNavigatedEarlyReturn(steps.length);
    }
    if (req.skipSnapshot) {
      return {
        url: location.href,
        title: document.title,
        errors,
        evalOutputs,
        rawEvalValues,
        escalations,
        cursor: st.cursor,
        canvases: [],
        timings: { execMs, settleMs: 0, snapMs: 0, totalInPageMs: +(performance.now() - tBatchStart).toFixed(2) },
        lines: []
      };
    }

    let settleMs = 0;
    if ((steps.length > 0 || req.hadPreSteps) && req.settle !== false) {
      const tSettle0 = performance.now();
      await waitQuietDom(14, 100);
      settleMs = +(performance.now() - tSettle0).toFixed(2);
    }
    if (store.navigating) {
      return makeNavigatedEarlyReturn(steps.length);
    }

    const tSnap0 = performance.now();
    const prevMap = st.lastLinesByUid;
    const prevCrc = st.lastCrc;
    st.preBatchLinesByUid = prevMap;
    st.preBatchCrc = prevCrc;
    const snap = collectSnapshot(query, 180, foldTables, fullPage);
    if (!query) {
      st.lastLinesByUid = snap.currentMap;
      st.lastCrc = snap.newCrc;
      st.lastSnapSeq = store.mutationSeq;
    }
    st.lastQuery = query;
    st.lastFoldTables = foldTables;
    st.lastFullPage = fullPage;

    const nativeTools = (navigator.modelContext && Array.isArray(navigator.modelContext.tools))
      ? navigator.modelContext.tools.map(t => t.name)
      : [];
    const webMcpTools = Array.from(new Set([...nativeTools, ...virtualTools.keys()]));

    let useDiff = !forceFull && !query && (hadBaseline || steps.length > 0 || req.hadPreSteps) && (req.diff === true || ((steps.length > 0 || req.hadPreSteps) && hadBaseline));
    let outLines = [];
    if (useDiff) {
      if (prevCrc === snap.newCrc) {
        outLines = [`= unchanged (crc=${snap.newCrc}, seq=${store.mutationSeq})`];
      } else {
        const delta = computeDiff(prevMap, snap.currentMap);
        if (delta.length === 0) {
          outLines = [`= unchanged (crc=${snap.newCrc}, seq=${store.mutationSeq})`];
        } else {
          let removedCount = 0;
          for (const uid of prevMap.keys()) {
            if (!snap.currentMap.has(uid)) removedCount++;
          }
          const majorRouteSwap = prevMap.size >= 8 && removedCount >= Math.ceil(prevMap.size * 0.75);
          if ((req.diff !== true || majorRouteSwap) && delta.length > 24 && delta.join("\n").length > snap.lines.join("\n").length) {
            outLines = snap.lines;
            useDiff = false;
          } else {
            outLines = delta;
          }
        }
      }
    } else {
      outLines = snap.lines;
    }
    const snapMs = +(performance.now() - tSnap0).toFixed(2);

    return {
      url: location.href,
      title: document.title,
      vw: window.innerWidth || 1280,
      vh: window.innerHeight || 800,
      crc: snap.newCrc,
      seq: store.mutationSeq,
      count: snap.totalCount,
      cursor: st.cursor,
      webMcpTools,
      canvases: snap.canvases || [],
      layoutIssues: snap.layoutIssues || [],
      useDiff,
      errors,
      evalOutputs,
      rawEvalValues,
      escalations,
      timings: { execMs, settleMs, snapMs, totalInPageMs: +(performance.now() - tBatchStart).toFixed(2) },
      lines: outLines
    };
  }

  function refreshAfterOcr(opts) {
    const agentId = String((opts && opts.agent) || "default");
    const st = getAgentState(agentId);
    if (opts && opts.ocrTargets) {
      registerOcrTargets(opts.ocrTargets);
    }
    const query = String((opts && opts.query) || "");
    const foldTables = (opts && opts.fullTable) !== true;
    const fullPage = Boolean(opts && opts.fullPage);
    const useDiff = Boolean(opts && opts.useDiff);
    const prevMap = st.preBatchLinesByUid || st.lastLinesByUid;
    const prevCrc = st.preBatchCrc || st.lastCrc;
    const snap = collectSnapshot(query, 180, foldTables, fullPage);
    if (!query) {
      st.lastLinesByUid = snap.currentMap;
      st.lastCrc = snap.newCrc;
      st.lastSnapSeq = store.mutationSeq;
    }
    let outLines = [];
    if (useDiff) {
      if (prevCrc === snap.newCrc) {
        outLines = [`= unchanged (crc=${snap.newCrc}, seq=${store.mutationSeq})`];
      } else {
        const delta = computeDiff(prevMap, snap.currentMap);
        outLines = delta.length > 0 ? delta : [`= unchanged (crc=${snap.newCrc}, seq=${store.mutationSeq})`];
      }
    } else {
      outLines = snap.lines;
    }
    return {
      crc: snap.newCrc,
      seq: store.mutationSeq,
      count: snap.totalCount,
      lines: outLines
    };
  }

  function deepElementFromPoint(x, y) {
    let hit = document.elementFromPoint(x, y);
    while (hit && hit.shadowRoot && typeof hit.shadowRoot.elementFromPoint === "function") {
      const inner = hit.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === hit) break;
      hit = inner;
    }
    return hit;
  }

  function isTrustedClickHit(el, hit) {
    if (!hit) return false;
    if (hit === el || (el.contains && el.contains(hit))) return true;
    if (tagOf(hit) === "label" && (hit.control === el || (el.id && hit.htmlFor === el.id))) return true;
    if (tagOf(hit) === "label" && el.control && (hit === el.control || (el.control.contains && el.control.contains(hit)))) return true;
    return false;
  }

  function resolveTrustedClickPoint(el) {
    if (!el || el.ownerDocument !== document || el.disabled) return null;
    const vw = window.innerWidth || document.documentElement.clientWidth || 0;
    const vh = window.innerHeight || document.documentElement.clientHeight || 0;
    if (vw <= 0 || vh <= 0) return null;
    const rects = [];
    const eff = getEffectiveRect(el);
    if (eff.width >= 1 && eff.height >= 1) rects.push(eff);
    if (el.getClientRects) {
      for (const cr of el.getClientRects()) {
        if (cr.width >= 1 && cr.height >= 1) rects.push(cr);
      }
    }
    const ratios = [[0.5, 0.5], [0.5, 0.25], [0.5, 0.75], [0.25, 0.5], [0.75, 0.5]];
    for (const r of rects) {
      const clL = Math.max(0, r.left);
      const clT = Math.max(0, r.top);
      const clR = Math.min(vw - 1, r.right);
      const clB = Math.min(vh - 1, r.bottom);
      if (clR <= clL || clB <= clT) continue;
      for (const [rx, ry] of ratios) {
        const px = clL + (clR - clL) * rx;
        const py = clT + (clB - clT) * ry;
        if (isTrustedClickHit(el, deepElementFromPoint(px, py))) {
          return { x: px, y: py };
        }
      }
    }
    return null;
  }

  function scrollElIntoViewport(el) {
    if (!el) return;
    const scrollEl = (window.getComputedStyle(el).display === "contents" && el.firstElementChild) ? el.firstElementChild : el;
    try {
      scrollEl.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
    } catch (_) {}
    const vw = window.innerWidth || document.documentElement.clientWidth || 0;
    const vh = window.innerHeight || document.documentElement.clientHeight || 0;
    let r = getEffectiveRect(el);
    if (vw > 0 && vh > 0 && (r.left < 0 || r.right > vw || r.top < 0 || r.bottom > vh)) {
      let anc = el.parentElement;
      while (anc && anc !== document.documentElement && anc !== document.body) {
        const st = window.getComputedStyle(anc);
        if (anc.scrollWidth > anc.clientWidth && (st.overflowX === "auto" || st.overflowX === "scroll" || st.overflowX === "overlay")) {
          const ar = anc.getBoundingClientRect();
          const er = getEffectiveRect(el);
          if (er.left < ar.left || er.right > ar.right) {
            anc.scrollLeft += (er.left + er.width * 0.5) - (ar.left + ar.width * 0.5);
          }
        }
        if (anc.scrollHeight > anc.clientHeight && (st.overflowY === "auto" || st.overflowY === "scroll" || st.overflowY === "overlay")) {
          const ar = anc.getBoundingClientRect();
          const er = getEffectiveRect(el);
          if (er.top < ar.top || er.bottom > ar.bottom) {
            anc.scrollTop += (er.top + er.height * 0.5) - (ar.top + ar.height * 0.5);
          }
        }
        anc = anc.parentElement;
      }
      r = getEffectiveRect(el);
      if (r.left < 0 || r.right > vw || r.top < 0 || r.bottom > vh) {
        const dx = (r.right <= 0 || r.left >= vw) ? ((r.left + r.width * 0.5) - vw * 0.5) : 0;
        const dy = (r.bottom <= 0 || r.top >= vh) ? ((r.top + r.height * 0.5) - vh * 0.5) : 0;
        if (dx || dy) {
          try { window.scrollBy({ left: dx, top: dy, behavior: "instant" }); } catch (_) {}
        }
      }
    }
  }

  function getIframeWindowIndex(el) {
    try {
      if (el && el.contentWindow) {
        for (let k = 0; k < window.length; k++) {
          if (window[k] === el.contentWindow) return k;
        }
      }
    } catch (_) {}
    return -1;
  }

  function measureIframeCdpTarget(el) {
    scrollElIntoViewport(el);
    const eff = getEffectiveRect(el);
    const vw = window.innerWidth || document.documentElement.clientWidth || 0;
    const vh = window.innerHeight || document.documentElement.clientHeight || 0;
    let pt = resolveTrustedClickPoint(el);
    if (!pt && eff.width >= 1 && eff.height >= 1 && vw > 0 && vh > 0) {
      const clL = Math.max(0, eff.left);
      const clT = Math.max(0, eff.top);
      const clR = Math.min(vw - 1, eff.right);
      const clB = Math.min(vh - 1, eff.bottom);
      if (clR > clL && clB > clT) {
        pt = { x: clL + (clR - clL) * 0.5, y: clT + (clB - clT) * 0.5 };
      }
    }
    return {
      inViewport: Boolean(pt),
      x: pt ? Math.round(pt.x * 100) / 100 : null,
      y: pt ? Math.round(pt.y * 100) / 100 : null,
      frameIndex: getIframeWindowIndex(el),
      frameSrc: String(el.getAttribute("src") || el.src || ""),
      frameName: String(el.getAttribute("name") || ""),
      frameTitle: String(el.getAttribute("title") || el.getAttribute("aria-label") || ""),
      uid: getUid(el),
      label: getLabel(el)
    };
  }

  function prepIframeTarget(spec) {
    let el = spec && spec.uid ? resolveEl(spec.uid) : null;
    if (!el || tagOf(el) !== "iframe") {
      const frames = queryAllDeep("iframe", document);
      const idx = spec && typeof spec.frameIndex === "number" ? spec.frameIndex : -1;
      if (idx >= 0) {
        for (const cand of frames) {
          if (getIframeWindowIndex(cand) === idx) { el = cand; break; }
        }
      }
      if (!el && spec) {
        const fSrc = String(spec.frameSrc || "").trim();
        const fName = String(spec.frameName || "").trim();
        const fTitle = String(spec.frameTitle || "").trim();
        el = frames.find(f => (fName && f.getAttribute("name") === fName) || (fTitle && (f.getAttribute("title") === fTitle || f.getAttribute("aria-label") === fTitle)))
          || (fSrc ? frames.find(f => String(f.getAttribute("src") || f.src || "").trim() === fSrc) : null)
          || null;
      }
    }
    if (!el || tagOf(el) !== "iframe") return null;
    return measureIframeCdpTarget(el);
  }

  window.__fmu = {
    version: FMU_RUNTIME_VERSION,
    executeBatch,
    collectSnapshot,
    resolveEl,
    prepIframeTarget,
    invokeWebMcp,
    registerVirtualTools,
    registerOcrTargets,
    resolveOcrTarget,
    refreshAfterOcr,
    getAgentState,
    resetAgent
  };
  return true;
})();
"""

_GROUP_COLORS = ["blue", "purple", "cyan", "green", "orange", "pink", "yellow", "red"]
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_OCR_LINE_RE = re.compile(
    r'^(@ocr\d+)\s+canvas-text\s+"([^"]+)"\s+\((-?\d+),(-?\d+)\)(?:\s+\[(\d+)x(\d+)\])?'
)


class ExtensionWebSocketBridge:
    """RFC 6455 localhost WebSocket server for the outbound MV3 extension bridge (0 Chrome 144 modal popups)."""

    def __init__(
        self,
        preferred_port: int | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if preferred_port is None:
            preferred_port = int(os.environ.get("FMU_BRIDGE_PORT", "19222"))
        self.preferred_port = preferred_port
        self.port: int = 0
        self.port_fallback_reason: str | None = None
        self.on_event = on_event
        self._srv_sock: socket.socket | None = None
        self._client_sock: socket.socket | None = None
        self._client_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._ready_event = threading.Event()
        self._hello_event = threading.Event()
        self._hello_info: dict[str, Any] = {}
        self._sent_runtime_version: str = ""
        self._next_req_id = 1
        self._pending: dict[int, tuple[threading.Event, list[Any]]] = {}
        self._pending_lock = threading.Lock()
        self._stopped = False
        self._start_server()

    def _start_server(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bound = False
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                srv.bind(("127.0.0.1", self.preferred_port))
                bound = True
                break
            except OSError as exc:
                last_err = exc
                if attempt < 2:
                    time.sleep(0.04)
        if not bound:
            self.port_fallback_reason = str(last_err) if last_err else "address in use"
            srv.bind(("127.0.0.1", 0))
        srv.listen(8)
        self.port = int(srv.getsockname()[1])
        self._srv_sock = srv
        t = threading.Thread(target=self._accept_loop, name="fmu-ext-ws-bridge", daemon=True)
        t.start()

    def ensure_preferred_port(self) -> None:
        if self._stopped or self.preferred_port == 0 or self.port == self.preferred_port or self.is_connected():
            return
        test_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            test_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            test_srv.bind(("127.0.0.1", self.preferred_port))
        except OSError:
            test_srv.close()
            return
        old_srv = self._srv_sock
        test_srv.listen(8)
        self._srv_sock = test_srv
        self.port = int(test_srv.getsockname()[1])
        self.port_fallback_reason = None
        if old_srv is not None:
            try:
                old_srv.close()
            except OSError:
                pass
        t = threading.Thread(target=self._accept_loop, name="fmu-ext-ws-bridge", daemon=True)
        t.start()

    def is_connected(self) -> bool:
        with self._client_lock:
            return self._client_sock is not None and self._ready_event.is_set()

    def wait_connected(self, timeout: float = 2.0) -> bool:
        self.ensure_preferred_port()
        return self._ready_event.wait(timeout=timeout) and self.is_connected()

    def get_status(self) -> dict[str, Any]:
        self.ensure_preferred_port()
        connected = self.is_connected()
        if connected and not self._hello_info:
            self._hello_event.wait(timeout=0.15)
        return {
            "connected": connected,
            "port": self.port,
            "preferredPort": self.preferred_port,
            "wsUrl": f"ws://127.0.0.1:{self.preferred_port}/ext",
            "portFallbackReason": self.port_fallback_reason,
            "client": dict(self._hello_info),
        }

    def _accept_loop(self) -> None:
        while not self._stopped and self._srv_sock is not None:
            try:
                conn, _ = self._srv_sock.accept()
            except OSError:
                break
            threading.Thread(
                target=self._handle_client, args=(conn,), name="fmu-ext-ws-client", daemon=True
            ).start()

    @staticmethod
    def _recv_exact(sock: socket.socket, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("WebSocket closed")
            buf.extend(chunk)
        return bytes(buf)

    def _read_ws_frame_raw(self, sock: socket.socket) -> tuple[bool, int, bytes]:
        hdr = self._recv_exact(sock, 2)
        fin = (hdr[0] & 0x80) != 0
        opcode = hdr[0] & 0x0F
        masked = (hdr[1] & 0x80) != 0
        length = hdr[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(sock, 8))[0]
        mask_key = self._recv_exact(sock, 4) if masked else b""
        raw_payload = self._recv_exact(sock, length) if length > 0 else b""
        if masked and mask_key and length > 0:
            mask_rep = (mask_key * ((length + 3) // 4))[:length]
            raw_payload = (
                int.from_bytes(raw_payload, "big") ^ int.from_bytes(mask_rep, "big")
            ).to_bytes(length, "big")
        return fin, opcode, raw_payload

    def _read_ws_message(self, sock: socket.socket) -> tuple[int, bytes]:
        msg_opcode: int | None = None
        fragments: list[bytes] = []
        while True:
            fin, opcode, payload = self._read_ws_frame_raw(sock)
            if opcode >= 0x8:
                if opcode == 0x8:
                    return 0x8, b""
                if opcode == 0x9:
                    self._send_ws_frame(sock, payload, opcode=0xA)
                continue
            if opcode in (0x1, 0x2):
                if fin and not fragments:
                    return opcode, payload
                msg_opcode = opcode
                fragments = [payload]
                if fin:
                    return msg_opcode, b"".join(fragments)
            elif opcode == 0x0:
                fragments.append(payload)
                if fin:
                    return (msg_opcode if msg_opcode is not None else 0x1), b"".join(fragments)

    def _send_ws_frame(self, sock: socket.socket, payload: bytes, opcode: int = 0x1) -> None:
        header = bytearray([0x80 | (opcode & 0x0F)])
        n = len(payload)
        if n < 126:
            header.append(n)
        elif n <= 65535:
            header.append(126)
            header.extend(struct.pack("!H", n))
        else:
            header.append(127)
            header.extend(struct.pack("!Q", n))
        with self._send_lock:
            sock.sendall(bytes(header) + payload)

    def _handle_client(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(10.0)
            req_bytes = b""
            while b"\r\n\r\n" not in req_bytes and len(req_bytes) < 16384:
                chunk = conn.recv(4096)
                if not chunk:
                    conn.close()
                    return
                req_bytes += chunk
            header_text = req_bytes.decode("utf-8", errors="replace")
            key_match = re.search(r"(?i)^Sec-WebSocket-Key:\s*(.+?)\s*$", header_text, re.M)
            if not key_match:
                conn.close()
                return
            ws_key = key_match.group(1).strip()
            accept_val = base64.b64encode(
                hashlib.sha1((ws_key + _WS_GUID).encode("ascii")).digest()
            ).decode("ascii")
            resp = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept_val}\r\n\r\n"
            )
            conn.sendall(resp.encode("ascii"))
            conn.settimeout(None)

            with self._client_lock:
                if self._client_sock is not None:
                    try:
                        self._client_sock.close()
                    except OSError:
                        pass
                self._client_sock = conn
                self._sent_runtime_version = ""
                self._hello_info = {}
                self._hello_event.clear()
                self._ready_event.set()

            while not self._stopped:
                opcode, data = self._read_ws_message(conn)
                if opcode == 0x8:  # Close
                    break
                if opcode == 0x1:  # Text
                    try:
                        msg = json.loads(data.decode("utf-8", errors="replace"))
                    except Exception:
                        continue
                    if not isinstance(msg, dict):
                        continue
                    msg_type = msg.get("type")
                    if msg_type == "hello":
                        self._hello_info = msg
                        self._hello_event.set()
                        self._ready_event.set()
                        continue
                    if msg_type and self.on_event is not None:
                        try:
                            self.on_event(msg)
                        except _BROWSER_OPS_ERRORS:
                            pass
                        continue
                    req_id = msg.get("id")
                    if req_id is not None:
                        with self._pending_lock:
                            waiter = self._pending.pop(int(req_id), None)
                        if waiter is not None:
                            ev, box = waiter
                            box.append(msg.get("result"))
                            box.append(msg.get("error"))
                            ev.set()
        except _BROWSER_OPS_ERRORS:
            pass
        finally:
            with self._client_lock:
                if self._client_sock is conn:
                    self._client_sock = None
                    self._sent_runtime_version = ""
                    self._ready_event.clear()
            with self._pending_lock:
                for _, (ev, box) in list(self._pending.items()):
                    if not box:
                        box.extend([None, "Extension WebSocket disconnected"])
                    ev.set()
                self._pending.clear()
            try:
                conn.close()
            except OSError:
                pass

    def call_rpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 10.0,
        *,
        check_stopped: bool = True,
    ) -> Any:
        with self._client_lock:
            sock = self._client_sock
        if sock is None:
            raise ConnectionError("No MV3 extension connected to WebSocket bridge")
        with self._pending_lock:
            req_id = self._next_req_id
            self._next_req_id += 1
            ev = threading.Event()
            box: list[Any] = []
            self._pending[req_id] = (ev, box)
        payload = json.dumps({"id": req_id, "method": method, "params": params or {}}).encode("utf-8")
        try:
            self._send_ws_frame(sock, payload, opcode=0x1)
        except Exception as exc:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise ConnectionError(f"Failed sending to extension bridge: {exc}") from exc
        deadline = time.monotonic() + timeout
        while True:
            if check_stopped and is_user_stopped():
                with self._pending_lock:
                    self._pending.pop(req_id, None)
                raise RuntimeError(USER_STOPPED_ERROR)
            rem = deadline - time.monotonic()
            if rem <= 0:
                with self._pending_lock:
                    self._pending.pop(req_id, None)
                raise TimeoutError(f"Extension bridge RPC '{method}' timed out after {timeout}s")
            if ev.wait(timeout=min(0.05, rem)):
                break
        if len(box) >= 2 and box[1]:
            raise RuntimeError(str(box[1]))
        return box[0] if box else None

    def shutdown(self) -> None:
        self._stopped = True
        with self._client_lock:
            if self._client_sock is not None:
                try:
                    self._client_sock.close()
                except OSError:
                    pass
                self._client_sock = None
        if self._srv_sock is not None:
            try:
                self._srv_sock.close()
            except OSError:
                pass
            self._srv_sock = None


class _ExtensionCdpAdapter:
    def __init__(self, bridge: ExtensionWebSocketBridge, chrome_tab_id: int) -> None:
        self._bridge = bridge
        self.chrome_tab_id = chrome_tab_id

    def send(self, method: str, params: dict[str, Any] | None = None) -> Any:
        cmd_params = params or {}
        return self._bridge.call_rpc(
            "cdp",
            {
                "tabId": self.chrome_tab_id,
                "method": method,
                "cdpMethod": method,
                "commandParams": cmd_params,
                "cdpParams": cmd_params,
            },
        )


class _ExtensionPageAdapter:
    def __init__(
        self,
        bridge: ExtensionWebSocketBridge,
        chrome_tab_id: int,
        initial_url: str = "about:blank",
        initial_title: str = "",
        window_id: int | None = None,
        on_preview: Callable[[dict[str, Any], str], None] | None = None,
    ) -> None:
        self._bridge = bridge
        self.chrome_tab_id = chrome_tab_id
        self.url = initial_url
        self.title = initial_title
        self.window_id = window_id
        self._on_preview = on_preview
        self._closed = False

    def is_closed(self) -> bool:
        return self._closed or not self._bridge.is_connected()

    def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 15000) -> dict[str, Any]:
        res = self._bridge.call_rpc(
            "navigate",
            {
                "tabId": self.chrome_tab_id,
                "url": url,
                "timeoutMs": timeout,
                "skipPreview": not _preview_enabled(),
            },
            timeout=max(5.0, timeout / 1000.0 + 2.0),
        )
        if isinstance(res, dict) and res.get("url"):
            self.url = str(res["url"])
        else:
            self.url = url
        if isinstance(res, dict) and "title" in res:
            self.title = str(res.get("title") or "")
        if isinstance(res, dict) and isinstance(res.get("preview"), dict) and self._on_preview is not None:
            try:
                self._on_preview(res["preview"], self.url)
            except _BROWSER_OPS_ERRORS:
                pass
        return res or {"url": self.url}

    def set_input_files(self, selector: str, files: str | list[str]) -> dict[str, Any]:
        file_list = [str(files)] if isinstance(files, (str, Path)) else [str(f) for f in files]
        inline_files, inline_skipped = _build_inline_files_payload(file_list)
        rpc_params: dict[str, Any] = {
            "tabId": self.chrome_tab_id,
            "selector": selector,
            "files": file_list,
        }
        if inline_files:
            rpc_params["inlineFiles"] = inline_files
        if inline_skipped:
            rpc_params["inlineSkipped"] = inline_skipped
        res = self._bridge.call_rpc(
            "setInputFiles",
            rpc_params,
            timeout=15.0,
        )
        return res if isinstance(res, dict) else {"ok": True}

    def evaluate(self, expression: str, arg: Any = None) -> Any:
        expr_str = expression.strip()
        if expr_str == IN_PAGE_RUNTIME_JS.strip():
            return self._bridge.call_rpc(
                "evaluate",
                {"tabId": self.chrome_tab_id, "expression": expr_str, "runtimeInit": True},
            )
        if "window.__fmu.executeBatch" in expr_str and isinstance(arg, dict):
            batch_arg = dict(arg)
            if not _preview_enabled():
                batch_arg["skipPreview"] = True
            step_wait_s = 0.0
            norm_steps: list[Any] = []
            for st in batch_arg.get("steps") or []:
                if isinstance(st, dict):
                    st_norm = normalize_step_dict(st)
                    sop = str(st_norm.get("op") or st_norm.get("action") or "").lower()
                    if sop in ("waitfor", "wait_for", "waituntil"):
                        step_wait_s += max(
                            0.2,
                            float(st_norm.get("timeoutMs") or st_norm.get("timeout") or st_norm.get("ms") or 5000) / 1000.0,
                        )
                    elif sop in ("wait", "sleep", "delay", "settle"):
                        step_wait_s += max(0.0, float(st_norm.get("ms") or 30) / 1000.0)
                    raw_files = st_norm.get("files") or (st_norm.get("value") if sop == "upload" else None)
                    if raw_files and "inlineFiles" not in st_norm and not batch_arg.get("skipSnapshot"):
                        flist = [str(raw_files)] if isinstance(raw_files, (str, Path)) else [str(f) for f in raw_files]
                        inline_f, inline_skipped = _build_inline_files_payload(flist)
                        if inline_f:
                            st_norm = {**st_norm, "inlineFiles": inline_f}
                        elif inline_skipped:
                            st_norm = {**st_norm, "inlineSkipped": inline_skipped}
                    norm_steps.append(st_norm)
                else:
                    norm_steps.append(st)
            if norm_steps:
                batch_arg["steps"] = norm_steps
            rpc_timeout = max(18.0, step_wait_s + 12.0)
            rpc_payload: dict[str, Any] = {
                **batch_arg,
                "tabId": self.chrome_tab_id,
                "runtimeVersion": _FMU_RUNTIME_VERSION,
            }
            if self._bridge._sent_runtime_version != _FMU_RUNTIME_VERSION:
                rpc_payload["runtimeJs"] = IN_PAGE_RUNTIME_JS
            try:
                res = self._bridge.call_rpc("executeBatch", rpc_payload, timeout=rpc_timeout)
                self._bridge._sent_runtime_version = _FMU_RUNTIME_VERSION
            except Exception as exc:
                if "No runtimeJs available" in str(exc):
                    self._bridge._sent_runtime_version = ""
                    rpc_payload["runtimeJs"] = IN_PAGE_RUNTIME_JS
                    res = self._bridge.call_rpc("executeBatch", rpc_payload, timeout=rpc_timeout)
                    self._bridge._sent_runtime_version = _FMU_RUNTIME_VERSION
                else:
                    raise
            if isinstance(res, dict):
                if res.get("url"):
                    self.url = str(res["url"])
                if "title" in res:
                    self.title = str(res.get("title") or "")
                if isinstance(res.get("preview"), dict) and self._on_preview is not None:
                    try:
                        self._on_preview(res["preview"], self.url)
                    except _BROWSER_OPS_ERRORS:
                        pass
            return res
        return self._bridge.call_rpc(
            "evaluate",
            {"tabId": self.chrome_tab_id, "expression": expr_str, "arg": arg},
        )

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._bridge.call_rpc("closeTab", {"tabId": self.chrome_tab_id}, timeout=2.0)
            except _BROWSER_OPS_ERRORS:
                pass


class _ThreadProxy:
    """Forwards Playwright object attribute/method access onto the dedicated Playwright worker thread."""

    def __init__(self, engine: BrowserEngine, target: Any) -> None:
        object.__setattr__(self, "_engine", engine)
        object.__setattr__(self, "_target", target)

    def __bool__(self) -> bool:
        tgt = object.__getattribute__(self, "_target")
        return bool(tgt)

    def __getattr__(self, name: str) -> Any:
        eng: BrowserEngine = object.__getattribute__(self, "_engine")
        tgt: Any = object.__getattribute__(self, "_target")
        attr = eng._run_sync(lambda: getattr(tgt, name))
        if callable(attr):

            def _call(*args: Any, **kwargs: Any) -> Any:
                res = eng._run_sync(lambda: getattr(tgt, name)(*args, **kwargs))
                if res is not None and not isinstance(
                    res, (bool, int, float, str, bytes, list, dict, tuple, set)
                ):
                    return _ThreadProxy(eng, res)
                return res

            return _call
        if attr is not None and not isinstance(
            attr, (bool, int, float, str, bytes, list, dict, tuple, set)
        ):
            return _ThreadProxy(eng, attr)
        return attr


def _append_uid_boxes(sess: Any, lines: list[str]) -> list[str]:
    uids = [m.group(1) for line in lines if isinstance(line, str) and (m := _LINE_UID_RE.match(line))]
    if not uids:
        return lines
    boxes = sess._raw_page.evaluate(_UID_BOXES_JS, uids) or {}
    out_lines: list[str] = []
    for line in lines:
        m = _LINE_UID_RE.match(line) if isinstance(line, str) else None
        box = boxes.get(m.group(1)) if m else None
        out_lines.append(f"{line} box={','.join(str(int(v)) for v in box)}" if box else line)
    return out_lines


class BrowserTabSession:
    def __init__(
        self,
        engine: BrowserEngine,
        tab_id: str,
        page: Any,
        cdp_session: Any,
        group_name: str = "Agent",
        group_color: str = "blue",
        last_url: str = "about:blank",
        last_title: str = "",
        is_extension: bool = False,
    ) -> None:
        self._engine = engine
        self.tab_id = tab_id
        self._raw_page = page
        self._raw_cdp = cdp_session
        self.page = _ThreadProxy(engine, page)
        self.cdp = _ThreadProxy(engine, cdp_session)
        self.group_name = group_name
        self.group_color = group_color
        self.last_url = last_url
        self.last_title = last_title
        self.is_extension = is_extension
        self._exec_lock = threading.RLock()
        self.documented = False
        self._documented_agents: set[str] = set()
        self._focus_emulated = False
        self._runtime_ensured = False
        self._ocr_checked = False
        self._ocr_populated = False
        self._last_ocr_items: list[dict[str, Any]] = []
        self._screencast_attached = False
        self._screencast_running = False
        self._last_preview_push_mono = 0.0
        self.last_preview_path: str | None = None
        self.last_vw: int = 1280
        self.last_vh: int = 800
        self._custom_viewport: bool = False
        self._last_layout_sig: str = ""
        self._diag_lock = threading.Lock()
        self._diagnostics: list[dict[str, Any]] = []
        self._unreported_diags: list[dict[str, Any]] = []
        if not is_extension and hasattr(page, "on"):
            try:
                page.on("framenavigated", self._on_playwright_main_nav)
                page.on("pageerror", self._on_playwright_pageerror)
                page.on("console", self._on_playwright_console)
                page.on("response", self._on_playwright_response)
                page.on("requestfailed", self._on_playwright_requestfailed)
            except _BROWSER_OPS_ERRORS:
                pass

    def record_diagnostic(
        self,
        kind: str,
        message: str,
        *,
        url: str = "",
        status: int | None = None,
    ) -> None:
        msg = str(message or "").strip()
        if not msg:
            return
        u = str(url or "").strip()
        if u.endswith("/favicon.ico") or "/favicon.ico" in msg:
            return
        if "Refused to evaluate a string as JavaScript" in msg:
            return
        entry: dict[str, Any] = {"kind": kind, "message": msg[:500]}
        if u:
            entry["url"] = u
        if status is not None:
            entry["status"] = int(status)
        with self._diag_lock:
            self._diagnostics.append(entry)
            if len(self._diagnostics) > 60:
                del self._diagnostics[:-60]
            self._unreported_diags.append(entry)
            if len(self._unreported_diags) > 60:
                del self._unreported_diags[:-60]

    def get_diagnostics(self, *, clear: bool = False) -> list[dict[str, Any]]:
        with self._diag_lock:
            items = [dict(d) for d in self._diagnostics]
            if clear:
                self._diagnostics.clear()
                self._unreported_diags.clear()
            return items

    def clear_diagnostics(self) -> None:
        with self._diag_lock:
            self._diagnostics.clear()
            self._unreported_diags.clear()

    def consume_unreported_diagnostics(self) -> list[dict[str, Any]]:
        with self._diag_lock:
            if not self._unreported_diags:
                return []
            items = list(self._unreported_diags)
            self._unreported_diags.clear()
            return items

    def _on_playwright_pageerror(self, exc: Any) -> None:
        self.record_diagnostic("pageerror", str(exc), url=self.last_url)

    def _on_playwright_console(self, msg: Any) -> None:
        if msg.type != "error":
            return
        txt = str(msg.text or "").strip()
        if txt.startswith("Failed to load resource:"):
            return
        loc = msg.location or {}
        self.record_diagnostic("console.error", txt, url=str(loc.get("url") or self.last_url))

    def _on_playwright_response(self, resp: Any) -> None:
        status = int(resp.status)
        if status < 400:
            return
        req = resp.request
        if req.resource_type not in ("document", "fetch", "xhr"):
            return
        u = str(resp.url or "")
        self.record_diagnostic("http", f"{status} {req.method} {u}", url=u, status=status)

    def _on_playwright_requestfailed(self, req: Any) -> None:
        if req.resource_type not in ("document", "fetch", "xhr"):
            return
        fail = req.failure
        err_txt = str(fail.get("errorText") if isinstance(fail, dict) else (fail or "failed"))
        if "ERR_ABORTED" in err_txt:
            return
        u = str(req.url or "")
        self.record_diagnostic("network", f"{req.method} {u} ({err_txt})", url=u)

    def _on_playwright_main_nav(self, frame: Any) -> None:
        try:
            if frame == self._raw_page.main_frame:
                new_u = str(getattr(self._raw_page, "url", "") or "")
                if not _same_origin_and_path(self.last_url, new_u):
                    self._runtime_ensured = False
                    self._ocr_checked = False
                    self._ocr_populated = False
                    self._focus_emulated = False
        except Exception:
            self._runtime_ensured = False
            self._ocr_checked = False

    def enable_background_focus_emulation(self) -> None:
        if self._focus_emulated:
            return
        if self.is_extension:
            self._focus_emulated = True
            return
        try:
            self._raw_cdp.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        except _BROWSER_OPS_ERRORS:
            pass
        try:
            self._raw_cdp.send("Page.setWebLifecycleState", {"state": "active"})
        except _BROWSER_OPS_ERRORS:
            pass
        self._focus_emulated = True

    def start_playwright_screencast(self) -> None:
        # Playwright Python sync_api uses greenlet fiber switching; calling cdp.send()
        # inside a cdp.on("Page.screencastFrame") callback is re-entrant and leaves frames
        # unacknowledged during page.evaluate(), backpressuring Chromium's compositor
        # requestAnimationFrame(). On Playwright tabs we push crisp Page.captureScreenshot
        # frames on navigation, mid-batch boundaries, and post-batch completion instead.
        return

    def stop_playwright_screencast(self) -> None:
        return

    def capture_viewport_preview(self) -> str | None:
        if not _preview_enabled():
            return None
        try:
            if self.is_extension:
                chrome_tid = getattr(self._raw_page, "chrome_tab_id", None)
                if not chrome_tid:
                    return self.last_preview_path
                prev = self._engine._ext_bridge.call_rpc(
                    "capturePreview", {"tabId": chrome_tid, "maxWidth": 1440}, timeout=1.5
                )
                if isinstance(prev, dict) and isinstance(prev.get("jpeg"), str):
                    p_path = _write_web_preview_b64(prev["jpeg"])
                    if p_path:
                        self.last_preview_path = p_path
                        if prev.get("vw"):
                            self.last_vw = int(prev["vw"])
                        if prev.get("vh"):
                            self.last_vh = int(prev["vh"])
                        return p_path
                return self.last_preview_path

            page_x = 0.0
            page_y = 0.0
            vw = self.last_vw or 1280
            vh = self.last_vh or 800
            try:
                metrics = self._raw_cdp.send("Page.getLayoutMetrics", {})
                vp = (metrics or {}).get("cssVisualViewport") or (metrics or {}).get("visualViewport") or {}
                if float(vp.get("clientWidth") or 0) > 80 and float(vp.get("clientHeight") or 0) > 60:
                    page_x = max(0.0, float(vp.get("pageX") or 0.0))
                    page_y = max(0.0, float(vp.get("pageY") or 0.0))
                    vw = int(round(float(vp["clientWidth"])))
                    vh = int(round(float(vp["clientHeight"])))
            except _BROWSER_OPS_ERRORS:
                pass
            scale = min(1.0, max(0.25, 1440.0 / max(320, vw)))
            shot = self._raw_cdp.send(
                "Page.captureScreenshot",
                {
                    "format": "jpeg",
                    "quality": 68,
                    "optimizeForSpeed": True,
                    "clip": {
                        "x": page_x,
                        "y": page_y,
                        "width": vw,
                        "height": vh,
                        "scale": scale,
                    },
                },
            )
            b64_data = shot.get("data") if isinstance(shot, dict) else None
            p_path = _write_web_preview_b64(b64_data)
            if p_path:
                self.last_preview_path = p_path
                self.last_vw = vw
                self.last_vh = vh
                return p_path
        except _BROWSER_OPS_ERRORS:
            pass
        return self.last_preview_path

    def capture_screenshot(
        self,
        path: str | None = None,
        *,
        full_page: bool = False,
    ) -> tuple[str, int, int]:
        global _SCREENSHOT_SEQ
        cdp_params: dict[str, Any] = {"format": "png"}
        if full_page:
            metrics = self._raw_cdp.send("Page.getLayoutMetrics", {}) or {}
            cs = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
            cw = max(1, int(round(float(cs.get("width") or self.last_vw or 1280))))
            ch = max(1, min(16384, int(round(float(cs.get("height") or self.last_vh or 800)))))
            cdp_params["captureBeyondViewport"] = True
            cdp_params["clip"] = {"x": 0, "y": 0, "width": cw, "height": ch, "scale": 1}
        shot = self._raw_cdp.send("Page.captureScreenshot", cdp_params)
        b64_data = shot.get("data") if isinstance(shot, dict) else None
        if not isinstance(b64_data, str) or not b64_data:
            raise RuntimeError(f"Page.captureScreenshot returned empty data on tab {self.tab_id}")
        raw = base64.b64decode(b64_data)
        if path and str(path).strip():
            dest = Path(str(path).strip()).expanduser().resolve()
        else:
            with _PREVIEW_FILE_LOCK:
                _SCREENSHOT_SEQ += 1
                seq = _SCREENSHOT_SEQ
            safe_tab = re.sub(r"[^a-zA-Z0-9_.-]+", "_", self.tab_id)
            dest = Path(f"/tmp/fmu-screenshot-{safe_tab}-{seq}.png")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        img_w = self.last_vw or 1280
        img_h = self.last_vh or 800
        if len(raw) >= 24 and raw[:8] == b"\x89PNG\r\n\x1a\n":
            img_w, img_h = struct.unpack(">II", raw[16:24])
        return str(dest), int(img_w), int(img_h)

    def set_viewport(
        self,
        width: int | None = None,
        height: int | None = None,
        *,
        scale: float = 1.0,
        mobile: bool | None = None,
        reset: bool = False,
    ) -> tuple[int, int]:
        if reset or width is None or height is None:
            self._custom_viewport = False
            self._last_layout_sig = ""
            try:
                self._raw_cdp.send("Emulation.clearDeviceMetricsOverride", {})
            except _BROWSER_OPS_ERRORS:
                pass
            if not self.is_extension and not self._engine._is_cdp_chrome:
                self._raw_page.set_viewport_size({"width": 1280, "height": 800})
                self.last_vw, self.last_vh = 1280, 800
            else:
                try:
                    metrics = self._raw_cdp.send("Page.getLayoutMetrics", {}) or {}
                    vp = metrics.get("cssVisualViewport") or metrics.get("visualViewport") or {}
                    if vp.get("clientWidth") and vp.get("clientHeight"):
                        self.last_vw = int(round(float(vp["clientWidth"])))
                        self.last_vh = int(round(float(vp["clientHeight"])))
                except _BROWSER_OPS_ERRORS:
                    pass
            try:
                self._raw_page.evaluate("() => { if (window.__fmuStore) window.__fmuStore.mutationSeq++; }")
            except _BROWSER_OPS_ERRORS:
                pass
            return self.last_vw, self.last_vh

        w = max(200, int(round(float(width))))
        h = max(160, int(round(float(height))))
        sc = max(0.25, float(scale or 1.0))
        is_mob = bool(mobile) if mobile is not None else False
        if not self.is_extension and not self._engine._is_cdp_chrome:
            self._raw_page.set_viewport_size({"width": w, "height": h})
        self._raw_cdp.send(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": w,
                "height": h,
                "deviceScaleFactor": sc,
                "mobile": is_mob,
            },
        )
        try:
            self._raw_page.evaluate("() => { if (window.__fmuStore) window.__fmuStore.mutationSeq++; }")
        except _BROWSER_OPS_ERRORS:
            pass
        self._custom_viewport = True
        self._last_layout_sig = ""
        self.last_vw = w
        self.last_vh = h
        return w, h

    def ensure_runtime(self, force: bool = False) -> None:
        if self.is_extension and not force:
            self._runtime_ensured = True
            return
        if self._runtime_ensured and not force:
            return
        try:
            self._raw_page.evaluate(IN_PAGE_RUNTIME_JS)
            self._runtime_ensured = True
        except Exception as exc:
            if not _is_navigation_error(exc):
                raise
            if not self.is_extension:
                try:
                    self._raw_page.wait_for_load_state("domcontentloaded", timeout=10000)
                except _BROWSER_OPS_ERRORS:
                    pass
                self._focus_emulated = False
                self.enable_background_focus_emulation()
            else:
                time.sleep(0.1)
            self._raw_page.evaluate(IN_PAGE_RUNTIME_JS)
            self._runtime_ensured = True


class BrowserEngine:
    bounds_agents: set[str] | frozenset[str] = frozenset()

    def __init__(self) -> None:
        self._pw: Any = None
        self._raw_browser: Any = None
        self._context: Any = None
        self._is_cdp_chrome: bool = False
        self._tabs: dict[str, BrowserTabSession] = {}
        self._tab_last_urls: dict[str, str] = {}
        self._tab_last_titles: dict[str, str] = {}
        self._last_tab_steps: dict[str, list[dict[str, Any]]] = {}
        self._next_tab_num: int = 1
        self.cursors: dict[str, dict[str, Any]] = {}
        self._agent_colors: dict[str, str] = {}
        self.clients = SessionRegistry()
        self._strict_csp_hosts: set[str] = set()
        self._state_lock = threading.RLock()
        self._ext_bridge_inst: ExtensionWebSocketBridge | None = None
        self._ext_self_id: str = ""
        self._bridge_lock = threading.Lock()
        self._init_mono: float = time.monotonic()
        self._preview_lock = threading.Lock()
        self._preview_seq: int = 0
        self._preview_timer: threading.Timer | None = None

        self._cmd_q: queue.Queue[tuple[Callable[[], Any], threading.Event | None, list[Any] | None] | None] = queue.Queue()
        self._worker_tid: int = 0
        self._worker = threading.Thread(target=self._worker_loop, name="fmu-pw-worker", daemon=True)
        self._worker.start()

    def _cancel_pending_preview(self) -> int:
        with self._preview_lock:
            self._preview_seq += 1
            if self._preview_timer is not None:
                self._preview_timer.cancel()
                self._preview_timer = None
            return self._preview_seq

    def _schedule_async_preview(
        self,
        sess: BrowserTabSession,
        *,
        target: str,
        op: str,
        cur_info: dict[str, Any] | None = None,
        is_headless: bool = False,
        delay_s: float = 0.035,
    ) -> None:
        if not _preview_enabled() or sess.is_extension:
            return
        cx = int(cur_info["x"]) if cur_info and cur_info.get("x") is not None else None
        cy = int(cur_info["y"]) if cur_info and cur_info.get("y") is not None else None
        cl = str(cur_info.get("label") or cur_info.get("uid") or "") if cur_info else None

        def _capture_on_worker() -> None:
            with self._preview_lock:
                if seq != self._preview_seq:
                    return
            if not self._cmd_q.empty() or is_user_stopped():
                return
            if sess not in self._tabs.values() or not self._is_session_alive(sess):
                return
            p_path = sess.capture_viewport_preview()
            if p_path:
                notify_menubar_status(
                    "frame",
                    surface="web",
                    target=target,
                    op=op,
                    cursor_x=cx,
                    cursor_y=cy,
                    cursor_label=cl,
                    win_w=sess.last_vw,
                    win_h=sess.last_vh,
                    preview_path=p_path,
                    is_headless=is_headless,
                )

        def _enqueue_if_current() -> None:
            with self._preview_lock:
                if seq != self._preview_seq:
                    return
                self._preview_timer = None
            if self._cmd_q.empty() and not is_user_stopped():
                self._cmd_q.put((_capture_on_worker, None, None))

        with self._preview_lock:
            self._preview_seq += 1
            seq = self._preview_seq
            if self._preview_timer is not None:
                self._preview_timer.cancel()
            tmr = threading.Timer(delay_s, _enqueue_if_current)
            tmr.daemon = True
            self._preview_timer = tmr
            tmr.start()

    @property
    def _ext_bridge(self) -> ExtensionWebSocketBridge:
        with self._bridge_lock:
            if self._ext_bridge_inst is None:
                self._ext_bridge_inst = ExtensionWebSocketBridge(on_event=self._on_extension_event)
            else:
                self._ext_bridge_inst.on_event = self._on_extension_event
                self._ext_bridge_inst.ensure_preferred_port()
            return self._ext_bridge_inst

    def _on_extension_event(self, msg: dict[str, Any]) -> None:
        mtype = msg.get("type")
        if mtype == "tabDiagnostic":
            chrome_tid = msg.get("tabId")
            entry = msg.get("entry")
            if chrome_tid is None or not isinstance(entry, dict):
                return
            for s in list(self._tabs.values()):
                if s.is_extension and getattr(s._raw_page, "chrome_tab_id", None) == chrome_tid:
                    s.record_diagnostic(
                        str(entry.get("kind") or "console.error"),
                        str(entry.get("message") or ""),
                        url=str(entry.get("url") or ""),
                        status=int(entry["status"]) if entry.get("status") is not None else None,
                    )
                    break
            return
        if mtype != "screencastFrame":
            return
        b64_data = msg.get("data")
        if not isinstance(b64_data, str) or len(b64_data) < 64:
            return
        p_path = _write_web_preview_b64(b64_data)
        if not p_path:
            return
        chrome_tid = msg.get("tabId")
        matched_sess: BrowserTabSession | None = None
        for s in list(self._tabs.values()):
            if s.is_extension and getattr(s._raw_page, "chrome_tab_id", None) == chrome_tid:
                matched_sess = s
                break
        vw = int(msg.get("vw") or (matched_sess.last_vw if matched_sess else 1280) or 1280)
        vh = int(msg.get("vh") or (matched_sess.last_vh if matched_sess else 800) or 800)
        if matched_sess is not None:
            matched_sess.last_preview_path = p_path
            if vw > 80:
                matched_sess.last_vw = vw
            if vh > 60:
                matched_sess.last_vh = vh
            target_label = _short_menubar_target(matched_sess.last_url)
        else:
            target_label = "Chrome"
        notify_menubar_status(
            "frame",
            surface="web",
            target=target_label,
            preview_path=p_path,
            win_w=vw,
            win_h=vh,
            is_headless=False,
        )

    def _apply_inline_preview(
        self,
        sess: BrowserTabSession,
        preview_dict: Any,
        url_hint: str | None = None,
        push_frame: bool = True,
    ) -> str | None:
        if not isinstance(preview_dict, dict):
            return sess.last_preview_path
        b64_data = preview_dict.get("jpeg") or preview_dict.get("data")
        p_path = _write_web_preview_b64(b64_data if isinstance(b64_data, str) else None)
        if not p_path:
            return sess.last_preview_path
        sess.last_preview_path = p_path
        vw = int(preview_dict.get("vw") or sess.last_vw or 1280)
        vh = int(preview_dict.get("vh") or sess.last_vh or 800)
        if vw > 80:
            sess.last_vw = vw
        if vh > 60:
            sess.last_vh = vh
        if push_frame:
            notify_menubar_status(
                "frame",
                surface="web",
                target=_short_menubar_target(url_hint or sess.last_url),
                preview_path=p_path,
                win_w=vw,
                win_h=vh,
                is_headless=False,
            )
        return p_path

    def ensure_bridge(self) -> ExtensionWebSocketBridge:
        return self._ext_bridge

    @property
    def bridge_port(self) -> int:
        return self._ext_bridge.port

    @property
    def extension_connected(self) -> bool:
        return self._ext_bridge.is_connected()

    def get_bridge_status(self) -> dict[str, Any]:
        return self._ext_bridge.get_status()

    @staticmethod
    def _macros_dir() -> Path:
        custom = os.environ.get("FMU_MACROS_DIR", "").strip()
        base = Path(custom).expanduser() if custom else (Path.home() / ".fmu" / "macros")
        base.mkdir(parents=True, exist_ok=True)
        return base

    @staticmethod
    def _domain_from_url(url: str | None) -> str:
        if not url or url in ("*", "global", "about:blank"):
            return "global"
        raw = str(url).strip()
        if "://" not in raw:
            raw = "http://" + raw
        try:
            parsed = urlparse(raw)
            host = (parsed.hostname or "global").lower()
            return re.sub(r"[^a-z0-9._-]", "_", host) or "global"
        except Exception:
            return "global"

    @staticmethod
    def _is_external_url(url: str | None) -> bool:
        if not url:
            return False
        raw = str(url).strip()
        if raw in ("about:blank", "*"):
            return False
        if not _HAS_SCHEME_RE.match(raw) and "." in raw and " " not in raw:
            raw = _normalize_nav_url(raw)
        if not raw.startswith(("http://", "https://")):
            return False
        try:
            host = (urlparse(raw).hostname or "").lower()
        except Exception:
            return False
        if not host or host in ("localhost", "127.0.0.1", "::1", "example.com", "www.example.com"):
            return False
        return True

    def save_macro(
        self,
        name: str,
        steps: list[dict[str, Any]] | None = None,
        domain: str | None = None,
        description: str = "",
        tab_id: str | None = None,
    ) -> dict[str, Any]:
        return self._run_sync(lambda: self._save_macro_inner(name, steps, domain, description, tab_id))

    def _save_macro_inner(
        self,
        name: str,
        steps: list[dict[str, Any]] | None = None,
        domain: str | None = None,
        description: str = "",
        tab_id: str | None = None,
    ) -> dict[str, Any]:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("Macro name is required")
        target_tid = tab_id or "t1"
        if steps is None:
            recorded = self._last_tab_steps.get(target_tid) or []
            if not recorded and self._last_tab_steps:
                recorded = next(iter(self._last_tab_steps.values()), [])
            steps = list(recorded)
        if not steps:
            raise ValueError(f"No steps provided or recorded for macro '{clean_name}'")

        if not domain:
            ref_url = self._tab_last_urls.get(target_tid)
            if not ref_url and self._tab_last_urls:
                ref_url = next(iter(self._tab_last_urls.values()), "global")
            domain_key = self._domain_from_url(ref_url)
        else:
            domain_key = self._domain_from_url(domain)

        macro_file = self._macros_dir() / f"{domain_key}.json"
        existing: dict[str, Any] = {}
        if macro_file.is_file():
            try:
                existing = json.loads(macro_file.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        entry = {
            "name": clean_name,
            "domain": domain_key,
            "description": str(description or f"Auto-synthesized Virtual WebMCP macro ({len(steps)} steps)"),
            "steps": list(steps),
            "updatedAt": round(time.time(), 3),
        }
        existing[clean_name] = entry
        macro_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")

        for sess in list(self._tabs.values()):
            if self._is_session_alive(sess):
                sess_dom = self._domain_from_url(sess.last_url)
                if domain_key in ("global", sess_dom):
                    try:
                        sess._raw_page.evaluate(
                            "tools => window.__fmu && window.__fmu.registerVirtualTools && window.__fmu.registerVirtualTools(tools)",
                            [entry],
                        )
                    except _BROWSER_OPS_ERRORS:
                        pass
        return entry

    def get_macros_for_url(self, url: str | None) -> list[dict[str, Any]]:
        domain_key = self._domain_from_url(url)
        macros_by_name: dict[str, dict[str, Any]] = {}
        mdir = self._macros_dir()
        for key in ("global", domain_key):
            f = mdir / f"{key}.json"
            if f.is_file():
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        for mname, mval in data.items():
                            if isinstance(mval, dict) and mval.get("steps"):
                                macros_by_name[mname] = mval
                except (OSError, json.JSONDecodeError, ValueError):
                    pass
        return list(macros_by_name.values())

    def list_macros(self, domain: str | None = None) -> list[dict[str, Any]]:
        mdir = self._macros_dir()
        results: list[dict[str, Any]] = []
        files = [mdir / f"{self._domain_from_url(domain)}.json"] if domain else sorted(mdir.glob("*.json"))
        for f in files:
            if f.is_file():
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        for mval in data.values():
                            if isinstance(mval, dict) and mval.get("name"):
                                results.append(mval)
                except (OSError, json.JSONDecodeError, ValueError):
                    pass
        return results

    def delete_macro(self, name: str, domain: str | None = None) -> bool:
        mdir = self._macros_dir()
        deleted = False
        files = [mdir / f"{self._domain_from_url(domain)}.json"] if domain else sorted(mdir.glob("*.json"))
        for f in files:
            if f.is_file():
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and name in data:
                        data.pop(name, None)
                        f.write_text(json.dumps(data, indent=2), encoding="utf-8")
                        deleted = True
                except (OSError, json.JSONDecodeError, ValueError):
                    pass
        return deleted

    def _sync_canvas_ocr(
        self,
        sess: BrowserTabSession,
        canvases: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if canvases is None:
            try:
                sess.ensure_runtime(force=sess.is_extension)
                canvases = sess._raw_page.evaluate(
                    "() => (window.__fmu ? (window.__fmu.collectSnapshot('', 180, true).canvases || []) : [])"
                )
            except Exception:
                canvases = []
        if not canvases or not isinstance(canvases, list):
            return []

        from .mac_engine import MacNativeEngine

        mac_eng = MacNativeEngine()
        ocr_items: list[dict[str, Any]] = []
        idx = 1
        for c in canvases:
            data_url = str(c.get("dataUrl") or "")
            png_bytes = b""
            if data_url.startswith("data:image/png;base64,"):
                try:
                    png_bytes = base64.b64decode(data_url.split(",", 1)[1])
                except Exception:
                    png_bytes = b""
            if not png_bytes:
                continue
            safe_tid = re.sub(r"[^a-zA-Z0-9_-]", "_", sess.tab_id)
            safe_cuid = re.sub(r"[^a-zA-Z0-9_-]", "_", str(c.get("uid") or "c1"))
            tmp_png = Path(f"/tmp/fmu-canvas-{safe_tid}-{safe_cuid}.png")
            try:
                tmp_png.write_bytes(png_bytes)
                raw_ocr = mac_eng.execute(
                    {
                        "imagePath": str(tmp_png),
                        "clipX": float(c.get("x", 0)),
                        "clipY": float(c.get("y", 0)),
                        "clipW": float(c.get("w", 0)),
                        "clipH": float(c.get("h", 0)),
                    }
                )
                for line in raw_ocr.splitlines():
                    m = _OCR_LINE_RE.match(line.strip())
                    if m:
                        _, lbl, x_s, y_s, w_s, h_s = m.groups()
                        ocr_items.append(
                            {
                                "uid": f"@ocr{idx}",
                                "label": lbl,
                                "x": int(x_s),
                                "y": int(y_s),
                                "w": int(w_s or 0),
                                "h": int(h_s or 0),
                                "canvasUid": str(c.get("uid") or ""),
                            }
                        )
                        idx += 1
            except _BROWSER_OPS_ERRORS:
                pass
        try:
            sess._raw_page.evaluate(
                "items => window.__fmu && window.__fmu.registerOcrTargets && window.__fmu.registerOcrTargets(items)",
                ocr_items,
            )
            sess._ocr_populated = True
            sess._last_ocr_items = ocr_items
        except _BROWSER_OPS_ERRORS:
            pass
        return ocr_items

    def _worker_loop(self) -> None:
        self._worker_tid = threading.get_ident()
        while True:
            item = self._cmd_q.get()
            if item is None:
                break
            fn, ev, box = item
            if ev is None or box is None:
                if not self._cmd_q.empty():
                    continue
                try:
                    fn()
                except _BROWSER_OPS_ERRORS:
                    pass
                continue
            try:
                box.append(fn())
                box.append(None)
            except Exception as exc:
                box.append(None)
                box.append(exc)
            finally:
                ev.set()

    def _run_sync(self, fn: Callable[[], Any]) -> Any:
        if threading.get_ident() == self._worker_tid:
            return fn()
        caller_ep = getattr(_telemetry_ctx, "entrypoint", None)
        caller_bid = getattr(_telemetry_ctx, "batch_id", None)
        if caller_ep or caller_bid:
            orig_fn = fn

            def _wrapped_fn() -> Any:
                prev_c = set_telemetry_context(entrypoint=caller_ep, batch_id=caller_bid)
                try:
                    return orig_fn()
                finally:
                    reset_telemetry_context(prev_c)

            fn = _wrapped_fn
        ev = threading.Event()
        box: list[Any] = []
        self._cmd_q.put((fn, ev, box))
        ev.wait()
        if box[1] is not None:
            raise box[1]
        return box[0]

    @property
    def _browser(self) -> Any:
        if self._raw_browser is None:
            return None
        return _ThreadProxy(self, self._raw_browser)

    @_browser.setter
    def _browser(self, val: Any) -> None:
        self._raw_browser = val

    def group_title(self, agent_id: str) -> str:
        client, sub = self.clients.lookup(agent_id)
        if client is None:
            return "Agent" if agent_id == "default" else f"Agent {agent_id}"
        return f"{client.label} · {sub}" if sub else client.label

    def owner_for(self, scoped_id: str | None) -> str:
        client, _ = self.clients.lookup(scoped_id)
        return client.owner if client is not None else ""

    def in_scope(self, tab_id: str, scope: str | None) -> bool:
        """True when `tab_id` belongs to the caller's session scope (unscoped callers see every tab)."""
        if not scope:
            return True
        tab_scope = tab_id.split(":", 1)[0] if ":" in tab_id else ""
        return tab_scope == scope or tab_scope.startswith(f"{scope}.")

    def _get_agent_color(self, agent_id: str) -> str:
        client, _ = self.clients.lookup(agent_id)
        if client is not None:
            return client.color
        if agent_id not in self._agent_colors:
            self._agent_colors[agent_id] = _GROUP_COLORS[len(self._agent_colors) % len(_GROUP_COLORS)]
        return self._agent_colors[agent_id]

    def _is_browser_alive(self) -> bool:
        try:
            return self._raw_browser is not None and bool(self._raw_browser.is_connected())
        except Exception:
            return False

    def _is_session_alive(self, sess: BrowserTabSession) -> bool:
        try:
            if sess.is_extension:
                return self._ext_bridge.is_connected() and not bool(sess._raw_page.is_closed())
            return self._is_browser_alive() and not bool(sess._raw_page.is_closed())
        except Exception:
            return False

    def _evict_dead_browser(self) -> None:
        self._tabs = {k: v for k, v in self._tabs.items() if v.is_extension and self._is_session_alive(v)}
        self._context = None
        try:
            if self._raw_browser:
                self._raw_browser.close()
        except _BROWSER_OPS_ERRORS:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except _BROWSER_OPS_ERRORS:
            pass
        self._raw_browser = None
        self._pw = None
        self._is_cdp_chrome = False

    def _find_chrome_devtools_ws(self) -> str | None:
        # Never read ~/Library/Application Support/Google/Chrome/DevToolsActivePort:
        # Chrome 136+/144+ pops up a focus-stealing modal ("Allow remote debugging?")
        # on every new incoming WebSocket connection to the default user profile.
        cdp_url = os.environ.get("FMU_CDP_URL", "").strip()
        return cdp_url if cdp_url else None

    def _ensure_browser(self, mode: str = "chrome") -> Any:
        return self._run_sync(lambda: self._ensure_browser_inner(mode))

    def _ensure_browser_inner(self, mode: str = "chrome") -> Any:
        if self._raw_browser is not None:
            if self._is_browser_alive():
                return self._raw_browser
            self._evict_dead_browser()

        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        if mode == "chrome":
            ws_url = self._find_chrome_devtools_ws()
            if ws_url:
                try:
                    self._raw_browser = self._pw.chromium.connect_over_cdp(ws_url, timeout=1500)
                    self._context = (
                        self._raw_browser.contexts[0]
                        if self._raw_browser.contexts
                        else self._raw_browser.new_context()
                    )
                    self._is_cdp_chrome = True
                    return self._raw_browser
                except _BROWSER_OPS_ERRORS:
                    pass
        headless_shells = sorted(
            (Path.home() / "Library" / "Caches" / "ms-playwright").glob(
                "chromium_headless_shell-*/chrome-headless-shell-mac-*/chrome-headless-shell"
            )
        )
        chrome_bin = (
            str(headless_shells[-1])
            if headless_shells
            else "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        )
        launch_kwargs: dict[str, Any] = {
            "headless": True,
            "args": [
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--silent-debugger-extension-api",
            ],
        }
        if Path(chrome_bin).is_file():
            launch_kwargs["executable_path"] = chrome_bin
        self._raw_browser = self._pw.chromium.launch(**launch_kwargs)
        self._context = self._raw_browser.new_context(viewport={"width": 1280, "height": 800})
        self._is_cdp_chrome = False
        return self._raw_browser

    def _try_assign_chrome_tab_group(self, page_url: str, session_name: str, color: str = "blue") -> None:
        if not self._is_cdp_chrome or not self._raw_browser:
            return
        try:
            b_cdp = self._raw_browser.new_browser_cdp_session()
            targets = b_cdp.send("Target.getTargets").get("targetInfos", [])
            sw = next(
                (
                    t
                    for t in targets
                    if t.get("type") == "service_worker"
                    and str(t.get("url", "")).startswith("chrome-extension://")
                ),
                None,
            )
            if sw:
                attached = b_cdp.send(
                    "Target.attachToTarget", {"targetId": sw["targetId"], "flatten": True}
                )
                sid = attached.get("sessionId")
                if sid:
                    expr = (
                        "(async () => {"
                        "  if (!chrome?.tabGroups || !chrome?.tabs) return false;"
                        f"  const tabs = await chrome.tabs.query({{url: {json.dumps(page_url)}}});"
                        "  if (!tabs.length) return false;"
                        "  const tid = tabs[tabs.length - 1].id;"
                        f"  const gs = await chrome.tabGroups.query({{title: {json.dumps(session_name)}}});"
                        "  if (gs.length) { await chrome.tabs.group({tabIds: [tid], groupId: gs[0].id}); }"
                        "  else {"
                        "    const gid = await chrome.tabs.group({tabIds: [tid]});"
                        f"    await chrome.tabGroups.update(gid, {{title: {json.dumps(session_name)}, color: {json.dumps(color)}, collapsed: false}});"
                        "  }"
                        "  return true;"
                        "})()"
                    )
                    b_cdp.send(
                        "Runtime.evaluate",
                        {"expression": expr, "awaitPromise": True},
                    )
            b_cdp.detach()
        except _BROWSER_OPS_ERRORS:
            pass

    def _can_run_direct_mv3(self, *, mode: str = "chrome", tab_id: str | None = None) -> bool:
        if mode == "headless":
            return False
        if tab_id:
            with self._state_lock:
                existing = self._tabs.get(tab_id)
            if existing is not None:
                return bool(existing.is_extension)
        if os.environ.get("FMU_USE_EXTENSION", "1") == "0":
            return False
        if self._ext_bridge_inst is not None and self._ext_bridge.is_connected() and not self._find_chrome_devtools_ws():
            return True
        return False

    def create_tab(
        self,
        url: str = "about:blank",
        mode: str = "chrome",
        session_name: str = "Agent",
        tab_id: str | None = None,
        group_color: str = "blue",
        window: str | None = None,
        window_id: int | None = None,
        chrome_tab_id: int | None = None,
        reuse_existing: bool = False,
        require_extension: bool = False,
    ) -> BrowserTabSession:
        fn = lambda: self._create_tab_inner(
            url,
            mode,
            session_name,
            tab_id,
            group_color,
            window=window,
            window_id=window_id,
            chrome_tab_id=chrome_tab_id,
            reuse_existing=reuse_existing,
            require_extension=require_extension,
        )
        if self._can_run_direct_mv3(mode=mode, tab_id=tab_id):
            return fn()
        return self._run_sync(fn)

    def _extension_diagnostic_error(self) -> str:
        ext_dir = Path(__file__).resolve().parents[2] / "extension"
        port_note = ""
        if self._ext_bridge.port != self._ext_bridge.preferred_port:
            reason = self._ext_bridge.port_fallback_reason or "port in use"
            port_note = (
                f" (port {self._ext_bridge.preferred_port} already held by warm daemon: {reason}; "
                f"this instance fell back to port {self._ext_bridge.port} — route calls through the warm daemon socket)"
            )
        return (
            f"MV3 extension not connected on ws://127.0.0.1:{self._ext_bridge.preferred_port}/ext{port_note}; "
            f"load {ext_dir} in Chrome or pass mode='headless'"
        )

    def _should_use_extension_bridge(
        self,
        mode: str,
        require_extension: bool = False,
        url: str | None = None,
        window: str | None = None,
        window_id: int | None = None,
        chrome_tab_id: int | None = None,
        reuse_existing: bool = False,
        reattach: bool = False,
    ) -> bool:
        explicit_ext = (
            mode in ("extension", "chrome-extension", "live")
            or require_extension
            or chrome_tab_id is not None
        )
        if mode == "headless" and not explicit_ext:
            return False
        use_ext_env = os.environ.get("FMU_USE_EXTENSION", "1") != "0"
        must_use_ext = explicit_ext or (
            mode == "chrome"
            and use_ext_env
            and not self._find_chrome_devtools_ws()
            and (
                self._is_external_url(url)
                or window is not None
                or window_id is not None
                or reuse_existing
            )
        )
        if must_use_ext:
            if not self._ext_bridge.is_connected():
                cold_preferred = (
                    self._ext_bridge.port == self._ext_bridge.preferred_port
                    and (time.monotonic() - self._init_mono) < 5.0
                )
                self._ext_bridge.wait_connected(timeout=1.8 if cold_preferred else 0.35)
            if not self._ext_bridge.is_connected():
                raise RuntimeError(self._extension_diagnostic_error())
            return True
        if (
            reattach
            and mode == "chrome"
            and use_ext_env
            and self._ext_bridge_inst is not None
            and not self._ext_bridge.is_connected()
            and (time.monotonic() - self._init_mono) < 5.0
        ):
            self._ext_bridge.wait_connected(timeout=0.45)
        if not use_ext_env or self._ext_bridge_inst is None or not self._ext_bridge.is_connected():
            return False
        return True

    def _create_tab_inner(
        self,
        url: str = "about:blank",
        mode: str = "chrome",
        session_name: str = "Agent",
        tab_id: str | None = None,
        group_color: str = "blue",
        window: str | None = None,
        window_id: int | None = None,
        chrome_tab_id: int | None = None,
        reuse_existing: bool = False,
        require_extension: bool = False,
        reattach: bool = False,
        schedule_preview: bool = True,
    ) -> BrowserTabSession:
        self._cancel_pending_preview()
        with self._state_lock:
            if tab_id:
                tid = tab_id
            else:
                while f"t{self._next_tab_num}" in self._tabs:
                    self._next_tab_num += 1
                tid = f"t{self._next_tab_num}"
                self._next_tab_num += 1
            effective_url = url or self._tab_last_urls.get(tid) or "about:blank"
        must_use_ext = (
            mode in ("extension", "chrome-extension", "live")
            or require_extension
            or chrome_tab_id is not None
            or (
                mode == "chrome"
                and os.environ.get("FMU_USE_EXTENSION", "1") != "0"
                and not self._find_chrome_devtools_ws()
                and (
                    self._is_external_url(effective_url)
                    or window is not None
                    or window_id is not None
                    or reuse_existing
                )
            )
        )
        if tid in self._tabs:
            sess = self._tabs[tid]
            if (
                self._is_session_alive(sess)
                and (not must_use_ext or sess.is_extension)
                and not (mode == "headless" and sess.is_extension)
            ):
                if (
                    url
                    and url != "about:blank"
                    and sess._raw_page.url != url
                    and not (reuse_existing and _url_matches_target(sess._raw_page.url, url))
                ):
                    norm_u = _normalize_nav_url(url)
                    if sess._raw_page.url != norm_u:
                        sess._raw_page.goto(norm_u, wait_until="domcontentloaded", timeout=15000)
                        sess.last_url = str(getattr(sess._raw_page, "url", "") or norm_u)
                        self._tab_last_urls[tid] = sess.last_url
                        sess._focus_emulated = False
                        sess._runtime_ensured = False
                        sess._ocr_checked = False
                        sess._ocr_populated = False
                        sess.enable_background_focus_emulation()
                        sess.ensure_runtime()
                return sess
            self._tabs.pop(tid, None)

        if self._should_use_extension_bridge(
            mode,
            require_extension=require_extension,
            url=effective_url,
            window=window,
            window_id=window_id,
            chrome_tab_id=chrome_tab_id,
            reuse_existing=reuse_existing,
            reattach=reattach,
        ):
            try:
                rpc_params: dict[str, Any] = {
                    "url": effective_url,
                    "sessionId": tid,
                    "sessionName": session_name,
                    "color": group_color,
                    "owner": self.owner_for(tid),
                    "skipPreview": not _preview_enabled(),
                }
                if window:
                    rpc_params["window"] = str(window)
                if window_id is not None:
                    rpc_params["windowId"] = int(window_id)
                if chrome_tab_id is not None:
                    rpc_params["chromeTabId"] = int(chrome_tab_id)
                if reuse_existing:
                    rpc_params["reuseExisting"] = True
                if reattach:
                    rpc_params["reattach"] = True
                ext_res = self._ext_bridge.call_rpc("createBrowserTab", rpc_params, timeout=15.0)
                ext_chrome_tid = int(ext_res.get("tabId", chrome_tab_id or 1))
                chrome_win_id = ext_res.get("windowId")
                actual_url = str(ext_res.get("url") or effective_url)
                actual_title = str(ext_res.get("title") or "")
                self._next_tab_num += 1
                sess_holder: list[BrowserTabSession] = []

                def _handle_ext_preview(prev_obj: dict[str, Any], u_hint: str) -> None:
                    if sess_holder:
                        self._apply_inline_preview(sess_holder[0], prev_obj, url_hint=u_hint, push_frame=True)

                ext_page = _ExtensionPageAdapter(
                    self._ext_bridge,
                    ext_chrome_tid,
                    initial_url=actual_url,
                    initial_title=actual_title,
                    window_id=int(chrome_win_id) if chrome_win_id is not None else None,
                    on_preview=_handle_ext_preview,
                )
                ext_cdp = _ExtensionCdpAdapter(self._ext_bridge, ext_chrome_tid)
                sess = BrowserTabSession(
                    engine=self,
                    tab_id=tid,
                    page=ext_page,
                    cdp_session=ext_cdp,
                    group_name=session_name,
                    group_color=group_color,
                    last_url=actual_url,
                    last_title=actual_title,
                    is_extension=True,
                )
                sess_holder.append(sess)
                with self._state_lock:
                    if tab_id:
                        self._next_tab_num += 1
                    self._tabs[tid] = sess
                    self._tab_last_urls[tid] = actual_url
                    if actual_title:
                        self._tab_last_titles[tid] = actual_title
                if isinstance(ext_res.get("preview"), dict):
                    self._apply_inline_preview(sess, ext_res["preview"], url_hint=actual_url, push_frame=True)
                sess.ensure_runtime()
                return sess
            except Exception as exc:
                if USER_STOPPED_ERROR in str(exc) or is_user_stopped() or must_use_ext:
                    raise

        def _create_pw_session() -> BrowserTabSession:
            self._ensure_browser_inner(mode=mode)
            if tab_id:
                with self._state_lock:
                    self._next_tab_num += 1
            page = self._context.new_page()
            cdp = self._context.new_cdp_session(page)
            norm_eff_url = _normalize_nav_url(effective_url) if effective_url and effective_url != "about:blank" else "about:blank"
            pw_sess = BrowserTabSession(
                engine=self,
                tab_id=tid,
                page=page,
                cdp_session=cdp,
                group_name=session_name,
                group_color=group_color,
                last_url=norm_eff_url,
                is_extension=False,
            )
            pw_sess.enable_background_focus_emulation()
            pw_sess.start_playwright_screencast()
            with self._state_lock:
                self._tabs[tid] = pw_sess

            if norm_eff_url and norm_eff_url != "about:blank":
                page.goto(norm_eff_url, wait_until="domcontentloaded", timeout=15000)
                pw_sess.last_url = page.url or norm_eff_url
                with self._state_lock:
                    self._tab_last_urls[tid] = pw_sess.last_url
                self._try_assign_chrome_tab_group(page.url, session_name, color=group_color)
                if schedule_preview:
                    self._schedule_async_preview(
                        pw_sess,
                        target=_short_menubar_target(page.url),
                        op="goto",
                        cur_info=None,
                        is_headless=(not self._is_cdp_chrome),
                    )
            pw_sess.ensure_runtime()
            return pw_sess

        if threading.get_ident() != self._worker_tid:
            return self._run_sync(_create_pw_session)
        return _create_pw_session()

    def attach_existing_tab(
        self,
        *,
        tab_id: str | None = None,
        chrome_tab_id: int | None = None,
        url_pattern: str | None = None,
        window: str | None = None,
        window_id: int | None = None,
        session_name: str | None = None,
        group_color: str = "blue",
        scope: str | None = None,
    ) -> BrowserTabSession | None:
        fn = lambda: self._attach_existing_tab_inner(
            tab_id=tab_id,
            chrome_tab_id=chrome_tab_id,
            url_pattern=url_pattern,
            window=window,
            window_id=window_id,
            session_name=session_name,
            group_color=group_color,
            scope=scope,
        )
        if self._can_run_direct_mv3(tab_id=tab_id):
            return fn()
        return self._run_sync(fn)

    def _attach_existing_tab_inner(
        self,
        *,
        tab_id: str | None = None,
        chrome_tab_id: int | None = None,
        url_pattern: str | None = None,
        window: str | None = None,
        window_id: int | None = None,
        session_name: str | None = None,
        group_color: str = "blue",
        scope: str | None = None,
    ) -> BrowserTabSession | None:
        if chrome_tab_id is not None:
            for existing in list(self._tabs.values()):
                if (
                    existing.is_extension
                    and self._is_session_alive(existing)
                    and getattr(existing._raw_page, "chrome_tab_id", None) == int(chrome_tab_id)
                ):
                    return existing
        if os.environ.get("FMU_USE_EXTENSION", "1") == "0":
            return None
        if not self._ext_bridge.is_connected():
            if chrome_tab_id is not None or (
                self._ext_bridge_inst is not None and (time.monotonic() - self._init_mono) < 3.0
            ):
                self._ext_bridge.wait_connected(timeout=0.25)
        if not self._ext_bridge.is_connected():
            if chrome_tab_id is not None:
                raise RuntimeError(self._extension_diagnostic_error())
            return None

        rpc_params: dict[str, Any] = {
            "allowMissing": True,
            "skipPreview": not _preview_enabled(),
        }
        if chrome_tab_id is not None:
            rpc_params["chromeTabId"] = int(chrome_tab_id)
        if url_pattern:
            rpc_params["urlPattern"] = str(url_pattern)
        if window:
            rpc_params["window"] = str(window)
        if window_id is not None:
            rpc_params["windowId"] = int(window_id)
        if session_name:
            rpc_params["sessionName"] = str(session_name)
        owner = self.owner_for(scope or tab_id)
        if owner:
            rpc_params["owner"] = owner

        try:
            ext_res = self._ext_bridge.call_rpc("getTab", rpc_params, timeout=4.0)
        except Exception:
            if chrome_tab_id is not None:
                raise
            return None

        if not isinstance(ext_res, dict) or not ext_res.get("found") or not ext_res.get("tabId"):
            return None

        matched_chrome_tid = int(ext_res["tabId"])
        actual_url = str(ext_res.get("url") or "about:blank")
        actual_title = str(ext_res.get("title") or "")
        chrome_win_id = ext_res.get("windowId")

        for existing in list(self._tabs.values()):
            if (
                existing.is_extension
                and self._is_session_alive(existing)
                and getattr(existing._raw_page, "chrome_tab_id", None) == matched_chrome_tid
            ):
                if actual_url:
                    existing.last_url = actual_url
                    existing._raw_page.url = actual_url
                    self._tab_last_urls[existing.tab_id] = actual_url
                if actual_title:
                    existing.last_title = actual_title
                    existing._raw_page.title = actual_title
                    self._tab_last_titles[existing.tab_id] = actual_title
                return existing

        prefix = f"{scope}:" if scope and self.clients.lookup(scope)[0] is not None else ""
        with self._state_lock:
            if tab_id:
                tid = tab_id
            else:
                while f"{prefix}t{self._next_tab_num}" in self._tabs:
                    self._next_tab_num += 1
                tid = f"{prefix}t{self._next_tab_num}"
            self._next_tab_num += 1
        eff_group = str(ext_res.get("groupTitle") or session_name or self.group_title(scope or "default"))
        sess_holder: list[BrowserTabSession] = []

        def _handle_ext_preview(prev_obj: dict[str, Any], u_hint: str) -> None:
            if sess_holder:
                self._apply_inline_preview(sess_holder[0], prev_obj, url_hint=u_hint, push_frame=True)

        ext_page = _ExtensionPageAdapter(
            self._ext_bridge,
            matched_chrome_tid,
            initial_url=actual_url,
            initial_title=actual_title,
            window_id=int(chrome_win_id) if chrome_win_id is not None else None,
            on_preview=_handle_ext_preview,
        )
        ext_cdp = _ExtensionCdpAdapter(self._ext_bridge, matched_chrome_tid)
        sess = BrowserTabSession(
            engine=self,
            tab_id=tid,
            page=ext_page,
            cdp_session=ext_cdp,
            group_name=eff_group,
            group_color=group_color,
            last_url=actual_url,
            last_title=actual_title,
            is_extension=True,
        )
        sess_holder.append(sess)
        with self._state_lock:
            self._tabs[tid] = sess
            self._tab_last_urls[tid] = actual_url
            if actual_title:
                self._tab_last_titles[tid] = actual_title
        if isinstance(ext_res.get("preview"), dict):
            self._apply_inline_preview(sess, ext_res["preview"], url_hint=actual_url, push_frame=True)
        return sess

    def list_tabs(
        self,
        query: str = "",
        *,
        window: str | None = None,
        window_id: int | None = None,
        session_name: str | None = None,
        include_internal: bool = False,
        mode: str = "chrome",
    ) -> str:
        return self._run_sync(
            lambda: self._list_tabs_inner(
                query=query,
                window=window,
                window_id=window_id,
                session_name=session_name,
                include_internal=include_internal,
                mode=mode,
            )
        )

    def _list_tabs_inner(
        self,
        *,
        query: str = "",
        window: str | None = None,
        window_id: int | None = None,
        session_name: str | None = None,
        include_internal: bool = False,
        mode: str = "chrome",
    ) -> str:
        q_low = str(query or "").strip().lower()
        chrome_tid_to_sess: dict[int, str] = {}
        for tid_k, s_v in list(self._tabs.items()):
            if s_v.is_extension and self._is_session_alive(s_v):
                ctid = getattr(s_v._raw_page, "chrome_tab_id", None)
                if ctid is not None:
                    chrome_tid_to_sess[int(ctid)] = tid_k

        lines: list[str] = []
        ext_used = False
        if mode != "headless" and os.environ.get("FMU_USE_EXTENSION", "1") != "0":
            if not self._ext_bridge.is_connected() and self._ext_bridge_inst is not None and (time.monotonic() - self._init_mono) < 3.0:
                self._ext_bridge.wait_connected(timeout=0.2)
            if self._ext_bridge.is_connected():
                rpc_p: dict[str, Any] = {"includeInternal": bool(include_internal)}
                if query:
                    rpc_p["query"] = str(query)
                if window:
                    rpc_p["window"] = str(window)
                if window_id is not None:
                    rpc_p["windowId"] = int(window_id)
                if session_name:
                    rpc_p["sessionName"] = str(session_name)
                try:
                    ext_res = self._ext_bridge.call_rpc("listTabs", rpc_p, timeout=4.0, check_stopped=False)
                    if isinstance(ext_res, dict) and isinstance(ext_res.get("tabs"), list):
                        ext_used = True
                        for t in ext_res["tabs"]:
                            if not isinstance(t, dict) or t.get("tabId") is None:
                                continue
                            ctid_i = int(t["tabId"])
                            parts = [f"@tab id={ctid_i}"]
                            if ctid_i in chrome_tid_to_sess:
                                parts.append(f"session={chrome_tid_to_sess[ctid_i]}")
                            if t.get("windowId") is not None:
                                parts.append(f"window={t['windowId']}")
                            g_title = str(t.get("groupTitle") or "")
                            if g_title:
                                parts.append(f"group={json.dumps(g_title, ensure_ascii=False)}")
                            if t.get("active"):
                                parts.append("[active]")
                            t_title = str(t.get("title") or "").replace("\n", " ").strip()
                            if len(t_title) > 90:
                                t_title = t_title[:87] + "..."
                            parts.append(json.dumps(t_title, ensure_ascii=False))
                            parts.append(str(t.get("url") or ""))
                            lines.append(" ".join(parts))
                except _BROWSER_OPS_ERRORS:
                    pass

        for tid_k, s_v in list(self._tabs.items()):
            if s_v.is_extension or not self._is_session_alive(s_v):
                continue
            u_str = str(getattr(s_v._raw_page, "url", "") or s_v.last_url or "")
            t_str = str(s_v.last_title or "")
            if q_low and q_low not in u_str.lower() and q_low not in t_str.lower() and q_low not in tid_k.lower():
                continue
            lines.append(
                f'@tab session={tid_k} bridge="playwright" {json.dumps(t_str, ensure_ascii=False)} {u_str}'
            )

        bridge_label = "mv3" if ext_used else ("playwright" if lines else ("mv3" if self._ext_bridge.is_connected() else "none"))
        header = f'# tabs count={len(lines)} bridge="{bridge_label}"'
        return "\n".join([header] + lines)

    def _require_ext_bridge(self) -> ExtensionWebSocketBridge:
        if os.environ.get("FMU_USE_EXTENSION", "1") == "0":
            raise RuntimeError("MV3 extension bridge disabled (FMU_USE_EXTENSION=0)")
        bridge = self._ext_bridge
        if not bridge.is_connected():
            cold_preferred = (
                bridge.port == bridge.preferred_port
                and (time.monotonic() - self._init_mono) < 5.0
            )
            bridge.wait_connected(timeout=1.8 if cold_preferred else 0.35)
        if not bridge.is_connected():
            raise RuntimeError(self._extension_diagnostic_error())
        return bridge

    def _list_extensions_raw(
        self,
        query: str = "",
        *,
        enabled: bool | None = None,
        ext_type: str | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        bridge = self._require_ext_bridge()
        ext_res = bridge.call_rpc("listExtensions", {}, timeout=4.0, check_stopped=False)
        raw_items = ext_res.get("extensions") if isinstance(ext_res, dict) else None
        if not isinstance(raw_items, list):
            raise RuntimeError(f"Unexpected listExtensions response from MV3 bridge: {ext_res!r}")
        self_id = str(ext_res.get("selfId") or "")
        if self_id:
            self._ext_self_id = self_id
        q_low = str(query or "").strip().lower()
        type_low = str(ext_type or "").strip().lower()
        items: list[dict[str, Any]] = []
        for ext in raw_items:
            if not isinstance(ext, dict):
                continue
            item = {
                "id": str(ext.get("id") or ""),
                "name": str(ext.get("name") or ""),
                "enabled": bool(ext.get("enabled")),
                "type": str(ext.get("type") or ""),
                "version": str(ext.get("version") or ""),
            }
            if enabled is not None and item["enabled"] is not bool(enabled):
                continue
            if type_low and item["type"].lower() != type_low:
                continue
            if q_low and (
                q_low not in item["id"].lower()
                and q_low not in item["name"].lower()
                and q_low not in item["type"].lower()
                and q_low not in item["version"].lower()
            ):
                continue
            items.append(item)
        items.sort(key=lambda item: (item["name"].lower(), item["id"]))
        return items, self_id

    def list_extensions(
        self,
        query: str = "",
        *,
        enabled: bool | None = None,
        ext_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """List installed Chrome extensions via the MV3 bridge's management permission."""
        items, _self_id = self._list_extensions_raw(query=query, enabled=enabled, ext_type=ext_type)
        return items

    @staticmethod
    def _resolve_extension_target(items: list[dict[str, Any]], needle: str) -> dict[str, Any]:
        low = needle.lower()
        for match_fn in (
            lambda e: e["id"] == needle,
            lambda e: e["id"].lower() == low,
            lambda e: e["name"] == needle,
            lambda e: e["name"].lower() == low,
            lambda e: low in e["name"].lower(),
        ):
            hits = [e for e in items if match_fn(e)]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                choices = ", ".join(
                    f'{json.dumps(e["name"], ensure_ascii=False)} (id={e["id"]})' for e in hits[:8]
                )
                raise RuntimeError(
                    f"Multiple Chrome extensions match {needle!r}: {choices}; pass the exact extension ID instead"
                )
        raise RuntimeError(
            f"No Chrome extension found matching {needle!r}; call cua.listExtensions() to see installed extensions"
        )

    def set_extension_enabled(self, id_or_name: str, enabled: bool) -> dict[str, Any]:
        """Enable or disable a Chrome extension by unique name or ID, refusing to disable the bridge itself."""
        if not isinstance(enabled, bool):
            raise ValueError("setExtensionEnabled requires a boolean 'enabled' argument")
        needle = str(id_or_name or "").strip()
        if not needle:
            raise ValueError("setExtensionEnabled requires a non-empty extension id or name")

        items, self_id = self._list_extensions_raw()
        target = self._resolve_extension_target(items, needle)
        if not enabled and self_id and target["id"] == self_id:
            raise RuntimeError(
                f"Refusing to disable the active Faster Mac Use bridge extension ({target['name'] or self_id}, id={self_id})"
            )

        bridge = self._require_ext_bridge()
        res = bridge.call_rpc(
            "setExtensionEnabled",
            {"id": target["id"], "name": target["name"], "enabled": enabled},
            timeout=5.0,
        )
        if not isinstance(res, dict):
            raise RuntimeError(f"Unexpected setExtensionEnabled response from MV3 bridge: {res!r}")
        return {
            "id": str(res.get("id") or target["id"]),
            "name": str(res.get("name") or target["name"]),
            "enabled": bool(res.get("enabled")) if "enabled" in res else enabled,
            "type": str(res.get("type") or target["type"]),
            "version": str(res.get("version") or target["version"]),
        }

    @staticmethod
    def _format_extensions_text(items: list[dict[str, Any]], *, self_id: str = "") -> str:
        lines = [f'# extensions count={len(items)} bridge="mv3"']
        for ext in items:
            parts = [
                f'@ext id={ext["id"]}',
                f'enabled={"true" if ext["enabled"] else "false"}',
                f'type={ext["type"] or "extension"}',
                f'version={json.dumps(ext["version"], ensure_ascii=False)}',
            ]
            if self_id and ext["id"] == self_id:
                parts.append("[self]")
            parts.append(json.dumps(ext["name"], ensure_ascii=False))
            lines.append(" ".join(parts))
        return "\n".join(lines)

    @staticmethod
    def _format_extension_state_text(ext: dict[str, Any], *, self_id: str = "") -> str:
        parts = [
            f'# extension id={ext["id"]}',
            f'enabled={"true" if ext["enabled"] else "false"}',
            f'type={ext["type"] or "extension"}',
            f'version={json.dumps(ext["version"], ensure_ascii=False)}',
        ]
        if self_id and ext["id"] == self_id:
            parts.append("[self]")
        parts.append(json.dumps(ext["name"], ensure_ascii=False))
        return " ".join(parts)

    def get_tab(self, tab_id: str | None = None) -> BrowserTabSession:
        return self._run_sync(lambda: self._get_tab_inner(tab_id))

    def _get_tab_inner(self, tab_id: str | None = None) -> BrowserTabSession:
        if tab_id and tab_id in self._tabs and self._is_session_alive(self._tabs[tab_id]):
            return self._tabs[tab_id]
        for tid, sess in list(self._tabs.items()):
            if self._is_session_alive(sess):
                return sess
            self._tabs.pop(tid, None)
        return self._create_tab_inner(self._tab_last_urls.get(tab_id or "t1", "about:blank"), tab_id=tab_id)

    def close_tab(self, tab_id: str) -> str:
        return self._run_sync(lambda: self._close_tab_inner(tab_id))

    def _close_tab_inner(self, tab_id: str) -> str:
        self._cancel_pending_preview()
        sess = self._tabs.pop(tab_id, None)
        self._tab_last_urls.pop(tab_id, None)
        self._tab_last_titles.pop(tab_id, None)
        if sess is not None:
            try:
                sess._raw_page.close()
            except _BROWSER_OPS_ERRORS:
                pass
        return f"# web tab={tab_id} closed"

    def close_agent_tabs(self, scope: str | None = None) -> str:
        return self._run_sync(lambda: self._close_agent_tabs_inner(scope))

    def _close_agent_tabs_inner(self, scope: str | None = None) -> str:
        self._cancel_pending_preview()
        closed_chrome_ids: set[int] = set()
        closed_chrome = 0
        if self._ext_bridge_inst is not None:
            if not self._ext_bridge.is_connected():
                self._ext_bridge.wait_connected(timeout=2.0)
            if self._ext_bridge.is_connected():
                client = self.clients.by_short(scope) if scope else None
                params: dict[str, Any] = {}
                if client is not None:
                    params["owner"] = client.owner
                    params["groupTitle"] = client.label
                elif scope:
                    params["owner"] = self.owner_for(scope)
                res = self._ext_bridge.call_rpc("closeAgentTabs", params, timeout=10.0)
                closed_chrome_ids = {int(t) for t in res.get("tabIds") or []}
                closed_chrome = int(res.get("closedCount") or 0)
        closed_pages = 0
        for tid, sess in list(self._tabs.items()):
            if sess.is_extension:
                if getattr(sess._raw_page, "chrome_tab_id", None) not in closed_chrome_ids:
                    continue
                sess._raw_page._closed = True
            else:
                if not self.in_scope(tid, scope):
                    continue
                try:
                    sess._raw_page.close()
                except _BROWSER_OPS_ERRORS:
                    pass
                closed_pages += 1
            self._tabs.pop(tid, None)
            self._tab_last_urls.pop(tid, None)
            self._tab_last_titles.pop(tid, None)
        return f"# web agent tabs closed chrome={closed_chrome} playwright={closed_pages}"

    def retire_client(self, client: Any) -> None:
        """Session ended: close its blank agent tabs, collapse its tab groups, and forget its fmu tab handles."""
        self._run_sync(lambda: self._retire_client_inner(client))

    def _retire_client_inner(self, client: Any) -> None:
        mine = {tid: sess for tid, sess in self._tabs.items() if self.in_scope(tid, client.short_id)}
        titles = sorted({client.label, *(str(sess.group_name) for sess in mine.values() if sess.group_name)})
        if self._ext_bridge_inst is not None and self._ext_bridge.is_connected():
            try:
                self._ext_bridge.call_rpc("retireOwner", {"owner": client.owner, "titles": titles}, timeout=6.0)
            except _BROWSER_OPS_ERRORS:
                pass
        for tid, sess in mine.items():
            if not sess.is_extension:
                try:
                    sess._raw_page.close()
                except _BROWSER_OPS_ERRORS:
                    pass
            self._tabs.pop(tid, None)
            self._tab_last_urls.pop(tid, None)
            self._tab_last_titles.pop(tid, None)

    def agent_sessions(self) -> list[dict[str, Any]]:
        now = time.time()
        out: list[dict[str, Any]] = []
        for client in self.clients.live():
            tabs = [
                sess
                for tid, sess in list(self._tabs.items())
                if self.in_scope(tid, client.short_id) and self._is_session_alive(sess)
            ]
            out.append(
                {
                    "id": client.short_id,
                    "label": client.label,
                    "color": client.color,
                    "harness": client.ident.harness,
                    "tabs": len(tabs),
                    "idleSec": int(now - client.last_active),
                }
            )
        return out

    def _eval_step_via_cdp(self, sess: BrowserTabSession, raw_code: str) -> tuple[bool, Any]:
        candidates = _build_js_eval_candidates(raw_code)

        def _send(expr_body: str) -> tuple[bool, Any]:
            wrapped = (
                "(async () => {\n"
                "  const __fmuRun = async () => {\n"
                f"{expr_body}\n"
                "  };\n"
                "  const __val = await __fmuRun();\n"
                "  if (__val === undefined) return { __fmuEvalBox: true, hasValue: false, raw: null };\n"
                "  if (__val === null || typeof __val === 'string' || typeof __val === 'number' || typeof __val === 'boolean') {\n"
                "    return { __fmuEvalBox: true, hasValue: true, raw: __val };\n"
                "  }\n"
                "  if (typeof __val === 'bigint') {\n"
                "    return { __fmuEvalBox: true, hasValue: true, raw: String(__val) };\n"
                "  }\n"
                "  try {\n"
                "    const __seen = new WeakSet();\n"
                "    const __s = JSON.stringify(__val, (_k, v) => {\n"
                "      if (typeof v === 'bigint') return String(v);\n"
                "      if (v && typeof v === 'object') { if (__seen.has(v)) return '[Circular]'; __seen.add(v); }\n"
                "      return v;\n"
                "    });\n"
                "    if (__s !== undefined) return { __fmuEvalBox: true, hasValue: true, raw: JSON.parse(__s) };\n"
                "  } catch (_) {}\n"
                "  return { __fmuEvalBox: true, hasValue: true, raw: String(__val) };\n"
                "})()"
            )
            cdp_res = sess._raw_cdp.send(
                "Runtime.evaluate",
                {
                    "expression": wrapped,
                    "awaitPromise": True,
                    "returnByValue": True,
                },
            )
            if isinstance(cdp_res, dict) and cdp_res.get("exceptionDetails"):
                exc_det = cdp_res["exceptionDetails"]
                res_obj = cdp_res.get("result") or {}
                desc = res_obj.get("description") or exc_det.get("text") or "Runtime.evaluate error"
                raise RuntimeError(str(desc))
            if isinstance(cdp_res, dict) and isinstance(cdp_res.get("result"), dict):
                val = cdp_res["result"].get("value")
                if isinstance(val, dict) and val.get("__fmuEvalBox") is True:
                    return bool(val.get("hasValue")), val.get("raw")
                if "value" in cdp_res["result"]:
                    return True, val
            return False, None

        last_err: Exception | None = None
        for idx, cand in enumerate(candidates):
            try:
                return _send(cand)
            except Exception as exc:
                last_err = exc
                if idx + 1 < len(candidates) and "SyntaxError" in str(exc):
                    continue
                raise
        if last_err is not None:
            raise last_err
        return False, None

    def _handle_eval_escalations(
        self,
        sess: BrowserTabSession,
        res: dict[str, Any],
        *,
        agent_id: str,
        query: str = "",
        full_table: bool = False,
        full_page: bool = False,
        skip_snapshot: bool = False,
    ) -> None:
        if not isinstance(res, dict):
            return
        escs = res.get("escalations")
        if not isinstance(escs, list) or not escs:
            return
        eval_outs = list(res.get("evalOutputs") or [])
        raw_vals = list(res.get("rawEvalValues") or [])
        errs = list(res.get("errors") or [])
        ran_any = False
        for esc in escs:
            if not isinstance(esc, dict) or esc.get("reason") != "eval_csp" or esc.get("handled"):
                continue
            ran_any = True
            esc["handled"] = True
            try:
                cur_host = (urlparse(_normalize_nav_url(sess.last_url)).hostname or "").lower()
                if cur_host:
                    self._strict_csp_hosts.add(cur_host)
            except ValueError:
                pass
            try:
                has_val, val = self._eval_step_via_cdp(sess, str(esc.get("code") or ""))
                if has_val:
                    eval_outs.append(val if isinstance(val, str) else json.dumps(val, ensure_ascii=False))
                raw_vals.append(val if has_val else None)
            except Exception as exc:
                step_no = esc.get("step", 1)
                errs.append(f"step {step_no} (eval): {exc}")
        if not ran_any:
            return
        res["evalOutputs"] = eval_outs
        res["rawEvalValues"] = raw_vals
        res["errors"] = errs
        if not skip_snapshot:
            try:
                refreshed = sess._raw_page.evaluate(
                    "opts => window.__fmu.refreshAfterOcr(opts)",
                    {
                        "agent": agent_id,
                        "query": query,
                        "fullTable": full_table,
                        "fullPage": full_page,
                        "useDiff": bool(res.get("useDiff")),
                    },
                )
                if isinstance(refreshed, dict):
                    res["crc"] = refreshed.get("crc", res.get("crc"))
                    res["seq"] = refreshed.get("seq", res.get("seq"))
                    res["count"] = refreshed.get("count", res.get("count"))
                    res["lines"] = refreshed.get("lines", res.get("lines"))
            except _BROWSER_OPS_ERRORS:
                pass

    def _settle_playwright_navigation(self, sess: BrowserTabSession, prev_url: str, tab_id: str) -> None:
        if sess.is_extension:
            return
        deadline = time.monotonic() + 0.16
        while time.monotonic() < deadline:
            try:
                if sess._raw_page.is_closed():
                    return
                if prev_url and sess._raw_page.url != prev_url:
                    break
            except Exception:
                break
            time.sleep(0.015)
        try:
            sess._raw_page.wait_for_load_state("domcontentloaded", timeout=10000)
        except _BROWSER_OPS_ERRORS:
            pass
        if not sess._raw_page.is_closed():
            if sess._raw_page.url:
                sess.last_url = sess._raw_page.url
                self._tab_last_urls[tab_id] = sess._raw_page.url
            sess._focus_emulated = False
            sess._runtime_ensured = False
            sess._ocr_checked = False
            sess._ocr_populated = False
            sess.enable_background_focus_emulation()
            sess.ensure_runtime()

    def _dispatch_cdp_mouse_click(self, sess: BrowserTabSession, x: float, y: float) -> None:
        sess._raw_cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "none"})
        sess._raw_cdp.send(
            "Input.dispatchMouseEvent",
            {"type": "mousePressed", "x": x, "y": y, "button": "left", "buttons": 1, "clickCount": 1},
        )
        sess._raw_cdp.send(
            "Input.dispatchMouseEvent",
            {"type": "mouseReleased", "x": x, "y": y, "button": "left", "buttons": 0, "clickCount": 1},
        )

    def _dispatch_cdp_key(self, sess: BrowserTabSession, raw_key: str) -> None:
        ks = _parse_cdp_key_spec(raw_key)
        down_params: dict[str, Any] = {
            "type": "keyDown",
            "key": ks["key"],
            "code": ks["code"],
            "windowsVirtualKeyCode": ks["vk"],
            "nativeVirtualKeyCode": ks["vk"],
            "modifiers": ks["modifiers"],
        }
        if ks.get("text") is not None:
            down_params["text"] = ks["text"]
            down_params["unmodifiedText"] = ks["unmodifiedText"]
        if ks.get("commands"):
            down_params["commands"] = ks["commands"]
        up_params: dict[str, Any] = {
            "type": "keyUp",
            "key": ks["key"],
            "code": ks["code"],
            "windowsVirtualKeyCode": ks["vk"],
            "nativeVirtualKeyCode": ks["vk"],
            "modifiers": ks["modifiers"],
        }
        sess._raw_cdp.send("Input.dispatchKeyEvent", down_params)
        sess._raw_cdp.send("Input.dispatchKeyEvent", up_params)

    def _dispatch_cdp_text_keys(self, sess: BrowserTabSession, text: str) -> None:
        for ch in text:
            if ch in ("\n", "\r"):
                self._dispatch_cdp_key(sess, "Enter")
            elif 32 <= ord(ch) <= 126:
                ks = _parse_cdp_key_spec(ch)
                down_params: dict[str, Any] = {
                    "type": "keyDown",
                    "key": ch,
                    "code": ks["code"],
                    "windowsVirtualKeyCode": ks["vk"],
                    "nativeVirtualKeyCode": ks["vk"],
                    "modifiers": ks["modifiers"],
                    "text": ch,
                    "unmodifiedText": ch.lower() if "A" <= ch <= "Z" else ch,
                }
                up_params: dict[str, Any] = {
                    "type": "keyUp",
                    "key": ch,
                    "code": ks["code"],
                    "windowsVirtualKeyCode": ks["vk"],
                    "nativeVirtualKeyCode": ks["vk"],
                    "modifiers": ks["modifiers"],
                }
                sess._raw_cdp.send("Input.dispatchKeyEvent", down_params)
                sess._raw_cdp.send("Input.dispatchKeyEvent", up_params)
            else:
                sess._raw_cdp.send("Input.insertText", {"text": ch})
            time.sleep(0.005)

    def _prep_cdp_iframe_target(self, sess: BrowserTabSession, spec: dict[str, Any]) -> dict[str, Any]:
        uid = str(spec.get("uid") or spec.get("label") or "iframe")
        try:
            prep = sess._raw_page.evaluate(
                "s => (window.__fmu && typeof window.__fmu.prepIframeTarget === 'function' ? window.__fmu.prepIframeTarget(s) : null)",
                {
                    "uid": spec.get("uid"),
                    "frameSrc": spec.get("frameSrc"),
                    "frameName": spec.get("frameName"),
                    "frameTitle": spec.get("frameTitle"),
                    "frameIndex": spec.get("frameIndex"),
                },
            )
        except _BROWSER_OPS_ERRORS:
            prep = None
        if isinstance(prep, dict):
            if prep.get("inViewport") is False:
                raise RuntimeError(f"cross-origin iframe {uid} is outside visible viewport")
            out = dict(spec)
            if prep.get("x") is not None and prep.get("y") is not None:
                out["x"] = float(prep["x"])
                out["y"] = float(prep["y"])
            if isinstance(prep.get("frameIndex"), int) and prep["frameIndex"] >= 0:
                out["frameIndex"] = prep["frameIndex"]
            return out
        return spec

    def _probe_playwright_iframe(
        self,
        sess: BrowserTabSession,
        spec: dict[str, Any],
        action: str,
        value: str = "",
        *,
        target_frame: Any = None,
    ) -> tuple[dict[str, Any], Any]:
        frame_src = str(spec.get("frameSrc") or "").strip()
        frame_name = str(spec.get("frameName") or "").strip()
        want_frame_idx = spec.get("frameIndex")
        exp_norm = _normalize_field_value(value)
        arg = {
            "action": action,
            "expectedHash": _fnv1a32(value),
            "expectedLen": len(value),
            "expectedNormHash": _fnv1a32(exp_norm),
            "expectedNormLen": len(exp_norm),
        }
        if target_frame is not None:
            try:
                out = target_frame.evaluate(_IFRAME_PROBE_JS, arg)
                if isinstance(out, dict):
                    return out, target_frame
            except _BROWSER_OPS_ERRORS:
                pass
            return {"matched": False}, target_frame
        try:
            frames = [f for f in sess._raw_page.frames if f != sess._raw_page.main_frame]
        except _BROWSER_OPS_ERRORS:
            return {"matched": False}, None
        ranked: list[tuple[int, Any]] = []
        for f in frames:
            try:
                f_url = str(f.url or "")
                f_name = str(f.name or "")
            except _BROWSER_OPS_ERRORS:
                continue
            score = 0
            if frame_src:
                if f_url == frame_src:
                    score = 100 + (10 if (frame_name and f_name == frame_name) else 0)
                elif _same_origin_and_path(f_url, frame_src):
                    score = 80 + (10 if (frame_name and f_name == frame_name) else 0)
                else:
                    continue
                if frame_name and f_name and f_name != frame_name:
                    score -= 20
            else:
                score = 60 if (frame_name and f_name == frame_name) else 10
            ranked.append((score, f))
        ranked.sort(key=lambda item: item[0], reverse=True)
        best_res: dict[str, Any] = {"matched": False}
        best_rank: tuple[int, int, int, int] = (-1, -1, -1, -1)
        best_frame: Any = None
        for score, f in ranked:
            try:
                out = f.evaluate(_IFRAME_PROBE_JS, arg)
            except _BROWSER_OPS_ERRORS:
                continue
            if not isinstance(out, dict) or not out.get("matched"):
                continue
            exact_idx = (
                1
                if (isinstance(want_frame_idx, int) and want_frame_idx >= 0 and out.get("frameIndex") == want_frame_idx)
                else 0
            )
            focused_edit = 1 if (out.get("hasFocus") and out.get("activeIsEditable")) else 0
            focused = 1 if out.get("hasFocus") else 0
            cand_rank = (exact_idx, focused_edit, focused, score)
            if not best_res.get("matched") or cand_rank > best_rank:
                best_res = out
                best_rank = cand_rank
                best_frame = f
        return best_res, best_frame

    def _clear_cdp_focused_input(
        self,
        sess: BrowserTabSession,
        spec: dict[str, Any],
        *,
        target_frame: Any = None,
    ) -> None:
        select_all_key = "Meta+a" if sys.platform == "darwin" else "Control+a"
        self._dispatch_cdp_key(sess, select_all_key)
        self._dispatch_cdp_key(sess, "Backspace")
        self._dispatch_cdp_key(sess, "Delete")
        time.sleep(0.02)
        st, _ = self._probe_playwright_iframe(sess, spec, "verify", "", target_frame=target_frame)
        if st.get("matched") and int(st.get("curLen") or 0) > 0:
            self._probe_playwright_iframe(sess, spec, "focus_and_select", target_frame=target_frame)
            self._dispatch_cdp_key(sess, "Backspace")
            time.sleep(0.015)
            st2, _ = self._probe_playwright_iframe(sess, spec, "verify", "", target_frame=target_frame)
            if st2.get("matched") and int(st2.get("curLen") or 0) > 0:
                self._probe_playwright_iframe(sess, spec, "force_clear", target_frame=target_frame)

    def _dispatch_cdp_input(self, sess: BrowserTabSession, spec: dict[str, Any]) -> None:
        kind = str(spec.get("kind") or "fill")
        if kind == "key":
            self._dispatch_cdp_key(sess, str(spec.get("key") or "Enter"))
            return
        if kind == "type":
            self._dispatch_cdp_text_keys(sess, str(spec.get("value") or ""))
            return

        spec = self._prep_cdp_iframe_target(sess, spec)
        x = float(spec["x"])
        y = float(spec["y"])
        value = str(spec.get("value") or "")
        clear = bool(spec.get("clear", True))
        uid = str(spec.get("uid") or spec.get("label") or "iframe")
        self._dispatch_cdp_mouse_click(sess, x, y)
        time.sleep(0.03)
        _probe_res, pinned_frame = self._probe_playwright_iframe(sess, spec, "probe")
        self._probe_playwright_iframe(sess, spec, "focus_and_select", target_frame=pinned_frame)
        if clear:
            self._clear_cdp_focused_input(sess, spec, target_frame=pinned_frame)
        if value:
            sess._raw_cdp.send("Input.insertText", {"text": value})
            time.sleep(0.025)
        verify_res, _ = self._probe_playwright_iframe(sess, spec, "verify", value, target_frame=pinned_frame)
        if verify_res.get("matched") and not verify_res.get("verified"):
            self._probe_playwright_iframe(sess, spec, "focus_and_select", target_frame=pinned_frame)
            self._clear_cdp_focused_input(sess, spec, target_frame=pinned_frame)
            if value:
                self._dispatch_cdp_text_keys(sess, value)
                time.sleep(0.025)
            verify_res, _ = self._probe_playwright_iframe(sess, spec, "verify", value, target_frame=pinned_frame)
            if verify_res.get("matched") and not verify_res.get("verified"):
                exp_len = len(_normalize_field_value(value))
                act_len = int(verify_res.get("actualNormLen") or 0)
                raise RuntimeError(
                    f"cross-origin iframe verification failed for {uid} (expected_len={exp_len}, actual_len={act_len})"
                )

    def _eval_batch_handling_navigation(
        self,
        sess: BrowserTabSession,
        payload: dict[str, Any],
        *,
        tab_id: str,
    ) -> dict[str, Any]:
        prev_url = str(getattr(sess._raw_page, "url", "") or sess.last_url or "")
        orig_steps = list(payload.get("steps") or [])
        can_cdp_click = (not sess.is_extension) and (sess._raw_cdp is not None)
        post_nav_safe_ops = {
            "wait",
            "sleep",
            "delay",
            "settle",
            "waitfor",
            "wait_for",
            "waituntil",
            "text",
            "readtext",
            "readmain",
            "eval",
            "evaluate",
            "snapshot",
            "diff",
        }

        def _invoke(p: dict[str, Any]) -> dict[str, Any]:
            if "strictCsp" not in p and self._strict_csp_hosts:
                cur_u = str(getattr(sess._raw_page, "url", "") or sess.last_url or "")
                try:
                    cur_h = (urlparse(_normalize_nav_url(cur_u)).hostname or "").lower()
                except ValueError:
                    cur_h = ""
                if cur_h and cur_h in self._strict_csp_hosts:
                    p = {**p, "strictCsp": True}
            try:
                out = sess._raw_page.evaluate("req => window.__fmu.executeBatch(req)", p)
                return out if isinstance(out, dict) else {}
            except Exception as inner_exc:
                msg = str(inner_exc)
                if (
                    "window.__fmu" in msg
                    or ("executeBatch" in msg and "undefined" in msg)
                    or "Cannot read properties of undefined" in msg
                ):
                    sess._focus_emulated = False
                    sess.enable_background_focus_emulation()
                    sess.ensure_runtime(force=True)
                    out = sess._raw_page.evaluate("req => window.__fmu.executeBatch(req)", p)
                    return out if isinstance(out, dict) else {}
                raise

        if sess.is_extension:
            try:
                return _invoke(payload)
            except Exception as exc:
                if not _is_navigation_error(exc) or sess._raw_page.is_closed():
                    raise
                time.sleep(0.08)
                nav_trigger_idx = next(
                    (
                        idx
                        for idx, stp in enumerate(orig_steps)
                        if str(stp.get("op") or stp.get("action") or "").lower()
                        in ("click", "press", "key", "eval", "evaluate", "webmcp", "calltool")
                    ),
                    -1,
                )
                rem_steps = (
                    [
                        stp
                        for stp in orig_steps[nav_trigger_idx + 1 :]
                        if str(stp.get("op") or stp.get("action") or "").lower() in post_nav_safe_ops
                    ]
                    if nav_trigger_idx >= 0
                    else []
                )
                follow_payload = dict(payload)
                follow_payload["steps"] = rem_steps
                if not follow_payload.get("skipSnapshot"):
                    follow_payload["diff"] = False
                follow_payload["virtualTools"] = self.get_macros_for_url(sess.last_url)
                res_after_err = _invoke(follow_payload)
                res_after_err["didNavigate"] = True
                return res_after_err

        cur_steps = orig_steps
        step_offset = int(payload.get("stepOffset") or 0)
        had_pre_steps = bool(payload.get("hadPreSteps"))
        cur_diff = payload.get("diff")
        did_navigate = False
        acc_errors: list[Any] = []
        acc_evals: list[Any] = []
        acc_raw_vals: list[Any] = []
        acc_escalations: list[Any] = []
        acc_exec_ms = 0.0
        last_cursor: Any = None
        max_slices = len(orig_steps) + 3
        res: dict[str, Any] = {}

        for _ in range(max_slices):
            slice_payload = dict(payload)
            slice_payload["steps"] = cur_steps
            slice_payload["stepOffset"] = step_offset
            slice_payload["hadPreSteps"] = had_pre_steps
            if cur_diff is not None:
                slice_payload["diff"] = cur_diff
            slice_payload["cdpClick"] = can_cdp_click
            slice_payload["virtualTools"] = self.get_macros_for_url(sess.last_url)
            try:
                res = _invoke(slice_payload)
            except Exception as exc:
                if not _is_navigation_error(exc) or sess._raw_page.is_closed():
                    raise
                self._settle_playwright_navigation(sess, prev_url, tab_id)
                prev_url = str(getattr(sess._raw_page, "url", "") or sess.last_url or "")
                did_navigate = True
                nav_trigger_idx = next(
                    (
                        idx
                        for idx, stp in enumerate(cur_steps)
                        if str(stp.get("op") or stp.get("action") or "").lower()
                        in ("click", "press", "key", "eval", "evaluate", "webmcp", "calltool")
                    ),
                    -1,
                )
                if nav_trigger_idx >= 0:
                    step_offset += nav_trigger_idx + 1
                    cur_steps = [
                        stp
                        for stp in cur_steps[nav_trigger_idx + 1 :]
                        if str(stp.get("op") or stp.get("action") or "").lower() in post_nav_safe_ops
                    ]
                else:
                    step_offset += len(cur_steps)
                    cur_steps = []
                had_pre_steps = True
                if not payload.get("skipSnapshot"):
                    cur_diff = False
                if payload.get("skipSnapshot") and not cur_steps:
                    return {
                        "didNavigate": True,
                        "url": prev_url,
                        "title": sess.last_title,
                        "errors": acc_errors,
                        "evalOutputs": acc_evals,
                        "rawEvalValues": acc_raw_vals,
                        "escalations": acc_escalations,
                        "cursor": last_cursor,
                        "canvases": [],
                        "timings": {"execMs": round(acc_exec_ms, 2)},
                        "lines": [],
                    }
                continue

            acc_errors.extend(res.get("errors") or [])
            acc_evals.extend(res.get("evalOutputs") or [])
            acc_raw_vals.extend(res.get("rawEvalValues") or [])
            acc_escalations.extend(res.get("escalations") or [])
            tm = res.get("timings") if isinstance(res.get("timings"), dict) else {}
            acc_exec_ms += float(tm.get("execMs") or 0.0)
            if res.get("cursor") is not None:
                last_cursor = res.get("cursor")

            cdp_click = res.get("cdpClick") if isinstance(res, dict) else None
            cdp_input = res.get("cdpInput") if isinstance(res, dict) else None
            if (isinstance(cdp_click, dict) or isinstance(cdp_input, dict)) and can_cdp_click:
                next_idx = int(res.get("nextStepIndex") if res.get("nextStepIndex") is not None else len(cur_steps))
                click_url = str(res.get("url") or getattr(sess._raw_page, "url", "") or prev_url)
                step_offset += next_idx
                cur_steps = cur_steps[next_idx:] if next_idx < len(cur_steps) else []
                had_pre_steps = True
                action_label = "click" if isinstance(cdp_click, dict) else str((cdp_input or {}).get("kind") or "input")
                try:
                    if isinstance(cdp_click, dict):
                        self._dispatch_cdp_mouse_click(sess, float(cdp_click["x"]), float(cdp_click["y"]))
                    elif isinstance(cdp_input, dict):
                        self._dispatch_cdp_input(sess, cdp_input)
                except Exception as click_exc:
                    if not _is_navigation_error(click_exc):
                        acc_errors.append(f"step {step_offset} ({action_label}): {click_exc}")
                try:
                    if not sess._raw_page.is_closed():
                        sess._raw_page.wait_for_timeout(15)
                except _BROWSER_OPS_ERRORS:
                    pass
                if not sess._raw_page.is_closed():
                    post_click_url = str(getattr(sess._raw_page, "url", "") or "")
                    if click_url and post_click_url and post_click_url != click_url:
                        sess.last_url = post_click_url
                        self._tab_last_urls[tab_id] = post_click_url
                        if not _same_origin_and_path(click_url, post_click_url):
                            self._settle_playwright_navigation(sess, click_url, tab_id)
                            prev_url = str(getattr(sess._raw_page, "url", "") or sess.last_url or "")
                            did_navigate = True
                            if not payload.get("skipSnapshot"):
                                cur_diff = False
                if payload.get("skipSnapshot") and not cur_steps:
                    return {
                        "didNavigate": did_navigate,
                        "url": str(getattr(sess._raw_page, "url", "") or sess.last_url or ""),
                        "title": sess.last_title,
                        "errors": acc_errors,
                        "evalOutputs": acc_evals,
                        "rawEvalValues": acc_raw_vals,
                        "escalations": acc_escalations,
                        "cursor": last_cursor,
                        "canvases": [],
                        "timings": {"execMs": round(acc_exec_ms, 2)},
                        "lines": [],
                    }
                continue

            if isinstance(res, dict) and res.get("navigated"):
                next_idx = int(res.get("nextStepIndex") if res.get("nextStepIndex") is not None else len(cur_steps))
                nav_from = str(res.get("url") or prev_url)
                step_offset += next_idx
                cur_steps = cur_steps[next_idx:] if next_idx < len(cur_steps) else []
                had_pre_steps = True
                self._settle_playwright_navigation(sess, nav_from, tab_id)
                prev_url = str(getattr(sess._raw_page, "url", "") or sess.last_url or "")
                did_navigate = True
                if not payload.get("skipSnapshot"):
                    cur_diff = False
                if payload.get("skipSnapshot") and not cur_steps:
                    return {
                        "didNavigate": True,
                        "url": prev_url,
                        "title": sess.last_title,
                        "errors": acc_errors,
                        "evalOutputs": acc_evals,
                        "rawEvalValues": acc_raw_vals,
                        "escalations": acc_escalations,
                        "cursor": last_cursor,
                        "canvases": [],
                        "timings": {"execMs": round(acc_exec_ms, 2)},
                        "lines": [],
                    }
                continue

            break

        if isinstance(res, dict):
            if did_navigate:
                res["didNavigate"] = True
            res["errors"] = acc_errors
            res["evalOutputs"] = acc_evals
            res["rawEvalValues"] = acc_raw_vals
            res["escalations"] = acc_escalations
            if last_cursor is not None and not res.get("cursor"):
                res["cursor"] = last_cursor
            if isinstance(res.get("timings"), dict):
                res["timings"]["execMs"] = round(acc_exec_ms, 2)
        return res

    def evaluate_value(
        self,
        code: str,
        *,
        agent: str = "default",
        session: str | None = None,
        mode: str = "chrome",
        session_name: str = "Agent",
        window: str | None = None,
        window_id: int | None = None,
        chrome_tab_id: int | None = None,
        require_extension: bool = False,
        step_override: dict[str, Any] | None = None,
    ) -> Any:
        if is_user_stopped():
            raise RuntimeError(USER_STOPPED_ERROR)

        def _run_once(force_reattach: bool = False) -> Any:
            t0 = time.perf_counter()
            agent_id = str(agent or "default").strip() or "default"
            default_tab = "t1" if agent_id == "default" else f"{agent_id}:t1"
            tab_id = str(session or default_tab)
            group_color = self._get_agent_color(agent_id)
            restore_url = self._tab_last_urls.get(tab_id) or "about:blank"
            req_ext = (
                bool(require_extension)
                or mode in ("extension", "chrome-extension", "live")
                or chrome_tab_id is not None
            )
            needs_ext = req_ext or (
                mode == "chrome"
                and os.environ.get("FMU_USE_EXTENSION", "1") != "0"
                and not self._find_chrome_devtools_ws()
                and (self._is_external_url(restore_url) or window is not None or window_id is not None)
            )
            if (
                tab_id not in self._tabs
                or not self._is_session_alive(self._tabs[tab_id])
                or (needs_ext and not self._tabs[tab_id].is_extension)
            ):
                sess = self._create_tab_inner(
                    url=restore_url,
                    mode=mode,
                    session_name=session_name,
                    tab_id=tab_id,
                    group_color=group_color,
                    window=window,
                    window_id=window_id,
                    chrome_tab_id=chrome_tab_id,
                    reuse_existing=force_reattach,
                    require_extension=req_ext,
                    reattach=(force_reattach or tab_id not in self._tab_last_urls),
                    schedule_preview=False,
                )
            else:
                sess = self._tabs[tab_id]
            t_att_ms = (time.perf_counter() - t0) * 1000.0
            with sess._exec_lock:
                sess.enable_background_focus_emulation()
                sess.ensure_runtime()
                step_item = normalize_step_dict(dict(step_override)) if step_override is not None else {"op": "eval", "code": str(code)}
                op_name = str(step_item.get("op") or "eval").lower()
                cur_host = ""
                try:
                    cur_host = (urlparse(_normalize_nav_url(sess.last_url)).hostname or "").lower()
                except ValueError:
                    pass
                bridge_kind = "mv3" if sess.is_extension else ("cdp" if self._is_cdp_chrome else "playwright")
                if op_name in ("eval", "evaluate") and cur_host and cur_host in self._strict_csp_hosts:
                    t_esc0 = time.perf_counter()
                    try:
                        has_val, val = self._eval_step_via_cdp(sess, str(step_item.get("code") or code))
                        esc_ms = (time.perf_counter() - t_esc0) * 1000.0
                        out_val = val if has_val else None
                        out_str = "" if out_val is None else (out_val if isinstance(out_val, str) else json.dumps(out_val, ensure_ascii=False))
                        record_perf_event(
                            {
                                "surface": "web",
                                "bridge": bridge_kind,
                                "agent": agent_id,
                                "session": sess.tab_id,
                                "target": sanitize_url_target(sess.last_url),
                                "ops": [op_name],
                                "step_count": 1,
                                "els": 0,
                                "mode_out": "unchanged",
                                "out_chars": len(out_str),
                                "est_tokens": max(1, (len(out_str) + 3) // 4) if out_str else 1,
                                "escalations": ["eval_csp_cached"],
                                "error_count": 0,
                                "errors": [],
                                "timings_ms": {
                                    "total": round((time.perf_counter() - t0) * 1000.0, 2),
                                    "tab_attach": round(t_att_ms, 2),
                                    "bridge_overhead": 0.0,
                                    "in_page_exec": 0.0,
                                    "in_page_settle": 0.0,
                                    "in_page_snap": 0.0,
                                    "escalation": round(esc_ms, 2),
                                    "ocr": 0.0,
                                },
                            }
                        )
                        return out_val
                    except Exception as csp_exc:
                        record_perf_event(
                            {
                                "surface": "web",
                                "bridge": bridge_kind,
                                "agent": agent_id,
                                "session": sess.tab_id,
                                "target": sanitize_url_target(sess.last_url),
                                "ops": [op_name],
                                "step_count": 1,
                                "escalations": ["eval_csp_cached"],
                                "error_count": 1,
                                "errors": [sanitize_error(f"step 1 (eval): {csp_exc}")],
                                "timings_ms": {"total": round((time.perf_counter() - t0) * 1000.0, 2)},
                            }
                        )
                        raise RuntimeError(f"step 1 (eval): {csp_exc}") from csp_exc

                t_ip0 = time.perf_counter()
                res = self._eval_batch_handling_navigation(
                    sess,
                    {
                        "agent": agent_id,
                        "steps": [step_item],
                        "settle": False,
                        "skipSnapshot": True,
                        "returnRawEval": True,
                    },
                    tab_id=tab_id,
                )
                ip_wall_ms = (time.perf_counter() - t_ip0) * 1000.0
                t_esc0 = time.perf_counter()
                self._handle_eval_escalations(
                    sess,
                    res,
                    agent_id=agent_id,
                    skip_snapshot=True,
                )
                esc_ms = (time.perf_counter() - t_esc0) * 1000.0
                errs = res.get("errors") or []
                raw_vals = res.get("rawEvalValues")
                eval_outs = res.get("evalOutputs")
                ret_val: Any = None
                if isinstance(raw_vals, list) and raw_vals:
                    ret_val = raw_vals[-1]
                elif isinstance(eval_outs, list) and eval_outs:
                    ret_val = eval_outs[-1]
                out_str = "" if ret_val is None else (ret_val if isinstance(ret_val, str) else json.dumps(ret_val, ensure_ascii=False))
                tm = res.get("timings") if isinstance(res.get("timings"), dict) else {}
                exec_ms = float(tm.get("execMs") or 0.0)
                tot_js_ms = float(tm.get("totalInPageMs") or 0.0)
                nav_w_ms = float(res.get("navWaitMs") or 0.0)
                escs_logged = [
                    "eval_csp_cached"
                    if e.get("reason") == "eval_csp" and e.get("cached")
                    else str(e["reason"])
                    for e in (res.get("escalations") or [])
                    if isinstance(e, dict) and e.get("reason")
                ]
                record_perf_event(
                    {
                        "surface": "web",
                        "bridge": bridge_kind,
                        "agent": agent_id,
                        "session": sess.tab_id,
                        "target": sanitize_url_target(sess.last_url),
                        "ops": [op_name],
                        "step_count": 1,
                        "els": 0,
                        "mode_out": "unchanged",
                        "out_chars": len(out_str),
                        "est_tokens": max(1, (len(out_str) + 3) // 4) if out_str else 1,
                        "escalations": escs_logged,
                        "error_count": len(errs),
                        "errors": [sanitize_error(e) for e in errs[:3]],
                        "timings_ms": {
                            "total": round((time.perf_counter() - t0) * 1000.0, 2),
                            "tab_attach": round(t_att_ms, 2),
                            "bridge_overhead": round(max(0.0, ip_wall_ms - tot_js_ms - nav_w_ms), 2),
                            "nav_wait": round(nav_w_ms, 2),
                            "in_page_exec": round(exec_ms, 2),
                            "in_page_settle": 0.0,
                            "in_page_snap": 0.0,
                            "escalation": round(esc_ms, 2),
                            "ocr": 0.0,
                        },
                    }
                )
                if errs:
                    raise RuntimeError("; ".join(str(e) for e in errs))
                return ret_val

        def _run() -> Any:
            try:
                return _run_once(False)
            except Exception as exc:
                msg = str(exc)
                if any(
                    tok in msg
                    for tok in (
                        "No tab with id",
                        "No tab with given id",
                        "No target with given id",
                        "Tab not found",
                        "Target page, context or browser has been closed",
                        "TargetClosedError",
                        "Session closed",
                    )
                ):
                    agent_id = str(agent or "default").strip() or "default"
                    default_tab = "t1" if agent_id == "default" else f"{agent_id}:t1"
                    tab_id = str(session or default_tab)
                    with self._state_lock:
                        self._tabs.pop(tab_id, None)
                    return _run_once(True)
                raise

        eff_agent = str(agent or "default").strip() or "default"
        eff_tid = str(session or ("t1" if eff_agent == "default" else f"{eff_agent}:t1"))
        if self._can_run_direct_mv3(mode=mode, tab_id=eff_tid):
            return _run()
        return self._run_sync(_run)

    def screenshot_tab(
        self,
        *,
        path: str | None = None,
        full_page: bool = False,
        agent: str = "default",
        session: str | None = None,
        mode: str = "chrome",
        session_name: str = "Agent",
        window: str | None = None,
        window_id: int | None = None,
        chrome_tab_id: int | None = None,
        require_extension: bool = False,
    ) -> str:
        if is_user_stopped():
            raise RuntimeError(USER_STOPPED_ERROR)

        def _capture() -> str:
            t0 = time.perf_counter()
            agent_id = str(agent or "default").strip() or "default"
            default_tab = "t1" if agent_id == "default" else f"{agent_id}:t1"
            tab_id = str(session or default_tab)
            sess = self._tabs.get(tab_id)
            if sess is None or not self._is_session_alive(sess):
                sess = self._create_tab_inner(
                    url=self._tab_last_urls.get(tab_id) or "about:blank",
                    mode=mode,
                    session_name=session_name,
                    tab_id=tab_id,
                    group_color=self._get_agent_color(agent_id),
                    window=window,
                    window_id=window_id,
                    chrome_tab_id=chrome_tab_id,
                    require_extension=require_extension,
                    reattach=tab_id not in self._tab_last_urls,
                    schedule_preview=False,
                )
            with sess._exec_lock:
                saved_path, _, _ = sess.capture_screenshot(path=path, full_page=full_page)
            bridge_kind = "mv3" if sess.is_extension else ("cdp" if self._is_cdp_chrome else "playwright")
            record_perf_event(
                {
                    "surface": "web",
                    "bridge": bridge_kind,
                    "agent": agent_id,
                    "session": sess.tab_id,
                    "target": sanitize_url_target(sess.last_url),
                    "ops": ["screenshot"],
                    "step_count": 1,
                    "els": 0,
                    "mode_out": "unchanged",
                    "out_chars": len(saved_path),
                    "est_tokens": max(1, (len(saved_path) + 3) // 4),
                    "error_count": 0,
                    "errors": [],
                    "timings_ms": {"total": round((time.perf_counter() - t0) * 1000.0, 2)},
                }
            )
            return saved_path

        eff_agent = str(agent or "default").strip() or "default"
        eff_tid = str(session or ("t1" if eff_agent == "default" else f"{eff_agent}:t1"))
        if self._can_run_direct_mv3(mode=mode, tab_id=eff_tid):
            return _capture()
        return self._run_sync(_capture)

    def get_tab_errors(self, tab_id: str, *, clear: bool = False) -> list[dict[str, Any]]:
        sess = self._tabs.get(tab_id)
        if sess is None:
            return []
        return sess.get_diagnostics(clear=clear)

    def clear_tab_errors(self, tab_id: str) -> None:
        sess = self._tabs.get(tab_id)
        if sess is not None:
            sess.clear_diagnostics()

    def audit_tab_layout(
        self,
        tab_id: str,
        *,
        widths: list[int] | None = None,
        height: int = 812,
    ) -> dict[str, Any]:
        target_widths = [max(200, int(w)) for w in (widths or [320, 375, 768, 1024, 1280])]
        h = max(160, int(height))

        def _audit() -> dict[str, Any]:
            sess = self._tabs.get(tab_id)
            if sess is None or not self._is_session_alive(sess):
                raise RuntimeError(f"Tab not found: {tab_id}")
            sess.ensure_runtime(force=sess.is_extension)
            had_custom = sess._custom_viewport
            prev_vw, prev_vh = sess.last_vw, sess.last_vh
            by_width: dict[str, list[str]] = {}
            total = 0
            try:
                for w in target_widths:
                    sess.set_viewport(width=w, height=h, mobile=(w < 768))
                    issues = sess._raw_page.evaluate(
                        "async () => { window.dispatchEvent(new Event('resize')); await new Promise((r) => setTimeout(r, 24)); return window.__fmu ? (window.__fmu.collectSnapshot('', 180, true, false).layoutIssues || []) : []; }"
                    )
                    issue_list = [str(x) for x in (issues or [])]
                    by_width[str(w)] = issue_list
                    total += len(issue_list)
            finally:
                if had_custom:
                    sess.set_viewport(width=prev_vw, height=prev_vh, mobile=(prev_vw < 768))
                else:
                    sess.set_viewport(reset=True)
                try:
                    sess._raw_page.evaluate("() => { window.dispatchEvent(new Event('resize')); }")
                except _BROWSER_OPS_ERRORS:
                    pass
            out_audit = {
                "clean": total == 0,
                "issueCount": total,
                "widths": by_width,
            }
            bridge_kind = "mv3" if sess.is_extension else ("cdp" if self._is_cdp_chrome else "playwright")
            out_json = json.dumps(out_audit)
            record_perf_event(
                {
                    "surface": "web",
                    "bridge": bridge_kind,
                    "agent": "default",
                    "session": sess.tab_id,
                    "target": sanitize_url_target(sess.last_url),
                    "ops": ["layoutaudit"],
                    "step_count": len(target_widths),
                    "els": total,
                    "mode_out": "unchanged",
                    "out_chars": len(out_json),
                    "est_tokens": max(1, (len(out_json) + 3) // 4),
                    "error_count": 0,
                    "errors": [],
                    "timings_ms": {"total": round((time.perf_counter() - t_aud0) * 1000.0, 2)},
                }
            )
            return out_audit

        t_aud0 = time.perf_counter()
        if self._can_run_direct_mv3(tab_id=tab_id):
            return _audit()
        return self._run_sync(_audit)

    def execute_batch(self, req: dict[str, Any]) -> str:
        if req.get("agent") in self.bounds_agents:
            req = {**req, "bounds": True}
        is_list_ext = bool(req.get("listExtensions")) or isinstance(req.get("listExtensions"), dict)
        is_set_ext = bool(req.get("setExtensionEnabled")) or isinstance(req.get("setExtensionEnabled"), dict)
        if not any(
            req.get(k) or isinstance(req.get(k), dict)
            for k in ("reset", "bridgeStatus", "reloadExtension", "listTabs", "listExtensions")
        ) and is_user_stopped():
            return USER_STOPPED_ERROR
        if is_list_ext or is_set_ext:
            return self._execute_batch_inner(req)
        if req.get("bridgeStatus") or req.get("reloadExtension"):
            return self._execute_batch_with_retry(req)
        if not req.get("reset") and not req.get("listTabs"):
            agent_id = str(req.get("agent") or req.get("cursor") or "default").strip() or "default"
            default_tab = "t1" if agent_id == "default" else f"{agent_id}:t1"
            tab_id = str(req.get("session") or req.get("tab") or default_tab)
            mode = str(req.get("mode") or "chrome")
            if self._can_run_direct_mv3(mode=mode, tab_id=tab_id):
                return self._execute_batch_with_retry(req)
        return self._run_sync(lambda: self._execute_batch_with_retry(req))

    def _execute_batch_with_retry(self, req: dict[str, Any]) -> str:
        t0 = time.perf_counter()
        try:
            return self._execute_batch_inner(req)
        except Exception as exc:
            msg = str(exc)
            if USER_STOPPED_ERROR in msg or is_user_stopped():
                return USER_STOPPED_ERROR
            if "MV3 extension not connected on ws://" in msg:
                record_perf_event(
                    {
                        "surface": "web",
                        "bridge": "mv3",
                        "agent": str(req.get("agent") or "default"),
                        "target": sanitize_url_target(req.get("url")),
                        "step_count": len(req.get("steps") or []),
                        "error_count": 1,
                        "errors": [sanitize_error(msg)],
                        "timings_ms": {"total": round((time.perf_counter() - t0) * 1000.0, 2)},
                    }
                )
                raise
            if any(
                tok in msg
                for tok in (
                    "No tab with id",
                    "No tab with given id",
                    "No target with given id",
                    "Tab not found",
                    "Target page, context or browser has been closed",
                    "TargetClosedError",
                    "Connection closed",
                    "Session closed",
                    "Browser has been closed",
                    "Extension WebSocket disconnected",
                    "No MV3 extension connected",
                )
            ) or not self._is_browser_alive():
                tab_id = req.get("session") or req.get("tab") or "t1"
                self._tabs.pop(tab_id, None)
                if not self._is_browser_alive():
                    self._evict_dead_browser()
                return self._execute_batch_inner(req)
            record_perf_event(
                {
                    "surface": "web",
                    "bridge": str(req.get("mode") or "chrome"),
                    "agent": str(req.get("agent") or "default"),
                    "target": sanitize_url_target(req.get("url")),
                    "step_count": len(req.get("steps") or []),
                    "error_count": 1,
                    "errors": [sanitize_error(msg)],
                    "timings_ms": {"total": round((time.perf_counter() - t0) * 1000.0, 2)},
                }
            )
            raise

    def _execute_batch_inner(self, req: dict[str, Any]) -> str:
        t0 = time.perf_counter()
        if req.get("reset"):
            self._reset_inner(agent=req.get("agent") or req.get("cursor"))
            return "# fmu-web state reset"

        if req.get("bridgeStatus"):
            st = self._ext_bridge.get_status()
            fb = f' fallbackReason="{st["portFallbackReason"]}"' if st.get("portFallbackReason") else ""
            ver = f' version="{st["client"].get("version")}"' if isinstance(st.get("client"), dict) and st["client"].get("version") else ""
            return (
                f'# fmu-web bridge connected={str(st["connected"]).lower()} '
                f'port={st["port"]} preferredPort={st["preferredPort"]} '
                f'ws="{st["wsUrl"]}"{ver}{fb}'
            )

        if req.get("reloadExtension"):
            self._ext_bridge.call_rpc("reloadExtension", {}, timeout=3.0, check_stopped=False)
            return "# fmu-web extension reloading"

        if req.get("listExtensions") or isinstance(req.get("listExtensions"), dict):
            le_spec = req["listExtensions"]
            if isinstance(le_spec, dict):
                q_val = str(le_spec.get("query") or req.get("query") or "")
                en_raw = le_spec.get("enabled")
                en_filter = bool(en_raw) if isinstance(en_raw, bool) else None
                t_raw = le_spec.get("type") or le_spec.get("extType")
                fmt = str(le_spec.get("format") or req.get("format") or "text").lower()
            else:
                q_val = le_spec if isinstance(le_spec, str) else str(req.get("query") or "")
                en_filter = None
                t_raw = None
                fmt = str(req.get("format") or "text").lower()
            items, self_id = self._list_extensions_raw(
                query=q_val,
                enabled=en_filter,
                ext_type=str(t_raw) if t_raw else None,
            )
            if fmt == "json":
                return json.dumps(items, ensure_ascii=False)
            return self._format_extensions_text(items, self_id=self_id)

        if req.get("setExtensionEnabled") or isinstance(req.get("setExtensionEnabled"), dict):
            se_spec = req["setExtensionEnabled"]
            if not isinstance(se_spec, dict):
                raise ValueError("setExtensionEnabled payload must be an object with 'id'/'name' and boolean 'enabled'")
            target_key = (
                se_spec.get("id")
                or se_spec.get("extensionId")
                or se_spec.get("idOrName")
                or se_spec.get("id_or_name")
                or se_spec.get("name")
                or se_spec.get("extension")
                or ""
            )
            updated = self.set_extension_enabled(str(target_key), se_spec.get("enabled"))  # type: ignore[arg-type]
            if str(se_spec.get("format") or req.get("format") or "text").lower() == "json":
                return json.dumps(updated, ensure_ascii=False)
            return self._format_extension_state_text(updated, self_id=getattr(self, "_ext_self_id", ""))

        if req.get("listTabs"):
            lt_spec = req["listTabs"]
            if isinstance(lt_spec, dict):
                return self._list_tabs_inner(
                    query=str(lt_spec.get("query") or req.get("query") or ""),
                    window=str(lt_spec["window"]) if lt_spec.get("window") else (str(req["window"]) if req.get("window") else None),
                    window_id=int(lt_spec["windowId"]) if lt_spec.get("windowId") is not None else (int(req["windowId"]) if req.get("windowId") is not None else None),
                    session_name=str(lt_spec["sessionName"]) if lt_spec.get("sessionName") else (str(req["sessionName"]) if req.get("sessionName") else None),
                    include_internal=bool(lt_spec.get("includeInternal", False)),
                    mode=str(lt_spec.get("mode") or req.get("mode") or "chrome"),
                )
            q_val = lt_spec if isinstance(lt_spec, str) else str(req.get("query") or "")
            return self._list_tabs_inner(
                query=q_val,
                window=str(req["window"]) if req.get("window") else None,
                window_id=int(req["windowId"]) if req.get("windowId") is not None else None,
                session_name=str(req["sessionName"]) if req.get("sessionName") else None,
                include_internal=bool(req.get("includeInternal", False)),
                mode=str(req.get("mode") or "chrome"),
            )

        if req.get("saveMacro"):
            m_spec = req["saveMacro"]
            if isinstance(m_spec, str):
                entry = self._save_macro_inner(m_spec)
            elif isinstance(m_spec, dict):
                entry = self._save_macro_inner(
                    name=m_spec.get("name", ""),
                    steps=m_spec.get("steps"),
                    domain=m_spec.get("domain"),
                    description=m_spec.get("description", ""),
                    tab_id=req.get("session") or req.get("tab"),
                )
            else:
                raise ValueError("Invalid saveMacro specification")
            return f'# fmu-web macro saved name="{entry["name"]}" domain="{entry["domain"]}" steps={len(entry["steps"])}'

        agent_id = str(req.get("agent") or req.get("cursor") or "default").strip() or "default"
        default_tab = "t1" if agent_id == "default" else f"{agent_id}:t1"
        tab_id = str(req.get("session") or req.get("tab") or default_tab)
        default_group = self.group_title(agent_id)
        group_name = str(req.get("sessionName") or req.get("group") or default_group)
        group_color = self._get_agent_color(agent_id)
        mode = str(req.get("mode") or "chrome")
        require_ext = bool(req.get("requireExtension")) or mode in ("extension", "chrome-extension", "live")
        window_filter = str(req["window"]) if req.get("window") else None
        window_id = int(req["windowId"]) if req.get("windowId") is not None else None
        chrome_tab_id = int(req["chromeTabId"]) if req.get("chromeTabId") is not None else None
        reuse_existing = bool(req.get("reuseExisting"))
        url = req.get("url")
        steps = [
            normalize_step_dict(s) if isinstance(s, dict) else s
            for s in (req.get("steps") or [])
        ]

        if any((s.get("op") or s.get("action")) == "close" for s in steps) and len(steps) == 1:
            return self._close_tab_inner(tab_id)

        while steps and (steps[0].get("op") or steps[0].get("action")) in ("open", "goto", "navigate"):
            step_url = str(steps[0].get("url") or steps[0].get("value") or "").strip()
            if step_url:
                url = step_url
            steps = steps[1:]

        restore_url = url or self._tab_last_urls.get(tab_id) or "about:blank"
        if is_user_stopped():
            return USER_STOPPED_ERROR
        mb_target = _short_menubar_target(restore_url)
        mb_op = (
            str((steps[0].get("op") or steps[0].get("action") or "")).strip().lower()
            if steps and isinstance(steps[0], dict)
            else ("diff" if req.get("diff") else "snap")
        ) or "work"
        existing_sess = self._tabs.get(tab_id)
        initial_prev = existing_sess.last_preview_path if existing_sess is not None else None
        initial_vw = existing_sess.last_vw if existing_sess is not None else None
        initial_vh = existing_sess.last_vh if existing_sess is not None else None
        initial_hl = (
            (not existing_sess.is_extension and not self._is_cdp_chrome)
            if existing_sess is not None
            else (mode == "headless")
        )
        notify_menubar_status(
            "working",
            surface="web",
            target=mb_target,
            op=mb_op,
            agent=self.group_title(agent_id),
            step_count=len(steps),
            preview_path=initial_prev,
            win_w=initial_vw,
            win_h=initial_vh,
            is_headless=initial_hl,
        )
        done_payload: dict[str, Any] = {
            "surface": "web",
            "target": mb_target,
            "op": mb_op,
            "agent": self.group_title(agent_id),
            "step_count": len(steps),
            "preview_path": initial_prev,
            "win_w": initial_vw,
            "win_h": initial_vh,
            "is_headless": initial_hl,
        }
        try:
            return self._execute_batch_core(
                req=req,
                t0=t0,
                agent_id=agent_id,
                tab_id=tab_id,
                group_name=group_name,
                group_color=group_color,
                mode=mode,
                require_ext=require_ext,
                window_filter=window_filter,
                window_id=window_id,
                chrome_tab_id=chrome_tab_id,
                reuse_existing=reuse_existing,
                url=url,
                steps=steps,
                restore_url=restore_url,
                mb_op=mb_op,
                done_payload=done_payload,
            )
        finally:
            cur_sess = self._tabs.get(tab_id)
            if cur_sess is not None and not cur_sess.is_extension:
                cur_sess.stop_playwright_screencast()
            notify_menubar_status("done", **done_payload)

    def _execute_batch_core(
        self,
        *,
        req: dict[str, Any],
        t0: float,
        agent_id: str,
        tab_id: str,
        group_name: str,
        group_color: str,
        mode: str,
        require_ext: bool,
        window_filter: str | None,
        window_id: int | None,
        chrome_tab_id: int | None,
        reuse_existing: bool,
        url: Any,
        steps: list[dict[str, Any]],
        restore_url: str,
        mb_op: str,
        done_payload: dict[str, Any],
    ) -> str:
        self._cancel_pending_preview()
        needs_ext_for_req = (
            require_ext
            or chrome_tab_id is not None
            or (
                mode == "chrome"
                and os.environ.get("FMU_USE_EXTENSION", "1") != "0"
                and not self._find_chrome_devtools_ws()
                and (
                    self._is_external_url(restore_url)
                    or window_filter is not None
                    or window_id is not None
                    or reuse_existing
                )
            )
        )

        reattach_candidate = bool(
            url is None
            and chrome_tab_id is None
            and tab_id not in self._tabs
            and tab_id not in self._tab_last_urls
            and (steps or req.get("diff") is not None or req.get("query"))
        )
        if (
            tab_id not in self._tabs
            or not self._is_session_alive(self._tabs[tab_id])
            or (needs_ext_for_req and not self._tabs[tab_id].is_extension)
        ):
            old_sess = self._tabs.pop(tab_id, None)
            if old_sess is not None and not old_sess.is_extension:
                try:
                    old_sess._raw_page.close()
                except _BROWSER_OPS_ERRORS:
                    pass
            sess = self._create_tab_inner(
                url=restore_url,
                mode=mode,
                session_name=group_name,
                tab_id=tab_id,
                group_color=group_color,
                window=window_filter,
                window_id=window_id,
                chrome_tab_id=chrome_tab_id,
                reuse_existing=reuse_existing,
                require_extension=require_ext,
                reattach=reattach_candidate,
                schedule_preview=False,
            )
        else:
            sess = self._tabs[tab_id]
            sess.start_playwright_screencast()
            if (
                url
                and url != sess._raw_page.url
                and not (reuse_existing and _url_matches_target(sess._raw_page.url, url))
            ):
                norm_u = _normalize_nav_url(url)
                if norm_u != sess._raw_page.url:
                    sess._raw_page.goto(norm_u, wait_until="domcontentloaded", timeout=15000)
                    sess.last_url = str(getattr(sess._raw_page, "url", "") or norm_u)
                    self._tab_last_urls[tab_id] = sess.last_url
                    sess._focus_emulated = False
                    sess._runtime_ensured = False
                    sess._ocr_checked = False
                    sess._ocr_populated = False
                    sess.documented = False
                    sess._documented_agents.clear()
                    self._last_tab_steps.pop(tab_id, None)

        sess._exec_lock.acquire()
        try:
            return self._execute_batch_session(
                sess=sess,
                req=req,
                t0=t0,
                agent_id=agent_id,
                tab_id=tab_id,
                mode=mode,
                require_ext=require_ext,
                steps=steps,
                mb_op=mb_op,
                done_payload=done_payload,
            )
        finally:
            sess._exec_lock.release()

    def _execute_batch_session(
        self,
        *,
        sess: BrowserTabSession,
        req: dict[str, Any],
        t0: float,
        agent_id: str,
        tab_id: str,
        mode: str,
        require_ext: bool,
        steps: list[dict[str, Any]],
        mb_op: str,
        done_payload: dict[str, Any],
    ) -> str:
        sess.enable_background_focus_emulation()
        sess.start_playwright_screencast()
        sess.ensure_runtime()
        tab_attach_ms = (time.perf_counter() - t0) * 1000.0
        in_page_wall_ms = 0.0
        nav_wait_ms = 0.0
        in_page_exec_ms = 0.0
        in_page_settle_ms = 0.0
        in_page_snap_ms = 0.0
        in_page_total_js_ms = 0.0
        escalation_ms = 0.0
        ocr_ms = 0.0
        fixed_wait_ms = 0.0
        all_escalations: list[str] = []

        def _accum_in_page(r: Any, dt_ms: float) -> None:
            nonlocal in_page_wall_ms, nav_wait_ms, in_page_exec_ms, in_page_settle_ms, in_page_snap_ms, in_page_total_js_ms
            in_page_wall_ms += dt_ms
            if isinstance(r, dict):
                nav_wait_ms += float(r.get("navWaitMs") or 0.0)
                tm = r.get("timings")
                if isinstance(tm, dict):
                    in_page_exec_ms += float(tm.get("execMs") or 0.0)
                    in_page_settle_ms += float(tm.get("settleMs") or 0.0)
                    in_page_snap_ms += float(tm.get("snapMs") or 0.0)
                    in_page_total_js_ms += float(tm.get("totalInPageMs") or 0.0)
                for esc_item in (r.get("escalations") or []):
                    if isinstance(esc_item, dict) and esc_item.get("reason"):
                        reason = str(esc_item["reason"])
                        if reason == "eval_csp" and esc_item.get("cached"):
                            reason = "eval_csp_cached"
                        all_escalations.append(reason)

        active_steps = [
            s for s in steps if (s.get("op") or s.get("action")) != "close"
        ]
        recordable_steps = [
            s
            for s in active_steps
            if str(s.get("op") or s.get("action") or "").lower()
            not in (
                "snapshot",
                "diff",
                "webmcp",
                "calltool",
                "screenshot",
                "viewport",
                "setviewport",
                "resetviewport",
                "errors",
                "clearerrors",
            )
        ]
        if recordable_steps:
            prev_rec = self._last_tab_steps.setdefault(tab_id, [])
            prev_rec.extend(recordable_steps)
            if len(prev_rec) > 40:
                del prev_rec[:-40]

        virtual_tools = self.get_macros_for_url(sess.last_url)

        full_page = bool(req.get("fullPage", False))
        has_mutating_steps = any(
            str(s.get("op") or s.get("action") or "").lower()
            not in ("wait", "sleep", "delay", "settle", "waitfor", "wait_for", "waituntil", "text", "readtext", "readmain", "errors", "clearerrors")
            for s in active_steps
        )

        if req.get("ocr") is not False and (
            req.get("ocr") is True
            or (has_mutating_steps and not sess._ocr_checked and not sess._ocr_populated)
        ):
            t_ocr0 = time.perf_counter()
            sess._ocr_checked = True
            try:
                has_canvas = bool(
                    sess._raw_page.evaluate("() => document.querySelectorAll('canvas').length > 0")
                )
            except Exception:
                has_canvas = False
            if has_canvas:
                self._sync_canvas_ocr(sess)
            ocr_ms += (time.perf_counter() - t_ocr0) * 1000.0

        meta_lines: list[str] = []
        boundary_indices = [
            idx
            for idx, s in enumerate(active_steps)
            if (
                s.get("files")
                or str(s.get("op") or s.get("action") or "").lower()
                in (
                    "upload",
                    "goto",
                    "navigate",
                    "open",
                    "screenshot",
                    "viewport",
                    "setviewport",
                    "resetviewport",
                    "errors",
                    "clearerrors",
                )
                or (
                    str(s.get("op") or s.get("action") or "").lower()
                    in ("wait", "sleep", "delay", "settle")
                    and int(s.get("ms") or 0) >= 80
                )
            )
        ]

        navigated_in_batch = False
        viewport_changed_in_batch = False
        if boundary_indices:
            last_idx = 0
            combined_errors: list[str] = []
            combined_evals: list[str] = []
            res: dict[str, Any] = {}
            had_pre_on_current_page = False
            initial_batch_url = str(getattr(sess._raw_page, "url", "") or sess.last_url or "")
            for b_idx in boundary_indices:
                if is_user_stopped():
                    return USER_STOPPED_ERROR
                b_step = active_steps[b_idx]
                b_op = str(b_step.get("op") or b_step.get("action") or "").lower()
                if b_op in ("wait", "sleep", "delay", "settle"):
                    pre_steps = active_steps[last_idx:b_idx]
                    if pre_steps:
                        t_ip0 = time.perf_counter()
                        res = self._eval_batch_handling_navigation(
                            sess,
                            {
                                "agent": agent_id,
                                "steps": pre_steps,
                                "virtualTools": virtual_tools,
                                "query": req.get("query") or "",
                                "diff": req.get("diff"),
                                "fullTable": req.get("fullTable", False),
                                "fullPage": full_page,
                                "settle": False,
                                "skipSnapshot": True,
                            },
                            tab_id=tab_id,
                        )
                        _accum_in_page(res, (time.perf_counter() - t_ip0) * 1000.0)
                        t_esc0 = time.perf_counter()
                        self._handle_eval_escalations(
                            sess,
                            res,
                            agent_id=agent_id,
                            query=req.get("query") or "",
                            full_table=bool(req.get("fullTable", False)),
                            full_page=full_page,
                            skip_snapshot=True,
                        )
                        escalation_ms += (time.perf_counter() - t_esc0) * 1000.0
                        combined_errors.extend(res.get("errors") or [])
                        combined_evals.extend(res.get("evalOutputs") or [])
                        if res.get("didNavigate"):
                            navigated_in_batch = True
                            had_pre_on_current_page = False
                        else:
                            had_pre_on_current_page = True
                    wait_ms = int(b_step.get("ms") or 30)
                    t_wait0 = time.perf_counter()
                    slept = interruptible_sleep(wait_ms / 1000.0)
                    fixed_wait_ms += (time.perf_counter() - t_wait0) * 1000.0
                    if not slept:
                        return USER_STOPPED_ERROR
                    if not sess.is_extension and not sess._raw_page.is_closed():
                        try:
                            sess._raw_page.wait_for_load_state("domcontentloaded", timeout=5000)
                        except _BROWSER_OPS_ERRORS:
                            pass
                        cur_u = str(getattr(sess._raw_page, "url", "") or "")
                        if cur_u and cur_u != initial_batch_url:
                            sess.last_url = cur_u
                            self._tab_last_urls[tab_id] = cur_u
                            if not _same_origin_and_path(initial_batch_url, cur_u):
                                navigated_in_batch = True
                                had_pre_on_current_page = False
                                sess._focus_emulated = False
                                sess._runtime_ensured = False
                                sess._ocr_checked = False
                        sess.enable_background_focus_emulation()
                        sess.ensure_runtime()
                elif b_op in ("goto", "navigate", "open"):
                    pre_steps = active_steps[last_idx:b_idx]
                    if pre_steps:
                        t_ip0 = time.perf_counter()
                        res = self._eval_batch_handling_navigation(
                            sess,
                            {
                                "agent": agent_id,
                                "steps": pre_steps,
                                "virtualTools": virtual_tools,
                                "query": req.get("query") or "",
                                "diff": req.get("diff"),
                                "fullTable": req.get("fullTable", False),
                                "fullPage": full_page,
                                "settle": True,
                                "skipSnapshot": True,
                            },
                            tab_id=tab_id,
                        )
                        _accum_in_page(res, (time.perf_counter() - t_ip0) * 1000.0)
                        t_esc0 = time.perf_counter()
                        self._handle_eval_escalations(
                            sess,
                            res,
                            agent_id=agent_id,
                            query=req.get("query") or "",
                            full_table=bool(req.get("fullTable", False)),
                            full_page=full_page,
                            skip_snapshot=True,
                        )
                        escalation_ms += (time.perf_counter() - t_esc0) * 1000.0
                        combined_errors.extend(res.get("errors") or [])
                        combined_evals.extend(res.get("evalOutputs") or [])
                    raw_nav_url = str(b_step.get("url") or b_step.get("value") or "").strip()
                    nav_url = _normalize_nav_url(raw_nav_url) if raw_nav_url else ""
                    if nav_url and nav_url != sess._raw_page.url:
                        t_nav0 = time.perf_counter()
                        if not sess.is_extension and not self._is_cdp_chrome and mode == "chrome" and self._is_external_url(nav_url):
                            self._should_use_extension_bridge(mode, require_extension=require_ext, url=nav_url)
                        sess._raw_page.goto(nav_url, wait_until="domcontentloaded", timeout=15000)
                        sess.last_url = str(getattr(sess._raw_page, "url", "") or nav_url)
                        self._tab_last_urls[tab_id] = sess.last_url
                        sess._focus_emulated = False
                        sess._runtime_ensured = False
                        sess._ocr_checked = False
                        sess._ocr_populated = False
                        sess.enable_background_focus_emulation()
                        sess.ensure_runtime()
                        tab_attach_ms += (time.perf_counter() - t_nav0) * 1000.0
                        virtual_tools = self.get_macros_for_url(sess.last_url)
                        had_pre_on_current_page = False
                        navigated_in_batch = True
                elif b_op in ("screenshot", "viewport", "setviewport", "resetviewport", "errors", "clearerrors"):
                    pre_steps = active_steps[last_idx:b_idx]
                    if pre_steps:
                        t_ip0 = time.perf_counter()
                        res = self._eval_batch_handling_navigation(
                            sess,
                            {
                                "agent": agent_id,
                                "steps": pre_steps,
                                "virtualTools": virtual_tools,
                                "query": req.get("query") or "",
                                "diff": req.get("diff"),
                                "fullTable": req.get("fullTable", False),
                                "fullPage": full_page,
                                "settle": True,
                                "skipSnapshot": True,
                            },
                            tab_id=tab_id,
                        )
                        _accum_in_page(res, (time.perf_counter() - t_ip0) * 1000.0)
                        t_esc0 = time.perf_counter()
                        self._handle_eval_escalations(
                            sess,
                            res,
                            agent_id=agent_id,
                            query=req.get("query") or "",
                            full_table=bool(req.get("fullTable", False)),
                            full_page=full_page,
                            skip_snapshot=True,
                        )
                        escalation_ms += (time.perf_counter() - t_esc0) * 1000.0
                        combined_errors.extend(res.get("errors") or [])
                        combined_evals.extend(res.get("evalOutputs") or [])
                        if res.get("didNavigate"):
                            navigated_in_batch = True
                            had_pre_on_current_page = False
                        else:
                            had_pre_on_current_page = True
                    t_esc0 = time.perf_counter()
                    try:
                        if b_op == "screenshot":
                            raw_shot_path = b_step.get("path") or b_step.get("file") or b_step.get("value")
                            shot_fp = bool(b_step.get("fullPage") or b_step.get("full_page"))
                            saved_p, sw, sh = sess.capture_screenshot(
                                path=str(raw_shot_path) if raw_shot_path else None,
                                full_page=shot_fp,
                            )
                            meta_lines.append(f'# screenshot path="{saved_p}" size={sw}x{sh}')
                        elif b_op in ("viewport", "setviewport", "resetviewport"):
                            vw_new, vh_new = sess.set_viewport(
                                width=b_step.get("width"),
                                height=b_step.get("height"),
                                scale=float(b_step.get("scale") or b_step.get("deviceScaleFactor") or 1.0),
                                mobile=b_step.get("mobile"),
                                reset=bool(b_step.get("reset") or b_op == "resetviewport"),
                            )
                            meta_lines.append(f"# viewport size={vw_new}x{vh_new}")
                            viewport_changed_in_batch = True
                        elif b_op == "errors":
                            combined_evals.append(
                                json.dumps(sess.get_diagnostics(clear=bool(b_step.get("clear"))), ensure_ascii=False)
                            )
                        elif b_op == "clearerrors":
                            sess.clear_diagnostics()
                    except Exception as host_exc:
                        combined_errors.append(f"step {b_idx + 1} ({b_op}): {host_exc}")
                    escalation_ms += (time.perf_counter() - t_esc0) * 1000.0
                else:
                    pre_steps = active_steps[last_idx : b_idx + 1]
                    t_ip0 = time.perf_counter()
                    res = self._eval_batch_handling_navigation(
                        sess,
                        {
                            "agent": agent_id,
                            "steps": pre_steps,
                            "virtualTools": virtual_tools,
                            "query": req.get("query") or "",
                            "diff": req.get("diff"),
                            "fullTable": req.get("fullTable", False),
                            "fullPage": full_page,
                            "settle": False,
                            "skipSnapshot": True,
                        },
                        tab_id=tab_id,
                    )
                    _accum_in_page(res, (time.perf_counter() - t_ip0) * 1000.0)
                    t_esc0 = time.perf_counter()
                    self._handle_eval_escalations(
                        sess,
                        res,
                        agent_id=agent_id,
                        query=req.get("query") or "",
                        full_table=bool(req.get("fullTable", False)),
                        full_page=full_page,
                        skip_snapshot=True,
                    )
                    combined_errors.extend(res.get("errors") or [])
                    combined_evals.extend(res.get("evalOutputs") or [])
                    files = b_step.get("files") or b_step.get("value")
                    if files:
                        file_list = [files] if isinstance(files, str) else list(files)
                        file_escs = [
                            e
                            for e in (res.get("escalations") or [])
                            if isinstance(e, dict) and e.get("reason") == "file_input"
                        ]
                        last_esc = file_escs[-1] if file_escs else {}
                        esc_uid = last_esc.get("uid") if isinstance(last_esc, dict) else None
                        already_handled = bool(last_esc.get("handled")) if isinstance(last_esc, dict) else False
                        if not already_handled:
                            sel = f'[data-fmu-upload-uid="{esc_uid}"]' if esc_uid else "input[type='file']"
                            try:
                                if sess.is_extension:
                                    sess._raw_page.set_input_files(sel, file_list)
                                else:
                                    if esc_uid and sess._raw_page.locator(sel).count() == 0:
                                        sel = "input[type='file']"
                                    sess._raw_page.set_input_files(sel, file_list)
                            except Exception as upload_exc:
                                combined_errors.append(str(upload_exc))
                    escalation_ms += (time.perf_counter() - t_esc0) * 1000.0
                    had_pre_on_current_page = True
                last_idx = b_idx + 1

            if is_user_stopped():
                return USER_STOPPED_ERROR
            cur_post_url = str(getattr(sess._raw_page, "url", "") or sess.last_url or "")
            if (
                cur_post_url
                and initial_batch_url
                and cur_post_url != initial_batch_url
                and not _same_origin_and_path(initial_batch_url, cur_post_url)
            ):
                navigated_in_batch = True
            post_steps = active_steps[last_idx:]
            t_ip0 = time.perf_counter()
            res = self._eval_batch_handling_navigation(
                sess,
                {
                    "agent": agent_id,
                    "steps": post_steps,
                    "virtualTools": self.get_macros_for_url(sess.last_url) if navigated_in_batch else virtual_tools,
                    "query": req.get("query") or "",
                    "diff": False if (navigated_in_batch or (viewport_changed_in_batch and req.get("diff") is None)) else req.get("diff"),
                    "fullTable": req.get("fullTable", False),
                    "fullPage": full_page,
                    "settle": req.get("settle", True),
                    "hadPreSteps": had_pre_on_current_page or bool(post_steps),
                },
                tab_id=tab_id,
            )
            _accum_in_page(res, (time.perf_counter() - t_ip0) * 1000.0)
            if res.get("didNavigate"):
                navigated_in_batch = True
            t_esc0 = time.perf_counter()
            self._handle_eval_escalations(
                sess,
                res,
                agent_id=agent_id,
                query=req.get("query") or "",
                full_table=bool(req.get("fullTable", False)),
                full_page=full_page,
                skip_snapshot=False,
            )
            escalation_ms += (time.perf_counter() - t_esc0) * 1000.0
            res["errors"] = combined_errors + (res.get("errors") or [])
            res["evalOutputs"] = combined_evals + (res.get("evalOutputs") or [])
        else:
            if is_user_stopped():
                return USER_STOPPED_ERROR
            t_ip0 = time.perf_counter()
            res = self._eval_batch_handling_navigation(
                sess,
                {
                    "agent": agent_id,
                    "steps": active_steps,
                    "virtualTools": virtual_tools,
                    "query": req.get("query") or "",
                    "diff": req.get("diff"),
                    "fullTable": req.get("fullTable", False),
                    "fullPage": full_page,
                    "settle": req.get("settle", True),
                },
                tab_id=tab_id,
            )
            _accum_in_page(res, (time.perf_counter() - t_ip0) * 1000.0)
            if res.get("didNavigate"):
                navigated_in_batch = True
            t_esc0 = time.perf_counter()
            self._handle_eval_escalations(
                sess,
                res,
                agent_id=agent_id,
                query=req.get("query") or "",
                full_table=bool(req.get("fullTable", False)),
                full_page=full_page,
                skip_snapshot=False,
            )
            escalation_ms += (time.perf_counter() - t_esc0) * 1000.0

        if is_user_stopped():
            return USER_STOPPED_ERROR
        sess._ocr_checked = True

        if (
            req.get("ocr") is not False
            and res.get("canvases")
            and (has_mutating_steps or not sess._ocr_populated or req.get("ocr") is True)
        ):
            t_ocr1 = time.perf_counter()
            new_ocr = self._sync_canvas_ocr(sess, res.get("canvases"))
            try:
                refreshed = sess._raw_page.evaluate(
                    "opts => window.__fmu.refreshAfterOcr(opts)",
                    {
                        "agent": agent_id,
                        "ocrTargets": new_ocr,
                        "query": req.get("query") or "",
                        "fullTable": req.get("fullTable", False),
                        "fullPage": full_page,
                        "useDiff": bool(res.get("useDiff")),
                    },
                )
                if isinstance(refreshed, dict):
                    res["crc"] = refreshed.get("crc", res.get("crc"))
                    res["seq"] = refreshed.get("seq", res.get("seq"))
                    res["count"] = refreshed.get("count", res.get("count"))
                    res["lines"] = refreshed.get("lines", res.get("lines"))
            except _BROWSER_OPS_ERRORS:
                pass
            ocr_ms += (time.perf_counter() - t_ocr1) * 1000.0

        if sess._raw_page and not sess._raw_page.is_closed() and sess._raw_page.url:
            sess.last_url = sess._raw_page.url
            self._tab_last_urls[tab_id] = sess._raw_page.url
        if isinstance(res, dict) and "title" in res:
            sess.last_title = str(res.get("title") or "")
            self._tab_last_titles[tab_id] = sess.last_title

        cur_info = res.get("cursor") or {}
        if cur_info and cur_info.get("uid"):
            self.cursors[agent_id] = {
                "agent": agent_id,
                "surface": "web",
                "target": sess.tab_id,
                "group": sess.group_name,
                "color": sess.group_color,
                "uid": cur_info.get("uid", ""),
                "label": cur_info.get("label", ""),
                "x": int(cur_info.get("x", 0)),
                "y": int(cur_info.get("y", 0)),
                "op": cur_info.get("op", ""),
            }

        if any((s.get("op") or s.get("action")) == "close" for s in steps):
            self._close_tab_inner(tab_id)

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        has_move = any((s.get("op") or s.get("action") or "").lower() in ("move", "hover", "cursor", "point") for s in active_steps)
        header = f'# web tab={sess.tab_id} group="{sess.group_name}"'
        if sess.is_extension:
            header += ' bridge="mv3"'
            if getattr(sess._raw_page, "window_id", None) is not None:
                header += f' window={sess._raw_page.window_id}'
        if agent_id != "default":
            header += f' agent="{agent_id}"'
        if (agent_id != "default" or has_move) and cur_info.get("uid"):
            header += f' cursor={cur_info["uid"]}@({cur_info.get("x", 0)},{cur_info.get("y", 0)})'
        header += (
            f' els={res.get("count", 0)} crc={res.get("crc", "")} '
            f'seq={res.get("seq", 0)} ms={elapsed_ms:.1f}'
        )
        if req.get("bounds"):
            header += f' vp={res.get("vw") or sess.last_vw}x{res.get("vh") or sess.last_vh}'
        if res.get("webMcpTools"):
            header += f' webmcp={json.dumps(res["webMcpTools"])}'
        if (agent_id not in sess._documented_agents and not sess.documented) or req.get("showDocs"):
            header += "\n# api: click(uid|text), fill(uid,val), select(uid,val), move(uid|text), press(key), waitFor(text|sel), wait(ms), text(sel?), eval(code), screenshot(path?,fullPage?), setViewport(w,h), layoutAudit(widths?), errors(), callTool(name,args), saveMacro(name,steps), diff(), snapshot(full=True)"
            sess.documented = True
            sess._documented_agents.add(agent_id)

        out = [header]
        if meta_lines:
            out.extend(meta_lines)
        if res.get("errors"):
            out.append("! errors: " + "; ".join(res["errors"]))
        new_diags = sess.consume_unreported_diagnostics()
        if new_diags:
            diag_preview = "; ".join(f"[{d['kind']}] {d['message']}" for d in new_diags[:3])
            if len(new_diags) > 3:
                diag_preview += f" (+{len(new_diags) - 3} more)"
            out.append(f"! page_errors({len(new_diags)}): {diag_preview}")
        cur_url_low = str(sess.last_url or "").lower()
        is_dev_url = any(
            tok in cur_url_low
            for tok in ("://localhost", "://127.0.0.1", "://[::1]", ".local/", ".local:", ".localhost", "file://")
        )
        if sess._custom_viewport or viewport_changed_in_batch or is_dev_url:
            layout_issues = [str(x) for x in (res.get("layoutIssues") or []) if x]
            if layout_issues:
                sig = f"{sess.last_vw}x{sess.last_vh}|" + "|".join(layout_issues)
                if viewport_changed_in_batch or navigated_in_batch or sig != sess._last_layout_sig:
                    sess._last_layout_sig = sig
                    l_prev = "; ".join(layout_issues[:3])
                    if len(layout_issues) > 3:
                        l_prev += f" (+{len(layout_issues) - 3} more)"
                    out.append(f"! layout_issues({len(layout_issues)}): {l_prev}")
            else:
                sess._last_layout_sig = ""
        any_eval_truncated = False
        if res.get("evalOutputs"):
            eval_max_overrides = [
                int(s["maxChars"]) if isinstance(s, dict) and s.get("maxChars") is not None else None
                for s in active_steps
                if isinstance(s, dict) and str(s.get("op") or s.get("action") or "").lower() in ("eval", "evaluate")
            ]
            for idx_ev, ev in enumerate(res["evalOutputs"]):
                ov = eval_max_overrides[idx_ev] if idx_ev < len(eval_max_overrides) else None
                fmt_ev, was_trunc = _format_eval_output_with_flag(ev, max_chars_override=ov)
                if was_trunc:
                    any_eval_truncated = True
                out.append(f"> {fmt_ev}")
        res_lines = res.get("lines") or []
        if req.get("bounds"):
            res_lines = _append_uid_boxes(sess, res_lines)
        out.extend(res_lines)
        final_out = "\n".join(out)

        if res.get("unchanged") or any(isinstance(l, str) and l.startswith("= unchanged") for l in res_lines):
            mode_out = "unchanged"
        elif res.get("useDiff"):
            mode_out = "diff"
        else:
            mode_out = "snapshot"

        bridge_kind = "mv3" if sess.is_extension else ("cdp" if self._is_cdp_chrome else "playwright")
        bridge_overhead_ms = max(0.0, in_page_wall_ms - in_page_total_js_ms - nav_wait_ms)
        errs_list = res.get("errors") or []
        perf_payload: dict[str, Any] = {
            "surface": "web",
            "bridge": bridge_kind,
            "agent": agent_id,
            "session": sess.tab_id,
            "target": sanitize_url_target(sess.last_url),
            "ops": [
                str(s.get("op") or s.get("action") or "").lower()
                for s in active_steps
                if (s.get("op") or s.get("action"))
            ],
            "step_count": len(active_steps),
            "els": int(res.get("count") or 0),
            "mode_out": mode_out,
            "out_chars": len(final_out),
            "est_tokens": max(1, (len(final_out) + 3) // 4),
            "escalations": all_escalations,
            "error_count": len(errs_list),
            "errors": [sanitize_error(e) for e in errs_list[:3]],
            "timings_ms": {
                "total": round(elapsed_ms, 2),
                "tab_attach": round(tab_attach_ms, 2),
                "bridge_overhead": round(bridge_overhead_ms, 2),
                "nav_wait": round(nav_wait_ms, 2),
                "in_page_exec": round(in_page_exec_ms, 2),
                "in_page_settle": round(in_page_settle_ms, 2),
                "in_page_snap": round(in_page_snap_ms, 2),
                "escalation": round(escalation_ms, 2),
                "ocr": round(ocr_ms, 2),
                "fixed_wait": round(fixed_wait_ms, 2),
            },
        }
        if any_eval_truncated:
            perf_payload["truncated_eval"] = True
        record_perf_event(perf_payload)
        is_headless_tab = not sess.is_extension and not self._is_cdp_chrome
        preview_file: str | None = sess.last_preview_path
        if _preview_enabled() and sess in self._tabs.values():
            if sess.is_extension and isinstance(res.get("preview"), dict):
                preview_file = self._apply_inline_preview(
                    sess, res["preview"], url_hint=sess.last_url, push_frame=False
                )

        final_op = (
            str(cur_info.get("op") or "")
            or (
                str((active_steps[-1].get("op") or active_steps[-1].get("action") or "")).strip().lower()
                if active_steps and isinstance(active_steps[-1], dict)
                else mb_op
            )
        )
        vw = int(res.get("vw") or sess.last_vw or 1280)
        vh = int(res.get("vh") or sess.last_vh or 800)
        sess.last_vw = vw
        sess.last_vh = vh

        target_short = _short_menubar_target(sess.last_url)
        done_payload.update(
            {
                "surface": "web",
                "target": target_short,
                "op": final_op,
                "agent": agent_id,
                "step_count": len(active_steps),
                "cursor_x": int(cur_info["x"]) if cur_info and cur_info.get("x") is not None else None,
                "cursor_y": int(cur_info["y"]) if cur_info and cur_info.get("y") is not None else None,
                "cursor_label": str(cur_info.get("label") or cur_info.get("uid") or "") if cur_info else None,
                "win_w": vw,
                "win_h": vh,
                "preview_path": preview_file,
                "is_headless": is_headless_tab,
            }
        )
        if _preview_enabled() and not sess.is_extension and sess in self._tabs.values():
            self._schedule_async_preview(
                sess,
                target=target_short,
                op=final_op,
                cur_info=cur_info,
                is_headless=is_headless_tab,
            )
        return final_out

    def reset(self, agent: str | None = None) -> None:
        self._run_sync(lambda: self._reset_inner(agent))

    def _reset_inner(self, agent: str | None = None) -> None:
        self._cancel_pending_preview()
        if agent and agent not in ("default", "all"):
            client, sub = self.clients.lookup(agent)
            if client is not None and not sub:
                self._close_agent_tabs_inner(agent)
                for tid in [k for k in self._tabs if self.in_scope(k, agent)]:
                    self._tabs.pop(tid, None)
                    self._tab_last_urls.pop(tid, None)
                    self._tab_last_titles.pop(tid, None)
            prefix = f"{agent}:"
            for tid in [k for k in self._tabs if k.startswith(prefix) or k == agent]:
                self._close_tab_inner(tid)
            for sess in list(self._tabs.values()):
                try:
                    sess._raw_page.evaluate(
                        "aid => window.__fmu && window.__fmu.resetAgent && window.__fmu.resetAgent(aid)",
                        agent,
                    )
                    sess._documented_agents.discard(agent)
                except _BROWSER_OPS_ERRORS:
                    pass
            self.cursors.pop(agent, None)
            return
        for sess in list(self._tabs.values()):
            try:
                sess._raw_page.close()
            except _BROWSER_OPS_ERRORS:
                pass
        self._tabs.clear()
        if self._context is not None and not self._is_cdp_chrome:
            try:
                self._context.clear_cookies()
            except _BROWSER_OPS_ERRORS:
                pass
        self._tab_last_urls.clear()
        self._tab_last_titles.clear()
        self._last_tab_steps.clear()
        self.cursors.clear()
        self._next_tab_num = 1

    def shutdown(self) -> None:
        self._run_sync(self._shutdown_inner)

    def _shutdown_inner(self) -> None:
        self._cancel_pending_preview()
        for sess in list(self._tabs.values()):
            if not sess.is_extension:
                try:
                    sess._raw_page.close()
                except _BROWSER_OPS_ERRORS:
                    pass
        self._tabs.clear()
        self._evict_dead_browser()
        with self._bridge_lock:
            if self._ext_bridge_inst is not None:
                self._ext_bridge_inst.shutdown()
                self._ext_bridge_inst = None
        flush_telemetry()


