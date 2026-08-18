/* Indicator display spec — turns the raw `indicators` blob into labelled chips.
   Keys mirror trader/signals.py::evaluate(). Unknown keys still render, so new
   signals show up without touching this file. */

import { esc, smartNum, fmtPctRaw } from "./format.js";

const signTone = (v) => v > 0 ? "good" : v < 0 ? "bad" : "";

const SPEC = {
  price: {
    label: "price",
    fmt: (v) => "$" + smartNum(v),
  },
  volume_ratio: {
    label: "vol",
    fmt: (v) => v.toFixed(1) + "×",
    tone: (v) => v === 0 ? "bad" : v >= 2.5 ? "hot" : "",
  },
  gap_pct: {
    label: "gap",
    fmt: (v) => fmtPctRaw(v),
    tone: signTone,
  },
  momentum_pct: {
    label: "mom",
    fmt: (v) => fmtPctRaw(v),
    tone: signTone,
  },
  rsi: {
    label: "rsi",
    fmt: (v) => v.toFixed(1),
    tone: (v) => v >= 75 ? "hot" : v <= 25 ? "cool" : "",
  },
  vwap_deviation_pct: {
    label: "vwap",
    fmt: (v) => fmtPctRaw(v, 1),
    tone: signTone,
  },
  relative_volume_tod: {
    label: "rvol",
    fmt: (v) => v.toFixed(1) + "×",
    tone: (v) => v >= 3 ? "hot" : "",
  },
  bid_ask_imbalance: {
    label: "bid",
    fmt: (v) => (v * 100).toFixed(0) + "%",
    tone: (v) => v >= 0.7 ? "good" : v <= 0.3 ? "bad" : "",
  },
  correlation_break_pct: {
    label: "vs spy",
    fmt: (v) => fmtPctRaw(v, 1),
    tone: signTone,
  },
  hourly_trend: {
    label: "1h",
    fmt: (v) => v,
    tone: (v) => v === "bullish" ? "good" : v === "bearish" ? "bad" : "",
  },
  spread_pct: {
    label: "spread",
    fmt: (v) => v.toFixed(2) + "%",
    tone: (v) => v > 0.3 ? "bad" : "",
  },
  sma_fast: { label: "sma·9", fmt: (v) => smartNum(v) },
  sma_slow: { label: "sma·21", fmt: (v) => smartNum(v) },
};

/* Most decision-relevant first; anything unlisted is appended alphabetically. */
const ORDER = [
  "price", "momentum_pct", "volume_ratio", "rvol", "relative_volume_tod", "rsi",
  "gap_pct", "vwap_deviation_pct", "bid_ask_imbalance", "correlation_break_pct",
  "hourly_trend", "spread_pct", "sma_fast", "sma_slow",
];

function chip(key, value) {
  const spec = SPEC[key];
  let label = key.replace(/_/g, " ");
  let text;
  let tone = "";
  if (spec) {
    label = spec.label;
    try {
      text = spec.fmt(value);
    } catch {
      text = String(value);
    }
    tone = spec.tone ? spec.tone(value) : "";
  } else {
    text = typeof value === "number" ? smartNum(value) : String(value);
  }
  return `<span class="chip${tone ? " chip--" + tone : ""}">` +
         `<i>${esc(label)}</i><b>${esc(text)}</b></span>`;
}

export function renderChips(indicators) {
  if (!indicators || typeof indicators !== "object") return "";
  const keys = Object.keys(indicators);
  const ordered = [
    ...ORDER.filter((k) => k in indicators),
    ...keys.filter((k) => !ORDER.includes(k)).sort(),
  ];
  const html = ordered
    .filter((k) => indicators[k] !== null && indicators[k] !== undefined)
    .map((k) => chip(k, indicators[k]))
    .join("");
  return html ? `<div class="chips">${html}</div>` : "";
}
