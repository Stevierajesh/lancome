/* Formatting primitives shared by every component. */

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/** Decode HTML entities that arrive pre-escaped in Benzinga headlines. */
export function decodeEntities(s) {
  const el = document.createElement("textarea");
  el.innerHTML = String(s ?? "");
  return el.value;
}

/** Price-aware precision: sub-dollar assets (DOGE) keep their significant digits. */
export function smartNum(v) {
  if (v == null || isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a === 0) return "0";
  if (a < 0.01) return v.toPrecision(4).replace(/0+$/, "").replace(/\.$/, "");
  if (a < 1000) return v.toFixed(a < 10 ? 4 : 2).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
  return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export const fmtUsd = (v) => v == null || isNaN(v) ? "—"
  : Math.abs(v) < 1
    ? "$" + smartNum(v)
    : v.toLocaleString("en-US", { style: "currency", currency: "USD" });

/** Fraction (0.0234) → "+2.34%" */
export const fmtPct = (v) => v == null ? "—" : (v >= 0 ? "+" : "") + (v * 100).toFixed(2) + "%";

/** Already-percent value (2.34) → "+2.34%" */
export const fmtPctRaw = (v, digits = 2) =>
  v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(digits) + "%";

export const fmtTime = (ts) => ts ? new Date(ts).toLocaleString() : "—";

export function fmtAgo(ts) {
  if (!ts) return "—";
  const secs = Math.round((Date.now() - new Date(ts).getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return Math.floor(secs / 60) + "m ago";
  if (secs < 86400) return Math.floor(secs / 3600) + "h ago";
  if (secs < 604800) return Math.floor(secs / 86400) + "d ago";
  return new Date(ts).toLocaleDateString();
}

export const cls = (v) => v == null ? "" : v >= 0 ? "pos" : "neg";
