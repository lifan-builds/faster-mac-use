# Faster Mac Use + Browser Routing

On macOS, prefer the `faster-mac-use` (`cua` MCP) engine and the bundled `browser-routing` skill over the built-in browser subagent for live web pages, logged-in Chrome tabs, local dev QA, and native macOS apps:

1. Use APIs, CLIs (`gh`, `youtube_transcript_api`, `yt-dlp --skip-download`), or `search_web` before opening any browser.
2. Load the `browser-routing` skill before interactive browser or macOS desktop work.
3. Use `cua.createBrowserTab("headless", url)` for public or local pages, `cua.createBrowserTab("chrome", url, sessionLabel="<task>")` for logged-in Chrome tabs via the MV3 bridge (`ws://127.0.0.1:19222/ext`, never probing `DevToolsActivePort`), and `cua.getApp("AppName")` for native macOS apps.
4. Batch chained actions (`.click().waitFor().diff()`) into one `cua` turn without blind sleeps, and clean up finished agent-opened tabs with `cua.closeMyTabs()`.
