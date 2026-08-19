"""LLM judge with pluggable backends and news-aware prompts.

Supported backends:
  - "ollama"  (default) — local model via Ollama HTTP API, no cost
  - "codex"   — legacy codex CLI subprocess (requires ChatGPT subscription)
  - "none"    — auto-approve everything (for testing)
"""

import json
import logging
import os
import subprocess
import tempfile
import urllib.request
from abc import ABC, abstractmethod

from . import config
from .enrichment import CaseFile

log = logging.getLogger("judge")

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "judge_schema.json")

# ---------------------------------------------------------------------------
# Prompt.. Going to replace with a specialized lighter model instead a general purpose LLM. The prompt is still useful for testing and debugging.
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """You are a trade filter for an intraday PAPER-trading bot. This is a paper account with fake money, so the goal is to learn from real market dynamics, not to avoid every loss.

A rule-based system proposed a trade. Your job is to decide whether it has a plausible short-term edge over the next few hours. You are NOT a risk manager; hard risk limits (stop-loss, position sizing, daily loss cap) already handle downside protection.

Approve trades that have an identifiable edge, even if the setup is early or imperfect. Veto or lower confidence when the edge is too weak:
- Price is in freefall with accelerating selling, not merely below SMA or oversold
- The signal directly contradicts strong opposing momentum or the hourly trend without clear reversal evidence
- Liquidity is thin, the spread is wide for the expected move, or the trade would need a perfect fill to work
- Recent bars are only drifting or choppy with no volume expansion or directional follow-through
- The same symbol recently flipped direction without a material new setup
- News explicitly contradicts this specific position

Being oversold or below a moving average is NOT sufficient reason to veto a buy — those are exactly the conditions that produce entries. A trend reversal doesn't need to be "confirmed" to be worth a paper trade.

When the evidence is mixed, approve only if the trade still has a concrete catalyst, liquidity, and price action that can plausibly overcome spread and noise. Do not approve merely because no single fatal flaw is present.

Proposed trade: {side} {symbol}
Rule that fired: {reason}
Current indicators: {indicators}

Recent 5-minute bars (oldest first, last {n_bars} bars):
{bars}

{news_section}

Discovery context: {discovery}

Portfolio context:
{portfolio}

Run context:
{run_context}

Use the run context for calibration: the date and target equity explain the
paper-trading objective, but they are not permission to force marginal trades.
Never approve solely because a target exists; approve only when the proposed
setup has a concrete edge under the listed risk limits.

Respond with JSON only: {{"decision": "approve" or "veto", "confidence": 0-1, "reason": "one sentence"}}"""


def _build_prompt(case: CaseFile) -> str:
    recent = case.bars[-40:]
    bar_lines = "\n".join(
        f"{b['t']}  o={b['o']} h={b['h']} l={b['l']} c={b['c']} v={int(b['v'])}"
        for b in recent
    )
    if case.news:
        headlines = "\n".join(
            f"  {n['created_at']}  {n['headline']}" for n in case.news
        )
        news_section = f"Recent news for {case.symbol}:\n{headlines}"
    else:
        news_section = f"No recent news for {case.symbol}."

    discovery = "N/A"
    if case.scanner_context:
        parts = []
        if case.scanner_context.get("source"):
            parts.append(f"source={case.scanner_context['source']}")
        if case.scanner_context.get("score"):
            parts.append(f"score={case.scanner_context['score']}")
        meta = case.scanner_context.get("scanner_meta", {})
        if meta.get("pct_change"):
            parts.append(f"move={meta['pct_change']:.2f}%")
        if meta.get("volume"):
            parts.append(f"volume={meta['volume']}")
        discovery = ", ".join(parts) if parts else "watchlist"

    return PROMPT_TEMPLATE.format(
        side=case.signal.side.upper() if case.signal else "EVALUATE",
        symbol=case.symbol,
        reason=case.signal.reason if case.signal else "news/scanner trigger",
        indicators=json.dumps(case.signal.indicators if case.signal else {}),
        n_bars=len(recent),
        bars=bar_lines,
        news_section=news_section,
        discovery=discovery,
        portfolio=json.dumps(case.portfolio, indent=2),
        run_context=json.dumps(case.run_context, indent=2),
    )


VETO = {"decision": "veto", "confidence": 0.0, "reason": "judge error"}

# ---------------------------------------------------------------------------
# Base + implementations
# ---------------------------------------------------------------------------


class JudgeBase(ABC):
    @abstractmethod
    def evaluate(self, case: CaseFile) -> dict:
        """Return {"decision": "approve"|"veto", "confidence": float, "reason": str}"""


class OllamaJudge(JudgeBase):
    """Local Ollama model via HTTP. Zero cost."""

    def __init__(self, model: str = "", url: str = "", timeout: int = 0):
        self.model = model or config.OLLAMA_MODEL
        self.url = (url or config.OLLAMA_URL).rstrip("/")
        self.timeout = timeout or config.JUDGE_TIMEOUT_SECONDS

    def evaluate(self, case: CaseFile) -> dict:
        prompt = _build_prompt(case)
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
        }).encode()

        req = urllib.request.Request(
            f"{self.url}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
            content = data["message"]["content"]
            verdict = json.loads(content)
            if verdict.get("decision") not in ("approve", "veto"):
                raise ValueError(f"bad decision: {verdict!r}")
            verdict.setdefault("confidence", 0.5)
            verdict.setdefault("reason", "")
            return verdict
        except Exception as e:
            log.warning("ollama judge failed: %s", e)
            return {**VETO, "reason": f"judge error ({type(e).__name__})"}


class CodexJudge(JudgeBase):
    """Legacy: shells out to codex CLI. Requires local codex install + ChatGPT sub."""

    def evaluate(self, case: CaseFile) -> dict:
        prompt = _build_prompt(case)
        out_file = tempfile.NamedTemporaryFile(mode="r", suffix=".json", delete=False)
        try:
            result = subprocess.run(
                [
                    "codex", "exec",
                    "--sandbox", "read-only",
                    "--skip-git-repo-check",
                    "--ephemeral",
                    "--ignore-user-config",
                    "--color", "never",
                    "--output-schema", SCHEMA_PATH,
                    "--output-last-message", out_file.name,
                    prompt,
                ],
                capture_output=True,
                text=True,
                timeout=config.JUDGE_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                log.warning("codex exec failed (rc=%s): %s", result.returncode, result.stderr[-500:])
                return {**VETO, "reason": "judge error (codex failed)"}
            verdict = json.loads(out_file.read().strip())
            if verdict.get("decision") not in ("approve", "veto"):
                raise ValueError(f"bad decision: {verdict!r}")
            return verdict
        except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError, OSError) as e:
            log.warning("codex judge failure: %s", e)
            return {**VETO, "reason": f"judge error ({type(e).__name__})"}
        finally:
            out_file.close()
            os.unlink(out_file.name)


class NoOpJudge(JudgeBase):
    """Auto-approve everything. For testing without an LLM."""

    def evaluate(self, case: CaseFile) -> dict:
        return {"decision": "approve", "confidence": 1.0, "reason": "judge disabled"}


def create_judge(backend: str = "") -> JudgeBase:
    backend = backend or config.JUDGE_BACKEND
    if backend == "ollama":
        return OllamaJudge()
    if backend == "codex":
        return CodexJudge()
    if backend == "none":
        return NoOpJudge()
    raise ValueError(f"unknown judge backend: {backend!r}")
