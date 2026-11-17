#!/usr/bin/env python3
"""Comprehensive unit and integration test suite for Faster Mac Use (fmu)."""

from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

os.environ.setdefault("FMU_RUNTIME_SOCK", "/tmp/fmu-test-suite.sock")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "benchmarks"))

from fmu.cua_runtime import FasterMacUseRuntime  # noqa: E402
from fmu.guard import BackgroundIntrusionGuard  # noqa: E402
from test_app_server import BenchWebServer  # noqa: E402


class TestFasterMacUse(unittest.TestCase):
    server: BenchWebServer
    rt: FasterMacUseRuntime
    _log_tmp: tempfile.TemporaryDirectory[str]
    _port_guard: Any = None

    @classmethod
    def setUpClass(cls) -> None:
        import socket

        cls._log_tmp = tempfile.TemporaryDirectory(prefix="fmu-test-logs-")
        os.environ["FMU_LOG_DIR"] = cls._log_tmp.name
        # Hold 127.0.0.1:19222 during the unit test suite if not already held by warm daemon,
        # so a live Chrome MV3 extension does not connect to the test suite's synthetic bridge.
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", 19222))
            s.listen(1)
            cls._port_guard = s
        except OSError:
            s.close()
            cls._port_guard = None
        cls.server = BenchWebServer().start()
        cls.rt = FasterMacUseRuntime()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.rt.shutdown()
        cls.server.stop()
        if cls._port_guard is not None:
            try:
                cls._port_guard.close()
            except Exception:
                pass
        cls._log_tmp.cleanup()

    def setUp(self) -> None:
        self.rt.execute({"reset": True, "resume": True})

    def test_01_web_uid_stability_and_top_banner_no_avalanche(self) -> None:
        url = f"{self.server.base_url}/orders"
        with BackgroundIntrusionGuard() as guard:
            snap = self.rt.execute({"surface": "web", "url": url, "session": "t1"})
            self.assertIn("# web tab=t1 group=\"🤖 Agent\"", snap)
            self.assertIn("webmcp=[\"approveOrder\"]", snap)
            self.assertIn("@u1 ", snap)

            # Trigger top-of-DOM toast insertion (inserts 3 elements at top of <main>)
            diff_out = self.rt.execute({
                "surface": "web",
                "session": "t1",
                "steps": [
                    {"op": "fill", "target": "Search orders", "value": "Enterprise"},
                    {"op": "select", "target": "Order status", "value": "pending"},
                    {"op": "click", "target": "Priority Only"},
                    {"op": "click", "target": "Apply Filters"},
                ],
            })
            # Verify no index-shift avalanche: diff should only contain the ~7 changed/added lines, not 100+ table rows
            diff_lines = [l for l in diff_out.splitlines() if l.startswith(("+", "-", "~"))]
            self.assertLessEqual(len(diff_lines), 10, f"Unexpected diff avalanche: {diff_out}")
            self.assertTrue(any('Filtered: query="Enterprise"' in l for l in diff_lines))

            # Unchanged poll should short-circuit via CRC-32
            unchanged = self.rt.execute({"surface": "web", "session": "t1", "diff": True})
            self.assertIn("= unchanged (crc=", unchanged)

        rep = guard.stop()
        self.assertTrue(rep.passed_background_invariant, f"Intrusion detected: {rep}")

    def test_02_webmcp_direct_tool_execution(self) -> None:
        url = f"{self.server.base_url}/orders"
        self.rt.execute({"code": f'let tab = await cua.createBrowserTab("chrome", "{url}");'})
        out = self.rt.execute({
            "code": (
                'await tab.callTool("approveOrder", '
                '{orderId: 409, query: "Enterprise", status: "pending", priority: true, tier: "express", note: "WebMCP FastPath"});'
            )
        })
        self.assertIn("webmcp(approveOrder): APPROVED #ORD-409 (express)", out)
        self.assertIn('note="WebMCP FastPath"', out)

    def test_03_focus_emulation_and_background_file_upload(self) -> None:
        pricing_url = f"{self.server.base_url}/pricing"
        out1 = self.rt.execute({
            "code": (
                f'let p = await cua.createBrowserTab({{url: "{pricing_url}", group: "🤖 Agent", active: false}});\n'
                'await p.click("Generate Quote Token");'
            )
        })
        self.assertIn("ENT-1000-OK", out1)

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
            tf.write("test-po-content")
            po_path = tf.name
        try:
            orders_url = f"{self.server.base_url}/orders"
            out2 = self.rt.execute({
                "code": (
                    f'let ord = await cua.createBrowserTab("chrome", "{orders_url}");\n'
                    'await ord.click("Inspect #ORD-409");\n'
                    f'await ord.upload("Attach signed PO", "{po_path}");\n'
                    'await ord.eval("document.getElementById(\'invoice-file\').files[0].name");'
                )
            })
            self.assertIn(Path(po_path).name, out2)
        finally:
            Path(po_path).unlink(missing_ok=True)

    def test_04_repl_print_and_readme_syntax_aliases(self) -> None:
        url = f"{self.server.base_url}/pricing"
        out = self.rt.execute({
            "code": f'tab = await cua.createBrowserTab({{url: "{url}", group: "🤖 Agent", active: false}})\nprint(await tab.snapshot())'
        })
        self.assertIn("Generate Quote Token", out)
        state = self.rt.execute({"code": "return await cua.getState();"})
        self.assertIn("TabHandle", state)

    def test_05_error_paths_and_optional_steps(self) -> None:
        url = f"{self.server.base_url}/pricing"
        self.rt.execute({"surface": "web", "url": url, "session": "t1"})
        # Non-optional missing target reports error without crashing
        err_out = self.rt.execute({
            "surface": "web",
            "session": "t1",
            "steps": [{"op": "click", "target": "#non-existent-btn"}],
        })
        self.assertIn("! errors: step 1 (click): target not found", err_out)

        # Optional missing target is silently skipped
        opt_out = self.rt.execute({
            "surface": "web",
            "session": "t1",
            "steps": [{"op": "click", "target": "#non-existent-btn", "optional": True}],
        })
        self.assertNotIn("! errors:", opt_out)

        # Missing macOS app returns clean error
        mac_err = self.rt.execute({"surface": "app", "app": "NonExistentApp_XYZ_999"})
        self.assertIn("Error: app not found", mac_err)

    def test_06_mac_native_calculator_and_aliases(self) -> None:
        with BackgroundIntrusionGuard() as guard:
            # Ensure Calculator starts at 0 before baseline snapshot
            self.rt.execute({"surface": "app", "app": "Calculator", "steps": [{"action": "key", "key": "escape"}]})
            snap = self.rt.execute({"surface": "app", "app": "Calculator", "diff": False})
            self.assertIn('app="Calculator"', snap)
            self.assertIn("@a", snap)

            # Test action/ref aliases and calculation (0 -> 42)
            calc_out = self.rt.execute({
                "surface": "app",
                "app": "Calculator",
                "steps": [
                    {"action": "press", "name": "7"},
                    {"action": "press", "name": "Multiply"},
                    {"action": "press", "name": "6"},
                    {"action": "press", "name": "Equals"},
                ],
            })
            self.assertIn("42", calc_out)

            # Unchanged diff check on Calculator
            unchanged = self.rt.execute({"surface": "app", "app": "Calculator", "diff": True})
            self.assertIn("= unchanged (crc=", unchanged)

            # OCR fallback & coordinate extraction check on Calculator
            ocr_out = self.rt.execute({
                "surface": "app",
                "app": "Calculator",
                "steps": [{"op": "ocr"}],
            })
            self.assertIn("[ocr:", ocr_out)
            self.assertIn("@ocr text", ocr_out)

        rep = guard.stop()
        self.assertTrue(rep.passed_background_invariant, f"Intrusion detected: {rep}")

    def test_07_context_window_stale_snapshot_masking(self) -> None:
        url = f"{self.server.base_url}/orders"
        self.rt.execute({"surface": "web", "url": url, "session": "t1"})
        self.rt.execute({
            "surface": "web",
            "session": "t1",
            "steps": [{"op": "click", "target": "Inspect #ORD-409"}],
        })
        self.rt.execute({
            "surface": "web",
            "session": "t1",
            "steps": [{"op": "click", "target": "Confirm Approval"}],
        })
        metrics = self.rt.context_tracker.metrics()
        self.assertEqual(metrics["turns"], 3)
        self.assertLess(metrics["cumulative_input_tokens_masked"], metrics["cumulative_input_tokens_unmasked"])
        self.assertLess(metrics["peak_context_masked"], metrics["peak_context_unmasked"])

    def test_08_cli_warm_daemon_persistence_across_processes(self) -> None:
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import fmu.cli; "
                "assert not {'fmu.cua_runtime', 'fmu.mac_engine', 'fmu.browser_engine', 'tiktoken'} & sys.modules.keys()",
            ],
            cwd=ROOT / "src",
            check=True,
            timeout=10,
        )
        fmu_bin = str(ROOT / "bin" / "fmu")
        url = f"{self.server.base_url}/pricing"
        subprocess.run([fmu_bin, json.dumps({"reset": True})], capture_output=True, text=True, timeout=15)

        out1 = subprocess.check_output(
            [fmu_bin, json.dumps({"code": f'let tab = await cua.createBrowserTab("chrome", "{url}");'})],
            text=True,
            timeout=20,
        )
        self.assertIn("Generate Quote Token", out1)

        # Second CLI invocation in a brand new process reuses the `tab` variable from the warm daemon!
        t1 = time.perf_counter()
        out2 = subprocess.check_output(
            [fmu_bin, json.dumps({"code": 'await tab.click("Generate Quote Token");'})],
            text=True,
            timeout=10,
        )
        warm_cli_ms = (time.perf_counter() - t1) * 1000.0
        self.assertIn("ENT-1000-OK", out2)
        self.assertLess(warm_cli_ms, 400.0, f"Warm CLI call took too long: {warm_cli_ms:.1f}ms")

        subprocess.run([fmu_bin, json.dumps({"shutdown_daemon": True})], capture_output=True, text=True, timeout=10)

    def test_09_crash_recovery_browser_and_mac_app_kill_mid_session(self) -> None:
        url = f"{self.server.base_url}/pricing"
        snap1 = self.rt.execute({"surface": "web", "url": url, "session": "t1"})
        self.assertIn("Generate Quote Token", snap1)
        # Simulate browser crash / force-close mid-session
        self.rt.browser._browser.close()
        # Turn 2 without URL should automatically reconnect, restore tab URL, and click
        out_web = self.rt.execute({
            "surface": "web",
            "session": "t1",
            "steps": [{"op": "click", "target": "Generate Quote Token"}],
        })
        self.assertIn("ENT-1000-OK", out_web)

        # Now test macOS Calculator force-kill (killall -9) mid-session + @a UID persistence
        self.rt.execute({"reset": True})
        self.rt.execute({"surface": "app", "app": "Calculator", "steps": [{"action": "key", "key": "escape"}]})
        c_snap = self.rt.execute({"surface": "app", "app": "Calculator", "diff": False})
        seven_uid = next(
            (l.split()[0] for l in c_snap.splitlines() if 'button "7"' in l),
            "",
        )
        self.assertTrue(seven_uid.startswith("@a"), f"Missing button 7 UID in: {c_snap}")
        subprocess.run(["killall", "-9", "Calculator"], capture_output=True)
        # Immediately click seven_uid (from the killed process) on Turn 2
        c_after_kill = self.rt.execute({
            "surface": "app",
            "app": "Calculator",
            "steps": [
                {"op": "click", "uid": seven_uid},
                {"op": "click", "name": "Multiply"},
                {"op": "click", "name": "6"},
                {"op": "click", "name": "Equals"},
            ],
        })
        self.assertNotIn("! errors:", c_after_kill)
        self.assertIn("42", c_after_kill)

    def test_10_multi_handle_repl_comments_and_cli_flags(self) -> None:
        url = f"{self.server.base_url}/pricing"
        # Single REPL block touching both TabHandle and AppHandle + inline // comments + backticks
        hybrid_code = f"""
        let tab = await cua.createBrowserTab("chrome", `{url}`); // open pricing tab
        await tab.click("Generate Quote Token"); // click quote button
        let calc = await cua.getApp("Calculator"); // switch to Mac app in same block
        await calc.pressKey("escape");
        await calc.click("8");
        """
        out = self.rt.execute({"code": hybrid_code})
        self.assertIn("ENT-1000-OK", out)
        self.assertIn('val="‎8"', out)

        # Verify CLI backward-compatible flags (--reset, stdin JSON, --mcp)
        fmu_bin = str(ROOT / "bin" / "fmu")
        r1 = subprocess.check_output([fmu_bin, "--reset"], text=True, timeout=10)
        self.assertIn("reset", r1)
        r2 = subprocess.check_output([fmu_bin], input='{"reset": true}\n', text=True, timeout=10)
        self.assertIn("reset", r2)
        mcp_in = (
            '{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'
            '{"jsonrpc":"2.0","id":2,"method":"ping"}\n'
            '{"jsonrpc":"2.0","id":3,"method":"tools/list"}\n'
        )
        mcp_out = subprocess.check_output([fmu_bin, "--mcp"], input=mcp_in, text=True, timeout=10)
        self.assertIn('"protocolVersion"', mcp_out)
        self.assertIn('"name": "cua"', mcp_out)

    def test_11_multi_cursor_parallel_agents_and_isolation(self) -> None:
        orders_url = f"{self.server.base_url}/orders"
        pricing_url = f"{self.server.base_url}/pricing"

        with BackgroundIntrusionGuard() as guard:
            # Execute 3 agents concurrently in a single parallel batch (2 Chrome Agent Tab Groups + 1 macOS App)
            par_out = self.rt.execute({
                "parallel": [
                    {
                        "agent": "agent-alpha",
                        "code": (
                            f'let tab = await cua.createBrowserTab("chrome", "{orders_url}"); '
                            'await tab.fill("Search orders", "Enterprise"); '
                            'await tab.select("Order status", "pending"); '
                            'await tab.click("Priority Only"); '
                            'await tab.click("Apply Filters"); '
                            'await tab.click("Inspect #ORD-409"); '
                            'await tab.fill("Approval note", "Alpha Parallel"); '
                            'await tab.select("Shipping tier", "express"); '
                            'await tab.click("Confirm Approval");'
                        ),
                    },
                    {
                        "agent": "agent-beta",
                        "code": (
                            f'let tab = await cua.createBrowserTab("chrome", "{pricing_url}"); '
                            'await tab.moveCursor("Generate Quote Token"); '
                            'await tab.click("Generate Quote Token");'
                        ),
                    },
                    {
                        "agent": "agent-gamma",
                        "code": (
                            'let app = await cua.getApp("Calculator"); '
                            'await app.pressKey("escape"); '
                            'await app.moveCursor("9"); '
                            'await app.click("9"); '
                            'await app.click("Multiply"); '
                            'await app.click("9"); '
                            'await app.click("Equals");'
                        ),
                    },
                ]
            })
            self.assertIn("=== [agent-alpha] ===", par_out)
            self.assertIn("APPROVED #ORD-409 (express)", par_out)
            self.assertIn("Alpha Parallel", par_out)
            self.assertIn("=== [agent-beta] ===", par_out)
            self.assertIn("ENT-1000-OK", par_out)
            self.assertIn("=== [agent-gamma] ===", par_out)
            self.assertIn("81", par_out)

            # Verify each agent's `tab` REPL handle is isolated (agent-beta's `tab` is still /pricing, not /orders)
            beta_state = self.rt.execute({"agent": "agent-beta", "code": "return await cua.getState();"})
            self.assertIn("agent=agent-beta", beta_state)
            self.assertIn("agent-beta:t1:TabHandle", beta_state)

            # Verify multi-cursor registry tracks all 3 agents' independent virtual cursors
            cursors_out = self.rt.execute({"getCursors": True})
            self.assertIn("# cursors count=3", cursors_out)
            self.assertIn('agent="agent-alpha" surface=web', cursors_out)
            self.assertIn('agent="agent-beta" surface=web', cursors_out)
            self.assertIn('agent="agent-gamma" surface=app app="Calculator"', cursors_out)

            # Resetting only agent-beta leaves agent-alpha and agent-gamma intact
            r_beta = self.rt.execute({"reset": True, "agent": "agent-beta"})
            self.assertIn("agent=agent-beta", r_beta)
            cursors_after = self.rt.execute({"getCursors": True})
            self.assertNotIn('agent="agent-beta"', cursors_after)
            self.assertIn('agent="agent-alpha"', cursors_after)
            self.assertIn('agent="agent-gamma"', cursors_after)

        rep = guard.stop()
        self.assertTrue(rep.passed_background_invariant, f"Intrusion detected: {rep}")

    def test_12_concurrent_daemon_socket_multi_agent(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        fmu_bin = str(ROOT / "bin" / "fmu")
        pricing_url = f"{self.server.base_url}/pricing"
        orders_url = f"{self.server.base_url}/orders"
        subprocess.run([fmu_bin, json.dumps({"reset": True})], capture_output=True, text=True, timeout=15)

        def call_cli(payload: dict[str, object]) -> str:
            return subprocess.check_output([fmu_bin, json.dumps(payload)], text=True, timeout=20)

        with ThreadPoolExecutor(max_workers=3) as ex:
            f1 = ex.submit(
                call_cli,
                {
                    "agent": "cli-1",
                    "code": f'let tab = await cua.createBrowserTab("chrome", "{pricing_url}"); await tab.click("Generate Quote Token");',
                },
            )
            f2 = ex.submit(
                call_cli,
                {
                    "agent": "cli-2",
                    "code": f'let tab = await cua.createBrowserTab("chrome", "{orders_url}"); await tab.click("Inspect #ORD-409");',
                },
            )
            f3 = ex.submit(
                call_cli,
                {
                    "agent": "cli-3",
                    "surface": "app",
                    "app": "Calculator",
                    "steps": [
                        {"op": "key", "name": "escape"},
                        {"op": "click", "name": "6"},
                        {"op": "click", "name": "Multiply"},
                        {"op": "click", "name": "7"},
                        {"op": "click", "name": "Equals"},
                    ],
                },
            )
            r1, r2, r3 = f1.result(), f2.result(), f3.result()

        self.assertIn("ENT-1000-OK", r1)
        self.assertIn("Approve #ORD-409", r2)
        self.assertIn("42", r3)
        subprocess.run([fmu_bin, json.dumps({"shutdown_daemon": True})], capture_output=True, text=True, timeout=10)

    def test_13_no_devtools_active_port_popup_and_tab_goto_folded_row(self) -> None:
        import os

        old_cdp = os.environ.pop("FMU_CDP_URL", None)
        os.environ["FMU_ATTACH_USER_CHROME"] = "1"
        try:
            self.assertIsNone(self.rt.browser._find_chrome_devtools_ws())
            os.environ["FMU_CDP_URL"] = "ws://127.0.0.1:9999/devtools/browser/mock"
            self.assertEqual(
                self.rt.browser._find_chrome_devtools_ws(),
                "ws://127.0.0.1:9999/devtools/browser/mock",
            )
        finally:
            os.environ.pop("FMU_ATTACH_USER_CHROME", None)
            if old_cdp is not None:
                os.environ["FMU_CDP_URL"] = old_cdp
            else:
                os.environ.pop("FMU_CDP_URL", None)

        pricing_url = f"{self.server.base_url}/pricing"
        orders_url = f"{self.server.base_url}/orders"
        self.rt.execute({"reset": True})

        out1 = self.rt.execute({"code": f'let tab = await cua.createBrowserTab("chrome", "{pricing_url}");'})
        self.assertIn("Generate Quote Token", out1)

        # Navigate via tab.goto() to /orders (folds rows 7..30) and target folded row #ORD-425 directly
        out2 = self.rt.execute({"code": f'await tab.goto("{orders_url}");'})
        self.assertIn("+24 rows", out2)
        self.assertNotIn("Inspect #ORD-425", out2)

        out3 = self.rt.execute(
            {
                "code": (
                    'await tab.click("Inspect #ORD-425"); '
                    'await tab.fill("Approval note", "Folded row target ok"); '
                    'await tab.click("Confirm Approval");'
                )
            }
        )
        self.assertIn("APPROVED #ORD-425", out3)

    def test_14_edge_cases_query_fulltable_goto_ordering_and_parallel_error_isolation(self) -> None:
        pricing_url = f"{self.server.base_url}/pricing"
        orders_url = f"{self.server.base_url}/orders"

        # 1. Mac AX query="nonexistent_xyz" (0 matches) must not poison baseline cache for subsequent steps
        self.rt.execute({"surface": "app", "app": "Calculator", "steps": [{"op": "key", "name": "escape"}]})
        self.rt.execute({"surface": "app", "app": "Calculator", "query": "nonexistent_xyz"})
        ax_after_q = self.rt.execute({"surface": "app", "app": "Calculator", "steps": [{"op": "click", "name": "7"}]})
        self.assertNotIn("! errors:", ax_after_q)
        self.assertIn("7", ax_after_q)

        # 2. Web fullTable=True override after initial folded snapshot must emit all unfolded rows
        self.rt.execute({"reset": True})
        folded = self.rt.execute({"surface": "web", "url": orders_url, "session": "t1"})
        self.assertIn("+24 rows", folded)
        unfolded = self.rt.execute({"surface": "web", "session": "t1", "fullTable": True, "diff": True})
        self.assertIn("Inspect #ORD-425", unfolded)

        # 3. Web query="Search" followed by query="" must not short-circuit to = unchanged
        self.rt.execute({"reset": True})
        self.rt.execute({"surface": "web", "url": orders_url, "session": "t1", "query": "Search"})
        after_web_q = self.rt.execute({"surface": "web", "session": "t1", "query": "", "diff": True})
        self.assertNotIn("= unchanged", after_web_q)
        self.assertIn("Apply Filters", after_web_q)

        # 4. Chronological step execution when goto() appears mid-batch after actions on the first page
        self.rt.execute({"reset": True})
        mid_goto = self.rt.execute(
            {
                "code": (
                    f'let tab = await cua.createBrowserTab("chrome", "{pricing_url}"); '
                    'await tab.click("Generate Quote Token"); '
                    f'await tab.goto("{orders_url}"); '
                    'await tab.click("Inspect #ORD-409");'
                )
            }
        )
        self.assertNotIn("! errors:", mid_goto)
        self.assertIn("Approve #ORD-409", mid_goto)

        # 5. Partial failure isolation in execute_parallel: one failing agent must not abort healthy agents
        par_res = self.rt.execute(
            {
                "parallel": [
                    {"agent": "bad-agent", "code": "await cua.nonExistentMethod();"},
                    {
                        "agent": "good-agent",
                        "code": (
                            f'let t = await cua.createBrowserTab("chrome", "{pricing_url}"); '
                            'await t.click("Generate Quote Token");'
                        ),
                    },
                ]
            }
        )
        self.assertIn("=== [bad-agent] ===", par_res)
        self.assertIn("! error: AttributeError", par_res)
        self.assertIn("=== [good-agent] ===", par_res)
        self.assertIn("ENT-1000-OK", par_res)

    def test_15_cli_help_version_invalid_json_and_swiftc_fallback(self) -> None:
        from unittest.mock import patch
        from fmu import mac_engine

        fmu_bin = str(ROOT / "bin" / "fmu")
        help_proc = subprocess.run([fmu_bin, "--help"], capture_output=True, text=True, check=False)
        self.assertEqual(help_proc.returncode, 0)
        self.assertIn("Usage:", help_proc.stdout)

        ver_proc = subprocess.run([fmu_bin, "--version"], capture_output=True, text=True, check=False)
        self.assertEqual(ver_proc.returncode, 0)
        self.assertIn("faster-mac-use 0.1.0", ver_proc.stdout)

        bad_proc = subprocess.run([fmu_bin, "not-valid-json"], capture_output=True, text=True, check=False)
        self.assertEqual(bad_proc.returncode, 2)
        self.assertIn("Error: Invalid JSON argument", bad_proc.stderr)
        self.assertNotIn("Traceback", bad_proc.stderr)

        mcp_in = "\n".join(
            json.dumps(r)
            for r in [
                {"jsonrpc": "2.0", "id": 0, "method": "server/discover"},
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            ]
        ) + "\n"
        mcp_proc = subprocess.run([fmu_bin, "mcp"], input=mcp_in, capture_output=True, text=True, check=False)
        self.assertEqual(mcp_proc.returncode, 0)
        mcp_replies = [json.loads(line) for line in mcp_proc.stdout.splitlines()]
        self.assertEqual([r["id"] for r in mcp_replies], [0, 1, 2])
        self.assertEqual(mcp_replies[0]["error"]["code"], -32601)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            (tmp_root / "src" / "fmu" / "native").mkdir(parents=True)
            (tmp_root / "src" / "fmu" / "native" / "fmu_ax.swift").write_text("import Foundation\n")
            with (
                patch.object(mac_engine, "ROOT", tmp_root),
                patch.object(mac_engine, "PKG_DIR", tmp_root / "src" / "fmu"),
                patch.object(mac_engine.subprocess, "run", side_effect=FileNotFoundError("swiftc")),
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    mac_engine._resolve_ax_binary(root_dir=tmp_root)
                self.assertIn("xcode-select --install", str(ctx.exception))

    def test_16_virtual_webmcp_macros_save_autoload_and_replay(self) -> None:
        import os

        pricing_url = f"{self.server.base_url}/pricing"
        orders_url = f"{self.server.base_url}/orders"
        with tempfile.TemporaryDirectory() as macro_tmp:
            old_mdir = os.environ.get("FMU_MACROS_DIR")
            os.environ["FMU_MACROS_DIR"] = macro_tmp
            try:
                self.rt.execute({"reset": True})
                # 1. Auto-synthesize a Virtual WebMCP macro from recorded steps on /pricing (which has no native navigator.modelContext)
                rec_out = self.rt.execute(
                    {
                        "code": (
                            f'let tab = await cua.createBrowserTab("chrome", "{pricing_url}"); '
                            'await tab.click("Generate Quote Token"); '
                            'await tab.saveMacro("auto_quote_macro");'
                        )
                    }
                )
                self.assertIn("ENT-1000-OK", rec_out)

                # Reset runtime & open fresh /pricing tab: macro should auto-load into webmcp=[...] and execute in 1 turn
                self.rt.execute({"reset": True})
                replay_out = self.rt.execute(
                    {
                        "code": (
                            f'let tab = await cua.createBrowserTab("chrome", "{pricing_url}"); '
                            'await tab.callTool("auto_quote_macro");'
                        )
                    }
                )
                self.assertIn("auto_quote_macro", replay_out)
                self.assertIn("ENT-1000-OK", replay_out)

                # 2. Parameterized Virtual WebMCP macro with {{template}} interpolation on /orders
                save_out = self.rt.execute(
                    {
                        "code": (
                            'return await cua.saveMacro("macro_approve_order", ['
                            '  {op: "fill", uid: "Search orders", value: "{{customer}}"},'
                            '  {op: "click", uid: "Apply Filters"},'
                            '  {op: "click", uid: "Inspect #ORD-412"},'
                            '  {op: "fill", uid: "Approval note", value: "{{note}}"},'
                            '  {op: "select", uid: "Shipping tier", value: "{{tier}}"},'
                            '  {op: "click", uid: "Confirm Approval"}'
                            '], "127.0.0.1", "Parameterized order approval macro");'
                        )
                    }
                )
                self.assertIn('name="macro_approve_order"', save_out)

                list_out = self.rt.execute({"code": "return await cua.listMacros();"})
                self.assertIn("macro_approve_order", list_out)
                self.assertIn("auto_quote_macro", list_out)

                param_out = self.rt.execute(
                    {
                        "code": (
                            f'let otab = await cua.createBrowserTab("chrome", "{orders_url}"); '
                            'await otab.callTool("macro_approve_order", {customer: "Linear", note: "Synthesized Macro OK", tier: "freight"});'
                        )
                    }
                )
                self.assertIn("APPROVED #ORD-412 (freight)", param_out)
                self.assertIn("Synthesized Macro OK", param_out)

                del_out = self.rt.execute({"code": 'return await cua.deleteMacro("macro_approve_order");'})
                self.assertIn("ok=true", del_out)
            finally:
                if old_mdir is not None:
                    os.environ["FMU_MACROS_DIR"] = old_mdir
                else:
                    os.environ.pop("FMU_MACROS_DIR", None)

    def test_17_canvas_semantic_ocr_fusion_and_click(self) -> None:
        canvas_url = f"{self.server.base_url}/canvas"
        self.rt.execute({"reset": True})

        # Open /canvas: buttons "Deploy Cluster" and "Rollback Node" exist ONLY as pixels on <canvas>
        snap = self.rt.execute(
            {"code": f'let tab = await cua.createBrowserTab("chrome", "{canvas_url}");'}
        )
        self.assertIn("canvas", snap)
        self.assertIn("@ocr", snap)
        self.assertIn("Deploy Cluster", snap)
        self.assertIn("Rollback Node", snap)

        # Click the canvas-drawn "Deploy Cluster" button by its OCR semantic label
        click_out = self.rt.execute({"code": 'await tab.click("Deploy Cluster");'})
        self.assertNotIn("! errors:", click_out)
        self.assertIn("DEPLOY_CLUSTER_OK", click_out)
        self.assertIn("DEPLOYED", click_out)

        # Click the second canvas-drawn button ("Rollback Node") via its @ocr UID
        full_after = self.rt.execute({"code": "return await tab.snapshot();"})
        rollback_uid = None
        for line in full_after.splitlines():
            if line.startswith("@ocr") and "Rollback Node" in line:
                rollback_uid = line.split()[0]
                break
        self.assertIsNotNone(rollback_uid, f"Expected @ocr UID for Rollback Node in:\n{full_after}")
        rb_out = self.rt.execute({"code": f'await tab.click("{rollback_uid}");'})
        self.assertNotIn("! errors:", rb_out)
        self.assertIn("ROLLBACK_NODE_OK", rb_out)

    def test_18_outbound_mv3_extension_websocket_bridge(self) -> None:
        import base64
        import os
        import socket
        import struct
        import threading

        self.rt.execute({"reset": True})
        port = self.rt.browser.bridge_port
        self.assertGreater(port, 0)

        def send_masked_ws_text(sock: socket.socket, text: str) -> None:
            payload = text.encode("utf-8")
            mask_key = b"\x12\x34\x56\x78"
            hdr = bytearray([0x81])
            n = len(payload)
            if n < 126:
                hdr.append(0x80 | n)
            elif n <= 65535:
                hdr.append(0x80 | 126)
                hdr.extend(struct.pack("!H", n))
            else:
                hdr.append(0x80 | 127)
                hdr.extend(struct.pack("!Q", n))
            hdr.extend(mask_key)
            masked = bytearray(payload)
            for i in range(n):
                masked[i] ^= mask_key[i & 3]
            sock.sendall(bytes(hdr) + bytes(masked))

        def recv_unmasked_ws_text(sock: socket.socket) -> str:
            hdr = sock.recv(2)
            if len(hdr) < 2:
                raise ConnectionError("EOF")
            length = hdr[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", sock.recv(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", sock.recv(8))[0]
            buf = bytearray()
            while len(buf) < length:
                chunk = sock.recv(length - len(buf))
                if not chunk:
                    raise ConnectionError("EOF")
                buf.extend(chunk)
            return buf.decode("utf-8")

        client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_sock.settimeout(5.0)
        client_sock.connect(("127.0.0.1", port))
        ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET /ext HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        client_sock.sendall(handshake.encode("ascii"))
        resp = client_sock.recv(4096).decode("utf-8", errors="replace")
        self.assertIn("101 Switching Protocols", resp)

        stop_mock = threading.Event()
        rpc_calls: list[str] = []

        def mock_extension_worker() -> None:
            send_masked_ws_text(
                client_sock,
                json.dumps({"type": "hello", "client": "fmu-mv3-extension", "version": "0.1.0"}),
            )
            while not stop_mock.is_set():
                try:
                    raw_msg = recv_unmasked_ws_text(client_sock)
                except Exception:
                    break
                msg = json.loads(raw_msg)
                req_id = msg.get("id")
                method = msg.get("method")
                params = msg.get("params") or {}
                rpc_calls.append(str(method))
                if method == "createBrowserTab":
                    res = {
                        "tabId": 777,
                        "groupId": 42,
                        "sessionName": params.get("sessionName", "🤖 Agent"),
                        "url": params.get("url", "about:blank"),
                    }
                elif method == "evaluate":
                    res = True if params.get("runtimeInit") else []
                elif method == "executeBatch":
                    steps = params.get("steps") or []
                    clicked = any(s.get("op") == "click" for s in steps)
                    res = {
                        "url": "http://127.0.0.1/ext-live",
                        "title": "Live Extension Profile Tab",
                        "crc": "abcd1234" if not clicked else "ef567890",
                        "seq": 1 if not clicked else 2,
                        "count": 2,
                        "cursor": {"agent": "default", "x": 120, "y": 64, "uid": "@u1", "label": "One-Click SSO", "op": "click" if clicked else "init"},
                        "webMcpTools": ["liveProfileAuth"],
                        "canvases": [],
                        "errors": [],
                        "evalOutputs": [],
                        "escalations": [],
                        "lines": (
                            ['~ @u2 status "SSO_READY" -> "SSO_AUTHENTICATED"']
                            if clicked
                            else ['@u1 button "One-Click SSO"', '@u2 status "SSO_READY"']
                        ),
                    }
                else:
                    res = {"ok": True}
                send_masked_ws_text(client_sock, json.dumps({"id": req_id, "result": res}))

        worker = threading.Thread(target=mock_extension_worker, daemon=True)
        worker.start()
        try:
            self.assertTrue(self.rt.browser._ext_bridge.wait_connected(timeout=2.0))
            ext_out = self.rt.execute(
                {
                    "code": (
                        'let et = await cua.createBrowserTab("extension", "http://127.0.0.1/ext-live"); '
                        'await et.click("@u1");'
                    )
                }
            )
            self.assertIn('bridge="mv3"', ext_out)
            self.assertIn("SSO_AUTHENTICATED", ext_out)
            self.assertIn("createBrowserTab", rpc_calls)
            self.assertIn("executeBatch", rpc_calls)
        finally:
            stop_mock.set()
            try:
                client_sock.close()
            except Exception:
                pass
            worker.join(timeout=1.0)

        # Wait briefly for disconnect and verify seamless fallback to headless Chromium
        for _ in range(20):
            if not self.rt.browser.extension_connected:
                break
            time.sleep(0.02)
        self.assertFalse(self.rt.browser.extension_connected)
        fallback_out = self.rt.execute(
            {"code": f'let ht = await cua.createBrowserTab("chrome", "{self.server.base_url}/pricing");'}
        )
        self.assertNotIn('bridge="mv3"', fallback_out)
        self.assertIn("Generate Quote Token", fallback_out)

    def test_19_ax_observer_dirty_tracking_and_fast_diff(self) -> None:
        # Initial snapshot registers AXObserver on Calculator PID
        snap = self.rt.execute(
            {
                "surface": "app",
                "app": "Calculator",
                "steps": [{"op": "key", "name": "escape"}],
                "diff": False,
            }
        )
        self.assertIn('app="Calculator"', snap)

        # Unchanged Turn 2+ diff should fast-path via AXObserver dirty-tracking in < 5ms
        latencies_ms: list[float] = []
        last_diff = ""
        for _ in range(5):
            t0 = time.perf_counter()
            last_diff = self.rt.execute({"surface": "app", "app": "Calculator", "diff": True})
            latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        self.assertIn("= unchanged", last_diff)
        self.assertIn("obs=", last_diff)
        avg_ms = sum(latencies_ms) / len(latencies_ms)
        self.assertLess(avg_ms, 5.0, f"Expected AXObserver unchanged diff < 5.0ms, got {avg_ms:.2f}ms")

        # Mutating Calculator state must immediately reflect in the delta
        mut_diff = self.rt.execute(
            {
                "surface": "app",
                "app": "Calculator",
                "steps": [{"op": "click", "name": "5"}],
                "diff": True,
            }
        )
        self.assertNotIn("= unchanged", mut_diff)
        self.assertIn("5", mut_diff)

    def test_20_site_packages_swift_binary_fallback(self) -> None:
        from fmu import mac_engine

        self.assertTrue(
            (mac_engine.PKG_DIR / "native" / "fmu_ax.swift").is_file(),
            "Bundled fmu_ax.swift must exist",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_site_root = Path(tmpdir) / "site_root"
            fake_cache = Path(tmpdir) / "fmu_cache"
            fake_site_root.mkdir(parents=True, exist_ok=True)
            resolved = mac_engine._resolve_ax_binary(
                root_dir=fake_site_root,
                cache_dir=fake_cache,
            )
            self.assertEqual(resolved, fake_cache / "fmu-ax")
            self.assertTrue(resolved.is_file())

    def test_21_rich_text_contenteditable_draftjs_prosemirror_replace_and_append(self) -> None:
        import tempfile

        editor_url = f"{self.server.base_url}/editor"
        self.rt.execute({"reset": True})

        snap = self.rt.execute({"surface": "web", "url": editor_url, "session": "t1"})
        self.assertIn('textbox "What\'s happening?"', snap)
        self.assertIn('textbox "输入正文描述，真诚有价值的分享予人温暖"', snap)

        # Turn 1: Fill both Draft.js (with beforeinput listener that removes placeholder) and ProseMirror editors
        turn1 = self.rt.execute(
            {
                "surface": "web",
                "session": "t1",
                "steps": [
                    {"op": "fill", "target": "What's happening?", "value": "Draft v1 initial"},
                    {"op": "fill", "target": "填写标题", "value": "RedNote Title v1"},
                    {"op": "fill", "target": "输入正文描述", "value": "ProseMirror body v1"},
                    {"op": "eval", "code": "Boolean(document.getElementById('x-placeholder'))"},
                ],
            }
        )
        self.assertNotIn("! errors:", turn1)
        self.assertIn('val="Draft v1 initial"', turn1)
        self.assertNotIn("Draft v1 initialDraft v1 initial", turn1)
        self.assertIn('val="ProseMirror body v1"', turn1)
        self.assertIn("> false", turn1)

        # Turn 2: Fill again with multi-paragraph text using the same stable label (even after placeholder unmount)
        turn2 = self.rt.execute(
            {
                "surface": "web",
                "session": "t1",
                "steps": [
                    {"op": "fill", "target": "What's happening?", "value": "Draft v2 line 1\nDraft v2 line 2"},
                    {"op": "fill", "target": "输入正文描述", "value": "ProseMirror line 1\nProseMirror line 2"},
                    {"op": "eval", "code": "document.querySelectorAll('#x-composer [data-block=\"true\"]').length"},
                ],
            }
        )
        self.assertNotIn("! errors:", turn2)
        self.assertIn('textbox "What\'s happening?"', turn2)
        self.assertIn('-> val="Draft v2 line 1 Draft v2 line 2"', turn2)
        self.assertNotIn("Draft v1 initialDraft v2", turn2)
        self.assertIn('-> val="ProseMirror line 1 ProseMirror line 2"', turn2)
        self.assertNotIn("ProseMirror body v1ProseMirror", turn2)
        self.assertIn("> 2", turn2)

        # Turn 3: Explicit append=True + upload via sibling button of hidden <input type="file"> + publish
        with tempfile.TemporaryDirectory() as tmpdir:
            media_file = Path(tmpdir) / "demo_clip.mp4"
            media_file.write_bytes(b"fake-mp4-payload")
            turn3 = self.rt.execute(
                {
                    "surface": "web",
                    "session": "t1",
                    "steps": [
                        {"op": "fill", "target": "What's happening?", "value": " #fmu", "append": True},
                        {"op": "upload", "target": "Add photos or video", "files": [str(media_file)]},
                        {"op": "click", "target": "Publish Post"},
                        {"op": "eval", "code": "document.getElementById('publish-status').textContent"},
                    ],
                }
            )
        self.assertNotIn("! errors:", turn3)
        self.assertIn(
            'PUBLISHED x="Draft v2 line 1 | Draft v2 line 2 #fmu" title="RedNote Title v1" '
            'body="ProseMirror line 1 | ProseMirror line 2" media="MEDIA:demo_clip.mp4"',
            turn3,
        )

    def test_22_extension_connection_diagnostics_multi_window_and_file_upload(self) -> None:
        import subprocess

        self.rt.execute({"reset": True})
        if self.rt.browser._ext_bridge_inst is not None:
            self.rt.browser._ext_bridge_inst.shutdown()
            self.rt.browser._ext_bridge_inst = None

        # 1. Bridge status when disconnected
        st_disc = self.rt.execute({"bridgeStatus": True})
        self.assertIn("# fmu-web bridge connected=false", st_disc)
        self.assertIn("preferredPort=19222", st_disc)

        # 2. Default mode="chrome" on external URL (or surface="browser") fails fast instead of silent headless fallback
        with self.assertRaises(RuntimeError) as ctx_ext_url:
            self.rt.execute(
                {
                    "surface": "browser",
                    "url": "https://x.com/home",
                }
            )
        self.assertIn("MV3 extension not connected on ws://127.0.0.1:", str(ctx_ext_url.exception))
        self.assertIn("extension in Chrome or pass mode='headless'", str(ctx_ext_url.exception))

        # 3. Explicit mode="extension" on localhost also fails fast when disconnected
        with self.assertRaises(RuntimeError) as ctx:
            self.rt.execute(
                {
                    "surface": "web",
                    "mode": "extension",
                    "url": f"{self.server.base_url}/pricing",
                }
            )
        self.assertIn("MV3 extension not connected on ws://127.0.0.1:", str(ctx.exception))

        # 4. Run real extension/background.js in Node against ExtensionWebSocketBridge to verify multi-window & upload
        port = self.rt.browser.bridge_port
        bg_js_path = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
        bg_js_patched = bg_js_path.replace("ws://127.0.0.1:19222/ext", f"ws://127.0.0.1:{port}/ext")

        node_harness = f"""
const state = {{
  createdTabs: [],
  updatedTabs: [],
  groupCalls: [],
  groupUpdates: [],
  setFileCalls: [],
  tabs: [
    {{ id: 101, windowId: 100, groupId: 10, title: "Bilibili Video", url: "https://www.bilibili.com/video/1", status: "complete", active: true }},
    {{ id: 201, windowId: 200, groupId: -1, title: "Home / X", url: "https://x.com/home", status: "complete", active: true }},
  ],
  groups: [
    {{ id: 10, windowId: 100, title: "🤖 Agent", color: "blue", collapsed: false }},
  ],
  windows: [
    {{ id: 100, focused: true, type: "normal" }},
    {{ id: 200, focused: false, type: "normal" }},
  ],
}};

globalThis.chrome = {{
  tabs: {{
    query: async (q) => {{
      return state.tabs.filter(t => {{
        if (q.windowId !== undefined && t.windowId !== q.windowId) return false;
        if (q.groupId !== undefined && t.groupId !== q.groupId) return false;
        return true;
      }});
    }},
    get: async (tabId) => state.tabs.find(t => t.id === tabId) || {{ id: tabId, windowId: 200, status: "complete", url: "https://x.com/compose/post" }},
    create: async (opts) => {{
      const newTab = {{
        id: 888,
        windowId: opts.windowId || 100,
        groupId: -1,
        title: "Compose / X",
        url: opts.url || "about:blank",
        pendingUrl: undefined,
        status: "complete",
        active: Boolean(opts.active),
      }};
      state.createdTabs.push(opts);
      state.tabs.push(newTab);
      return newTab;
    }},
    update: async (tabId, opts) => {{
      state.updatedTabs.push({{ tabId, opts }});
      const t = state.tabs.find(x => x.id === tabId);
      if (t && opts.url) t.url = opts.url;
      return t || {{ id: tabId, windowId: 200, status: "complete", url: opts.url }};
    }},
    group: async (opts) => {{
      state.groupCalls.push(opts);
      if (opts.groupId) return opts.groupId;
      const winId = (opts.createProperties && opts.createProperties.windowId) || 200;
      const newGid = 55;
      state.groups.push({{ id: newGid, windowId: winId, title: "", color: "blue", collapsed: false }});
      return newGid;
    }},
    onRemoved: {{ addListener: () => {{}} }},
  }},
  tabGroups: {{
    query: async (q) => state.groups.filter(g => (q.title === undefined || g.title === q.title) && (q.windowId === undefined || g.windowId === q.windowId)),
    update: async (gid, opts) => {{ state.groupUpdates.push({{ gid, opts }}); return {{ id: gid, ...opts }}; }},
  }},
  windows: {{
    getAll: async () => state.windows,
  }},
  debugger: {{
    attach: async () => {{
      // Simulate MV3 service worker waking up when debugger is already attached
      throw new Error("Another debugger is already attached to the tab with id: 888");
    }},
    sendCommand: async (target, method, params) => {{
      if (method === "DOM.getDocument") return {{ root: {{ nodeId: 1 }} }};
      if (method === "DOM.querySelector") return {{ nodeId: 42 }};
      if (method === "DOM.setFileInputFiles") {{
        state.setFileCalls.push({{ tabId: target.tabId, files: params.files, nodeId: params.nodeId }});
        return {{}};
      }}
      if (method === "Runtime.evaluate") {{
        const expr = params.expression || "";
        if (expr.startsWith("window.__fmu.executeBatch(")) {{
          const hasUpload = expr.includes("demo_video.mp4") || expr.includes('"upload"');
          return {{
            result: {{
              value: {{
                url: "https://x.com/compose/post",
                title: "Compose / X",
                crc: hasUpload ? "55667788" : "11223344",
                seq: hasUpload ? 2 : 1,
                count: 2,
                cursor: {{ agent: "default", x: 80, y: 40, uid: "@u1", label: "Add photos or video", op: "click" }},
                webMcpTools: [],
                canvases: [],
                errors: [],
                evalOutputs: expr.includes("__fmuState") ? [JSON.stringify(state)] : [],
                escalations: hasUpload ? [{{ reason: "file_input", uid: "@u1", step: 1 }}] : [],
                lines: ['@u1 file "Add photos or video"', '@u2 textbox "What\\'s happening?"'],
              }}
            }}
          }};
        }}
        if (expr.includes("collectSnapshot('', 180, true).canvases")) {{
          return {{ result: {{ value: [] }} }};
        }}
        return {{ result: {{ value: true }} }};
      }}
      return {{}};
    }},
    onDetach: {{ addListener: () => {{}} }},
  }},
  alarms: {{
    create: () => {{}},
    onAlarm: {{ addListener: () => {{}} }},
  }},
  runtime: {{
    onInstalled: {{ addListener: () => {{}} }},
    onStartup: {{ addListener: () => {{}} }},
  }},
}};

{bg_js_patched}
setInterval(() => {{}}, 1000);
"""
        node_proc = subprocess.Popen(
            ["node", "-e", node_harness],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertTrue(self.rt.browser.ensure_bridge().wait_connected(timeout=3.0))
            st_conn = self.rt.execute({"code": "return await cua.getBridgeStatus();"})
            self.assertIn("connected=true", st_conn)

            # Open tab in Window 2 (matching "Home / X") with reuseExisting=false:
            # Must create a new tab in Window 2 (windowId=200), NOT overwrite tab 201, and NOT group into Window 1 (groupId=10)!
            ext_res = self.rt.execute(
                {
                    "code": (
                        'let xtab = await cua.createBrowserTab({mode: "chrome", url: "https://x.com/compose/post", window: "Home / X", reuseExisting: false}); '
                        'await xtab.upload("@u1", "/tmp/demo_video.mp4"); '
                        'await xtab.evaluate("__fmuState");'
                    )
                }
            )
            self.assertIn('bridge="mv3"', ext_res)
            self.assertIn("window=200", ext_res)
            state_lines = [l for l in ext_res.splitlines() if l.startswith("> {")]
            self.assertTrue(state_lines, f"Missing eval state in output: {ext_res}")
            ext_state = json.loads(state_lines[0][len("> "):])
            self.assertEqual(len(ext_state["createdTabs"]), 1)
            self.assertEqual(ext_state["createdTabs"][0]["windowId"], 200)
            self.assertEqual(ext_state["createdTabs"][0]["active"], False)
            self.assertEqual(len(ext_state["updatedTabs"]), 0)
            self.assertEqual(ext_state["groupCalls"][0]["tabIds"], [888])
            self.assertEqual(ext_state["groupCalls"][0]["createProperties"]["windowId"], 200)
            self.assertEqual(len(ext_state["setFileCalls"]), 1)
            self.assertEqual(ext_state["setFileCalls"][0]["files"], ["/tmp/demo_video.mp4"])
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

    def test_23_web_steps_schema_validation_and_aliases(self) -> None:
        editor_url = f"{self.server.base_url}/editor"
        self.rt.execute({"reset": True})
        self.rt.execute({"surface": "web", "url": editor_url, "session": "t1"})

        # 1. Missing op / unsupported op / missing target return explicit step errors instead of silent no-ops
        bad_steps_out = self.rt.execute(
            {
                "surface": "web",
                "session": "t1",
                "steps": [
                    {"target": "Publish Post"},
                    {"op": "teleport", "target": "Publish Post"},
                    {"op": "click"},
                ],
            }
        )
        self.assertIn("step 1: missing 'op'", bad_steps_out)
        self.assertIn("step 2: unsupported op 'teleport'", bad_steps_out)
        self.assertIn("step 3 (click): missing target", bad_steps_out)

        # 2. Aliases action="type", target="...", text="..." work seamlessly
        alias_out = self.rt.execute(
            {
                "surface": "web",
                "session": "t1",
                "steps": [
                    {"action": "type", "target": "填写标题", "text": "Schema Alias Title"},
                    {"action": "click", "target": "Publish Post"},
                ],
            }
        )
        self.assertNotIn("! errors:", alias_out)
        self.assertIn('title="Schema Alias Title"', alias_out)

    def test_24_mv3_field_learnings_csp_eval_upload_perm_draftjs_paste_tab_url_and_dialog_file(self) -> None:
        import subprocess
        import tempfile

        self.rt.execute({"reset": True})

        # 1. Multi-paragraph Draft.js fill (empty editor without selectNodeContents corruption + 3 paragraphs)
        #    and dialog-scoped <input type="file"> (>4 levels deep inside [role="dialog"] when earlier <input type="file"> exists)
        editor_url = f"{self.server.base_url}/editor"
        with tempfile.TemporaryDirectory() as tmpdir:
            modal_img = Path(tmpdir) / "thread_card.jpg"
            modal_img.write_bytes(b"fake-jpeg")
            ed_out = self.rt.execute(
                {
                    "surface": "web",
                    "url": editor_url,
                    "session": "t1",
                    "steps": [
                        {
                            "op": "fill",
                            "target": "What's happening?",
                            "value": "Para 1\n\nPara 2\n\nPara 3",
                        },
                        {
                            "op": "upload",
                            "target": "Add modal media",
                            "files": [str(modal_img)],
                        },
                        {
                            "op": "eval",
                            "code": (
                                "const blocks = Array.from(document.querySelectorAll('#x-composer [data-block=\"true\"]')).map(b => b.textContent.trim()); "
                                "return { "
                                "  blocks, "
                                "  corrupted: document.getElementById('x-composer').dataset.emptySelectCorrupted || '0', "
                                "  bgMedia: document.getElementById('bg-media-status').textContent, "
                                "  modalMedia: document.getElementById('modal-media-status').textContent "
                                "};"
                            ),
                        },
                    ],
                }
            )
        self.assertNotIn("! errors:", ed_out)
        eval_json_lines = [l for l in ed_out.splitlines() if l.startswith("> {")]
        self.assertTrue(eval_json_lines, f"Expected JSON eval output in: {ed_out}")
        ed_data = json.loads(eval_json_lines[0][2:])
        self.assertEqual(ed_data["blocks"], ["Para 1", "", "Para 2", "", "Para 3"])
        self.assertEqual(ed_data["corrupted"], "0")
        self.assertEqual(ed_data["bgMedia"], "BG_NO_MEDIA")
        self.assertEqual(ed_data["modalMedia"], "MODAL_MEDIA:thread_card.jpg")

        # 2. Strict-CSP page (no 'unsafe-eval') + top-level await in op: "eval" + TabHandle.url / TabHandle.title
        csp_url = f"{self.server.base_url}/strict-csp"
        csp_out = self.rt.execute(
            {
                "code": (
                    f'let tab = await cua.createBrowserTab({{mode: "headless", url: "{csp_url}"}});\n'
                    'let gotTab = await cua.getTab();\n'
                    'await gotTab.eval("const v = await Promise.resolve(\'CSP_AWAIT_OK\'); document.getElementById(\'csp-status\').textContent = v; return v;");\n'
                    'return { url: gotTab.url, title: gotTab.title };'
                )
            }
        )
        self.assertNotIn("! errors:", csp_out)
        self.assertIn("> CSP_AWAIT_OK", csp_out)
        self.assertIn(csp_url, csp_out)
        self.assertIn("Strict CSP App", csp_out)

        # 3. Verify bridge="mv3" extension/background.js handling of:
        #    (a) eval_csp escalation with top-level await + DOM refresh
        #    (b) explicit error when DOM.setFileInputFiles leaves el.files.length === 0 ("Allow access to file URLs" off)
        #    (c) TabHandle.url and TabHandle.title on MV3 extension tabs
        self.rt.execute({"reset": True})
        port = self.rt.browser.bridge_port
        bg_js_path = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
        bg_js_patched = bg_js_path.replace("ws://127.0.0.1:19222/ext", f"ws://127.0.0.1:{port}/ext")

        node_harness = f"""
const state = {{
  evalExpressions: [],
  allowFileUrls: false,
}};

globalThis.chrome = {{
  extension: {{
    isAllowedFileSchemeAccess: async () => state.allowFileUrls,
  }},
  tabs: {{
    query: async () => [{{ id: 301, windowId: 300, groupId: -1, title: "Compose / X", url: "https://x.com/compose/post", status: "complete", active: false }}],
    get: async (tabId) => ({{ id: tabId, windowId: 300, title: "Compose / X", status: "complete", url: "https://x.com/compose/post" }}),
    create: async (opts) => ({{ id: 301, windowId: opts.windowId || 300, groupId: -1, title: "Compose / X", url: opts.url || "https://x.com/compose/post", status: "complete", active: false }}),
    update: async (tabId, opts) => ({{ id: tabId, windowId: 300, title: "Compose / X", status: "complete", url: opts.url }}),
    group: async () => 77,
    onRemoved: {{ addListener: () => {{}} }},
  }},
  tabGroups: {{
    query: async () => [],
    update: async (gid, opts) => ({{ id: gid, ...opts }}),
  }},
  windows: {{
    getAll: async () => [{{ id: 300, focused: false, type: "normal" }}],
  }},
  debugger: {{
    attach: async () => {{}},
    sendCommand: async (target, method, params) => {{
      if (method === "DOM.getDocument") return {{ root: {{ nodeId: 1 }} }};
      if (method === "DOM.querySelector") return {{ nodeId: 99 }};
      if (method === "DOM.setFileInputFiles") return {{}};
      if (method === "Runtime.evaluate") {{
        const expr = params.expression || "";
        state.evalExpressions.push(expr);
        if (expr.startsWith("window.__fmu.executeBatch(")) {{
          const isUpload = expr.includes("blocked.jpg");
          const isCspEval = expr.includes("await Promise.resolve");
          return {{
            result: {{
              value: {{
                url: "https://x.com/compose/post",
                title: "Compose / X",
                crc: "11112222",
                seq: 1,
                count: 2,
                useDiff: true,
                cursor: {{ agent: "default", x: 50, y: 50, uid: "@u1", label: "Add photos or video", op: "click" }},
                webMcpTools: [],
                canvases: [],
                errors: [],
                evalOutputs: [],
                escalations: isCspEval
                  ? [{{ step: 1, reason: "eval_csp", code: "const r = await Promise.resolve('MV3_CSP_OK'); return r;" }}]
                  : (isUpload ? [{{ step: 1, reason: "file_input", uid: "@u1" }}] : []),
                lines: ['@u1 button "Add photos or video"'],
              }}
            }}
          }};
        }}
        if (expr.startsWith("(async () =>")) {{
          if (expr.includes("__enableFileAccess")) {{
            state.allowFileUrls = true;
            return {{ result: {{ value: "ENABLED_CHECK" }} }};
          }}
          return {{ result: {{ value: "MV3_CSP_OK" }} }};
        }}
        if (expr.includes("fileCount")) {{
          return {{ result: {{ value: {{ found: true, fileCount: 0 }} }} }};
        }}
        if (expr.includes("refreshAfterOcr")) {{
          return {{ result: {{ value: {{ crc: "33334444", seq: 2, count: 2, lines: ['+ @u9 status "MV3_CSP_OK"'] }} }} }};
        }}
        return {{ result: {{ value: true }} }};
      }}
      return {{}};
    }},
    onDetach: {{ addListener: () => {{}} }},
  }},
  alarms: {{
    create: () => {{}},
    onAlarm: {{ addListener: () => {{}} }},
  }},
  runtime: {{
    onInstalled: {{ addListener: () => {{}} }},
    onStartup: {{ addListener: () => {{}} }},
  }},
}};

{bg_js_patched}
setInterval(() => {{}}, 1000);
"""
        node_proc = subprocess.Popen(
            ["node", "-e", node_harness],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertTrue(self.rt.browser.ensure_bridge().wait_connected(timeout=3.0))
            mv3_out = self.rt.execute(
                {
                    "code": (
                        'let xtab = await cua.createBrowserTab({mode: "chrome", url: "https://x.com/compose/post", reuseExisting: true});\n'
                        'let cur = await cua.getTab();\n'
                        'let evalOut = await cur.eval("const r = await Promise.resolve(\'MV3_CSP_OK\'); return r;").diff();\n'
                        'let upOut1 = await cur.upload("@u1", "/tmp/blocked.jpg").diff();\n'
                        'await cur.eval("const x = await Promise.resolve(\'__enableFileAccess\'); return x;").diff();\n'
                        'let upOut2 = await cur.upload("@u1", "/tmp/blocked.jpg").diff();\n'
                        'return { url: cur.url, title: cur.title, evalOut: evalOut, upOut1: upOut1, upOut2: upOut2 };'
                    )
                }
            )
            self.assertIn("https://x.com/compose/post", mv3_out)
            self.assertIn("Compose / X", mv3_out)
            self.assertIn("> MV3_CSP_OK", mv3_out)
            self.assertIn('+ @u9 status "MV3_CSP_OK"', mv3_out)
            expected_err = 'upload failed: enable "Allow access to file URLs" for the fmu extension in chrome://extensions'
            self.assertEqual(mv3_out.count(expected_err), 2)
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

    def test_25_telemetry_perf_logging_privacy_sanitization_and_perf_report(self) -> None:
        from fmu.telemetry import flush_telemetry, load_perf_events

        with tempfile.TemporaryDirectory(prefix="fmu-perf-audit-") as tmp_logs:
            prev_dir = os.environ.get("FMU_LOG_DIR")
            os.environ["FMU_LOG_DIR"] = tmp_logs
            try:
                self.rt.execute({"reset": True})
                secret_url = f"{self.server.base_url}/orders?api_key=TOP_SECRET_123#frag_secret"
                self.rt.execute(
                    {
                        "surface": "web",
                        "url": secret_url,
                        "session": "t1",
                    }
                )
                self.rt.execute(
                    {
                        "surface": "web",
                        "session": "t1",
                        "steps": [
                            {"op": "fill", "target": "Search orders", "value": "SUPER_SECRET_PASSWORD_999"},
                            {"op": "click", "target": "NonExistentButton"},
                        ],
                    }
                )
                self.rt.execute({"app": "Calculator", "steps": [{"op": "key", "name": "escape"}]})
                flush_telemetry()

                events = load_perf_events(days=7, log_dir=Path(tmp_logs))
                self.assertGreaterEqual(len(events), 3)

                raw_dump = "\n".join(p.read_text(encoding="utf-8") for p in Path(tmp_logs).glob("perf-*.jsonl"))
                self.assertNotIn("TOP_SECRET_123", raw_dump)
                self.assertNotIn("frag_secret", raw_dump)
                self.assertNotIn("SUPER_SECRET_PASSWORD_999", raw_dump)

                web_events = [e for e in events if e.get("surface") == "web"]
                app_events = [e for e in events if e.get("surface") == "app"]
                self.assertGreaterEqual(len(web_events), 2)
                self.assertGreaterEqual(len(app_events), 1)
                self.assertIn("in_page_snap", web_events[0]["timings_ms"])
                self.assertIn("bridge_overhead", web_events[0]["timings_ms"])
                self.assertIn("native_ax", app_events[0]["timings_ms"])

                md_report = self.rt.execute({"code": "return await cua.getPerfReport({days: 7});"})
                self.assertIn("# fmu-perf window=7d", md_report)
                self.assertIn("## Latency by Surface (ms)", md_report)
                self.assertIn("web:playwright:", md_report)
                self.assertIn("app:ax:", md_report)

                json_report_raw = self.rt.execute({"perfReport": {"days": 7, "format": "json"}})
                json_report = json.loads(json_report_raw)
                self.assertEqual(json_report["total_calls"], len(events))
                self.assertGreater(json_report["tokens"]["avg"], 0)
                self.assertEqual(json_report["error_calls"], 1)

                cli_out = subprocess.check_output(
                    [sys.executable, str(ROOT / "bin" / "fmu"), "--perf", "7"],
                    env={**os.environ, "FMU_LOG_DIR": tmp_logs},
                    text=True,
                )
                self.assertIn("# fmu-perf window=7d", cli_out)
            finally:
                if prev_dir is None:
                    os.environ.pop("FMU_LOG_DIR", None)
                else:
                    os.environ["FMU_LOG_DIR"] = prev_dir

    def test_26_macos_menubar_working_status_and_one_click_stop(self) -> None:
        import threading

        url = f"{self.server.base_url}/pricing"
        with BackgroundIntrusionGuard() as guard:
            # 1. Trigger a native macOS AX command + web command and verify menu bar working status & live PiP preview
            self.rt.execute({"app": "Calculator", "steps": [{"op": "key", "name": "escape"}]})
            st_app = self.rt.execute({"statusBar": True})
            if "hud=true" in st_app and "preview=false" in st_app:
                st_app = self.rt.execute({"togglePreview": True})
            self.assertIn("# fmu-menubar", st_app)
            self.assertIn("visible=true", st_app)
            self.assertIn("hud=true", st_app)
            self.assertIn("preview=true", st_app)
            self.assertIn("stopped=false", st_app)
            self.assertIn('state="working"', st_app)
            self.assertIn('target="Calculator"', st_app)
            self.assertIn("⏹ Stop", st_app)

            # 1b. Trigger Click-to-Highlight Target Window on Screen and verify highlight=true
            hl_out = self.rt.execute({"code": "return await cua.highlightWindow();"})
            self.assertIn("highlight=true", hl_out)

            # 1c. Verify 1-click Live Window Preview collapse/expand toggle and UI snapshot export
            tog_off = self.rt.execute({"togglePreview": True})
            self.assertIn("preview=false", tog_off)
            tog_on = self.rt.execute({"togglePreview": True})
            self.assertIn("preview=true", tog_on)
            with tempfile.TemporaryDirectory() as ui_tmp:
                dump_out = self.rt.execute({"dumpUiSnapshots": ui_tmp})
                self.assertIn("fmu-ui-snapshots", dump_out)
                self.assertTrue((Path(ui_tmp) / "hud-working.png").exists())

            self.rt.execute({"surface": "web", "url": url, "session": "t1"})
            # Wait briefly for async menubar status queue worker to flush
            for _ in range(20):
                st_web = self.rt.execute({"code": "return await cua.getStatus();"})
                if 'surface="web"' in st_web:
                    break
                time.sleep(0.02)
            self.assertIn('state="working"', st_web)
            self.assertIn('surface="web"', st_web)
            self.assertIn("preview=true", st_web)
            self.assertIn("⏹ Stop", st_web)

            # 2. Simulate 1-click on the top-right macOS menu bar item -> stops agent immediately
            click_stop = self.rt.mac.execute({"clickStatusBar": True})
            self.assertIn('state="stopped"', click_stop)
            self.assertIn("stopped=true", click_stop)
            st_stopped = self.rt.execute({"statusBar": True})
            self.assertIn("stopped=true", st_stopped)
            self.assertIn('state="stopped"', st_stopped)
            self.assertIn("🛑 fmu Stopped  ▶ Resume", st_stopped)

            # 3. Verify subsequent web, app, and REPL calls are blocked by the stop latch
            blocked_web = self.rt.execute(
                {"surface": "web", "session": "t1", "steps": [{"op": "click", "target": "Generate Quote Token"}]}
            )
            self.assertIn("Error: [USER_STOPPED]", blocked_web)

            blocked_app = self.rt.execute({"app": "Calculator", "steps": [{"op": "click", "name": "7"}]})
            self.assertIn("Error: [USER_STOPPED]", blocked_app)

            blocked_repl = self.rt.execute({"code": 'let c = await cua.getApp("Calculator"); await c.click("7");'})
            self.assertIn("Error: [USER_STOPPED]", blocked_repl)

            # 4. Simulate 1-click on the menu bar item again (when stopped) -> resumes agent
            click_resume = self.rt.mac.execute({"clickStatusBar": True})
            self.assertIn("stopped=false", click_resume)
            self.assertIn('state="idle"', click_resume)
            self.assertIn("⚪ fmu Idle", click_resume)
            self.assertNotIn("⏹ Stop", click_resume)
            st_resumed = self.rt.execute({"statusBar": True})
            self.assertIn("stopped=false", st_resumed)
            self.assertIn('state="idle"', st_resumed)

            # 5. Verify mid-flight interrupt of a long wait when user clicks Stop in menu bar
            inflight_results: list[str] = []

            def run_long_wait() -> None:
                res = self.rt.execute(
                    {
                        "surface": "web",
                        "session": "t1",
                        "steps": [{"op": "wait", "ms": 3000}],
                    }
                )
                inflight_results.append(res)

            t0 = time.perf_counter()
            worker = threading.Thread(target=run_long_wait, daemon=True)
            worker.start()
            time.sleep(0.12)
            self.rt.mac.execute({"clickStatusBar": True})
            worker.join(timeout=2.0)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self.assertTrue(inflight_results, "In-flight wait thread did not finish")
            self.assertIn("Error: [USER_STOPPED]", inflight_results[0])
            self.assertLess(elapsed_ms, 800.0, f"Expected mid-flight stop in <800ms, took {elapsed_ms:.1f}ms")

            # 6. Resume via cua.resume() and verify normal operation works again
            res_out = self.rt.execute({"code": "return await cua.resume();"})
            self.assertIn("stopped=false", res_out)
            ok_after = self.rt.execute(
                {"surface": "web", "session": "t1", "steps": [{"op": "click", "target": "Generate Quote Token"}]}
            )
            self.assertIn("ENT-1000-OK", ok_after)

        rep = guard.stop()
        self.assertTrue(rep.passed_background_invariant, f"Intrusion detected: {rep}")

    def test_27_field_audit_fixes(self) -> None:
        self.rt.execute({"reset": True})
        orders_url = f"{self.server.base_url}/orders"
        snap0 = self.rt.execute({"surface": "web", "url": orders_url, "session": "t1"})
        inspect_uid = next(
            (l.split()[0] for l in snap0.splitlines() if 'button "Inspect #ORD-401"' in l),
            "",
        )
        self.assertTrue(inspect_uid.startswith("@u"), f"Missing Inspect #ORD-401 UID in: {snap0}")

        # 1. SPA DOM re-render (replace the button node so old ref.deref().isConnected == false)
        #    and custom element with closed shadow root (_sr) like <xhs-publish-btn>
        spa_and_shadow = self.rt.execute(
            {
                "surface": "web",
                "session": "t1",
                "steps": [
                    {
                        "op": "eval",
                        "code": (
                            "const btns = Array.from(document.querySelectorAll('button')); "
                            "const oldBtn = btns.find(b => b.textContent.includes('Inspect #ORD-401')); "
                            "const clone = oldBtn.cloneNode(true); "
                            "clone.setAttribute('onclick', oldBtn.getAttribute('onclick') || ''); "
                            "oldBtn.replaceWith(clone); "
                            "const host = document.createElement('xhs-publish-btn'); "
                            "const sr = host.attachShadow({mode: 'closed'}); "
                            "host._sr = sr; "
                            "const sbtn = document.createElement('button'); "
                            "sbtn.textContent = '发布笔记'; "
                            "sbtn.style.width = '80px'; sbtn.style.height = '32px'; "
                            "sbtn.onclick = () => { document.body.dataset.xhsPublished = 'YES_SHADOW'; }; "
                            "sr.appendChild(sbtn); "
                            "document.body.appendChild(host); "
                            "return oldBtn.isConnected;"
                        ),
                    },
                    {"op": "click", "uid": inspect_uid},
                ],
                "diff": False,
            }
        )
        self.assertNotIn("! errors:", spa_and_shadow)
        self.assertIn("> false", spa_and_shadow)
        self.assertIn("Approve #ORD-401", spa_and_shadow)
        self.assertIn('button "发布笔记"', spa_and_shadow)

        # Click the button inside the closed shadow root (_sr) by text
        shadow_click = self.rt.execute(
            {
                "surface": "web",
                "session": "t1",
                "steps": [
                    {"op": "click", "target": "发布笔记"},
                    {"op": "eval", "code": "document.body.dataset.xhsPublished"},
                ],
            }
        )
        self.assertNotIn("! errors:", shadow_click)
        self.assertIn("> YES_SHADOW", shadow_click)

        # 2. Folded table diff compactness: replacing 30 tbody rows does not emit 30+ individual -/+ folded lines
        self.rt.execute(
            {
                "surface": "web",
                "session": "t1",
                "steps": [
                    {
                        "op": "eval",
                        "code": (
                            "const tb = document.querySelector('tbody'); "
                            "let html = ''; "
                            "for (let i = 1; i <= 30; i++) { "
                            "  html += `<tr><td><a href='#r${i}'>Thread A-${i}</a></td><td><button>Archive A-${i}</button></td></tr>`; "
                            "} "
                            "tb.innerHTML = html;"
                        ),
                    }
                ],
                "diff": False,
            }
        )
        table_diff = self.rt.execute(
            {
                "surface": "web",
                "session": "t1",
                "steps": [
                    {
                        "op": "eval",
                        "code": (
                            "const tb = document.querySelector('tbody'); "
                            "let html = ''; "
                            "for (let i = 1; i <= 30; i++) { "
                            "  html += `<tr><td><a href='#n${i}'>Thread B-${i}</a></td><td><button>Archive B-${i}</button></td></tr>`; "
                            "} "
                            "tb.innerHTML = html;"
                        ),
                    }
                ],
                "diff": True,
            }
        )
        self.assertIn("folded items", table_diff)
        self.assertNotIn("Thread A-25", table_diff)
        self.assertNotIn("Thread B-25", table_diff)
        self.assertLess(len(table_diff.splitlines()), 35, f"Folded table diff too verbose: {table_diff}")

        # 3. Native fmu-ax tolerant StepSpec: op="snapshot" in steps, string path="View > Basic", float ms
        ax_out = self.rt.execute(
            {
                "surface": "app",
                "app": "Calculator",
                "steps": [
                    {"op": "wait", "ms": 10.5},
                    {"op": "menu", "path": "View > Basic"},
                    {"op": "snapshot"},
                ],
            }
        )
        self.assertNotIn("! errors:", ax_out)
        self.assertNotIn("Error: invalid request schema", ax_out)
        self.assertIn('app="Calculator"', ax_out)

    def test_28_live_pixel_view_screencast_fragmented_ws_and_zoom(self) -> None:
        import base64
        import hashlib
        import socket
        import struct
        import threading

        # Minimal valid 1x1 JPEG bytes + padding comment so base64 > 160 KB for RFC 6455 fragmentation test
        tiny_jpeg = (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xfe\x00\x08fmu-test"
            b"\xff\xdb\x00C\x00" + (b"\x08" * 64)
            + b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
            b"\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\x7f\xff\xd9"
        )
        tiny_b64 = base64.b64encode(tiny_jpeg).decode("ascii")

        def send_masked_frame(sock: socket.socket, fin: int, opcode: int, payload: bytes) -> None:
            mask_key = b"\x37\xfa\x21\x3d"
            header = bytearray([((fin & 1) << 7) | (opcode & 0x0F)])
            ln = len(payload)
            if ln < 126:
                header.append(0x80 | ln)
            elif ln < 65536:
                header.append(0x80 | 126)
                header.extend(struct.pack("!H", ln))
            else:
                header.append(0x80 | 127)
                header.extend(struct.pack("!Q", ln))
            header.extend(mask_key)
            masked = bytes(b ^ mask_key[i & 3] for i, b in enumerate(payload))
            sock.sendall(bytes(header) + masked)

        def recv_server_text(sock: socket.socket) -> str:
            b0, b1 = sock.recv(2)
            ln = b1 & 0x7F
            if ln == 126:
                ln = struct.unpack("!H", sock.recv(2))[0]
            elif ln == 127:
                ln = struct.unpack("!Q", sock.recv(8))[0]
            buf = bytearray()
            while len(buf) < ln:
                buf.extend(sock.recv(ln - len(buf)))
            return bytes(buf).decode("utf-8")

        # 1. Verify RFC 6455 multi-fragment (>150 KB) + interleaved Ping + live screencastFrame push
        from fmu.browser_engine import ExtensionWebSocketBridge

        received_events: list[dict] = []
        bridge = ExtensionWebSocketBridge(preferred_port=0, on_event=lambda ev: received_events.append(ev))
        client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        stop_frag = threading.Event()
        try:
            client_sock.connect(("127.0.0.1", bridge.port))
            ws_key = base64.b64encode(b"fragment-ws-test").decode("ascii")
            client_sock.sendall(
                (
                    f"GET /ext HTTP/1.1\r\nHost: 127.0.0.1:{bridge.port}\r\n"
                    f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                    f"Sec-WebSocket-Key: {ws_key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
                ).encode("ascii")
            )
            hs = client_sock.recv(1024).decode("latin1")
            expected_accept = base64.b64encode(
                hashlib.sha1((ws_key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
            ).decode("ascii")
            self.assertIn(expected_accept, hs)
            send_masked_frame(client_sock, 1, 0x1, b'{"type":"hello","version":"0.3.0"}')
            self.assertTrue(bridge.wait_connected(timeout=2.0))

            def frag_worker() -> None:
                req_raw = recv_server_text(client_sock)
                req_obj = json.loads(req_raw)
                # Push a live screencastFrame event BEFORE the RPC response
                ev_bytes = json.dumps(
                    {
                        "type": "screencastFrame",
                        "tabId": 909,
                        "data": tiny_b64,
                        "vw": 1440,
                        "vh": 900,
                        "ts": 123456,
                    }
                ).encode("utf-8")
                send_masked_frame(client_sock, 1, 0x1, ev_bytes)

                # Send a >180 KB RPC response split across 3 fragments + an interleaved Ping control frame
                big_pad = "A" * 190_000
                resp_bytes = json.dumps(
                    {
                        "id": req_obj["id"],
                        "result": {"ok": True, "padLen": len(big_pad), "pad": big_pad, "preview": {"jpeg": tiny_b64, "vw": 1440, "vh": 900}},
                    }
                ).encode("utf-8")
                c1 = resp_bytes[:70_000]
                c2 = resp_bytes[70_000:140_000]
                c3 = resp_bytes[140_000:]
                send_masked_frame(client_sock, 0, 0x1, c1)
                send_masked_frame(client_sock, 1, 0x9, b"ping")  # Interleaved RFC 6455 Ping!
                send_masked_frame(client_sock, 0, 0x0, c2)
                send_masked_frame(client_sock, 1, 0x0, c3)

            t_frag = threading.Thread(target=frag_worker, daemon=True)
            t_frag.start()
            rpc_res = bridge.call_rpc("capturePreview", {"tabId": 909}, timeout=3.0)
            t_frag.join(timeout=2.0)
            self.assertEqual(rpc_res["padLen"], 190_000)
            self.assertEqual(len(rpc_res["pad"]), 190_000)
            self.assertEqual(len(received_events), 1)
            self.assertEqual(received_events[0]["type"], "screencastFrame")
            self.assertEqual(received_events[0]["tabId"], 909)
        finally:
            stop_frag.set()
            try:
                client_sock.close()
            except Exception:
                pass
            bridge.shutdown()

        # 2. Verify end-to-end real pixel Live View on headless web + HD Zoom toggle + native Calculator
        with BackgroundIntrusionGuard() as guard:
            self.rt.execute({"reset": True})
            preview_jpg = Path("/tmp/fmu-preview-web.jpg")
            preview_jpg.unlink(missing_ok=True)
            pricing_url = f"{self.server.base_url}/pricing"
            web_out = self.rt.execute(
                {
                    "surface": "web",
                    "mode": "headless",
                    "url": pricing_url,
                    "session": "live1",
                    "steps": [{"op": "click", "target": "Generate Quote Token"}],
                }
            )
            self.assertIn("ENT-1000-OK", web_out)
            for _ in range(35):
                if preview_jpg.is_file():
                    break
                time.sleep(0.025)
            self.assertTrue(preview_jpg.is_file(), "Expected real CDP JPEG viewport capture at /tmp/fmu-preview-web.jpg")
            self.assertTrue(preview_jpg.read_bytes().startswith(b"\xff\xd8\xff"))

            st_web = ""
            for _ in range(35):
                st_web = self.rt.execute({"statusBar": True})
                if 'surface="web"' in st_web and "hasFrame=true" in st_web:
                    break
                time.sleep(0.025)
            self.assertIn('surface="web"', st_web)
            self.assertIn("hasFrame=true", st_web)
            self.assertIn("zoomed=false", st_web)

            # Toggle HD Zoom on and off
            z_on = self.rt.execute({"code": "return await cua.toggleZoom();"})
            self.assertIn("zoomed=true", z_on)
            z_off = self.rt.execute({"toggleZoom": True})
            self.assertIn("zoomed=false", z_off)

            # Native Calculator live pixel capture + snapshot export
            self.rt.execute({"app": "Calculator", "steps": [{"op": "click", "name": "8"}]})
            with tempfile.TemporaryDirectory() as snap_dir:
                snap_res = self.rt.execute({"dumpUiSnapshots": snap_dir})
                self.assertIn("fmu-ui-snapshots", snap_res)
                hud_png = Path(snap_dir) / "hud-working.png"
                self.assertTrue(hud_png.is_file())
                self.assertGreater(hud_png.stat().st_size, 4000)

        rep = guard.stop()
        self.assertTrue(rep.passed_background_invariant, f"Intrusion detected: {rep}")

    def test_29_checkout_booking_regressions_eval_nav_repl_iframes_and_mui_cards(self) -> None:
        self.rt.execute({"reset": True})
        checkout_url = f"{self.server.base_url}/checkout"
        vehicles_url = f"{self.server.base_url}/vehicles"
        csp_url = f"{self.server.base_url}/strict-csp"

        # 1. Issue 4 & Issue 3: Visible cross-origin PCI <iframe> placeholders + createBrowserTab kwargs + JS REPL await tab.eval / shorthand
        snap_out = self.rt.execute(
            {
                "code": (
                    f'const tab = await cua.createBrowserTab("headless", "{checkout_url}", sessionName="🤖 Checkout", windowId=42);\n'
                    "return await tab.snapshot();"
                )
            }
        )
        self.assertIn('group="🤖 Checkout"', snap_out)
        for fid, ftitle in (
            ("first-data-payment-field-name", "Cardholder Name"),
            ("first-data-payment-field-card", "Card Number"),
            ("first-data-payment-field-exp", "Expiration Date"),
            ("first-data-payment-field-cvv", "Security Code"),
        ):
            self.assertIn(f'id="{fid}"', snap_out)
            self.assertIn(f'title="{ftitle}"', snap_out)
        self.assertIn(f'origin="http://localhost:{self.server.port}"', snap_out)
        self.assertIn("[visible, cross-origin]", snap_out)
        # Even <iframe role="presentation"> must emit role `iframe` (not `presentation`)
        self.assertNotIn('presentation "Expiration Date"', snap_out)
        self.assertIn('iframe "Expiration Date"', snap_out)
        # Hidden / 3440x1 / same-origin about:blank iframes must NOT pollute snapshot
        self.assertNotIn("tracking-pixel", snap_out)
        self.assertNotIn("Stripe Metrics", snap_out)
        self.assertNotIn("hidden-beacon", snap_out)
        self.assertNotIn("same-origin-blank", snap_out)
        # CSS display:contents button must be detected as visible and clickable
        self.assertIn('button "Recent search card"', snap_out)
        # Verify DOM ordering: payment iframes appear between Email address and Shipping Address
        self.assertLess(
            snap_out.index("Email address"),
            snap_out.index("first-data-payment-field-card"),
        )
        self.assertLess(
            snap_out.index("first-data-payment-field-cvv"),
            snap_out.index("Shipping Address"),
        )

        # Also verify First Data HTTPS origin reporting, cua.getTab(sessionName=...) reuse, and display:contents click
        fd_snap = self.rt.execute(
            {
                "code": (
                    'const tab = await cua.getTab(sessionName="🤖 Checkout");\n'
                    "await tab.click('Recent search card').eval(\"(() => { document.getElementById('first-data-payment-field-card').setAttribute('src', 'https://lib.paymentjs.firstdata.com/v2/card'); return 'SWAPPED'; })()\").snapshot();"
                )
            }
        )
        self.assertIn("tab=t1 ", fd_snap)
        self.assertIn("> SWAPPED", fd_snap)
        self.assertIn('origin="https://lib.paymentjs.firstdata.com"', fd_snap)
        self.assertIn('val="YYC Pickup"', fd_snap)

        # Issue 3: JS-style REPL `await tab.eval(...)`, `.length`, & `{ fields, iframes }` shorthand transpile cleanly
        repl_val_out = self.rt.execute(
            {
                "code": (
                    'const tab = await cua.getTab(sessionName="🤖 Checkout");\n'
                    "const fields = await tab.eval(\"Array.from(document.querySelectorAll('input')).map(el => el.id)\");\n"
                    "const iframes = await tab.eval(\"(() => { const n = document.querySelectorAll('iframe.pci-frame').length; return n; })()\");\n"
                    'return { url: await tab.eval("location.href"), fields, fieldCount: fields.length, strLen: "abcd".length, iframes };'
                )
            }
        )
        self.assertNotIn("TabHandle", repl_val_out)
        self.assertIn(checkout_url, repl_val_out)
        self.assertIn("'fields': ['checkout-email', 'shipping-address']", repl_val_out)
        self.assertIn("'fieldCount': 2", repl_val_out)
        self.assertIn("'strLen': 4", repl_val_out)
        self.assertIn("'iframes': 4", repl_val_out)

        # 2. Issue 1: tab.eval() on sync IIFEs, async IIFEs / Promises, multi-statement blocks, >4 KB JSON.stringify, and truncation notice
        eval_batch_out = self.rt.execute(
            {
                "code": (
                    "const tab = await cua.getTab();\n"
                    'tab.eval("(() => { const x = 1; return JSON.stringify({ syncIife: x }); })()");\n'
                    'tab.eval("(async () => { await new Promise(r => setTimeout(r, 15)); return JSON.stringify({ asyncIife: true }); })();");\n'
                    'tab.eval("const a = 11; const b = 31; JSON.stringify({ multiStmtSum: a + b });");\n'
                    "tab.eval(\"JSON.stringify(Array.from({length: 110}, (_, i) => { return { idx: i, label: 'return date item ' + 'X'.repeat(48) }; }))\");\n"
                    "return await tab.flush();"
                )
            }
        )
        self.assertIn('> {"syncIife":1}', eval_batch_out)
        self.assertIn('> {"asyncIife":true}', eval_batch_out)
        self.assertIn('> {"multiStmtSum":42}', eval_batch_out)
        big_lines = [
            line[2:]
            for line in eval_batch_out.splitlines()
            if line.startswith("> [") and "return date item" in line
        ]
        self.assertEqual(len(big_lines), 1, f"Expected >4KB JSON array output line, got: {eval_batch_out[:600]}")
        self.assertGreater(len(big_lines[0]), 7000)
        parsed_big = json.loads(big_lines[0])
        self.assertEqual(len(parsed_big), 110)
        self.assertEqual(parsed_big[109]["idx"], 109)

        # Verify explicit truncation notice when output exceeds FMU_MAX_EVAL_CHARS (never silently dropped)
        prev_cap = os.environ.get("FMU_MAX_EVAL_CHARS")
        os.environ["FMU_MAX_EVAL_CHARS"] = "512"
        try:
            trunc_out = self.rt.execute(
                {
                    "code": (
                        "const tab = await cua.getTab();\n"
                        'tab.eval("(() => { return \'Z\'.repeat(5000); })()");\n'
                        "return await tab.flush();"
                    )
                }
            )
            self.assertIn("[truncated: showing 512 of 5000 chars; set FMU_MAX_EVAL_CHARS to increase]", trunc_out)
        finally:
            if prev_cap is None:
                os.environ.pop("FMU_MAX_EVAL_CHARS", None)
            else:
                os.environ["FMU_MAX_EVAL_CHARS"] = prev_cap

        # Strict-CSP page sync IIFE + async IIFE via CDP Runtime.evaluate escalation
        csp_iife_out = self.rt.execute(
            {
                "code": (
                    f'const ctab = await cua.createBrowserTab("headless", "{csp_url}");\n'
                    'ctab.eval("(() => { const k = \'CSP_SYNC_IIFE_OK\'; return k; })()");\n'
                    'ctab.eval("(async () => { const p = await Promise.resolve(\'CSP_ASYNC_IIFE_OK\'); return JSON.stringify({ p }); })()");\n'
                    "return await ctab.flush();"
                )
            }
        )
        self.assertNotIn("! errors:", csp_iife_out)
        self.assertIn("> CSP_SYNC_IIFE_OK", csp_iife_out)
        self.assertIn('> {"p":"CSP_ASYNC_IIFE_OK"}', csp_iife_out)

        # 3. Issue 2: Click-triggered full-page navigation + tab.wait(ms) fluent chaining
        nav_out_1 = self.rt.execute(
            {
                "code": (
                    f'const ntab = await cua.createBrowserTab("headless", "{checkout_url}");\n'
                    'return await ntab.click("Continue to Confirmation").snapshot();'
                )
            }
        )
        self.assertNotIn("! errors:", nav_out_1)
        self.assertNotIn("-32000", nav_out_1)
        self.assertIn("CONFIRMED:ORD-991", nav_out_1)

        nav_out_2 = self.rt.execute(
            {
                "code": (
                    "const ntab = await cua.getTab();\n"
                    'await ntab.click("Back to Checkout").wait(100).snapshot();\n'
                    'return await ntab.click("Express Checkout Link").wait(120).eval("location.href").flush();'
                )
            }
        )
        self.assertNotIn("! errors:", nav_out_2)
        self.assertIn(f"> {self.server.base_url}/confirmation?order=ORD-992", nav_out_2)
        self.assertIn("CONFIRMED:ORD-992", nav_out_2)

        # Also verify MV3 extension background.js handles CDP `-32000 Inspected target navigated or closed` cleanly
        bridge = self.rt.browser.ensure_bridge()
        bg_js_src = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
        bg_js_patched = bg_js_src.replace(
            'ws://127.0.0.1:19222/ext',
            f'ws://127.0.0.1:{bridge.port}/ext',
        )
        mv3_nav_harness = f"""
const navState = {{
  url: "https://shop.example.com/checkout",
  title: "Checkout",
  status: "complete",
  pendingUrl: "",
  groupId: 77,
}};
globalThis.chrome = {{
  tabs: {{
    query: async () => [{{ id: 501, windowId: 77, groupId: navState.groupId, url: navState.url, title: navState.title, status: navState.status }}],
    get: async (id) => ({{ id, windowId: 77, groupId: navState.groupId, url: navState.url, pendingUrl: navState.pendingUrl, title: navState.title, status: navState.status }}),
    create: async (opts) => ({{ id: 501, windowId: opts.windowId || 77, url: opts.url, status: "complete", title: "Checkout" }}),
    update: async (id, opts) => {{ if (opts.url) navState.url = opts.url; return {{ id, windowId: 77, url: navState.url, status: "complete" }}; }},
    group: async () => 77,
    remove: async () => ({{}}),
  }},
  tabGroups: {{
    get: async (gid) => ({{ id: gid, windowId: 77, title: "🤖 Checkout" }}),
    query: async () => [{{ id: 77, windowId: 77, title: "🤖 Checkout" }}],
    update: async (gid) => ({{ id: gid, windowId: 77 }}),
  }},
  windows: {{
    getAll: async () => [{{ id: 77, focused: false }}],
  }},
  debugger: {{
    attach: async () => ({{}}),
    sendCommand: async (_target, method, params) => {{
      if (method === "Runtime.evaluate") {{
        const expr = String((params && params.expression) || "");
        if (expr.startsWith("window.__fmu.executeBatch(")) {{
          const rawJson = expr.slice("window.__fmu.executeBatch(".length, -1);
          const payload = JSON.parse(rawJson);
          const steps = payload.steps || [];
          const hasClick = steps.some(s => (s.op || s.action) === "click");
          if (hasClick && navState.url.endsWith("/checkout")) {{
            navState.status = "loading";
            navState.pendingUrl = "https://shop.example.com/checkout/complete";
            setTimeout(() => {{
              navState.url = "https://shop.example.com/checkout/complete";
              navState.title = "Order Complete";
              navState.pendingUrl = "";
              navState.status = "complete";
            }}, 45);
            throw new Error(JSON.stringify({{ code: -32000, message: "Inspected target navigated or closed" }}));
          }}
          const evalOuts = steps
            .filter(s => (s.op || s.action) === "eval")
            .map(() => navState.url);
          return {{
            result: {{
              value: {{
                url: navState.url,
                title: navState.title,
                vw: 1280,
                vh: 800,
                crc: navState.url.endsWith("/complete") ? "99887766" : "11223344",
                seq: 2,
                count: 2,
                useDiff: false,
                cursor: {{ agent: "default", x: 60, y: 60, uid: "@u1", label: "Place Order", op: "click" }},
                webMcpTools: [],
                canvases: [],
                errors: [],
                evalOutputs: evalOuts,
                rawEvalValues: evalOuts,
                escalations: [],
                lines: navState.url.endsWith("/complete")
                  ? ['@u1 heading "Thank you for your order"', '@u2 status "Order #88412"']
                  : ['@u1 button "Place Order"'],
              }}
            }}
          }};
        }}
        return {{ result: {{ value: true }} }};
      }}
      return {{}};
    }},
    onDetach: {{ addListener: () => {{}} }},
  }},
  alarms: {{ create: () => {{}}, onAlarm: {{ addListener: () => {{}} }} }},
  runtime: {{ onInstalled: {{ addListener: () => {{}} }}, onStartup: {{ addListener: () => {{}} }} }},
}};
{bg_js_patched}
setInterval(() => {{}}, 1000);
"""
        node_proc = subprocess.Popen(
            ["node", "-e", mv3_nav_harness],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertTrue(bridge.wait_connected(timeout=3.0))
            mv3_nav_out = self.rt.execute(
                {
                    "code": (
                        'const sTab = await cua.createBrowserTab("chrome", "https://shop.example.com/checkout", sessionName="🤖 Checkout", windowId=77, reuseExisting=true);\n'
                        'return await sTab.click("@u1").wait(40).eval("location.href").snapshot();'
                    )
                }
            )
            self.assertNotIn("-32000", mv3_nav_out)
            self.assertNotIn("Inspected target navigated or closed", mv3_nav_out)
            self.assertIn("> https://shop.example.com/checkout/complete", mv3_nav_out)
            self.assertIn('heading "Thank you for your order"', mv3_nav_out)
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

        # 4. Issue 5: 16 clickable React/MUI `.MuiCard-root` vehicle cards appear in snapshot() & click cleanly
        veh_snap = self.rt.execute(
            {
                "code": (
                    f'const vtab = await cua.createBrowserTab("headless", "{vehicles_url}");\n'
                    "return await vtab.snapshot();"
                )
            }
        )
        card_lines = [l for l in veh_snap.splitlines() if " card " in l]
        self.assertEqual(
            len(card_lines),
            16,
            f"Expected all 16 .MuiCard-root vehicle cards in snapshot, got {len(card_lines)}:\n{veh_snap}",
        )
        rogue_lines = [l for l in card_lines if "Nissan Rogue" in l]
        self.assertEqual(len(rogue_lines), 1)
        rogue_uid = rogue_lines[0].split()[0]

        # Click by @u UID (triggers React-style DOM re-render of all 16 cards)
        click_uid_out = self.rt.execute(
            {
                "code": (
                    "const vtab = await cua.getTab();\n"
                    f'return await vtab.click("{rogue_uid}").diff();'
                )
            }
        )
        self.assertNotIn("! errors:", click_uid_out)
        self.assertIn("SELECTED:7:Midsize SUV:62", click_uid_out)

        # Click another card by text label after full card list DOM replacement
        click_txt_out = self.rt.execute(
            {
                "code": (
                    "const vtab = await cua.getTab();\n"
                    'return await vtab.click("Tesla Model 3").diff();'
                )
            }
        )
        self.assertNotIn("! errors:", click_txt_out)
        self.assertIn("SELECTED:11:Electric:66", click_txt_out)

    def test_30_yyc_rental_audit_waitfor_main_text_footer_fold_list_tabs_smart_diff_and_repl(self) -> None:
        booking_url = f"{self.server.base_url}/rental-booking"
        csp_url = f"{self.server.base_url}/strict-csp"

        # 1. Primary <main> static text + uncovered <tr> rows + auto-collapsed <footer> links
        init_snap = self.rt.execute(
            {
                "code": (
                    f'const btab = await cua.createBrowserTab("headless", "{booking_url}");\n'
                    "return await btab.snapshot();"
                )
            }
        )
        self.assertIn("MOCK9042XYZ", init_snap)
        self.assertIn("CAD 76.29", init_snap)
        self.assertIn("#mock1001 | Hertz | Activated | 10% Back", init_snap)
        self.assertIn("# footer: +16 items folded (", init_snap)
        self.assertIn("pass fullPage=true or query=... to expand)", init_snap)
        self.assertNotIn('"Cookie Settings"', init_snap)
        self.assertIn("waitFor(text|sel)", init_snap)

        # Folded footer links remain directly clickable by label without fullPage=True,
        # and chained .snapshot() after an action uses smart diff instead of repeating the whole page!
        folded_click_diff = self.rt.execute(
            {
                "code": (
                    "const btab = await cua.getTab();\n"
                    'return await btab.click("Investor Relations").snapshot();'
                )
            }
        )
        self.assertNotIn("! errors:", folded_click_diff)
        self.assertIn("FOOTER:InvestorRelations", folded_click_diff)
        # Smart diff should NOT repeat unchanged buttons or table rows
        self.assertNotIn('"Apply Promo"', folded_click_diff)
        self.assertNotIn("#mock1002 | Avis", folded_click_diff)

        # Standalone 0-step tab.snapshot() returns full snapshot, and fullPage=True expands footer links
        full_standalone = self.rt.execute(
            {
                "code": (
                    "const btab = await cua.getTab();\n"
                    "return await btab.snapshot();"
                )
            }
        )
        self.assertIn('"Apply Promo"', full_standalone)
        self.assertIn("# footer: +16 items folded", full_standalone)

        full_page_snap = self.rt.execute(
            {
                "code": (
                    "const btab = await cua.getTab();\n"
                    "return await btab.snapshot(fullPage=true);"
                )
            }
        )
        self.assertIn('"Cookie Settings"', full_page_snap)
        self.assertIn('"Investor Relations"', full_page_snap)
        self.assertNotIn("items folded", full_page_snap)

        # 2. Event-driven waitFor (MutationObserver wakeup in ~90ms instead of 4000ms sleep) + timeout handling
        t_w0 = time.perf_counter()
        wait_out = self.rt.execute(
            {
                "code": (
                    "const btab = await cua.getTab();\n"
                    'return await btab.click("Apply Promo").waitFor({text: "Promo 200692 Applied", gone: ".loading-spinner", timeoutMs: 4000}).diff();'
                )
            }
        )
        wait_elapsed = time.perf_counter() - t_w0
        self.assertNotIn("! errors:", wait_out)
        self.assertIn("Promo 200692 Applied (-15%)", wait_out)
        self.assertLess(wait_elapsed, 1.5, f"waitFor should wake on MutationObserver, took {wait_elapsed:.2f}s")

        wait_timeout_out = self.rt.execute(
            {
                "code": (
                    "const btab = await cua.getTab();\n"
                    'return await btab.waitFor("#does-not-exist", 110).diff();'
                )
            }
        )
        self.assertIn('! errors: step 1 (waitFor): timed out after 110ms waiting for "#does-not-exist"', wait_timeout_out)

        wait_opt_out = self.rt.execute(
            {
                "code": (
                    "const btab = await cua.getTab();\n"
                    'return await btab.waitFor({selector: "#does-not-exist", timeoutMs: 60, optional: true}).diff();'
                )
            }
        )
        self.assertNotIn("! errors:", wait_opt_out)

        # 3. SPA history.pushState('?vehicle=suv') preserves compact diff across query-param updates
        push_diff = self.rt.execute(
            {
                "code": (
                    "const btab = await cua.getTab();\n"
                    'return await btab.click("Filter SUVs").wait(100).snapshot();'
                )
            }
        )
        self.assertNotIn("! errors:", push_diff)
        self.assertIn("Showing: Midsize SUV", push_diff)
        self.assertNotIn('"Apply Promo"', push_diff)

        # 4. tab.text() as value (including on strict-CSP page with 0 EvalError) and chained step
        txt_val = self.rt.execute(
            {
                "code": (
                    "const btab = await cua.getTab();\n"
                    'const summary = await btab.text("#booking-summary");\n'
                    "return summary;"
                )
            }
        )
        self.assertIn("MOCK9042XYZ", txt_val)
        self.assertIn("CAD 76.29", txt_val)
        self.assertNotIn("# web tab=", txt_val)

        csp_txt = self.rt.execute(
            {
                "code": (
                    f'const ctab = await cua.createBrowserTab("headless", "{csp_url}");\n'
                    "return await ctab.text();"
                )
            }
        )
        self.assertTrue(len(csp_txt) > 0)
        self.assertNotIn("EvalError", csp_txt)

        # 5. REPL multiline JS string, bare evalValue non-str JSON output, destructuring, and safe builtins
        multiline_code = (
            'const btab = await cua.getTab("rental-booking");\n'
            'const Ml = await btab.eval("(() => {\n  const a = \'MOCK\';\n  const b = \'9042XYZ\';\n  return a + b;\n})()");\n'
            'const { code, ok } = { code: Ml.trim(), ok: Ml.includes("9042") && ["a", "b", "c"].slice(0, 2).join("-") === "a-b" && all([true, 1 === 1]) };\n'
            "return `${code}:${ok}`;"
        )
        ml_out = self.rt.execute({"code": multiline_code})
        self.assertEqual(ml_out, "MOCK9042XYZ:True")

        bare_eval_val = self.rt.execute(
            {
                "code": (
                    'const btab = await cua.getTab("rental-booking");\n'
                    'await btab.evalValue("({ total: 76.29, currency: \'CAD\' })");'
                )
            }
        )
        self.assertEqual(json.loads(bare_eval_val), {"total": 76.29, "currency": "CAD"})

        # 6. Cross-agent cua.getTab("audit:t1") lookup without spawning about:blank
        self.rt.execute(
            {
                "agent": "audit",
                "code": f'await cua.createBrowserTab("headless", "{booking_url}");',
            }
        )
        tabs_before = set(self.rt.browser._tabs.keys())
        self.assertIn("audit:t1", tabs_before)
        cross_out = self.rt.execute(
            {
                "code": (
                    'const at = await cua.getTab("audit:t1");\n'
                    "return at.url;"
                )
            }
        )
        self.assertEqual(cross_out, f"{booking_url}?vehicle=suv")
        self.assertEqual(set(self.rt.browser._tabs.keys()), tabs_before)
        with self.assertRaises(RuntimeError):
            self.rt.execute({"code": 'await cua.getTab("ghost:t99");'})
        self.assertEqual(set(self.rt.browser._tabs.keys()), tabs_before)

        # 7. Real MV3 background.js harness: cua.listTabs(), getTab(chromeTabId), bare-domain reuseExisting preservation,
        #    bare-domain navigation normalization, and 1-time runtimeJs caching across multiple batches!
        bridge = self.rt.browser.ensure_bridge()
        bg_js = (Path(__file__).resolve().parents[1] / "extension" / "background.js").read_text(encoding="utf-8")
        bg_js_patched = bg_js.replace("ws://127.0.0.1:19222/ext", f"ws://127.0.0.1:{bridge.port}/ext")
        mv3_tabs_harness = f"""
const fs = require("fs");
const state = {{
  tabs: [
    {{ id: 501, windowId: 88, groupId: 12, active: true, title: "Hertz Vehicle Selection", url: "https://www.hertz.com/us/en/book/vehicles", status: "complete" }},
    {{ id: 502, windowId: 88, groupId: -1, active: false, title: "Expedia Car Search", url: "https://www.expedia.ca/carsearch?locn=YYC", status: "complete" }}
  ],
  updateCalls: [],
  createdUrls: [],
  runtimeInitEvals: 0,
  batchEvals: 0,
}};
global.chrome = {{
  tabs: {{
    query: async (q) => state.tabs.filter(t => (q.windowId === undefined || t.windowId === q.windowId) && (q.groupId === undefined || t.groupId === q.groupId)),
    get: async (id) => {{
      const f = state.tabs.find(t => t.id === id);
      if (!f) throw new Error("No tab with id " + id);
      return f;
    }},
    create: async (opts) => {{
      state.createdUrls.push(opts.url);
      const nt = {{ id: 503, windowId: opts.windowId || 88, groupId: -1, active: false, title: "Avis", url: opts.url, status: "complete" }};
      state.tabs.push(nt);
      return nt;
    }},
    update: async (id, opts) => {{
      state.updateCalls.push({{ id, opts }});
      const f = state.tabs.find(t => t.id === id);
      if (f && opts.url) f.url = opts.url;
      return f || {{ id, windowId: 88, url: opts.url || "", status: "complete" }};
    }},
    group: async () => 12,
    remove: async () => ({{}}),
    onUpdated: {{ addListener: () => {{}} }},
  }},
  tabGroups: {{
    get: async (gid) => ({{ id: gid, windowId: 88, title: "🤖 Agent" }}),
    query: async () => [{{ id: 12, windowId: 88, title: "🤖 Agent" }}],
    update: async (gid) => ({{ id: gid, windowId: 88 }}),
  }},
  windows: {{
    getAll: async () => [{{ id: 88, focused: false }}],
  }},
  debugger: {{
    attach: async () => ({{}}),
    sendCommand: async (_target, method, params) => {{
      if (method === "Runtime.evaluate") {{
        const expr = String((params && params.expression) || "");
        if (expr.includes("FMU_RUNTIME_VERSION")) {{
          state.runtimeInitEvals += 1;
          return {{ result: {{ value: true }} }};
        }}
        if (expr.startsWith("window.__fmu.executeBatch(")) {{
          state.batchEvals += 1;
          const payload = JSON.parse(expr.slice("window.__fmu.executeBatch(".length, -1));
          const steps = payload.steps || [];
          const evalOuts = steps
            .filter(s => (s.op || s.action) === "eval")
            .map(() => JSON.stringify(state));
          return {{
            result: {{
              value: {{
                url: "https://www.hertz.com/us/en/book/vehicles",
                title: "Hertz Vehicle Selection",
                vw: 1280,
                vh: 800,
                crc: "a1b2c3d4",
                seq: state.batchEvals,
                count: 2,
                useDiff: state.batchEvals > 1 && payload.diff !== false,
                unchanged: state.batchEvals > 1 && payload.diff !== false && evalOuts.length === 0,
                cursor: {{ agent: "default", x: 50, y: 50, uid: "@u1", label: "Nissan Rogue", op: "click" }},
                webMcpTools: [],
                canvases: [],
                errors: [],
                evalOutputs: evalOuts,
                rawEvalValues: evalOuts,
                escalations: [],
                lines: state.batchEvals > 1 && payload.diff !== false
                  ? ["= unchanged (crc=a1b2c3d4, seq=2)"]
                  : ['@u1 card "Midsize SUV - Nissan Rogue CAD 76.29"'],
              }}
            }}
          }};
        }}
        return {{ result: {{ value: false }} }};
      }}
      return {{}};
    }},
    onDetach: {{ addListener: () => {{}} }},
  }},
  alarms: {{ create: () => {{}}, onAlarm: {{ addListener: () => {{}} }} }},
  runtime: {{ onInstalled: {{ addListener: () => {{}} }}, onStartup: {{ addListener: () => {{}} }} }},
}};
{bg_js_patched}
setInterval(() => {{}}, 1000);
"""
        node_proc = subprocess.Popen(
            ["node", "-e", mv3_tabs_harness],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertTrue(bridge.wait_connected(timeout=3.0))
            bridge_st = self.rt.execute({"bridgeStatus": True})
            self.assertIn('version="0.4.0"', bridge_st)

            tabs_list = self.rt.execute({"code": 'return await cua.listTabs("hertz");'})
            self.assertIn('@tab id=501', tabs_list)
            self.assertIn('group="🤖 Agent"', tabs_list)
            self.assertIn("https://www.hertz.com/us/en/book/vehicles", tabs_list)
            self.assertNotIn("expedia.ca", tabs_list)

            # Attach via getTab("hertz.com") AND createBrowserTab({url: "hertz.com", reuseExisting: true})
            # Neither should clobber the deep /us/en/book/vehicles URL!
            attach_state_raw = self.rt.execute(
                {
                    "code": (
                        'const h1 = await cua.getTab("hertz.com");\n'
                        "const d1 = await h1.diff();\n"
                        'const h2 = await cua.createBrowserTab({mode: "chrome", url: "hertz.com", reuseExisting: true});\n'
                        'const a1 = await cua.createBrowserTab({mode: "chrome", url: "avis.ca", reuseExisting: false});\n'
                        'return await h1.eval("__state__");'
                    )
                }
            )
            harness_state = json.loads(attach_state_raw)
            self.assertEqual(
                harness_state["updateCalls"],
                [],
                f"Expected zero chrome.tabs.update URL clobbers on reuseExisting, got {harness_state['updateCalls']}",
            )
            self.assertEqual(harness_state["createdUrls"], ["https://avis.ca"])
            # Tab 501 ran multiple executeBatch calls, yet runtimeInitEvals on tab 501 + tab 503 should be 2 total (1 per tab, not per batch!)
            self.assertEqual(harness_state["runtimeInitEvals"], 2)
            self.assertGreaterEqual(harness_state["batchEvals"], 4)
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

    def test_repl_raw_arrow_eval_callbacks_and_quoted_arrow_eval(self) -> None:
        from fmu.cua_runtime import FasterMacUseRuntime

        rt = FasterMacUseRuntime()
        try:
            out = rt.execute(
                {
                    "code": (
                        'const tab = await cua.createBrowserTab("headless", "data:text/html,<button>UPGRADE NOW</button>");\n'
                        "const res = await tab.eval(() => {\n"
                        '  const btn = [...document.querySelectorAll("button")].find(el => /UPGRADE NOW/i.test(el.textContent || ""));\n'
                        "  return { tag: btn ? btn.tagName : null };\n"
                        "});\n"
                        'const quoted = await tab.eval("() => ({ count: document.querySelectorAll(\'button\').length })");\n'
                        "return JSON.stringify({ res, quoted });"
                    )
                }
            )
            parsed = json.loads(out.splitlines()[-1])
            self.assertEqual(parsed["res"], {"tag": "BUTTON"})
            self.assertEqual(parsed["quoted"], {"count": 1})
        finally:
            rt.shutdown()

    def test_mv3_no_tab_with_id_evicts_stale_tab_and_retries(self) -> None:
        from fmu.browser_engine import BrowserEngine

        be = BrowserEngine()
        be._tabs["t1"] = object()  # type: ignore[assignment]
        calls = 0

        def fake_inner(req: dict) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("No tab with id: 563976657.")
            return "# recovered"

        be._execute_batch_inner = fake_inner  # type: ignore[method-assign]
        res = be._execute_batch_with_retry({"session": "t1", "mode": "extension", "url": "https://slh.com"})
        self.assertEqual(res, "# recovered")
        self.assertEqual(calls, 2)
        self.assertNotIn("t1", be._tabs)

    def test_31_qa_screenshot_viewport_and_runtime_errors(self) -> None:
        import struct

        qa_url = f"{self.server.base_url}/qa-test"
        with tempfile.TemporaryDirectory(prefix="fmu-qa-shots-") as tmp_dir:
            mob_shot = str(Path(tmp_dir) / "mobile.png")
            full_shot = str(Path(tmp_dir) / "fullpage.png")

            with BackgroundIntrusionGuard() as guard:
                # 1. Desktop initial state + responsive switch to mobile (390x844) + chained & value screenshots
                mob_out = self.rt.execute(
                    {
                        "code": (
                            f'const tab = await cua.createBrowserTab("headless", "{qa_url}");\n'
                            f'await tab.setViewport(390, 844, {{ scale: 2, mobile: true }}).screenshot("{mob_shot}");\n'
                            "return await tab.snapshot({ full: true });"
                        )
                    }
                )
                self.assertIn('- @u1 button "Desktop Menu"', mob_out)
                self.assertIn('+ @u5 button "Mobile Drawer"', mob_out)
                final_snap = mob_out.split("# web tab=t1")[-1]
                self.assertIn('@u5 button "Mobile Drawer"', final_snap)
                self.assertNotIn("Desktop Menu", final_snap)
                mob_bytes = Path(mob_shot).read_bytes()
                self.assertEqual(mob_bytes[:8], b"\x89PNG\r\n\x1a\n")
                mw, mh = struct.unpack(">II", mob_bytes[16:24])
                self.assertEqual((mw, mh), (780, 1688))

                # 2. Reset viewport back to desktop (1280x800) + value-returned fullPage screenshot
                ret_path_out = self.rt.execute(
                    {
                        "code": (
                            "const tab = await cua.getTab();\n"
                            "await tab.resetViewport();\n"
                            f'const saved = await tab.screenshot("{full_shot}", {{ fullPage: true }});\n'
                            "const snap = await tab.snapshot({ full: true });\n"
                            "return JSON.stringify({ saved, hasDesktop: snap.includes('Desktop Menu'), hasMobile: snap.includes('Mobile Drawer') });"
                        )
                    }
                )
                ret_info = json.loads(ret_path_out.splitlines()[-1])
                self.assertEqual(Path(ret_info["saved"]).resolve(), Path(full_shot).resolve())
                self.assertTrue(ret_info["hasDesktop"])
                self.assertFalse(ret_info["hasMobile"])
                full_bytes = Path(full_shot).read_bytes()
                fw, fh = struct.unpack(">II", full_bytes[16:24])
                self.assertEqual(fw, 1280)
                self.assertGreaterEqual(fh, 2100)

                # 3. Trigger console.error + HTTP 500 + uncaught pageerror and verify inline ! page_errors & tab.errors()
                bug_out = self.rt.execute(
                    {
                        "code": (
                            "const tab = await cua.getTab();\n"
                            'await tab.click("Trigger Bugs").waitFor("Triggered").wait(80);'
                        )
                    }
                )
                self.assertIn("! page_errors(", bug_out)

                errs_json = self.rt.execute(
                    {
                        "code": (
                            "const tab = await cua.getTab();\n"
                            "return JSON.stringify(await tab.errors());"
                        )
                    }
                )
                errs = json.loads(errs_json.splitlines()[-1])
                kinds = {e["kind"] for e in errs}
                self.assertIn("console.error", kinds)
                self.assertIn("http", kinds)
                self.assertIn("pageerror", kinds)
                self.assertTrue(any("Hydration mismatch" in e["message"] for e in errs))
                self.assertTrue(any(e.get("status") == 500 and "/api/qa-fail" in e["message"] for e in errs))
                self.assertTrue(any("Uncaught checkout crash" in e["message"] for e in errs))

                # 4. Verify clearErrors() clears diagnostics cleanly
                after_clear = self.rt.execute(
                    {
                        "code": (
                            "const tab = await cua.getTab();\n"
                            "await tab.clearErrors();\n"
                            "return JSON.stringify(await tab.errors());"
                        )
                    }
                )
                self.assertEqual(json.loads(after_clear.splitlines()[-1]), [])

                # 5. Verify MV3 extension tabDiagnostic routing
                sess = self.rt.browser._tabs.get("t1")
                self.assertIsNotNone(sess)
                assert sess is not None
                sess.is_extension = True
                setattr(sess._raw_page, "chrome_tab_id", 991)
                try:
                    self.rt.browser._on_extension_event(
                        {
                            "type": "tabDiagnostic",
                            "tabId": 991,
                            "entry": {
                                "kind": "http",
                                "status": 503,
                                "url": "https://api.example.com/v1/pay",
                                "message": "503 POST https://api.example.com/v1/pay",
                            },
                        }
                    )
                    ext_errs = self.rt.browser.get_tab_errors("t1", clear=True)
                    self.assertEqual(len(ext_errs), 1)
                    self.assertEqual(ext_errs[0]["status"], 503)
                finally:
                    sess.is_extension = False

                # 6. Verify macOS native app screenshot step (captures PNG when Screen Recording is granted, or skips cleanly when optional)
                app_shot = str(Path(tmp_dir) / "calc.png")
                calc_out = self.rt.execute(
                    {
                        "app": "Calculator",
                        "steps": [
                            {"op": "key", "name": "escape"},
                            {"op": "screenshot", "value": app_shot, "optional": True},
                        ],
                    }
                )
                self.assertNotIn("! errors:", calc_out)
                if "# screenshot path=" in calc_out:
                    self.assertTrue(Path(app_shot).is_file())
                    self.assertEqual(Path(app_shot).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

            rep = guard.stop()
            self.assertTrue(rep.passed_background_invariant, f"Intrusion detected: {rep}")


if __name__ == "__main__":
    unittest.main(verbosity=2)






