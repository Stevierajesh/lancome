/* Pure render functions: data in, HTML string out. No fetching, no DOM lookups. */

import {
  esc, decodeEntities, fmtUsd, fmtPct, fmtPctRaw, fmtTime, fmtAgo, cls, smartNum,
} from "./format.js";
import { renderChips } from "./indicators.js";

/* ------------------------------------------------------------- stat cards */

export function statCard(label, value, klass = "") {
  return `<div class="card">
    <div class="label">${esc(label)}</div>
    <div class="value ${klass}">${value}</div>
  </div>`;
}

export function statGroup(label, cards) {
  return `<div class="statgroup">
    <div class="statgroup__label">${esc(label)}</div>
    <div class="cards">${cards.join("")}</div>
  </div>`;
}

/* ------------------------------------------------------------ event cards */

const badge = (kind, text) =>
  `<span class="badge badge--${esc(kind)}">${esc(text ?? kind)}</span>`;

function verdictBlock(v) {
  if (!v) return "";
  const pct = Math.round((v.confidence ?? 0) * 100);
  return `<div class="verdict verdict--${esc(v.decision)}">
    <div class="verdict__head">
      ${badge(v.decision)}
      <span class="conf"><span class="conf__bar" style="width:${pct}%"></span></span>
      <span class="conf__num mono">${pct}%</span>
    </div>
    ${v.reason ? `<p class="verdict__reason clamp" title="click to expand">${esc(v.reason)}</p>` : ""}
  </div>`;
}

/** Per-event-type body. Returns { rule, note, chips }. */
function eventBody(e) {
  switch (e.event) {
    case "signal": {
      return {
        rule: esc(e.rule),
        chips: renderChips(e.indicators),
        note: e.trigger ? `triggered by ${esc(e.trigger)}` : "",
      };
    }
    case "entry": {
      const size = e.qty != null ? `${smartNum(e.qty)} shares`
        : e.notional != null ? fmtUsd(e.notional) : "";
      return {
        rule: `Bought ${esc(size)}${e.price != null ? ` @ ${fmtUsd(e.price)}` : ""}`,
        note: esc(e.rule),
        chips: "",
      };
    }
    case "exit": {
      const pl = e.position?.unrealized_plpc;
      const tag = pl != null ? ` <span class="${cls(pl)}">${fmtPct(pl)}</span>` : "";
      return {
        rule: `Closed position${tag}`,
        note: esc(e.reason),
        chips: "",
      };
    }
    case "news": {
      return {
        rule: esc(decodeEntities(e.headline)),
        note: e.source ? `via ${esc(e.source)}` : "",
        chips: "",
      };
    }
    case "liquidate": {
      const symbols = Object.keys(e.positions || {});
      return {
        rule: `Sold all — ${e.count} position${e.count === 1 ? "" : "s"} closed`,
        note: [symbols.join(", "), e.failed?.length ? `failed: ${e.failed.join(", ")}` : ""]
          .filter(Boolean).map(esc).join(" · "),
        chips: "",
      };
    }
    case "scanner": {
      const meta = e.scanner_meta || {};
      const bits = {};
      if (meta.pct_change != null) bits.move = meta.pct_change;
      if (meta.price != null) bits.price = meta.price;
      if (meta.volume != null) bits.volume = meta.volume;
      const chips = Object.keys(bits).length ? renderChips({
        ...(bits.price != null ? { price: bits.price } : {}),
        ...(bits.move != null ? { gap_pct: bits.move } : {}),
      }) : "";
      return {
        rule: `Discovered by scanner${e.source ? ` (${esc(e.source)})` : ""}`,
        note: meta.volume != null ? `volume ${Number(meta.volume).toLocaleString()}` : "",
        chips,
      };
    }
    default:
      return { rule: esc(e.event), note: "", chips: "" };
  }
}

export function eventCard(e) {
  const { rule, note, chips } = eventBody(e);
  const side = e.side
    ? `<span class="side side--${esc(e.side)}">${esc(e.side)}</span>` : "";
  return `<article class="ev ev--${esc(e.event)}">
    <header class="ev__head">
      ${badge(e.event)}
      ${e.symbol ? `<span class="ev__sym">${esc(e.symbol)}</span>` : ""}
      ${side}
      <time class="ev__time" title="${esc(fmtTime(e.ts))}">${esc(fmtAgo(e.ts))}</time>
    </header>
    ${rule ? `<p class="ev__rule">${rule}</p>` : ""}
    ${note ? `<p class="ev__note">${note}</p>` : ""}
    ${chips}
    ${verdictBlock(e.verdict)}
    <details class="raw"><summary>raw json</summary><pre>${esc(JSON.stringify(e, null, 2))}</pre></details>
  </article>`;
}

/* --------------------------------------------------------------- positions */

export function positionsTable(positions) {
  const rows = Object.entries(positions || {});
  if (!rows.length) return '<div class="empty">No open positions</div>';
  return `<div class="tablewrap"><table>
    <thead><tr><th>Sym</th><th>Qty</th><th>Entry</th><th>Now</th><th>P/L</th></tr></thead>
    <tbody>${rows.map(([sym, p]) => `<tr>
      <td class="mono">${esc(sym)}</td>
      <td class="mono">${smartNum(p.qty)}</td>
      <td class="mono">${fmtUsd(p.avg_entry_price)}</td>
      <td class="mono">${fmtUsd(p.current_price)}</td>
      <td class="mono ${cls(p.unrealized_plpc)}">${fmtPct(p.unrealized_plpc)}</td>
    </tr>`).join("")}</tbody>
  </table></div>`;
}

/* --------------------------------------------------------------- watchlist */

export function watchlistChips(entries) {
  if (!entries?.length) return '<div class="empty">Scanner idle</div>';
  return `<div class="wl">${entries.map((w) => {
    const meta = w.scanner_meta || {};
    const bits = [];
    if (meta.pct_change != null) bits.push(fmtPctRaw(meta.pct_change, 1));
    if (w.score != null) bits.push(`★${w.score}`);
    return `<span class="wlchip wlchip--${esc(w.source)}" title="${esc(w.source)}${
      meta.volume ? ` · vol ${Number(meta.volume).toLocaleString()}` : ""}">
      <span class="wlchip__sym">${esc(w.symbol)}</span>
      ${bits.length ? `<span class="wlchip__meta">${esc(bits.join(" "))}</span>` : ""}
    </span>`;
  }).join("")}</div>`;
}

/* -------------------------------------------------------------------- news */

export function newsItem(n) {
  return `<div class="newsitem">
    <div class="newsitem__head">
      <span class="newsitem__sym">${esc(n.symbol)}</span>
      ${n.source ? `<span class="wlchip__meta">${esc(n.source)}</span>` : ""}
      <time class="newsitem__time" title="${esc(fmtTime(n.created_at || n.ts))}">${esc(fmtAgo(n.created_at || n.ts))}</time>
    </div>
    <p class="newsitem__headline">${esc(decodeEntities(n.headline))}</p>
  </div>`;
}
