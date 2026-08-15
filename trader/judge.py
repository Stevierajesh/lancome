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
# Prompt
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """You are a disciplined risk manager for an intraday PAPER-trading experiment.
A rule-based system proposed a trade. Your job is to approve or veto it. Be skeptical:
veto when the setup looks weak, choppy, or contradicted by the recent price action.

Proposed trade: {side} {symbol}
Rule that fired: {reason}
Current indicators: {indicators}

Recent 5-minute bars (oldest first, last {n_bars} bars):
{bars}

{news_section}

Discovery context: {discovery}

Portfolio context:
{portfolio}

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
