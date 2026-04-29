"use strict";

const fmtCHF = n => (n == null || !Number.isFinite(+n) ? "—"
  : Number(n).toLocaleString("de-CH",
    { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const fmtPct = n => (n == null || !Number.isFinite(+n) ? "—"
  : (n >= 0 ? "+" : "") + (n * 100).toFixed(2) + "%");
const fmtNum = (n, d = 4) => (n == null || !Number.isFinite(+n) ? "—"
  : Number(n).toFixed(d));
const cls = n => (n == null || !Number.isFinite(+n) ? ""
  : (n >= 0 ? "pos" : "neg"));

// --- Safe date helpers ----------------------------------------------------
// Safari / older WebKits throw "The string did not match the expected pattern"
// when parsing certain ISO timestamps (e.g. with microseconds or a "+00:00"
// offset). We normalise the string and fall back to the raw value if it still
// can't be parsed instead of bubbling the error up.
function parseDate(s) {
  if (s == null || s === "") return null;
  if (s instanceof Date) return Number.isNaN(s.getTime()) ? null : s;
  if (typeof s !== "string") return null;
  let str = s.trim();
  // Replace space separator with T (e.g. SQLite default format)
  str = str.replace(" ", "T");
  // Trim sub-second precision below ms (Safari refuses microseconds)
  str = str.replace(/(\.\d{3})\d+/, "$1");
  let d = new Date(str);
  if (!Number.isNaN(d.getTime())) return d;
  // Strip timezone if present
  d = new Date(str.replace(/[+-]\d{2}:?\d{2}$/, "").replace(/Z$/, ""));
  return Number.isNaN(d.getTime()) ? null : d;
}

const fmtDateTime = s => {
  const d = parseDate(s);
  return d ? d.toLocaleString("de-CH") : (s ?? "—");
};
const fmtDate = s => {
  const d = parseDate(s);
  return d ? d.toLocaleDateString("de-CH") : (s ?? "—");
};
const fmtTime = s => {
  const d = parseDate(s);
  return d ? d.toLocaleTimeString("de-CH") : (s ?? "—");
};

const $ = sel => document.querySelector(sel);

let equityChart = null;

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

async function postJSON(url) {
  const r = await fetch(url, { method: "POST" });
  return r.json();
}

function setStatus(msg, kind = "") {
  const el = $("#status");
  el.textContent = msg;
  el.className = "status " + (kind || "");
}

async function loadSummary() {
  const s = await getJSON("/api/summary");
  $("#kpi-total").textContent = fmtCHF(s.total_chf);
  const pnlEl = $("#kpi-pnl");
  pnlEl.textContent = fmtCHF(s.pnl_chf);
  pnlEl.className = "value " + cls(s.pnl_chf);
  $("#kpi-pnl-sub").textContent = "CHF (" + fmtPct(s.pnl_pct) + ")";
  $("#kpi-cash").textContent = fmtCHF(s.cash_chf);
  $("#kpi-holdings").textContent = fmtCHF(s.holdings_chf);
  $("#kpi-universe").textContent = s.universe_size;
  $("#kpi-cycle").textContent = fmtDateTime(s.last_cycle_at);
  $("#kpi-observer").textContent = s.observer_running ? "Observer läuft" : "Observer aus";

  const btn = $("#btn-observer");
  btn.textContent = s.observer_running ? "Observer ⏸" : "Observer ▶";
  btn.dataset.running = s.observer_running ? "1" : "0";

  // Positions table
  const tb = $("#tbl-positions tbody");
  tb.innerHTML = "";
  for (const p of s.positions) {
    const weight = s.total_chf > 0 ? p.market_value_chf / s.total_chf : 0;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${p.ticker}</td>
      <td class="num">${fmtNum(p.quantity)}</td>
      <td>${p.currency}</td>
      <td class="num">${fmtNum(p.price_native, 2)}</td>
      <td class="num">${fmtNum(p.fx_rate)}</td>
      <td class="num">${fmtCHF(p.market_value_chf)}</td>
      <td class="num">${(weight * 100).toFixed(1)}%</td>
      <td class="num ${cls(p.unrealized_pnl_pct)}">${fmtPct(p.unrealized_pnl_pct)}</td>`;
    tb.appendChild(tr);
  }
  if (s.positions.length === 0) {
    tb.innerHTML = `<tr><td colspan="8" style="color:var(--muted)">Keine Positionen.</td></tr>`;
  }
}

async function loadHistory() {
  const payload = await getJSON("/api/history");
  const history = payload.history || [];
  const benchmark = payload.benchmark || [];
  const benchTicker = payload.benchmark_ticker || "Benchmark";

  // Combine timeline so both series share an x-axis.
  const tsSet = new Set();
  for (const r of history) tsSet.add(r.ts);
  for (const r of benchmark) tsSet.add(r.ts);
  const timeline = [...tsSet].sort();
  const histMap = Object.fromEntries(history.map(r => [r.ts, r.total_chf]));
  const benchMap = Object.fromEntries(benchmark.map(r => [r.ts, r.total_chf]));
  const labels = timeline.map(t => fmtDateTime(t));
  const totals = timeline.map(t => histMap[t] ?? null);
  const bench = timeline.map(t => benchMap[t] ?? null);

  if (!equityChart) {
    const ctx = document.getElementById("chart-equity").getContext("2d");
    equityChart = new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Depot CHF",
            data: totals,
            borderColor: "#4ea1ff",
            backgroundColor: "rgba(78,161,255,0.12)",
            borderWidth: 2, pointRadius: 0, fill: true, tension: 0.25,
            spanGaps: true,
          },
          {
            label: "Benchmark (" + benchTicker + ")",
            data: bench,
            borderColor: "#f5b342",
            backgroundColor: "rgba(245,179,66,0.0)",
            borderWidth: 1.5, borderDash: [4, 4], pointRadius: 0,
            tension: 0.25, fill: false, spanGaps: true,
          },
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: {
          x: { ticks: { color: "#8a93a6" }, grid: { color: "#1f2530" } },
          y: { ticks: { color: "#8a93a6" }, grid: { color: "#1f2530" } }
        },
        plugins: {
          legend: { display: true, labels: { color: "#8a93a6" } }
        }
      }
    });
  } else {
    equityChart.data.labels = labels;
    equityChart.data.datasets[0].data = totals;
    equityChart.data.datasets[1].data = bench;
    equityChart.data.datasets[1].label = "Benchmark (" + benchTicker + ")";
    equityChart.update("none");
  }
}

async function loadForecasts() {
  const rows = await getJSON("/api/forecasts/latest");
  const buys = rows.filter(r => r.expected_return >= 0).slice(0, 8);
  const sells = rows.filter(r => r.expected_return < 0)
                    .sort((a, b) => a.expected_return - b.expected_return).slice(0, 8);
  const fillTbl = (sel, list, dirKey) => {
    const tb = document.querySelector(sel + " tbody");
    tb.innerHTML = "";
    for (const r of list) {
      const tr = document.createElement("tr");
      const dir = dirKey === "up" ? r.direction_prob : 1 - r.direction_prob;
      tr.innerHTML = `
        <td>${r.ticker}</td>
        <td class="num ${cls(r.expected_return)}">${fmtPct(r.expected_return)}</td>
        <td class="num">${(dir * 100).toFixed(0)}%</td>
        <td>${r.target_date}</td>`;
      tb.appendChild(tr);
    }
    if (list.length === 0)
      tb.innerHTML = `<tr><td colspan="4" style="color:var(--muted)">Keine.</td></tr>`;
  };
  fillTbl("#tbl-buys", buys, "up");
  fillTbl("#tbl-sells", sells, "down");

  // Accuracy panel
  const resolved = rows.filter(r => r.realized_return != null);
  const acc = $("#accuracy");
  if (resolved.length > 0) {
    const hits = resolved.filter(r =>
      Math.sign(r.expected_return) === Math.sign(r.realized_return)).length;
    const mae = resolved.reduce((s, r) =>
      s + Math.abs(r.expected_return - r.realized_return), 0) / resolved.length;
    acc.innerHTML = `
      <div class="big">${(hits / resolved.length * 100).toFixed(1)}%</div>
      Hit-Rate über ${resolved.length} aufgelöste Forecasts<br/>
      Mittlerer Fehler: ${(mae * 100).toFixed(2)}%`;
  } else {
    acc.textContent = "Noch keine aufgelösten Forecasts (warten auf Zielzeitpunkt).";
  }
}

async function loadTrades() {
  const rows = await getJSON("/api/trades?limit=20");
  const tb = $("#tbl-trades tbody");
  tb.innerHTML = "";
  for (const r of rows) {
    const tr = document.createElement("tr");
    const sideCls = r.side === "BUY" ? "pos" : "neg";
    tr.innerHTML = `
      <td>${fmtDateTime(r.ts)}</td>
      <td>${r.ticker}</td>
      <td class="${sideCls}">${r.side}</td>
      <td class="num">${fmtNum(r.quantity)}</td>
      <td class="num">${fmtCHF(r.quantity * r.price_chf)}</td>
      <td class="num">${fmtCHF(r.fee_chf)}</td>
      <td style="color:var(--muted)">${r.rationale || ""}</td>`;
    tb.appendChild(tr);
  }
  if (rows.length === 0)
    tb.innerHTML = `<tr><td colspan="7" style="color:var(--muted)">Noch keine Trades.</td></tr>`;
}

async function loadModels() {
  const m = await getJSON("/api/models");
  const tb = $("#tbl-models tbody");
  tb.innerHTML = "";
  for (const r of m.champions) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${r.model}</td>
      <td class="num">${Number(r.score).toFixed(3)}</td>
      <td>${r.metric}</td>
      <td>${fmtDate(r.updated_at)}</td>`;
    tb.appendChild(tr);
  }
  if (m.champions.length === 0)
    tb.innerHTML = `<tr><td colspan="4" style="color:var(--muted)">Noch nicht optimiert.</td></tr>`;
}

async function safeRun(name, fn) {
  try { await fn(); return null; }
  catch (e) { console.error(name, e); return name + ": " + e.message; }
}

async function refreshAll() {
  const errs = (await Promise.all([
    safeRun("summary",   loadSummary),
    safeRun("history",   loadHistory),
    safeRun("forecasts", loadForecasts),
    safeRun("trades",    loadTrades),
    safeRun("models",    loadModels),
  ])).filter(Boolean);
  $("#last-refresh").textContent = "letztes Update: " + fmtTime(new Date());
  if (errs.length === 0) {
    setStatus("ok", "ok");
  } else {
    setStatus("Fehler: " + errs.join(" | "), "err");
  }
}

// ---- Buttons --------------------------------------------------------
$("#btn-cycle").addEventListener("click", async () => {
  setStatus("Zyklus läuft …");
  const r = await postJSON("/api/cycle");
  setStatus(r.ok ? "Zyklus ok" : "Zyklus fehlgeschlagen", r.ok ? "ok" : "err");
  await refreshAll();
});

$("#btn-optimize").addEventListener("click", async () => {
  setStatus("Optimierung läuft …");
  const r = await postJSON("/api/optimize");
  setStatus("Optimiert: " + r.length + " Modelle", "ok");
  await loadModels();
});

$("#btn-observer").addEventListener("click", async () => {
  const btn = $("#btn-observer");
  if (btn.dataset.running === "1") {
    await postJSON("/api/observer/stop");
  } else {
    await postJSON("/api/observer/start?interval=900");
  }
  await loadSummary();
});

$("#btn-reset").addEventListener("click", async () => {
  if (!confirm("Demo-Depot wirklich zurücksetzen? Alle Trades und " +
               "Positionen werden gelöscht.")) return;
  const r = await postJSON("/api/portfolio/reset?confirm=yes");
  setStatus(r.ok ? "Depot zurückgesetzt" : "Fehler", r.ok ? "ok" : "err");
  await refreshAll();
});

// ---- Boot -----------------------------------------------------------
refreshAll();
setInterval(refreshAll, 15000);
