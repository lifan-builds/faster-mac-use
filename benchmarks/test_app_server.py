# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Faster Mac Use; Section 7 attribution terms in ATTRIBUTION.md.
"""Deterministic local SaaS web benchmark server testing dense DOMs, top-banner index shifts, and Focus Emulation."""

from __future__ import annotations
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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

  <!-- Background timeline hidden file input (appears earlier in DOM than modal) -->
  <div id="bg-timeline">
    <input id="bg-timeline-file" type="file" accept="image/*" style="display:none">
    <div id="bg-media-status" role="status">BG_NO_MEDIA</div>
  </div>

  <!-- X / Draft.js style composer -->
  <div class="editor-box DraftEditor-root">
    <div id="x-placeholder" class="public-DraftEditorPlaceholder-root">What's happening?</div>
    <div id="x-composer" data-testid="tweetTextarea_0" role="textbox" contenteditable="true" aria-multiline="true">
      <div data-contents="true"><div data-block="true"><span data-text="true"><br data-text="true"></span></div></div>
    </div>
    <div class="toolbar" style="margin-top:8px;">
      <input id="x-media-file" type="file" accept="video/*,image/*" style="display:none">
      <button id="x-media-btn" type="button" aria-label="Add photos or video">Add photos or video</button>
    </div>
  </div>

  <!-- Compose modal with file input >4 ancestor levels away from trigger button -->
  <div id="compose-modal" role="dialog" aria-modal="true" class="editor-box">
    <input id="modal-media-file" type="file" accept="video/*,image/*" style="display:none">
    <div id="modal-media-status" role="status">MODAL_NO_MEDIA</div>
    <div class="lvl-1"><div class="lvl-2"><div class="lvl-3"><div class="lvl-4"><div class="lvl-5">
      <button id="modal-media-btn" type="button" aria-label="Add modal media">Add modal media</button>
    </div></div></div></div></div>
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
      document.addEventListener("selectionchange", function() {
        const sel = window.getSelection && window.getSelection();
        if (draftState === "" && sel && sel.rangeCount > 0) {
          const r = sel.getRangeAt(0);
          if (r.startContainer === xComp && !r.collapsed) {
            xComp.dataset.emptySelectCorrupted = "1";
          }
        }
      });
      function renderDraftState() {
        const contents = xComp.querySelector("[data-contents='true']") || xComp;
        if (draftState.length === 0) {
          contents.innerHTML = '<div data-block="true"><span data-text="true"><br data-text="true"></span></div>';
          return;
        }
        const lines = draftState.split(/\\r?\\n/);
        contents.innerHTML = lines.map(line =>
          `<div data-block="true"><span data-text="true">${line ? line.replace(/&/g, "&amp;").replace(/</g, "&lt;") : '<br data-text="true">'}</span></div>`
        ).join("");
        const ph = document.getElementById("x-placeholder");
        if (draftState.length > 0 && ph) {
          ph.remove();
        }
      }
      xComp.addEventListener("paste", function(e) {
        if (!e.clipboardData) return;
        const pasted = e.clipboardData.getData("text/plain") || "";
        e.preventDefault();
        const sel = window.getSelection && window.getSelection();
        if (draftState.length > 0 && sel && sel.isCollapsed) {
          draftState += pasted;
        } else {
          draftState = pasted;
        }
        renderDraftState();
      });
      xComp.addEventListener("beforeinput", function(e) {
        if (e.inputType === "deleteContentBackward" || e.inputType === "deleteContentForward" || e.inputType === "deleteByCut") {
          e.preventDefault();
          draftState = "";
          renderDraftState();
        } else if (e.inputType === "insertText" || e.inputType === "insertFromPaste") {
          e.preventDefault();
          const data = e.data || "";
          if (data.includes("\\n")) {
            const parts = data.split(/\\r?\\n/);
            draftState = parts[parts.length - 1];
          } else {
            draftState += data;
          }
          renderDraftState();
        }
      });
      document.getElementById("x-media-file").addEventListener("change", function(e) {
        const f = e.target.files && e.target.files[0];
        document.getElementById("media-status").textContent = f ? `MEDIA:${f.name}` : "NO_MEDIA";
      });
      document.getElementById("bg-timeline-file").addEventListener("change", function(e) {
        const f = e.target.files && e.target.files[0];
        document.getElementById("bg-media-status").textContent = f ? `BG_MEDIA:${f.name}` : "BG_NO_MEDIA";
      });
      document.getElementById("modal-media-file").addEventListener("change", function(e) {
        const f = e.target.files && e.target.files[0];
        document.getElementById("modal-media-status").textContent = f ? `MODAL_MEDIA:${f.name}` : "MODAL_NO_MEDIA";
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


STRICT_CSP_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Strict CSP App — No unsafe-eval</title>
</head>
<body>
  <h1>Strict CSP Workspace</h1>
  <div id="csp-status" role="status">CSP_INITIAL</div>
  <button id="csp-btn" type="button">Run Action</button>
</body>
</html>
"""


CHECKOUT_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Hosted PCI Checkout</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 24px; }}
    .field-row {{ margin-bottom: 12px; }}
    .pci-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; max-width: 680px; margin: 12px 0; }}
    .pci-frame {{ width: 310px; height: 42px; border: 1px solid #94a3b8; border-radius: 6px; display: block; }}
  </style>
</head>
<body>
  <h1>Checkout Payment &amp; Shipping</h1>
  <div class="field-row">
    <input id="checkout-email" name="email" placeholder="Email address" aria-label="Email address" value="">
  </div>

  <div class="pci-grid" id="paymentjs-container">
    <div id="cc-name-wrap">
      <label for="cc-name-wrap">Name on Card</label>
      <iframe id="first-data-payment-field-name" name="first-data-payment-field-name" title="Cardholder Name" class="pci-frame" src="{pci_origin}/pci-frame?field=name"></iframe>
    </div>
    <div id="cc-card-wrap">
      <iframe id="first-data-payment-field-card" name="first-data-payment-field-card" title="Card Number" class="pci-frame" src="{pci_origin}/pci-frame?field=card"></iframe>
    </div>
    <div id="cc-exp-wrap">
      <iframe id="first-data-payment-field-exp" role="presentation" name="first-data-payment-field-exp" title="Expiration Date" class="pci-frame" src="{pci_origin}/pci-frame?field=exp"></iframe>
    </div>
    <div id="cc-cvv-wrap">
      <iframe id="first-data-payment-field-cvv" name="first-data-payment-field-cvv" title="Security Code" class="pci-frame" src="{pci_origin}/pci-frame?field=cvv"></iframe>
    </div>
  </div>

  <!-- Non-actionable iframes that must NOT clutter snapshot (even with role="presentation") -->
  <iframe id="tracking-pixel" role="presentation" title="Stripe Metrics" src="{pci_origin}/pci-frame?pixel=1" style="width:3440px;height:1px;border:0;"></iframe>
  <iframe id="hidden-beacon" src="{pci_origin}/pci-frame?beacon=1" style="display:none;width:200px;height:40px;"></iframe>
  <iframe id="same-origin-blank" src="about:blank" style="width:120px;height:30px;border:0;"></iframe>

  <!-- CSS display:contents button like Hertz recent-search card -->
  <button id="recent-search-btn" type="button" aria-label="Recent search card" style="display:contents;" onclick="document.getElementById('shipping-address').value='YYC Pickup'">
    <div style="padding:10px;border:1px solid #94a3b8;width:260px;">Calgary International Airport</div>
  </button>

  <div class="field-row">
    <input id="shipping-address" name="shippingAddress" placeholder="Shipping Address" aria-label="Shipping Address" value="">
  </div>

  <button id="place-order-btn" type="button" onclick="window.location.href='/confirmation?order=ORD-991'">Continue to Confirmation</button>
  <a id="express-nav-link" href="/confirmation?order=ORD-992">Express Checkout Link</a>
</body>
</html>
"""


CONFIRMATION_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Order Confirmation</title>
</head>
<body>
  <h1>Booking Confirmed</h1>
  <div id="confirm-status" role="status">CONFIRMED_READY</div>
  <a id="back-checkout" href="/checkout">Back to Checkout</a>
  <script>
    const params = new URLSearchParams(window.location.search);
    document.getElementById("confirm-status").textContent = "CONFIRMED:" + (params.get("order") || "NONE");
  </script>
</body>
</html>
"""


VEHICLES_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Select a Vehicle — Rental Booking</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 20px; }
    .filters { display: flex; gap: 16px; margin-bottom: 16px; }
    .vehicle-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; max-width: 920px; }
    .MuiCard-root {
      border: 1px solid #cbd5e1;
      border-radius: 10px;
      padding: 14px;
      cursor: pointer;
      background: #ffffff;
    }
    .vehicle-title { font-weight: 700; font-size: 15px; display: block; }
    .vehicle-meta { color: #475569; font-size: 13px; display: block; margin-top: 4px; }
    .rate-pill { color: #0f172a; font-weight: 600; margin-top: 8px; display: inline-block; }
  </style>
</head>
<body>
  <h1>Choose Your Vehicle</h1>
  <div id="selected-vehicle" role="status">VEHICLE_NONE</div>
  <div class="filters">
    <label><input type="checkbox" id="flt-suv" aria-label="SUV"> SUV</label>
    <label><input type="checkbox" id="flt-ev" aria-label="Electric"> Electric</label>
  </div>
  <div id="vehicles-grid" class="vehicle-grid"></div>
  <footer style="margin-top: 20px;">
    <a href="#rental-terms">Rental Terms</a>
  </footer>
  <script>
    const FLEET = [
      ["Economy", "Kia Rio or similar", 42],
      ["Compact", "Nissan Versa or similar", 45],
      ["Midsize", "Toyota Corolla or similar", 48],
      ["Standard", "Volkswagen Jetta or similar", 51],
      ["Fullsize", "Chevrolet Malibu or similar", 54],
      ["Compact SUV", "Chevrolet Trax or similar", 57],
      ["Midsize SUV", "Nissan Rogue or similar", 62],
      ["Standard SUV", "Ford Edge or similar", 68],
      ["Fullsize SUV", "Chevrolet Tahoe or similar", 89],
      ["Luxury Sedan", " Chrysler 300 or similar", 79],
      ["Electric", "Tesla Model 3 or similar", 66],
      ["Electric SUV", "Polestar 2 or similar", 72],
      ["Minivan", "Chrysler Pacifica or similar", 84],
      ["Convertible", "Ford Mustang or similar", 92],
      ["Pickup Truck", "RAM 1500 or similar", 76],
      ["Premium SUV", "GMC Yukon or similar", 109]
    ];

    function renderVehicles(selectedIdx) {
      const grid = document.getElementById("vehicles-grid");
      grid.innerHTML = "";
      FLEET.forEach(([cat, model, rate], idx) => {
        const card = document.createElement("div");
        card.className = "MuiPaper-root MuiCard-root vehicle-card";
        card.innerHTML = `
          <div class="MuiCardContent-root">
            <span class="vehicle-title">${cat} - ${model.trim()}</span>
            <span class="vehicle-meta">5 Seats · Automatic · Unlimited Miles</span>
            <div class="rate-pill">Pay Later $${rate}/day${selectedIdx === idx ? " (Selected)" : ""}</div>
          </div>
        `;
        const onSelect = () => {
          document.getElementById("selected-vehicle").textContent = `SELECTED:${idx + 1}:${cat}:${rate}`;
          renderVehicles(idx);
        };
        card.__reactProps$99fmu = { onClick: onSelect };
        card.addEventListener("click", onSelect);
        grid.appendChild(card);
      });
    }
    renderVehicles(-1);
  </script>
</body>
</html>
"""

RENTAL_BOOKING_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>YYC Rental Reservation Summary</title>
  <style>
    body { font-family: -apple-system, sans-serif; margin: 0; padding: 0; }
    header, main, footer { padding: 14px 20px; }
    .summary-box { border: 1px solid #cbd5e1; border-radius: 8px; padding: 12px; margin-bottom: 12px; }
    table { border-collapse: collapse; width: 100%; margin: 10px 0; }
    th, td { border: 1px solid #e2e8f0; padding: 6px 10px; text-align: left; }
    footer { background: #f8fafc; border-top: 1px solid #e2e8f0; margin-top: 24px; }
    footer a { display: inline-block; margin-right: 12px; margin-bottom: 6px; font-size: 13px; }
  </style>
</head>
<body>
  <header>
    <h1>Calgary Intl Airport (YYC) Rental</h1>
  </header>
  <main>
    <section id="booking-summary" class="summary-box">
      <h2>Reservation Confirmed</h2>
      <p>Confirmation Number: MOCK9042XYZ</p>
      <p>Estimated Total: CAD 76.29 (Taxes &amp; Airport Concession Included)</p>
      <div id="promo-banner">No promo code applied</div>
      <div id="spinner-slot"></div>
      <div id="breakdown-panel" style="display:none;">Base Rate CAD 54.00 + Concession CAD 22.29</div>
      <div id="filter-state">Showing: All Vehicles</div>
    </section>

    <div class="actions">
      <button id="apply-promo-btn" type="button">Apply Promo</button>
      <button id="toggle-breakdown-btn" type="button">Toggle Breakdown</button>
      <button id="pushstate-suv-btn" type="button">Filter SUVs</button>
    </div>

    <table id="offers-ledger">
      <thead>
        <tr><th>Card ID</th><th>Merchant</th><th>Status</th><th>Cashback</th></tr>
      </thead>
      <tbody>
        <tr><td>#mock1001</td><td>Hertz</td><td>Activated</td><td>10% Back</td></tr>
        <tr><td>#mock1002</td><td>Avis</td><td>Available</td><td>8% Back</td></tr>
      </tbody>
    </table>
  </main>

  <footer role="contentinfo">
    <a href="#f1" onclick="document.getElementById('filter-state').textContent='FOOTER:Privacy'; return false;">Privacy Policy</a>
    <a href="#f2">Terms of Use</a>
    <a href="#f3">Cookie Settings</a>
    <a href="#f4">Accessibility</a>
    <a href="#f5">Gold Plus Rewards</a>
    <a href="#f6">Best Rate Guarantee</a>
    <a href="#f7">Roadside Assistance</a>
    <a href="#f8">Travel Agents</a>
    <a href="#f9">Affiliate Program</a>
    <a href="#f10">Corporate Fleet</a>
    <a href="#f11">Sitemap</a>
    <a href="#f12">Press Room</a>
    <a href="#f13">Sustainability</a>
    <a href="#f14">Careers</a>
    <a href="#f15">Help Center</a>
    <a href="#f16" onclick="document.getElementById('filter-state').textContent='FOOTER:InvestorRelations'; return false;">Investor Relations</a>
  </footer>

  <script>
    document.getElementById("apply-promo-btn").addEventListener("click", () => {
      const slot = document.getElementById("spinner-slot");
      slot.innerHTML = '<span class="loading-spinner">Validating CDP 200692...</span>';
      setTimeout(() => {
        slot.innerHTML = "";
        document.getElementById("promo-banner").textContent = "Promo 200692 Applied (-15%)";
      }, 90);
    });
    document.getElementById("toggle-breakdown-btn").addEventListener("click", () => {
      const p = document.getElementById("breakdown-panel");
      p.style.display = p.style.display === "none" ? "block" : "none";
    });
    document.getElementById("pushstate-suv-btn").addEventListener("click", () => {
      history.pushState({ vehicle: "suv" }, "", "/rental-booking?vehicle=suv");
      document.getElementById("filter-state").textContent = "Showing: Midsize SUV";
    });
  </script>
</body>
</html>
"""


class _BenchHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        strict_csp = False
        port = self.server.server_address[1]
        if self.path.startswith("/pricing"):
            body = PRICING_HTML
        elif self.path.startswith("/canvas"):
            body = CANVAS_HTML
        elif self.path.startswith("/editor"):
            body = EDITOR_HTML
        elif self.path.startswith("/strict-csp"):
            body = STRICT_CSP_HTML
            strict_csp = True
        elif self.path.startswith("/checkout"):
            pci_origin = (
                "https://lib.paymentjs.firstdata.com"
                if "origin=firstdata" in self.path
                else f"http://localhost:{port}"
            )
            body = CHECKOUT_HTML_TEMPLATE.format(pci_origin=pci_origin)
        elif self.path.startswith("/pci-frame"):
            body = "<!doctype html><html><body><input aria-label='Secret PAN'></body></html>"
        elif self.path.startswith("/confirmation"):
            body = CONFIRMATION_HTML
        elif self.path.startswith("/vehicles"):
            body = VEHICLES_HTML
        elif self.path.startswith("/rental-booking"):
            body = RENTAL_BOOKING_HTML
        elif self.path.startswith("/qa-test"):
            body = (
                "<!doctype html><html><head>"
                "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                "<title>QA Responsive App</title>"
                "<style>"
                "body { margin: 0; font-family: system-ui; }"
                ".mobile-nav { display: none; }"
                ".desktop-nav { display: block; }"
                "@media (max-width: 600px) { .mobile-nav { display: block; } .desktop-nav { display: none; } }"
                ".tall-spacer { height: 2100px; background: linear-gradient(#f8fafc, #e2e8f0); }"
                "</style></head><body>"
                "<nav><button class='desktop-nav'>Desktop Menu</button>"
                "<button class='mobile-nav'>Mobile Drawer</button></nav>"
                "<main>"
                "<button id='btn-err' onclick=\""
                "console.error('Hydration mismatch on PriceCard');"
                "fetch('/api/qa-fail', {method: 'POST'});"
                "setTimeout(() => { throw new Error('Uncaught checkout crash'); }, 0);"
                "document.getElementById('qa-status').textContent = 'Triggered';"
                "\">Trigger Bugs</button>"
                "<p id='qa-status'>Idle</p>"
                "<div class='tall-spacer'></div>"
                "<button id='bottom-cta'>Bottom CTA</button>"
                "</main></body></html>"
            )
        else:
            body = ORDERS_HTML
        raw = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if strict_csp:
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        if self.path.startswith("/api/qa-fail"):
            raw = b'{"error":"Order DB timeout"}'
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self.send_response(200)
        self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        pass


class BenchWebServer:
    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _BenchHandler)
        self.port = int(self.server.server_address[1])
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
