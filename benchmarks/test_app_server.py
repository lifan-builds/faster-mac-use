"""Deterministic local SaaS web benchmark server testing dense DOMs, top-banner index shifts, and Focus Emulation."""

from __future__ import annotations
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading

ORDERS_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Acme Cloud — Enterprise Order Console</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 0; display: flex; }
    nav { width: 200px; background: #111827; color: #fff; padding: 16px; }
    nav a { display: block; color: #d1d5db; margin: 8px 0; text-decoration: none; }
    main { flex: 1; padding: 20px; }
    .banner { background: #fef3c7; border: 1px solid #f59e0b; padding: 8px; margin-bottom: 8px; }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; }
    td, th { border: 1px solid #e5e7eb; padding: 6px 8px; text-align: left; }
    #drawer { display: none; border: 2px solid #2563eb; padding: 16px; margin-top: 16px; background: #eff6ff; }
    #drawer.open { display: block; }
  </style>
</head>
<body>
  <nav aria-label="Primary Navigation">
    <h2>Acme Console</h2>
    <a href="#dashboard">Dashboard Overview</a>
    <a href="#orders">Order Management</a>
    <a href="#customers">Enterprise Customers</a>
    <a href="#invoices">Billing &amp; Invoices</a>
    <a href="#shipments">Global Logistics</a>
    <a href="#returns">RMA &amp; Returns</a>
    <a href="#analytics">Revenue Analytics</a>
    <a href="#audit">Compliance Audit Log</a>
    <a href="#webhooks">Developer Webhooks</a>
    <a href="#settings">Workspace Settings</a>
  </nav>
  <main>
    <div id="toast-container"></div>
    <h1>Enterprise Order Management</h1>

    <section aria-label="Filter Controls">
      <input id="search-input" placeholder="Search orders" aria-label="Search orders" value="">
      <select id="status-select" aria-label="Order status">
        <option value="all">All Statuses</option>
        <option value="pending">Pending</option>
        <option value="approved">Approved</option>
        <option value="flagged">Flagged</option>
      </select>
      <label><input type="checkbox" id="priority-only" aria-label="Priority Only"> Priority Only</label>
      <button id="apply-btn" type="button" onclick="applyFilters()">Apply Filters</button>
      <button id="reset-btn" type="button" onclick="resetFilters()">Reset Filters</button>
      <span id="filter-summary" role="status">Showing 30 orders</span>
    </section>

    <div id="drawer" role="region" aria-label="Order Approval Drawer">
      <h2 id="drawer-title">Approve Order</h2>
      <input id="approval-note" placeholder="Approval note" aria-label="Approval note" value="">
      <select id="tier-select" aria-label="Shipping tier">
        <option value="standard">Standard Ground</option>
        <option value="express">Express Air</option>
        <option value="freight">Dedicated Freight</option>
      </select>
      <input id="invoice-file" type="file" aria-label="Attach signed PO">
      <button id="confirm-approve" type="button" onclick="confirmApprove()">Confirm Approval</button>
    </div>

    <table>
      <thead>
        <tr><th>Order ID</th><th>Customer</th><th>Amount</th><th>Actions</th></tr>
      </thead>
      <tbody id="orders-tbody"></tbody>
    </table>
  </main>

  <script>
    const tbody = document.getElementById("orders-tbody");
    const customers = ["Stripe", "Linear", "Vercel", "Datadog", "Cloudflare", "Figma", "Notion", "Ramp", "Scale", " Rippling"];
    for (let i = 401; i <= 430; i++) {
      const cust = customers[(i - 401) % customers.length].trim() + " Enterprise";
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>#ORD-${i}</td>
        <td>${cust}</td>
        <td>$${(i * 37).toLocaleString()}.00</td>
        <td>
          <button type="button" onclick="openDrawer(${i})">Inspect #ORD-${i}</button>
          <button type="button" onclick="flagOrder(${i})">Flag #ORD-${i}</button>
          <a href="#receipt-${i}">Receipt #${i}</a>
        </td>
      `;
      tbody.appendChild(tr);
    }

    let activeOrder = null;
    function prependToast(msg) {
      // Inserts 3 interactive banner elements at the VERY TOP of <main>,
      // shifting sequential indices of all 120+ elements below it!
      const tc = document.getElementById("toast-container");
      const div = document.createElement("div");
      div.className = "banner";
      div.innerHTML = `
        <span role="alert">${msg}</span>
        <button type="button" onclick="this.parentElement.remove()">Dismiss Alert</button>
        <a href="#audit-log">View Audit Entry</a>
      `;
      tc.prepend(div);
    }

    function applyFilters() {
      const q = document.getElementById("search-input").value;
      const st = document.getElementById("status-select").value;
      const pri = document.getElementById("priority-only").checked;
      document.getElementById("filter-summary").textContent =
        `Filtered: query="${q}" status=${st} priority=${pri}`;
      prependToast(`Filter applied (${st}, priority=${pri})`);
    }

    function resetFilters() {
      document.getElementById("search-input").value = "";
      document.getElementById("status-select").value = "all";
      document.getElementById("priority-only").checked = false;
      document.getElementById("filter-summary").textContent = "Showing 30 orders";
    }

    function openDrawer(id) {
      activeOrder = id;
      const d = document.getElementById("drawer");
      d.className = "open";
      document.getElementById("drawer-title").textContent = `Approve #ORD-${id}`;
    }

    function flagOrder(id) {
      prependToast(`Order #ORD-${id} flagged for review`);
    }

    function confirmApprove() {
      const note = document.getElementById("approval-note").value;
      const tier = document.getElementById("tier-select").value;
      document.getElementById("filter-summary").textContent =
        `APPROVED #ORD-${activeOrder} (${tier}) note="${note}"`;
      document.getElementById("drawer").className = "";
      prependToast(`Order #ORD-${activeOrder} approved successfully`);
    }

    // Expose WebMCP modelContext for WebMCP-aware agents
    navigator.modelContext = {
      tools: [
        {
          name: "approveOrder",
          description: "Filter and directly approve an order ID with shipping tier and note",
          execute: (args = {}) => {
            if (args.query !== undefined) document.getElementById("search-input").value = args.query;
            if (args.status !== undefined) document.getElementById("status-select").value = args.status;
            if (args.priority !== undefined) document.getElementById("priority-only").checked = Boolean(args.priority);
            applyFilters();
            openDrawer(args.orderId || 409);
            document.getElementById("approval-note").value = args.note || "";
            document.getElementById("tier-select").value = args.tier || "express";
            confirmApprove();
            return document.getElementById("filter-summary").textContent;
          }
        }
      ]
    };
  </script>
</body>
</html>
"""

PRICING_HTML = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Acme Pricing &amp; Promo Validator</title></head>
<body>
  <h1>Background Focus &amp; rAF Promo Generator</h1>
  <div id="focus-badge" role="status">Checking focus...</div>
  <div id="promo-code" role="status">PENDING</div>
  <button id="gen-btn" type="button" onclick="generatePromo()">Generate Quote Token</button>
  <script>
    function updateFocusState() {
      const focused = document.hasFocus();
      document.getElementById("focus-badge").textContent = focused ? "FOCUS_ACTIVE" : "UNFOCUSED_THROTTLED";
    }
    updateFocusState();
    function generatePromo() {
      requestAnimationFrame(() => {
        const focused = document.hasFocus();
        document.getElementById("focus-badge").textContent = focused ? "FOCUS_ACTIVE" : "UNFOCUSED_THROTTLED";
        document.getElementById("promo-code").textContent = focused ? "ENT-1000-OK" : "THROTTLED-ERR";
      });
    }
  </script>
</body>
</html>
"""


CANVAS_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Acme Canvas — Zero-DOM Architecture Board</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 24px; background: #f8fafc; }
    canvas { border: 2px solid #0f172a; background: #ffffff; display: block; margin-top: 12px; }
  </style>
</head>
<body>
  <h1>Canvas Topology Controller</h1>
  <div id="canvas-status" role="status">CANVAS_IDLE</div>
  <canvas id="board-canvas" width="720" height="280" aria-label="Topology Canvas"></canvas>
  <script>
    const canvas = document.getElementById("board-canvas");
    const ctx = canvas.getContext("2d");
    let clusterState = "READY";

    function drawBoard() {
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      ctx.fillStyle = "#0f172a";
      ctx.font = "bold 26px system-ui, sans-serif";
      ctx.fillText("Cluster Status: " + clusterState, 40, 60);

      // Button 1: Deploy Cluster (x: 40..310, y: 120..190)
      ctx.fillStyle = "#1e293b";
      ctx.fillRect(40, 120, 270, 70);
      ctx.fillStyle = "#ffffff";
      ctx.font = "bold 24px system-ui, sans-serif";
      ctx.fillText("Deploy Cluster", 80, 163);

      // Button 2: Rollback Node (x: 360..630, y: 120..190)
      ctx.fillStyle = "#334155";
      ctx.fillRect(360, 120, 270, 70);
      ctx.fillStyle = "#ffffff";
      ctx.font = "bold 24px system-ui, sans-serif";
      ctx.fillText("Rollback Node", 405, 163);
    }

    drawBoard();

    canvas.addEventListener("click", (ev) => {
      const rect = canvas.getBoundingClientRect();
      const x = ev.clientX - rect.left;
      const y = ev.clientY - rect.top;
      if (x >= 40 && x <= 310 && y >= 120 && y <= 190) {
        clusterState = "DEPLOYED";
        document.getElementById("canvas-status").textContent = "DEPLOY_CLUSTER_OK";
        drawBoard();
      } else if (x >= 360 && x <= 630 && y >= 120 && y <= 190) {
        clusterState = "ROLLED_BACK";
        document.getElementById("canvas-status").textContent = "ROLLBACK_NODE_OK";
        drawBoard();
      }
    });
  </script>
</body>
</html>
"""


EDITOR_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Rich-Text Composer Suite — Draft.js &amp; ProseMirror</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 24px; }
    .editor-box { border: 1px solid #cbd5e1; padding: 12px; margin-bottom: 16px; border-radius: 8px; }
    [contenteditable="true"] { min-height: 48px; outline: none; padding: 8px; border: 1px solid #94a3b8; }
  </style>
</head>
<body>
  <h1>Rich-Text Social Composer</h1>
  <div id="publish-status" role="status">DRAFT_IDLE</div>
  <div id="media-status" role="status">NO_MEDIA</div>

  <!-- X / Draft.js style composer -->
  <div class="editor-box">
    <div id="x-placeholder" class="public-DraftEditorPlaceholder-root">What's happening?</div>
    <div id="x-composer" data-testid="tweetTextarea_0" role="textbox" contenteditable="true" aria-multiline="true">
      <div data-contents="true"><div data-block="true"><span data-text="true"></span></div></div>
    </div>
    <div class="toolbar" style="margin-top:8px;">
      <input id="x-media-file" type="file" accept="video/*,image/*" style="display:none">
      <button id="x-media-btn" type="button" aria-label="Add photos or video">Add photos or video</button>
    </div>
  </div>

  <!-- RedNote / ProseMirror style composer -->
  <div class="editor-box">
    <input id="rn-title" placeholder="填写标题会有更多赞哦" aria-label="填写标题会有更多赞哦" value="">
    <div id="rn-body" class="tiptap ProseMirror" contenteditable="true" data-placeholder="输入正文描述，真诚有价值的分享予人温暖">
      <p><br></p>
    </div>
  </div>

  <button id="publish-btn" type="button" onclick="publishPost()">Publish Post</button>

  <script>
    (function() {
      const xComp = document.getElementById("x-composer");
      let draftState = "";
      function renderDraftState() {
        const contents = xComp.querySelector("[data-contents='true']") || xComp;
        const lines = draftState.split(/\\r?\\n/);
        contents.innerHTML = lines.map(line =>
          `<div data-block="true"><span data-text="true">${line.replace(/&/g, "&amp;").replace(/</g, "&lt;")}</span></div>`
        ).join("");
        const ph = document.getElementById("x-placeholder");
        if (draftState.length > 0 && ph) {
          ph.remove();
        }
      }
      xComp.addEventListener("beforeinput", function(e) {
        if (e.inputType === "deleteContentBackward" || e.inputType === "deleteContentForward" || e.inputType === "deleteByCut") {
          e.preventDefault();
          draftState = "";
          renderDraftState();
        } else if (e.inputType === "insertText" || e.inputType === "insertFromPaste") {
          e.preventDefault();
          draftState += (e.data || "");
          renderDraftState();
        }
      });
      document.getElementById("x-media-file").addEventListener("change", function(e) {
        const f = e.target.files && e.target.files[0];
        document.getElementById("media-status").textContent = f ? `MEDIA:${f.name}` : "NO_MEDIA";
      });
    })();

    function publishPost() {
      const xText = (document.getElementById("x-composer").innerText || "").trim().replace(/\\r?\\n+/g, " | ");
      const rnTitle = (document.getElementById("rn-title").value || "").trim();
      const rnBody = (document.getElementById("rn-body").innerText || "").trim().replace(/\\r?\\n+/g, " | ");
      const media = (document.getElementById("media-status").textContent || "").trim();
      document.getElementById("publish-status").textContent =
        `PUBLISHED x="${xText}" title="${rnTitle}" body="${rnBody}" media="${media}"`;
    }
  </script>
</body>
</html>
"""


class _BenchHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/pricing"):
            body = PRICING_HTML
        elif self.path.startswith("/canvas"):
            body = CANVAS_HTML
        elif self.path.startswith("/editor"):
            body = EDITOR_HTML
        else:
            body = ORDERS_HTML
        raw = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt: str, *args: object) -> None:
        pass


class BenchWebServer:
    def __init__(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), _BenchHandler)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> BenchWebServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
