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


class _BenchHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = PRICING_HTML if self.path.startswith("/pricing") else ORDERS_HTML
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
