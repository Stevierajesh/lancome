"""LLM judge: asks the codex CLI (runs on your ChatGPT subscription, no API billing)
to approve or veto a candidate signal. Fails safe: any error = veto."""

import json
import logging
import os
import subprocess
import tempfile

from . import config
from .signals import Signal

log = logging.getLogger("judge")

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "judge_schema.json")

PROMPT_TEMPLATE = """You are a disciplined risk manager for an intraday PAPER-trading experiment.
A rule-based system proposed a trade. Your job is to approve or veto it. Be skeptical:
veto when the setup looks weak, choppy, or contradicted by the recent price action.
Do not browse the web or run commands — answer only from the data below.

Proposed trade: {side} {symbol}
Rule that fired: {reason}
Current indicators: {indicators}

Recent 5-minute bars (oldest first, last {n_bars} bars):
{bars}

Portfolio context:
{portfolio}

Respond with JSON: decision ("approve" or "veto"), confidence (0-1), reason (one sentence)."""


def judge_signal(signal: Signal, bars: list[dict], portfolio: dict) -> dict:
    """Return {"decision", "confidence", "reason"}. Vetoes on any failure."""
    if not config.JUDGE_ENABLED:
        return {"decision": "approve", "confidence": 1.0, "reason": "judge disabled"}

    recent = bars[-40:]
    bar_lines = "\n".join(
        f"{b['t']}  o={b['o']} h={b['h']} l={b['l']} c={b['c']} v={int(b['v'])}" for b in recent
    )
    prompt = PROMPT_TEMPLATE.format(
        side=signal.side.upper(),
        symbol=signal.symbol,
        reason=signal.reason,
        indicators=json.dumps(signal.indicators),
        n_bars=len(recent),
        bars=bar_lines,
        portfolio=json.dumps(portfolio, indent=2),
    )

    out_file = tempfile.NamedTemporaryFile(mode="r", suffix=".json", delete=False)
    try:
        result = subprocess.run(
            [
                "codex", "exec",
                "--sandbox", "read-only",
                "--skip-git-repo-check",
                "--ephemeral",
                "--ignore-user-config",  # user config pins a model retired for ChatGPT accounts
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
            return {"decision": "veto", "confidence": 0.0, "reason": "judge error (codex failed)"}

        verdict = json.loads(out_file.read().strip())
        if verdict.get("decision") not in ("approve", "veto"):
            raise ValueError(f"bad decision: {verdict!r}")
        return verdict
    except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError, OSError) as e:
        log.warning("judge failure: %s", e)
        return {"decision": "veto", "confidence": 0.0, "reason": f"judge error ({type(e).__name__})"}
    finally:
        out_file.close()
        os.unlink(out_file.name)
