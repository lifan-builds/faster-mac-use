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

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            (tmp_root / "src" / "native").mkdir(parents=True)
            (tmp_root / "src" / "fmu" / "native").mkdir(parents=True)
            (tmp_root / "src" / "native" / "fmu_ax.swift").write_text("import Foundation\n")
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
            (mac_engine.PKG_DIR / "native" / "fmu_ax.swift").is_file()
            or (mac_engine.ROOT / "src" / "native" / "fmu_ax.swift").is_file(),
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


if __name__ == "__main__":
    unittest.main(verbosity=2)





