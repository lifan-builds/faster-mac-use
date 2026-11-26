#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Faster Mac Use; Section 7 attribution terms in ATTRIBUTION.md.
"""Adversarial edge-case regression tests synthesized with gpt-5.4-pro.

Covers:
  (a) JS/meta/history client-side redirects & strict-CSP pages (incl. Trusted Types)
  (b) Tricky cua_repl Python/JS transpiler inputs (nested quotes, comments, subscripts,
      augmented assignments, safe vs blocked imports, raw JS callbacks & IIFEs)
  (c) Sibling OOPIF disambiguation & discarded/unloaded-tab edge cases (MV3 & Playwright)
"""

from __future__ import annotations

import http.server
import json
import os
from pathlib import Path
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
import unittest
from unittest import mock

os.environ.setdefault("FMU_RUNTIME_SOCK", "/tmp/fmu-test-adversarial.sock")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fmu.cua_runtime import FasterMacUseRuntime  # noqa: E402


def _make_mock_repl_runtime() -> tuple[FasterMacUseRuntime, list[str]]:
    rt = FasterMacUseRuntime.__new__(FasterMacUseRuntime)
    from concurrent.futures import ThreadPoolExecutor

    rt._is_mcp_stdio = False
    rt._threading = threading
    rt._global_lock = threading.RLock()
    rt._pool = ThreadPoolExecutor(max_workers=2)
    rt.bounds_agents = set()
    rt._agents = {}
    rt.mac = mock.Mock()
    rt.browser = mock.Mock()
    rt.browser._tabs = {}
    rt.browser._tab_last_urls = {}
    rt.browser._tab_last_titles = {}
    rt.browser.extension_connected = False
    rt.browser.attach_existing_tab.return_value = None
    rt.browser.group_title.return_value = "Agent"
    rt.browser.clients.lookup.return_value = (None, None)
    rt.browser._get_agent_color.return_value = "blue"
    rt.browser.in_scope.return_value = True
    rt.browser._is_session_alive.return_value = True
    rt.browser.execute_batch.return_value = '# web tab=t1 url="https://example.com"'

    eval_calls: list[str] = []

    def _fake_eval_value(**kwargs: Any) -> Any:
        code_arg = str(kwargs.get("code") or "")
        eval_calls.append(code_arg)
        return f"JS:{code_arg}"

    rt.browser.evaluate_value.side_effect = _fake_eval_value

    default_sess = rt._get_agent_session("default")
    rt.cua = default_sess.cua
    rt.context_tracker = default_sess.context_tracker
    rt._repl_env = default_sess.repl_env
    return rt, eval_calls


class _ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _start_route_server(
    routes: dict[str, dict[str, Any]],
) -> tuple[_ReusableTCPServer, int]:
    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            path_only = self.path.split("?", 1)[0]
            spec = routes.get(self.path) or routes.get(path_only)
            if spec is None:
                self.send_response(404)
                self.end_headers()
                return
            status = int(spec.get("status", 200))
            headers = dict(spec.get("headers") or {})
            body = str(spec.get("body", "")).encode("utf-8")
            self.send_response(status)
            if "Content-Type" not in headers and status == 200:
                headers["Content-Type"] = "text/html; charset=utf-8"
            for hk, hv in headers.items():
                self.send_header(hk, hv)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

    srv = _ReusableTCPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


class TestAdversarialRedirectsAndStrictCsp(unittest.TestCase):
    _log_tmp: tempfile.TemporaryDirectory[str]
    _port_guard: socket.socket | None = None
    rt: FasterMacUseRuntime

    @classmethod
    def setUpClass(cls) -> None:
        cls._log_tmp = tempfile.TemporaryDirectory(prefix="fmu-adv-logs-")
        os.environ["FMU_LOG_DIR"] = cls._log_tmp.name
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", 19222))
            s.listen(1)
            cls._port_guard = s
        except OSError:
            s.close()
            cls._port_guard = None
        cls.rt = FasterMacUseRuntime()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.rt.shutdown()
        if cls._port_guard is not None:
            try:
                cls._port_guard.close()
            except Exception:
                pass
        cls._log_tmp.cleanup()

    def setUp(self) -> None:
        self.rt.execute({"reset": True, "resume": True})

    def test_a1_client_side_redirects_meta_chain_pushstate_and_double_replacestate(self) -> None:
        cases = [
            {
                "id": "meta_refresh_uppercase_quoted_url",
                "routes": {
                    "/p": {
                        "status": 200,
                        "body": "<!doctype html><meta HTTP-EQUIV=\"REFRESH\" content=\"0; URL='/f?q=1#Q'\"><title>s</title>",
                    },
                    "/f": {"status": 200, "body": "<title>meta_ok</title><p>ok</p>"},
                },
                "start_path": "/p",
                "expected_url_substr": "/f?q=1#Q",
                "expected_title": "meta_ok",
            },
            {
                "id": "redirect_chain_302_meta_js_history",
                "routes": {
                    "/p": {"status": 302, "headers": {"Location": "/m"}, "body": ""},
                    "/m": {
                        "status": 200,
                        "body": "<!doctype html><meta http-equiv=refresh content='0;url=/j?m=1#mh'><title>m</title>",
                    },
                    "/j": {
                        "status": 200,
                        "body": "<!doctype html><script>location.replace('/r?j=1#k')</script>",
                    },
                    "/r": {
                        "status": 200,
                        "body": "<!doctype html><title>r</title><script>history.replaceState(null,'','/done?c=1#fin');document.title='chain_ok'</script>",
                    },
                },
                "start_path": "/p",
                "expected_url_substr": "/done?c=1#fin",
                "expected_title": "chain_ok",
            },
            {
                "id": "pushstate_unicode_title_query_fragment",
                "routes": {
                    "/p": {
                        "status": 200,
                        "body": "<!doctype html><title>x</title><script>history.pushState({},'ユニ✅','/u?q=%E2%9C%93#frag');document.title='ユニ✅'</script>",
                    }
                },
                "start_path": "/p",
                "expected_url_substr": "/u?q=%E2%9C%93#frag",
                "expected_title": "ユニ✅",
            },
            {
                "id": "double_replacestate_last_write_wins",
                "routes": {
                    "/p": {
                        "status": 200,
                        "body": "<!doctype html><title>0</title><script>history.replaceState(null,'','/a?x=1#1');document.title='mid';history.replaceState(null,'','/b?x=2#2');document.title='final'</script>",
                    }
                },
                "start_path": "/p",
                "expected_url_substr": "/b?x=2#2",
                "expected_title": "final",
            },
        ]

        for idx, case in enumerate(cases):
            with self.subTest(case=case["id"]):
                srv, port = _start_route_server(case["routes"])
                try:
                    start_url = f"http://127.0.0.1:{port}{case['start_path']}"
                    var = f"t_{idx}"
                    self.rt.execute({"code": f"{var} = cua.createBrowserTab('headless', {start_url!r})"})
                    deadline = time.monotonic() + 4.0
                    cur_url = ""
                    cur_title = ""
                    while time.monotonic() < deadline:
                        cur_url = self.rt.execute({"code": f"{var}.url()"})
                        cur_title = self.rt.execute({"code": f"{var}.title()"})
                        if case["expected_url_substr"] in cur_url and cur_title == case["expected_title"]:
                            break
                        time.sleep(0.03)
                    self.assertIn(case["expected_url_substr"], cur_url)
                    self.assertEqual(cur_title, case["expected_title"])
                finally:
                    srv.shutdown()
                    srv.server_close()

    def test_a1_mv3_pending_url_over_about_blank(self) -> None:
        """MV3 getTabInfo prefers pendingUrl over url='about:blank' before commit."""
        rt = FasterMacUseRuntime()
        bridge = rt.browser.ensure_bridge()
        bg_js = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
        bg_js_patched = bg_js.replace("ws://127.0.0.1:19222/ext", f"ws://127.0.0.1:{bridge.port}/ext")
        target_url = "https://shop.example.com/target?mv3=1#pending"

        harness = f"""
const state = {{
  tabs: [
    {{
      id: 810,
      windowId: 88,
      groupId: 20,
      active: false,
      discarded: false,
      status: "loading",
      url: "about:blank",
      pendingUrl: {json.dumps(target_url)},
      title: ""
    }}
  ],
  groups: [{{ id: 20, windowId: 88, title: "Agent", collapsed: false }}],
  storage: {{}}
}};
globalThis.chrome = {{
  storage: {{ local: {{ get: async (k) => ({{ [k]: state.storage[k] }}), set: async (o) => Object.assign(state.storage, o) }} }},
  tabs: {{
    query: async () => state.tabs,
    get: async (id) => {{
      const f = state.tabs.find(t => t.id === id);
      if (!f) throw new Error("No tab: " + id);
      return f;
    }},
    update: async (id, opts) => {{
      const f = state.tabs.find(t => t.id === id);
      if (f && opts.url) {{ f.url = opts.url; f.pendingUrl = ""; f.status = "complete"; }}
      return f;
    }},
    group: async () => 20,
    onCreated: {{ addListener: () => {{}} }},
    onUpdated: {{ addListener: () => {{}} }},
    onRemoved: {{ addListener: () => {{}} }}
  }},
  tabGroups: {{
    get: async (gid) => state.groups.find(g => g.id === gid),
    query: async () => state.groups,
    update: async (gid, opts) => Object.assign(state.groups[0], opts)
  }},
  windows: {{ getAll: async () => [{{ id: 88, focused: false }}] }},
  debugger: {{
    attach: async () => ({{}}),
    detach: async () => ({{}}),
    sendCommand: async (_t, method, params) => {{
      if (method === "Runtime.evaluate") {{
        const expr = String((params && params.expression) || "");
        if (expr.includes("FMU_RUNTIME_VERSION")) return {{ result: {{ value: true }} }};
        if (expr.startsWith("window.__fmu.executeBatch(")) {{
          return {{
            result: {{
              value: {{
                url: state.tabs[0].url, title: state.tabs[0].title,
                vw: 1280, vh: 800, crc: "aa", seq: 1, count: 0,
                useDiff: false, unchanged: false, webMcpTools: [], canvases: [],
                errors: [], evalOutputs: [], rawEvalValues: [], escalations: [], lines: []
              }}
            }}
          }};
        }}
        return {{ result: {{ value: true }} }};
      }}
      return {{}};
    }},
    onDetach: {{ addListener: () => {{}} }}
  }},
  alarms: {{ create: () => {{}}, onAlarm: {{ addListener: () => {{}} }} }},
  runtime: {{ onInstalled: {{ addListener: () => {{}} }}, onStartup: {{ addListener: () => {{}} }} }}
}};
{bg_js_patched}
setInterval(() => {{}}, 1000);
"""
        node_proc = subprocess.Popen(["node", "-e", harness], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(bridge.wait_connected(timeout=3.0))
            info = bridge.call_rpc("getTabInfo", {"tabId": 810}, timeout=3.0)
            self.assertTrue(info["found"])
            self.assertEqual(info["url"], target_url)
            self.assertEqual(info["pendingUrl"], target_url)
            self.assertEqual(info["title"], "")

            live_u = rt.execute({"code": "t = cua.getTab(chromeTabId=810)\nt.url()"})
            self.assertEqual(live_u, target_url)
        finally:
            node_proc.terminate()
            try:
                node_proc.wait(timeout=2.0)
            except Exception:
                node_proc.kill()
            if node_proc.stdout:
                node_proc.stdout.close()
            if node_proc.stderr:
                node_proc.stderr.close()
            rt.shutdown()

    def test_a2_strict_csp_variants_including_trusted_types_and_host_cache(self) -> None:
        csp_cases = [
            {
                "id": "default_none_script_none",
                "csp_header": "default-src 'none'; script-src 'none'; object-src 'none'; base-uri 'none'",
                "html": "<!doctype html><title>c1</title><div id='a'>sealed</div>",
                "eval_expressions": [
                    "document.title",
                    "document.getElementById('a').textContent",
                    "(() => { window.__c1 = (window.__c1 || 0) + 1; return String(window.__c1); })()",
                ],
                "expected_results": ["c1", "sealed", "1"],
            },
            {
                "id": "default_self_no_unsafe_eval",
                "csp_header": "default-src 'self'; object-src 'none'; base-uri 'none'",
                "html": "<!doctype html><div id='self'>ok</div><script>window.__inline='ran'</script>",
                "eval_expressions": [
                    "String(window.__inline === undefined)",
                    "(() => { const el = document.getElementById('self'); el.dataset.v = 'set'; return el.dataset.v; })()",
                    "document.getElementById('self').textContent",
                ],
                "expected_results": ["true", "set", "ok"],
            },
            {
                "id": "nonce_strict_dynamic",
                "csp_header": "script-src 'nonce-abc123' 'strict-dynamic'; object-src 'none'; base-uri 'none'",
                "html": "<!doctype html><div id='n'>nonce</div><script nonce='abc123'>window.__nonce='yes'</script><script>window.__blocked='no'</script>",
                "eval_expressions": [
                    "window.__nonce",
                    "String(window.__blocked === undefined)",
                    "document.getElementById('n').textContent",
                ],
                "expected_results": ["yes", "true", "nonce"],
            },
            {
                "id": "trusted_types_script_required",
                "csp_header": "script-src 'self'; trusted-types p; require-trusted-types-for 'script'; object-src 'none'; base-uri 'none'",
                "html": "<!doctype html><div id='tt'>tt</div>",
                "eval_expressions": [
                    "(() => { try { document.body.innerHTML = '<p id=bad>bad</p>'; return 'open'; } catch (e) { return e.name; } })()",
                    "(() => { const p = trustedTypes.createPolicy('p', { createHTML: s => s }); document.body.innerHTML = p.createHTML('<p id=ok>ok</p>'); return document.getElementById('ok').textContent; })()",
                ],
                "expected_results": ["TypeError", "ok"],
            },
        ]

        routes: dict[str, dict[str, Any]] = {}
        for case in csp_cases:
            routes[f"/{case['id']}"] = {
                "status": 200,
                "headers": {
                    "Content-Type": "text/html; charset=utf-8",
                    "Content-Security-Policy": case["csp_header"],
                },
                "body": case["html"],
            }
        routes["/cached_batch"] = {
            "status": 200,
            "headers": {
                "Content-Type": "text/html; charset=utf-8",
                "Content-Security-Policy": "default-src 'none'; script-src 'none'; object-src 'none'; base-uri 'none'",
            },
            "body": "<!doctype html><div id='b'>cache</div>",
        }

        srv, port = _start_route_server(routes)
        try:
            # 1. Live Chromium verification: CSP/nonce/Trusted-Types are enforced on page scripts & DOM sinks,
            #    while fmu evalValue() returns exact results across all four strict-CSP variants.
            for case in csp_cases:
                with self.subTest(case=case["id"]):
                    url = f"http://127.0.0.1:{port}/{case['id']}"
                    self.rt.execute({"code": f"ct = cua.createBrowserTab('headless', {url!r})"})
                    for expr, exp in zip(case["eval_expressions"], case["expected_results"]):
                        got = self.rt.execute({"code": f"ct.evalValue({expr!r})"})
                        self.assertEqual(got, exp)

            # 2. Verify IN_PAGE_RUNTIME_JS latches store.strictCsp on BOTH EvalError (unsafe-eval) AND
            #    Trusted Types TypeError ("TrustedScript"), caches host in _strict_csp_hosts, and
            #    pre-routes subsequent batches on the same host via CDP without re-invoking AsyncFunction.
            for err_kind, throw_js in (
                (
                    "eval_error_csp",
                    "throw new EvalError(\"Refused to evaluate a string as JavaScript because 'unsafe-eval' is not an allowed source of script in the following Content Security Policy directive\")",
                ),
                (
                    "trusted_types_type_error",
                    "throw new TypeError(\"Failed to construct 'AsyncFunction': This document requires 'TrustedScript' assignment.\")",
                ),
            ):
                with self.subTest(csp_escalation=err_kind):
                    self.rt.browser._strict_csp_hosts.clear()
                    cache_url = f"http://127.0.0.1:{port}/cached_batch"
                    self.rt.execute({"code": f"cb = cua.createBrowserTab('headless', {cache_url!r})"})
                    sess = self.rt.browser._tabs[self.rt.cua.handles[next(reversed(self.rt.cua.handles))].tab_id]
                    # Simulate Chrome MV3 strict-CSP / Trusted-Types blocking AsyncFunction constructor in-page
                    self.rt.browser._run_sync(
                        lambda: sess._raw_page.evaluate(
                            f"""() => {{
                                const AFProto = Object.getPrototypeOf(async function(){{}});
                                window.__origAFDesc = Object.getOwnPropertyDescriptor(AFProto, 'constructor');
                                Object.defineProperty(AFProto, 'constructor', {{
                                    value: function() {{ {throw_js}; }},
                                    configurable: true,
                                    writable: true
                                }});
                                window.__fmuStore.strictCsp = false;
                            }}"""
                        )
                    )
                    try:
                        r1 = self.rt.execute(
                            {"code": "cb.evalValue(\"(() => { window.__batch = (window.__batch || 0) + 1; return 'batch-' + window.__batch; })()\")"}
                        )
                        self.assertEqual(r1, "batch-1")
                        self.assertIn("127.0.0.1", self.rt.browser._strict_csp_hosts)
                        strict_latched = self.rt.browser._run_sync(
                            lambda: sess._raw_page.evaluate("() => Boolean(window.__fmuStore && window.__fmuStore.strictCsp)")
                        )
                        self.assertTrue(strict_latched)

                        # Batch 2 & 3 on same host use cached _strict_csp_hosts -> CDP direct without calling AsyncFunction
                        r2 = self.rt.execute(
                            {"code": "cb.evalValue(\"(() => { window.__batch = (window.__batch || 0) + 1; return 'batch-' + window.__batch; })()\")"}
                        )
                        self.assertEqual(r2, "batch-2")
                        r3 = self.rt.execute(
                            {"code": "cb.evalValue(\"document.getElementById('b').textContent + ':' + String(window.__batch)\")"}
                        )
                        self.assertEqual(r3, "cache:2")
                    finally:
                        self.rt.browser._run_sync(
                            lambda: sess._raw_page.evaluate(
                                "() => { if (window.__origAFDesc) Object.defineProperty(Object.getPrototypeOf(async function(){}), 'constructor', window.__origAFDesc); }"
                            )
                        )
                        self.rt.execute({"reset": True})
        finally:
            srv.shutdown()
            srv.server_close()


class TestAdversarialReplTranspiler(unittest.TestCase):
    def test_b1_nested_quotes_comments_and_iifes(self) -> None:
        cases = [
            # b1a: nested quotes & comments (gpt-5.4-pro + unescaped broken-quote variants)
            {
                "id": "broken_double_quote_attr_selector",
                "code": 't = cua.createBrowserTab("headless", "https://example.com")\na = t.eval("document.querySelector("[data-k=\'v\']").textContent")\n[a]',
                "expected": ["JS:document.querySelector(\"[data-k='v']\").textContent"],
                "eval_args": ["document.querySelector(\"[data-k='v']\").textContent"],
            },
            {
                "id": "broken_single_quote_attr_selector",
                "code": 't = cua.createBrowserTab("headless", "https://example.com")\na = t.eval(\'document.querySelector(\'[data-k="v"]\').textContent\')\n[a]',
                "expected": ["JS:document.querySelector('[data-k=\"v\"]').textContent"],
                "eval_args": ["document.querySelector('[data-k=\"v\"]').textContent"],
            },
            {
                "id": "broken_double_quote_with_max_chars_int_and_dict",
                "code": 't = cua.createBrowserTab("headless", "https://example.com")\na = t.eval("document.querySelector(".price")?.outerHTML", 200)\nb = t.eval("document.querySelector(".sku")?.textContent", {maxChars: 200})\n[a, b]',
                "expected": [
                    'JS:document.querySelector(".price")?.outerHTML',
                    'JS:document.querySelector(".sku")?.textContent',
                ],
                "eval_args": [
                    'document.querySelector(".price")?.outerHTML',
                    'document.querySelector(".sku")?.textContent',
                ],
            },
            {
                "id": "escaped_quotes_not_double_escaped",
                "code": 't = cua.createBrowserTab("headless", "https://example.com")\na = t.eval("document.querySelector(\\"[data-k=\\\\\\"v\\\\\\"]\\").getAttribute(\\"data-k\\")")\n[a]',
                "expected": ['JS:document.querySelector("[data-k=\\"v\\"]").getAttribute("data-k")'],
                "eval_args": ['document.querySelector("[data-k=\\"v\\"]").getAttribute("data-k")'],
            },
            {
                "id": "comment_stripping_preserves_url_and_hash_selector",
                "code": 't = cua.createBrowserTab("headless", "https://example.com")\n# strip me\nif True:\n    /* a; "b" */\n    a = t.eval("location.href = \\"https://a.com/x#frag\\"; document.querySelector(\\"#btn\\")")\n[a]',
                "expected": ['JS:location.href = "https://a.com/x#frag"; document.querySelector("#btn")'],
                "eval_args": ['location.href = "https://a.com/x#frag"; document.querySelector("#btn")'],
            },
            # b1b: raw JS callbacks & IIFEs (gpt-5.4-pro)
            {
                "id": "zero_arg_arrow_title",
                "code": 't = cua.createBrowserTab("headless", "https://example.com")\n[t.eval(() => document.title)]',
                "expected": ["JS:(() => document.title)()"],
                "eval_args": ["(() => document.title)()"],
            },
            {
                "id": "single_param_arrow_wrap",
                "code": 't = cua.createBrowserTab("headless", "https://example.com")\n[t.eval(x => x + 1)]',
                "expected": ["JS:(x => x + 1)()"],
                "eval_args": ["(x => x + 1)()"],
            },
            {
                "id": "async_arrow_regex_paren_slash",
                "code": 't = cua.createBrowserTab("headless", "https://example.com")\n[t.eval(async () => { const r = /a\\)b\\/c/; return r.source; })]',
                "expected": ["JS:(async () => { const r = /a\\)b\\/c/; return r.source; })()"],
                "eval_args": ["(async () => { const r = /a\\)b\\/c/; return r.source; })()"],
            },
            {
                "id": "function_expression_object_string_paren",
                "code": 't = cua.createBrowserTab("headless", "https://example.com")\n[t.eval(function() { return {k: "x)y"}; })]',
                "expected": ['JS:(function() { return {k: "x)y"}; })()'],
                "eval_args": ['(function() { return {k: "x)y"}; })()'],
            },
            {
                "id": "already_invoked_iife_passthrough",
                "code": 't = cua.createBrowserTab("headless", "https://example.com")\nres = {}\nres["k"] = t.eval((() => { const s = "a, b)"; return s; })())\n[res["k"]]',
                "expected": ['JS:(() => { const s = "a, b)"; return s; })()'],
                "eval_args": ['(() => { const s = "a, b)"; return s; })()'],
            },
        ]
        for case in cases:
            with self.subTest(case=case["id"]):
                rt, eval_calls = _make_mock_repl_runtime()
                try:
                    out = rt.execute({"code": case["code"]})
                    self.assertEqual(json.loads(out), case["expected"])
                    self.assertEqual(eval_calls, case["eval_args"])
                finally:
                    rt._pool.shutdown(wait=False)

    def test_b2_subscripts_augassign_and_safe_imports(self) -> None:
        valid_cases = [
            {
                "id": "nested_dict_subscript_assign_and_del",
                "code": 'res = {"a": {}, "tmp": 99, "keep": 2}\nres["a"]["b"] = 1\ndel res["tmp"]\n[res["a"]["b"], "tmp" in res, sorted(res.keys())]',
                "expected": [1, False, ["a", "keep"]],
                "eval_args": [],
            },
            {
                "id": "augassign_plus_times_or_on_subscripts",
                "code": 'state = {"n": 2, "flags": {"x": 1}}\narr = [3, 4]\nstate["n"] += 5\narr[-1] *= 6\nstate["flags"] |= {"y": 2}\n[state["n"], arr, state["flags"]["x"], state["flags"]["y"]]',
                "expected": [7, [3, 24], 1, 2],
                "eval_args": [],
            },
            {
                "id": "eval_rewrite_on_subscript_assign_and_augassign",
                "code": 't = cua.createBrowserTab("headless", "https://example.com")\nres = {"s": "prefix-"}\nres["v"] = t.eval("1")\nres["s"] += t.eval("2")\n[res["v"], res["s"]]',
                "expected": ["JS:1", "prefix-JS:2"],
                "eval_args": ["1", "2"],
            },
            {
                "id": "slice_assignment_and_hashy_url_string",
                "code": 'arr = [0, 1, 2, 3]\nmeta = {}\narr[1:3] = [8, 9]\nmeta["url"] = "https://example.com/p?q=a=b&x=1#frag=two"\n[arr, meta["url"]]',
                "expected": [[0, 8, 9, 3], "https://example.com/p?q=a=b&x=1#frag=two"],
                "eval_args": [],
            },
            {
                "id": "const_object_destructure_tuple_unpack_and_dictcomp",
                "code": 'const { a, b } = { a: 1, b: 2 }\nx, y = [3, 4]\nout = {k: v for k, v in [("sum", a + x), ("prod", b * y)]}\n[a, b, x, y, out["sum"], out["prod"]]',
                "expected": [1, 2, 3, 4, 4, 8],
                "eval_args": [],
            },
            {
                "id": "safe_imports_re_json_and_urllib_parse",
                "code": 'import re, json as _j; from urllib.parse import urlparse, parse_qs\nm = bool(re.fullmatch(r"[a-z]+_\\d+", "abc_123"))\ndoc = _j.loads("{\\"ok\\": true, \\"n\\": 2}")\nu = urlparse("https://example.com/p?a=1&a=2#frag")\nq = parse_qs("a=1&a=2")\n[m, doc["ok"], doc["n"], u.netloc, u.path, q["a"]]',
                "expected": [True, True, 2, "example.com", "/p", ["1", "2"]],
                "eval_args": [],
            },
            {
                "id": "safe_imports_collections_base64_hashlib_html",
                "code": 'from collections import Counter; import base64, hashlib, html\nc = Counter("abca")\nencoded = base64.b64encode(b"hi").decode()\ndigest = hashlib.sha256(b"abc").hexdigest()\nescaped = html.escape("<a&b>")\n[c["a"], c["b"], c["c"], encoded, digest, escaped]',
                "expected": [
                    2,
                    1,
                    1,
                    "aGk=",
                    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
                    "&lt;a&amp;b&gt;",
                ],
                "eval_args": [],
            },
        ]

        for case in valid_cases:
            with self.subTest(case=case["id"]):
                rt, eval_calls = _make_mock_repl_runtime()
                try:
                    out = rt.execute({"code": case["code"]})
                    self.assertEqual(json.loads(out), case["expected"])
                    self.assertEqual(eval_calls, case["eval_args"])
                finally:
                    rt._pool.shutdown(wait=False)

    def test_b2_blocked_imports_rejected(self) -> None:
        blocked_cases = [
            "import os",
            "import subprocess",
            "import sys",
            "import pathlib",
            "import urllib",
            "from urllib import request",
            "from urllib import parse, request",
            "import urllib.request",
            "from . import cli",
            '__import__("os")',
            "import importlib",
            "import builtins",
        ]
        rt, _ = _make_mock_repl_runtime()
        try:
            for code in blocked_cases:
                with self.subTest(code=code):
                    with self.assertRaises(ImportError):
                        rt.execute({"code": code})
        finally:
            rt._pool.shutdown(wait=False)


class TestAdversarialOopifAndDiscardedTabs(unittest.TestCase):
    def _run_mv3_node_harness(self, harness_body: str) -> dict[str, Any]:
        bg_js = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
        # Strip auto-connect WebSocket loop so unit assertions run synchronously in Node
        bg_js_unit = bg_js.replace("connectRelay();", "// connectRelay disabled for unit harness")
        script = f"""
{harness_body}
{bg_js_unit}
runScenario().then(res => {{
  process.stdout.write(JSON.stringify(res));
  process.exit(0);
}}).catch(err => {{
  process.stderr.write(String(err && err.stack || err));
  process.exit(1);
}});
"""
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 0, f"Node harness failed:\nSTDERR:\n{proc.stderr}\nSTDOUT:\n{proc.stdout}")
        return json.loads(proc.stdout)

    def test_c1_mv3_sibling_oopif_disambiguation_and_readonly_shadow_offscreen(self) -> None:
        harness = r"""
globalThis.chrome = {
  storage: { local: { get: async () => ({}), set: async () => {} } },
  tabs: {
    query: async () => [],
    get: async (id) => ({ id, windowId: 88, groupId: 20, discarded: false, status: "complete", url: "https://host.example/" }),
    onCreated: { addListener: () => {} },
    onUpdated: { addListener: () => {} },
    onRemoved: { addListener: () => {} }
  },
  tabGroups: { get: async () => ({}), query: async () => [], update: async () => ({}) },
  windows: { getAll: async () => [{ id: 88, focused: false }] },
  debugger: {
    attach: async () => ({}),
    detach: async () => ({}),
    sendCommand: async (_t, method, params) => {
      if (method === "Runtime.evaluate" && params && String(params.expression || "").includes("prepIframeTarget")) {
        if (globalThis.__prepMode === "offscreen") {
          return { result: { value: { inViewport: false, x: null, y: null, frameIndex: 1 } } };
        }
        return { result: { value: { inViewport: true, x: 120, y: 80, frameIndex: globalThis.__prepFrameIndex } } };
      }
      return {};
    },
    onDetach: { addListener: () => {} }
  },
  scripting: {
    executeScript: async ({ target, args, func }) => {
      const arg = args[0];
      const frames = globalThis.__mockFrames || [];
      const selected = (target && Array.isArray(target.frameIds))
        ? frames.filter(f => target.frameIds.includes(f.frameId))
        : frames;
      const results = [];
      for (const f of selected) {
        const makeEl = (spec) => {
          if (!spec || spec.inputType === "none") return null;
          const el = {
            tagName: "INPUT",
            type: spec.inputType || "text",
            disabled: Boolean(spec.disabled),
            readOnly: Boolean(spec.readonly),
            value: spec.value || "",
            isContentEditable: false,
            getAttribute: (k) => (k === "readonly" && spec.readonly ? "" : null),
            getBoundingClientRect: () => spec.hidden ? { width: 0, height: 0 } : { width: 140, height: 24 },
            focus: () => {},
            select: () => {},
            dispatchEvent: () => true
          };
          return el;
        };
        const inputEl = makeEl(f);
        const lightList = (!f.inShadow && inputEl && inputEl.type !== "hidden") ? [inputEl] : [];
        const shadowHost = f.inShadow && inputEl ? {
          shadowRoot: {
            activeElement: f.hasFocus ? inputEl : null,
            querySelectorAll: (sel) => (sel === "*" ? [inputEl] : [inputEl])
          }
        } : null;
        const winObj = {
          location: { href: f.href },
          name: f.name || "",
          getComputedStyle: () => ({ display: f.hidden ? "none" : "block", visibility: f.hidden ? "hidden" : "visible" }),
          HTMLInputElement: { prototype: {} },
          HTMLTextAreaElement: { prototype: {} }
        };
        const topObj = { length: f.nested ? 0 : Math.max(0, (f.frameIndex ?? -1) + 1) };
        if (!f.nested && f.frameIndex >= 0) topObj[f.frameIndex] = winObj;
        winObj.top = topObj;
        winObj.parent = f.nested ? { nestedParent: true } : topObj;
        const docObj = {
          activeElement: f.hasFocus ? (shadowHost || inputEl) : null,
          hasFocus: () => Boolean(f.hasFocus),
          querySelectorAll: (sel) => {
            if (sel === "*") return shadowHost ? [shadowHost] : lightList;
            return lightList;
          }
        };
        const prevWin = globalThis.window;
        const prevDoc = globalThis.document;
        const prevLoc = globalThis.location;
        globalThis.window = winObj;
        globalThis.document = docObj;
        globalThis.location = winObj.location;
        let out = null;
        try {
          out = func(arg);
        } finally {
          globalThis.window = prevWin;
          globalThis.document = prevDoc;
          globalThis.location = prevLoc;
        }
        results.push({ frameId: f.frameId, result: out });
      }
      return results;
    }
  },
  alarms: { create: () => {}, onAlarm: { addListener: () => {} } },
  runtime: { onInstalled: { addListener: () => {} }, onStartup: { addListener: () => {} } }
};

async function runScenario() {
  const out = {};

  // 1. Identical src + blank name: frameIndex=1 must beat stale hasFocus on frameIndex=0
  globalThis.__mockFrames = [
    { frameId: 101, href: "https://pay.example/hosted/card-number", name: "", frameIndex: 0, hasFocus: true, inputType: "text" },
    { frameId: 102, href: "https://pay.example/hosted/card-number", name: "", frameIndex: 1, hasFocus: false, inputType: "text" },
    { frameId: 103, href: "https://pay.example/hosted/expiry", name: "", frameIndex: 2, hasFocus: false, inputType: "text" }
  ];
  out.same_src_frame_index = (await probeAndPrepIframeInTab(1, {
    action: "probe", frameSrc: "https://pay.example/hosted/card-number", frameName: "", frameIndex: 1
  })).frameId;

  // 2. Exact href beats origin+path query variant even when wrong sibling hasFocus
  globalThis.__mockFrames = [
    { frameId: 201, href: "https://auth.bank.example/acs/challenge?session=aaa&step=1", name: "acsFrame", frameIndex: 0, hasFocus: true, inputType: "text" },
    { frameId: 202, href: "https://auth.bank.example/acs/challenge?session=bbb&step=2", name: "acsFrame", frameIndex: 1, hasFocus: false, inputType: "text" }
  ];
  out.exact_href_over_query = (await probeAndPrepIframeInTab(1, {
    action: "probe", frameSrc: "https://auth.bank.example/acs/challenge?session=bbb&step=2", frameName: "acsFrame", frameIndex: 7
  })).frameId;

  // 3. Matching frameName beats focused sibling with mismatched frameName
  globalThis.__mockFrames = [
    { frameId: 301, href: "https://checkout.example/embed/card-field", name: "upsell-frame", frameIndex: 0, hasFocus: true, inputType: "text" },
    { frameId: 302, href: "https://checkout.example/embed/card-field", name: "secure-pan", frameIndex: 1, hasFocus: false, inputType: "text" }
  ];
  out.frame_name_over_mismatch = (await probeAndPrepIframeInTab(1, {
    action: "probe", frameSrc: "https://checkout.example/embed/card-field", frameName: "secure-pan", frameIndex: 8
  })).frameId;

  // 4. Readonly and hidden-input siblings skipped in favor of editable sibling even when readonly hasFocus
  globalThis.__mockFrames = [
    { frameId: 401, href: "https://wallet.example/widget/card-input", name: "", frameIndex: 0, hasFocus: true, readonly: true, inputType: "text" },
    { frameId: 402, href: "https://wallet.example/widget/card-input", name: "", frameIndex: 1, hasFocus: false, inputType: "hidden", hidden: true },
    { frameId: 403, href: "https://wallet.example/widget/card-input", name: "", frameIndex: 2, hasFocus: false, inputType: "text" }
  ];
  out.readonly_hidden_skipped = (await probeAndPrepIframeInTab(1, {
    action: "probe", frameSrc: "https://wallet.example/widget/card-input", frameName: ""
  })).frameId;

  // 5. Shadow DOM editable input in target sibling selected by frameIndex
  globalThis.__mockFrames = [
    { frameId: 501, href: "https://checkout.example/components/card-number", name: "card-number-host", frameIndex: 0, hasFocus: true, inputType: "text" },
    { frameId: 502, href: "https://checkout.example/components/card-number", name: "card-number-host", frameIndex: 1, hasFocus: false, inShadow: true, inputType: "text" }
  ];
  out.shadow_dom_sibling = (await probeAndPrepIframeInTab(1, {
    action: "probe", frameSrc: "https://checkout.example/components/card-number", frameName: "card-number-host", frameIndex: 1
  })).frameId;

  // 6. Nested frameIndex=-1 preserves nested-child identity over focused top-level sibling
  globalThis.__mockFrames = [
    { frameId: 601, href: "https://verify.example/otp-field", name: "otp", frameIndex: 0, hasFocus: true, inputType: "text" },
    { frameId: 602, href: "https://verify.example/otp-field", name: "otp", frameIndex: -1, hasFocus: false, nested: true, inputType: "text" }
  ];
  out.nested_minus_one = (await probeAndPrepIframeInTab(1, {
    action: "probe", frameSrc: "https://verify.example/otp-field", frameName: "otp", frameIndex: -1
  })).frameId;

  // 7. Offscreen iframe rejected by prepCdpIframeInTab without silently falling back to another sibling
  globalThis.__prepMode = "offscreen";
  try {
    await prepCdpIframeInTab(1, { uid: "@u7", frameSrc: "https://3ds.example/challenge/input", frameIndex: 1 });
    out.offscreen_err = "";
  } catch (e) {
    out.offscreen_err = String(e && e.message || e);
  }
  return out;
}
"""
        res = self._run_mv3_node_harness(harness)
        self.assertEqual(res["same_src_frame_index"], 102)
        self.assertEqual(res["exact_href_over_query"], 202)
        self.assertEqual(res["frame_name_over_mismatch"], 302)
        self.assertEqual(res["readonly_hidden_skipped"], 403)
        self.assertEqual(res["shadow_dom_sibling"], 502)
        self.assertEqual(res["nested_minus_one"], 602)
        self.assertIn("outside visible viewport", res["offscreen_err"])

    def test_c2_mv3_discarded_and_unloaded_tab_edge_cases(self) -> None:
        harness = r"""
const state = {
  nextId: 507,
  attachCalls: [],
  tabs: [
    { id: 501, windowId: 88, groupId: 20, active: false, discarded: false, status: "unloaded", url: "https://example.com/a", pendingUrl: "", title: "Old" },
    { id: 550, windowId: 88, groupId: 20, active: true, discarded: false, status: "complete", url: "https://example.com/b", pendingUrl: "", title: "Live" },
    { id: 502, windowId: 89, groupId: -1, active: true, discarded: true, status: "unloaded", url: "https://app.example.com/dashboard?team=red", pendingUrl: "", title: "Dashboard" },
    { id: 503, windowId: 89, groupId: -1, active: false, discarded: false, status: "loading", url: "https://app.example.com/dashboard?team=red", pendingUrl: "", title: "Dashboard" },
    { id: 504, windowId: 90, groupId: 7, active: false, discarded: true, status: "unloaded", url: "https://docs.example.com/spec", pendingUrl: "", title: "Spec" },
    { id: 505, windowId: 91, groupId: 12, active: false, discarded: true, status: "unloaded", url: "https://example.com/work", pendingUrl: "", title: "Work" },
    { id: 506, windowId: 92, groupId: 3, active: false, discarded: true, status: "unloaded", url: "https://calendar.example.com/", pendingUrl: "", title: "Calendar" },
    { id: 508, windowId: 93, groupId: -1, active: false, discarded: true, status: "unloaded", url: "", pendingUrl: "https://login.example.com/oauth/start?client=fmu", title: "Sign in" }
  ],
  groups: [
    { id: 20, windowId: 88, title: "Agent", collapsed: false },
    { id: 7, windowId: 90, title: "Docs", collapsed: false },
    { id: 12, windowId: 91, title: "Work", collapsed: false },
    { id: 3, windowId: 92, title: "Cal", collapsed: false }
  ],
  storage: {}
};

globalThis.chrome = {
  storage: { local: { get: async (k) => ({ [k]: state.storage[k] }), set: async (o) => Object.assign(state.storage, o) } },
  tabs: {
    query: async () => state.tabs,
    get: async (id) => {
      const f = state.tabs.find(t => t.id === id);
      if (!f) throw new Error("No tab with id: " + id);
      return f;
    },
    create: async (opts) => {
      const nt = { id: state.nextId++, windowId: opts.windowId || 92, groupId: -1, active: false, discarded: false, status: "complete", url: opts.url, pendingUrl: "", title: "Fresh" };
      state.tabs.push(nt);
      return nt;
    },
    update: async (id, opts) => {
      const f = state.tabs.find(t => t.id === id);
      if (!f) throw new Error("No tab with id: " + id);
      if (opts.url) { f.url = opts.url; f.discarded = false; f.status = "complete"; }
      return f;
    },
    group: async () => 3,
    onCreated: { addListener: () => {} },
    onUpdated: { addListener: () => {} },
    onRemoved: { addListener: () => {} }
  },
  tabGroups: {
    get: async (gid) => state.groups.find(g => g.id === gid) || { id: gid, windowId: 88, title: "" },
    query: async () => state.groups,
    update: async (gid, opts) => opts
  },
  windows: { getAll: async () => [{ id: 88, focused: false }] },
  debugger: {
    attach: async (target) => { state.attachCalls.push(target.tabId); return {}; },
    detach: async () => ({}),
    sendCommand: async () => ({ result: { value: true } }),
    onDetach: { addListener: () => {} }
  },
  alarms: { create: () => {}, onAlarm: { addListener: () => {} } },
  runtime: { onInstalled: { addListener: () => {} }, onStartup: { addListener: () => {} } }
};

async function runScenario() {
  const out = {};

  // 1. status="unloaded" with discarded=false normalized to discarded:true in BOTH listTabs and getTabInfo
  const lt = (await handleRpc({ id: 1, method: "listTabs", params: { windowId: 88 } })).result.tabs;
  const t501List = lt.find(x => x.tabId === 501);
  const t501Info = (await handleRpc({ id: 2, method: "getTabInfo", params: { tabId: 501 } })).result;
  out.unloaded_list_discarded = t501List.discarded;
  out.unloaded_info_discarded = t501Info.discarded;

  // 2. Active discarded tab vs inactive loading live sibling -> getTab picks live sibling 503
  const gLive = (await handleRpc({ id: 3, method: "getTab", params: { urlPattern: "app.example.com/dashboard", windowId: 89 } })).result;
  out.live_over_active_discarded = { tabId: gLive.tabId, discarded: gLive.discarded };

  // 3. Only discarded tab matches -> getTab returns discarded:true WITHOUT calling debugger.attach
  state.attachCalls = [];
  const gOnly = (await handleRpc({ id: 4, method: "getTab", params: { urlPattern: "docs.example.com/spec", windowId: 90 } })).result;
  out.only_discarded_match = { tabId: gOnly.tabId, discarded: gOnly.discarded, attachCalls: [...state.attachCalls] };

  // 4. executeBatch on discarded tab 505 fast-fails with tab.goto hint and evicts debugger/runtime caches
  attachedDebuggers.add(505);
  verifiedRendererTabs.set(505, Date.now());
  initializedRuntimeTabs.set(505, "0.5.7");
  state.attachCalls = [];
  const batchErr = await handleRpc({ id: 5, method: "executeBatch", params: { tabId: 505, req: { steps: [] } } });
  out.discarded_batch = {
    error: batchErr.error || "",
    attachCalls: [...state.attachCalls],
    cacheCleared: !attachedDebuggers.has(505) && !verifiedRendererTabs.has(505) && !initializedRuntimeTabs.has(505)
  };

  // 5. createBrowserTab(reuseExisting=true) skips discarded exact match 506 and creates fresh tab 507
  const created = (await handleRpc({
    id: 6,
    method: "createBrowserTab",
    params: { url: "https://calendar.example.com/", reuseExisting: true, windowId: 92, skipPreview: true }
  })).result;
  out.reuse_skips_discarded = { tabId: created.tabId, reused: Boolean(created.reused) };

  // 6. Discarded tab 508 with empty url + populated pendingUrl surfaces pendingUrl in getTabInfo and error
  const info508 = (await handleRpc({ id: 7, method: "getTabInfo", params: { tabId: 508 } })).result;
  const err508 = await handleRpc({ id: 8, method: "executeBatch", params: { tabId: 508, req: { steps: [] } } });
  out.pending_url_discarded = { infoUrl: info508.url, infoPending: info508.pendingUrl, discarded: info508.discarded, err: err508.error || "" };

  // 7. Closed/vanished tab 509 mid-session throws clean closed-tab error & clears caches
  attachedDebuggers.add(509);
  verifiedRendererTabs.set(509, Date.now());
  const err509 = await handleRpc({ id: 9, method: "executeBatch", params: { tabId: 509, req: { steps: [] } } });
  out.vanished_tab = { error: err509.error || "", cacheCleared: !attachedDebuggers.has(509) && !verifiedRendererTabs.has(509) };

  return out;
}
"""
        res = self._run_mv3_node_harness(harness)
        self.assertTrue(res["unloaded_list_discarded"])
        self.assertTrue(res["unloaded_info_discarded"])
        self.assertEqual(res["live_over_active_discarded"], {"tabId": 503, "discarded": False})
        self.assertEqual(res["only_discarded_match"], {"tabId": 504, "discarded": True, "attachCalls": []})
        self.assertIn("discarded/unloaded", res["discarded_batch"]["error"])
        self.assertIn('tab.goto("https://example.com/work")', res["discarded_batch"]["error"])
        self.assertEqual(res["discarded_batch"]["attachCalls"], [])
        self.assertTrue(res["discarded_batch"]["cacheCleared"])
        self.assertEqual(res["reuse_skips_discarded"], {"tabId": 507, "reused": False})
        self.assertEqual(res["pending_url_discarded"]["infoUrl"], "https://login.example.com/oauth/start?client=fmu")
        self.assertEqual(res["pending_url_discarded"]["infoPending"], "https://login.example.com/oauth/start?client=fmu")
        self.assertTrue(res["pending_url_discarded"]["discarded"])
        self.assertIn("https://login.example.com/oauth/start?client=fmu", res["pending_url_discarded"]["err"])
        self.assertIn("was closed or does not exist", res["vanished_tab"]["error"])
        self.assertTrue(res["vanished_tab"]["cacheCleared"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
