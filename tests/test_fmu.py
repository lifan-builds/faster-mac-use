#!/usr/bin/env python3
"""Comprehensive unit and integration test suite for Faster Mac Use (fmu)."""

from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "benchmarks"))

from fmu.cua_runtime import FasterMacUseRuntime  # noqa: E402
from fmu.guard import BackgroundIntrusionGuard  # noqa: E402
from test_app_server import BenchWebServer  # noqa: E402


class TestFasterMacUse(unittest.TestCase):
    server: BenchWebServer
    rt: FasterMacUseRuntime

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = BenchWebServer().start()
        cls.rt = FasterMacUseRuntime()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.rt.shutdown()
        cls.server.stop()

    def setUp(self) -> None:
        self.rt.execute({"reset": True})

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


if __name__ == "__main__":
    unittest.main(verbosity=2)


