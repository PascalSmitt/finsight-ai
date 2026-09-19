"use strict";

const PALETTE = ["#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed", "#0891b2", "#db2777", "#4b5563"];
const CHART_METRICS = { revenue: "money", net_income: "money", net_margin: "pct", liabilities_to_assets: "pct" };

const state = { companies: [], rows: [], selected: new Set(), charts: {}, sessionId: null, busy: false };
const $ = (id) => document.getElementById(id);

// ---------- formatting (money values are USD millions) ----------
function fmt(v, kind) {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  if (kind === "money") {
    const a = Math.abs(v), s = v < 0 ? "-" : "";
    if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(2)}T`;
    if (a >= 1e3) return `${s}$${(a / 1e3).toFixed(1)}B`;
    return `${s}$${a.toFixed(0)}M`;
  }
  if (kind === "pct") return `${v.toFixed(1)}%`;
  return `${v.toFixed(2)}x`;
}
const colorOf = (ticker) => PALETTE[state.companies.findIndex((c) => c.ticker === ticker) % PALETTE.length];
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function setStatus(text, isError = false) {
  const el = $("status");
  el.textContent = text;
  el.classList.toggle("err", isError);
}

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    const err = new Error(`${res.status} ${res.statusText}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

// ---------- dashboard ----------
function renderChips() {
  const box = $("chips");
  box.replaceChildren();
  for (const c of state.companies) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip";
    b.style.setProperty("--c", colorOf(c.ticker));
    b.setAttribute("aria-pressed", String(state.selected.has(c.ticker)));
    const dot = Object.assign(document.createElement("span"), { className: "dot" });
    b.append(dot, `${c.name} (${c.ticker})`);
    b.addEventListener("click", () => {
      if (state.selected.has(c.ticker)) {
        if (state.selected.size === 1) return; // keep at least one company selected
        state.selected.delete(c.ticker);
      } else {
        state.selected.add(c.ticker);
      }
      renderChips();
      renderDashboard();
    });
    box.append(b);
  }
}

function selectedRows() {
  return state.rows.filter((r) => state.selected.has(r.ticker));
}

function renderKpis() {
  const box = $("kpis");
  box.replaceChildren();
  for (const t of state.selected) {
    const rows = state.rows.filter((r) => r.ticker === t);
    const r = rows[rows.length - 1];
    if (!r) continue;
    const card = document.createElement("div");
    card.className = "kpi";
    card.style.setProperty("--c", colorOf(t));
    const g = r.revenue_growth;
    const growth = g === null ? "–" : `${g >= 0 ? "▲" : "▼"} ${Math.abs(g).toFixed(1)}%`;

    const name = document.createElement("div");
    name.className = "name";
    name.append(Object.assign(document.createElement("span"), { textContent: r.company }),
                Object.assign(document.createElement("span"), { textContent: `FY${r.fiscal_year}` }));
    const big = Object.assign(document.createElement("div"), { className: "big", textContent: fmt(r.revenue, "money") });
    const line = (label, value, cls = "") => {
      const row = document.createElement("div");
      row.className = "row";
      row.append(Object.assign(document.createElement("span"), { textContent: label }),
                 Object.assign(document.createElement("span"), { textContent: value, className: cls }));
      return row;
    };
    card.append(name, big,
      line("Revenue growth", growth, g === null ? "" : g >= 0 ? "up" : "down"),
      line("Net margin", fmt(r.net_margin, "pct")),
      line("Liabilities / assets", fmt(r.liabilities_to_assets, "pct")));
    box.append(card);
  }
}

function chartTheme() {
  return { text: css("--muted"), grid: css("--border") };
}

function renderCharts() {
  const years = [...new Set(selectedRows().map((r) => r.fiscal_year))].sort();
  const theme = chartTheme();
  for (const [metric, kind] of Object.entries(CHART_METRICS)) {
    const datasets = [...state.selected].map((t) => ({
      label: state.companies.find((c) => c.ticker === t).name,
      data: years.map((y) => {
        const r = state.rows.find((x) => x.ticker === t && x.fiscal_year === y);
        return r ? r[metric] : null;
      }),
      borderColor: colorOf(t), backgroundColor: colorOf(t), tension: 0.25, spanGaps: true, pointRadius: 3,
    }));
    const existing = state.charts[metric];
    if (existing) existing.destroy();
    state.charts[metric] = new Chart($(`c-${metric}`), {
      type: "line",
      data: { labels: years.map(String), datasets },
      options: chartOptions(kind, theme, true),
    });
  }
}

function chartOptions(kind, theme, legend) {
  return {
    responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { display: legend, labels: { color: theme.text, boxWidth: 10, usePointStyle: true } },
      tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.parsed.y, kind)}` } },
    },
    scales: {
      x: { ticks: { color: theme.text }, grid: { color: theme.grid } },
      y: { ticks: { color: theme.text, callback: (v) => fmt(v, kind) }, grid: { color: theme.grid } },
    },
  };
}

function renderTable() {
  const body = document.querySelector("#table tbody");
  body.replaceChildren();
  const rows = selectedRows().sort((a, b) => a.ticker.localeCompare(b.ticker) || b.fiscal_year - a.fiscal_year);
  const latestYears = new Map();
  for (const r of rows) {
    const n = (latestYears.get(r.ticker) || 0) + 1;
    latestYears.set(r.ticker, n);
    if (n > 5) continue; // show the 5 most recent years per company
    const tr = document.createElement("tr");
    const cells = [
      [r.company, ""], [`FY${r.fiscal_year}`, ""], [fmt(r.revenue, "money"), "num"],
      [r.revenue_growth === null ? "–" : `${r.revenue_growth.toFixed(1)}%`, "num"],
      [fmt(r.net_income, "money"), "num"], [fmt(r.net_margin, "pct"), "num"],
      [fmt(r.total_assets, "money"), "num"], [fmt(r.total_liabilities, "money"), "num"],
      [fmt(r.operating_cash_flow, "money"), "num"],
    ];
    for (const [text, cls] of cells) {
      const td = document.createElement("td");
      td.textContent = text;
      if (cls) td.className = cls;
      tr.append(td);
    }
    body.append(tr);
  }
}

function renderDashboard() {
  renderKpis();
  renderCharts();
  renderTable();
}

// ---------- chat ----------
function addMessage(role, text, extra = {}) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.textContent = text;
  if (extra.chart) {
    const box = Object.assign(document.createElement("div"), { className: "mini" });
    const canvas = document.createElement("canvas");
    box.append(canvas);
    el.append(box);
    queueMicrotask(() => drawMiniChart(canvas, extra.chart));
  }
  if (extra.meta) {
    el.append(Object.assign(document.createElement("small"), { className: "meta", textContent: extra.meta }));
  }
  $("log").append(el);
  $("log").scrollTop = $("log").scrollHeight;
  return el;
}

function drawMiniChart(canvas, spec) {
  const theme = chartTheme();
  const color = PALETTE[0];
  const options = chartOptions(spec.kind, theme, false);
  options.plugins.title = { display: true, text: spec.title, color: theme.text, font: { size: 11 } };
  new Chart(canvas, {
    type: spec.type,
    data: {
      labels: spec.labels,
      datasets: spec.datasets.map((d) => ({ ...d, borderColor: color, backgroundColor: color + "cc", tension: 0.25 })),
    },
    options,
  });
}

function setSuggestions(list) {
  const box = $("suggestions");
  box.replaceChildren();
  for (const text of (list || []).slice(0, 4)) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = text;
    b.addEventListener("click", () => send(text));
    box.append(b);
  }
}

async function send(text) {
  text = text.trim();
  if (!text || state.busy) return;
  state.busy = true;
  $("send").disabled = true;
  addMessage("user", text);
  const typing = addMessage("bot", "Thinking…");
  typing.classList.add("typing");
  try {
    const data = await api("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: state.sessionId }),
    });
    state.sessionId = data.session_id;
    try { localStorage.setItem("finsight_session", state.sessionId); } catch (_) { /* storage unavailable */ }
    typing.remove();
    addMessage("bot", data.reply, { chart: data.chart, meta: data.mode === "llm" ? "AI mode" : "" });
    setSuggestions(data.suggestions);
  } catch (err) {
    typing.remove();
    if (err.status === 429) {
      addMessage("bot", "You are sending messages too quickly. Please wait a moment and try again.");
    } else {
      addMessage("bot", "I couldn't reach the FinSight server. If you are running it locally, start it with " +
        "run.bat (or: uvicorn app.main:app) and try again.");
      setStatus("Server offline", true);
    }
  } finally {
    state.busy = false;
    $("send").disabled = false;
    $("input").focus();
  }
}

function resetChat() {
  $("log").replaceChildren();
  state.sessionId = null;
  try { localStorage.removeItem("finsight_session"); } catch (_) { /* ignore */ }
  addMessage("bot", "Hi! Ask me about revenue, profit, margins, cash flow or leverage for any company shown here. " +
    "I can also compare companies and show trends.");
  setSuggestions(["What is Apple's revenue?", "How has Tesla's net income changed?", "Compare profit margins",
                  "Which company has the highest revenue growth?"]);
}

// ---------- boot ----------
async function init() {
  $("form").addEventListener("submit", (e) => { e.preventDefault(); const v = $("input").value; $("input").value = ""; send(v); });
  $("reset").addEventListener("click", resetChat);
  try { state.sessionId = localStorage.getItem("finsight_session"); } catch (_) { state.sessionId = null; }
  resetChat();
  try {
    [state.companies, state.rows] = await Promise.all([api("/api/companies"), api("/api/financials")]);
    const preferred = ["AAPL", "MSFT", "TSLA"].filter((t) => state.companies.some((c) => c.ticker === t));
    state.selected = new Set(preferred.length ? preferred : state.companies.slice(0, 3).map((c) => c.ticker));
    renderChips();
    renderDashboard();
    setStatus(`${state.companies.length} companies · FY${Math.min(...state.companies.map((c) => c.first_year))}–FY${Math.max(...state.companies.map((c) => c.latest_year))}`);
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderCharts);
  } catch (err) {
    setStatus("API unavailable", true);
  }
}
init();
