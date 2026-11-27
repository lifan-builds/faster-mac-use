# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Faster Mac Use; Section 7 attribution terms in ATTRIBUTION.md.
"""Installer for the bundled browser-routing skill and Antigravity plugin."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

BUNDLE_ROOT = Path(__file__).resolve().parent / "bundle"
SKILL_SOURCE = BUNDLE_ROOT / "skills" / "browser-routing" / "SKILL.md"
ANTIGRAVITY_SOURCE = BUNDLE_ROOT / "antigravity"


def _copy_file(src: Path, dst: Path, dry_run: bool) -> str:
    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    return str(dst)


def _write_json(payload: dict, dst: Path, dry_run: bool) -> str:
    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return str(dst)


def _build_mcp_config(use_installed_bin: bool) -> dict:
    if use_installed_bin:
        return {
            "mcpServers": {
                "faster-mac-use": {
                    "command": "fmu",
                    "args": ["mcp"],
                }
            }
        }
    return json.loads((ANTIGRAVITY_SOURCE / "mcp_config.json").read_text(encoding="utf-8"))


def install_antigravity_plugin(home: Path, dry_run: bool = False, use_installed_bin: bool = False) -> list[str]:
    plugin_dir = home / ".gemini" / "config" / "plugins" / "faster-mac-use"
    written = [
        _copy_file(ANTIGRAVITY_SOURCE / "plugin.json", plugin_dir / "plugin.json", dry_run),
        _write_json(_build_mcp_config(use_installed_bin), plugin_dir / "mcp_config.json", dry_run),
        _copy_file(SKILL_SOURCE, plugin_dir / "skills" / "browser-routing" / "SKILL.md", dry_run),
        _copy_file(ANTIGRAVITY_SOURCE / "rules" / "AGENTS.md", plugin_dir / "rules" / "AGENTS.md", dry_run),
        _copy_file(ANTIGRAVITY_SOURCE / "assets" / "logo.svg", plugin_dir / "assets" / "logo.svg", dry_run),
    ]
    return written


def install_skill(dest_root: Path, dry_run: bool = False) -> str:
    return _copy_file(SKILL_SOURCE, dest_root / "browser-routing" / "SKILL.md", dry_run)


def run_setup(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="fmu setup",
        description="Install the bundled browser-routing skill and Antigravity plugin for Faster Mac Use.",
    )
    parser.add_argument("--antigravity", action="store_true", help="Install the Antigravity plugin (MCP + browser-routing skill + routing rule)")
    parser.add_argument("--claude", action="store_true", help="Install browser-routing into ~/.claude/skills/")
    parser.add_argument("--codex", action="store_true", help="Install browser-routing into ~/.codex/skills/")
    parser.add_argument("--cursor", action="store_true", help="Install browser-routing into ~/.cursor/skills/")
    parser.add_argument("--skill-only", action="store_true", help="Install only the shared ~/.agents/skills/browser-routing/SKILL.md")
    parser.add_argument("--all", action="store_true", help="Install shared skill plus Antigravity, Claude Code, Codex, and Cursor targets when present")
    parser.add_argument("--use-installed-bin", action="store_true", help="Point Antigravity mcp_config.json to `fmu mcp` instead of `uvx --from faster-mac-use fmu mcp`")
    parser.add_argument("--dry-run", action="store_true", help="Show target paths without writing files")
    parser.add_argument("--home", type=Path, default=None, help="Override home directory (useful for testing)")
    args = parser.parse_args(argv)

    home = (args.home or Path.home()).expanduser().resolve()
    explicit = any((args.antigravity, args.claude, args.codex, args.cursor, args.skill_only, args.all))

    do_antigravity = args.antigravity or (args.all and (home / ".gemini").exists()) or (not explicit and (home / ".gemini").exists())
    do_claude = args.claude or ((args.all or not explicit) and (home / ".claude").exists())
    do_codex = args.codex or ((args.all or not explicit) and (home / ".codex").exists())
    do_cursor = args.cursor or ((args.all or not explicit) and (home / ".cursor").exists())
    do_shared = args.skill_only or args.all or not explicit

    written: list[str] = []
    notes: list[str] = []

    if do_shared:
        written.append(install_skill(home / ".agents" / "skills", dry_run=args.dry_run))
    if do_antigravity:
        written.extend(
            install_antigravity_plugin(home, dry_run=args.dry_run, use_installed_bin=args.use_installed_bin)
        )
        notes.append(
            "Antigravity plugin ready (~/.gemini/config/plugins/faster-mac-use/): loads automatically on the next turn without editing config.json."
        )
    if do_claude:
        written.append(install_skill(home / ".claude" / "skills", dry_run=args.dry_run))
        notes.append("Claude Code MCP: claude mcp add faster-mac-use -- uvx --from faster-mac-use fmu mcp")
    if do_codex:
        written.append(install_skill(home / ".codex" / "skills", dry_run=args.dry_run))
        notes.append("Codex MCP: codex mcp add faster-mac-use -- uvx --from faster-mac-use fmu mcp")
    if do_cursor:
        written.append(install_skill(home / ".cursor" / "skills", dry_run=args.dry_run))

    action = "Would write" if args.dry_run else "Installed"
    sys.stdout.write(f"# fmu-setup mode={'dry-run' if args.dry_run else 'applied'} files={len(written)}\n")
    for path in written:
        sys.stdout.write(f"- {action}: {path}\n")
    for note in notes:
        sys.stdout.write(f"Note: {note}\n")
    return 0
