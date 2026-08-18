/* Entry point: fetches, state, wiring. Rendering lives in components.js. */

import { fmtUsd, fmtPct, fmtTime, cls } from "./format.js";
import {
  statCard, statGroup, eventCard, positionsTable, watchlistChips, newsItem,
} from "./components.js";

const $ = (id) => document.getElementById(id);
const PAGE_SIZE = 24;

const state = { offset: 0, total: 0, eventType: "", chart: null, bot: null };

/* ------------------------------------------------------------------ masonry */

const FEED_ROW = 4;   // grid-auto-rows unit, must match --feed-row in the CSS
const FEED_GAP = 12;  // vertical breathing room, baked into each card's span

/** Give every card a row span matching its rendered height so grid packs the
    gaps instead of aligning cards into ragged rows. */
function packFeed() {
  const feed = $("events").querySelector(".feed");
  if (!feed) return;
  for (const card of feed.children) {
    const h = card.getBoundingClientRect().height;
    card.style.gridRowEnd = `span ${Math.ceil((h + FEED_GAP) / FEED_ROW)}`;
  }
}

/* Cards change height when a verdict expands or raw json opens; repack after
   the browser has applied the new layout. */
const repack = () => requestAnimationFrame(packFeed);

/* ------------------------------------------------------------- bot heartbeat */

function renderBotTimer() {
  const el = $("bot-timer");
  if (!state.bot) {
    el.innerHTML = '<span class="dot off"></span>no heartbeat yet';
    return;
  }
  const remaining = Math.round((new Date(state.bot.next_tick).getTime() - Date.now()) / 1000);
  const grace = 90; // seconds past next_tick before we call it offline
  if (remaining < -grace) {
    el.innerHTML = `<span class="dot off"></span>offline? last tick ${fmtTime(state.bot.last_tick)}`;
  } else if (remaining <= 0) {
    el.innerHTML = '<span class="dot on"></span>ticking now…';
  } else {
    const m = Math.floor(remaining / 60);
    const s = String(remaining % 60).padStart(2, "0");
    el.innerHTML = `<span class="dot on"></span>next tick <span class="count">${m}:${s}</span>` +
      (state.bot.stocks_open ? "" : ' <span style="color:var(--dim)">· crypto only</span>');
  }
}

/* -------------------------------------------------------------------- loads */

async function loadSummary() {
  const s = await (await fetch("/api/summary")).json();
  const a = s.account, st = s.stats;
  state.bot = s.bot;
  renderBotTimer();

  const err = $("alpaca-error");
  if (s.error) {
    err.style.display = "block";
    err.textContent = "⚠ Alpaca unavailable (stats limited to local logs): " + s.error;
  } else {
    err.style.display = "none";
  }

  const dayPl = a ? a.equity - a.last_equity : null;
  const dayPlPct = a && a.last_equity ? dayPl / a.last_equity : null;
  const nPos = Object.keys(s.positions || {}).length;

  $("stats").innerHTML =
    statGroup("Account", [
      statCard("Equity", fmtUsd(a?.equity)),
      statCard("Cash", fmtUsd(a?.cash)),
      statCard("Day P/L",
        `${fmtUsd(dayPl)} <span class="sub">${fmtPct(dayPlPct)}</span>`, cls(dayPl)),
      statCard("Open positions", nPos),
    ]) +
    statGroup("Activity", [
      statCard("Signals", st.signals),
      statCard("Approval rate",
        st.approval_rate == null ? "—" : (st.approval_rate * 100).toFixed(0) + "%"),
      statCard("Avg confidence", st.avg_confidence ?? "—"),
      statCard("Entries / Exits", `${st.entries} / ${st.exits}`),
      statCard("News events", st.news_events ?? 0),
      statCard("Scanner hits", st.scanner_discoveries ?? 0),
    ]);

  $("positions").innerHTML = positionsTable(s.positions);

  // Only offer the reset when there's something to flatten.
  const btn = $("liquidate");
  btn.hidden = nPos === 0;
  if (nPos === 0) disarmLiquidate();
}

/* ----------------------------------------------------------- liquidate ("sell all")

   Two-step: the first click arms the button, the second sends it. Arming lapses
   so a stray click can't sit primed waiting for the next one — but the window
   has to outlast reading the warning, or it disarms under you mid-sentence. */

const ARM_WINDOW_MS = 15000;

let armed = false, armTimer;

function disarmLiquidate() {
  armed = false;
  clearTimeout(armTimer);
  const btn = $("liquidate");
  btn.classList.remove("armed");
  btn.textContent = "Sell all";
}

function setStatus(text, kind = "") {
  $("liquidate-status").innerHTML = text ? `<span class="${kind}">${text}</span>` : "";
}

async function liquidate() {
  const btn = $("liquidate");

  if (!armed) {
    armed = true;
    btn.classList.add("armed");
    btn.textContent = "Confirm — sell all?";
    setStatus("Closes every open position at market and cancels resting orders. " +
              "The bot keeps trading and may re-enter on its next tick.", "warn");
    armTimer = setTimeout(() => { disarmLiquidate(); setStatus(""); }, ARM_WINDOW_MS);
    return;
  }

  clearTimeout(armTimer);
  btn.disabled = true;
  btn.textContent = "Selling…";
  setStatus("");

  try {
    const r = await fetch("/api/liquidate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: "LIQUIDATE" }),
    });
    const d = await r.json();
    if (!r.ok) {
      setStatus(`Failed: ${d.error || r.status}`, "err");
    } else if (d.failed?.length) {
      setStatus(`Closed ${d.count}, failed on ${d.failed.join(", ")}`, "err");
    } else {
      setStatus(d.message || `Closed ${d.count} position${d.count === 1 ? "" : "s"}.`, "done");
    }
  } catch (e) {
    setStatus(`Failed: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
    disarmLiquidate();
    refreshAll();
  }
}

async function loadHistory() {
  const [period, timeframe] = $("history-period").value.split("|");
  const r = await fetch(`/api/history?period=${period}&timeframe=${timeframe}`);
  if (!r.ok) return;
  const h = await r.json();
  const intraday = timeframe.endsWith("Min") || timeframe === "1H";

  /* Alpaca reports 0 equity for every day before the account was funded.
     Those leading zeros squash the real curve into a flat line at the top. */
  let stamps = h.timestamp || [], equity = h.equity || [];
  const start = equity.findIndex((v) => v > 0);
  if (start > 0) { stamps = stamps.slice(start); equity = equity.slice(start); }

  const labels = stamps.map((t) =>
    new Date(t * 1000).toLocaleDateString(undefined, {
      month: "short", day: "numeric", hour: intraday ? "numeric" : undefined,
    }));

  /* Floor the y-axis span at ±0.25% of the balance. Without this, Chart.js
     auto-scales a few cents of drift into a dramatic-looking slope. */
  let yMin, yMax;
  if (equity.length) {
    const lo = Math.min(...equity), hi = Math.max(...equity);
    const mid = (lo + hi) / 2;
    const span = Math.max(hi - lo, Math.abs(mid) * 0.005) * 1.5;
    yMin = mid - span / 2;
    yMax = mid + span / 2;
  }

  if (state.chart) state.chart.destroy();
  state.chart = new Chart($("equity-chart"), {
    type: "line",
    data: {
      labels,
      datasets: [{
        data: equity,
        borderColor: "#63a4e0",
        backgroundColor: "rgba(99,164,224,0.08)",
        borderWidth: 2,
        fill: true,
        pointRadius: 0,
        tension: 0.25,
      }],
    },
    options: {
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#5d6675", maxTicksLimit: 8 }, grid: { color: "#1b2230" } },
        y: {
          min: yMin, max: yMax,
          ticks: {
            color: "#5d6675",
            maxTicksLimit: 6,
            callback: (v) => "$" + v.toLocaleString("en-US", { maximumFractionDigits: 0 }),
          },
          grid: { color: "#1b2230" },
        },
      },
    },
  });
}

async function loadEvents() {
  const params = new URLSearchParams({
    symbol: $("f-symbol").value,
    event: state.eventType,
    decision: $("f-decision").value,
    q: $("f-q").value,
    limit: PAGE_SIZE,
    offset: state.offset,
  });
  const d = await (await fetch("/api/events?" + params)).json();
  state.total = d.total;

  const sel = $("f-symbol"), current = sel.value;
  sel.innerHTML = '<option value="">All symbols</option>' +
    d.symbols.map((s) => `<option${s === current ? " selected" : ""}>${s}</option>`).join("");

  /* Pin the container's height across the swap. Without this the document
     briefly collapses, the browser clamps scrollY, and a 30s auto-refresh
     yanks you back to the top while you're reading the feed. */
  const holder = $("events");
  holder.style.minHeight = holder.offsetHeight + "px";
  holder.innerHTML = d.events.length
    ? `<div class="feed">${d.events.map(eventCard).join("")}</div>`
    : '<div class="empty">No events match. The bot appends to logs/trades.jsonl as it runs.</div>';
  requestAnimationFrame(() => {
    packFeed();
    holder.style.minHeight = "";
  });

  $("event-count").textContent = state.total ? `${state.total} matching` : "";
  $("pg-info").textContent = state.total
    ? `${state.offset + 1}–${Math.min(state.offset + PAGE_SIZE, state.total)} of ${state.total}`
    : "0 events";
  $("pg-prev").disabled = state.offset === 0;
  $("pg-next").disabled = state.offset + PAGE_SIZE >= state.total;
}

async function loadScanner() {
  try {
    const d = await (await fetch("/api/scanner")).json();
    const wl = d.watchlist || [];
    $("watchlist-count").textContent = wl.length ? `${wl.length} symbols` : "";
    $("watchlist").innerHTML = watchlistChips(wl);
  } catch {
    $("watchlist").innerHTML = '<div class="empty">Scanner unavailable</div>';
  }
}

async function loadNews() {
  try {
    const d = await (await fetch("/api/news?limit=15")).json();
    const items = d.events || [];
    $("news-feed").innerHTML = items.length
      ? items.map(newsItem).join("")
      : '<div class="empty">No news yet</div>';
  } catch {
    $("news-feed").innerHTML = '<div class="empty">News unavailable</div>';
  }
}

function refreshAll() {
  loadSummary(); loadHistory(); loadEvents(); loadScanner(); loadNews();
  $("refresh-info").textContent = "updated " + new Date().toLocaleTimeString();
}

/* ------------------------------------------------------------------- wiring */

let debounce;

$("f-event").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-event]");
  if (!btn) return;
  state.eventType = btn.dataset.event;
  [...$("f-event").children].forEach((b) => b.classList.toggle("active", b === btn));
  state.offset = 0;
  loadEvents();
});

["f-symbol", "f-decision"].forEach((id) =>
  $(id).addEventListener("change", () => { state.offset = 0; loadEvents(); }));

$("f-q").addEventListener("input", () => {
  clearTimeout(debounce);
  debounce = setTimeout(() => { state.offset = 0; loadEvents(); }, 300);
});

$("f-clear").addEventListener("click", () => {
  ["f-symbol", "f-decision", "f-q"].forEach((id) => { $(id).value = ""; });
  state.eventType = "";
  [...$("f-event").children].forEach((b) => b.classList.toggle("active", !b.dataset.event));
  state.offset = 0;
  loadEvents();
});

$("pg-prev").addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - PAGE_SIZE);
  loadEvents();
  $("events").scrollIntoView({ behavior: "smooth", block: "start" });
});

$("pg-next").addEventListener("click", () => {
  state.offset += PAGE_SIZE;
  loadEvents();
  $("events").scrollIntoView({ behavior: "smooth", block: "start" });
});

$("history-period").addEventListener("change", loadHistory);
$("liquidate").addEventListener("click", liquidate);

/* Click a clamped judge reason to expand it in place. */
$("events").addEventListener("click", (e) => {
  const reason = e.target.closest(".verdict__reason");
  if (reason) reason.classList.toggle("clamp");
  if (reason || e.target.closest(".raw summary")) repack();
});

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(packFeed, 150);
});

setInterval(renderBotTimer, 1000);
setInterval(() => { if ($("auto-refresh").checked) refreshAll(); }, 30000);
refreshAll();
