"""Candidate Registry — resolve and generate candidate items from stored contexts.

Centralizes all LLM-based candidate generation for replay evaluation.
Supported candidate_types in v1: "prompt_version", "model_version".
"""
from __future__ import annotations

import json
import logging

from config import cfg

log = logging.getLogger(__name__)

SUPPORTED_CANDIDATE_TYPES = ("prompt_version", "model_version")


def resolve_candidate(candidate_type: str, candidate_key: str) -> dict:
    """Resolve a candidate_type + key into a generation config.

    Returns: {model, prompt_version, temperature}
    Raises ValueError for unsupported types.
    """
    if candidate_type not in SUPPORTED_CANDIDATE_TYPES:
        raise ValueError(
            f"Unsupported candidate_type '{candidate_type}'. "
            f"Must be one of: {SUPPORTED_CANDIDATE_TYPES}"
        )

    if candidate_type == "model_version":
        return {
            "model": candidate_key,
            "prompt_version": "v1",
            "temperature": 0,
        }
    elif candidate_type == "prompt_version":
        return {
            "model": cfg.AI_MODEL_OPTIMIZER,
            "prompt_version": candidate_key,
            "temperature": 0,
        }
    return {}


def resolve_baseline(candidate_type: str, baseline_key: str | None) -> dict | None:
    """Resolve baseline config. Returns None if no baseline specified."""
    if not baseline_key:
        return None
    return resolve_candidate(candidate_type, baseline_key)


def build_candidate_prompt(context_json: str, candidate_config: dict) -> tuple[str, str]:
    """Build (system_prompt, user_prompt) from stored context and candidate config.

    Returns the same prompt structure the optimizer uses.
    """
    from optimizer_prompts import (
        OPTIMIZER_SYSTEM_PROMPT,
        OPTIMIZER_USER_TEMPLATE,
        format_market_snapshot,
        format_sector_performance,
        format_time_patterns,
        format_rule_performance,
    )

    try:
        context = json.loads(context_json)
    except Exception:
        return OPTIMIZER_SYSTEM_PROMPT, "Error: could not parse stored context"

    rule_perf_text = format_rule_performance(context.get("rule_performance", []))

    user_prompt = OPTIMIZER_USER_TEMPLATE.format(
        lookback_days=context.get("lookback_days", 90),
        trade_count=context.get("trade_count", 0),
        current_regime=context.get("current_regime", "unknown"),
        pnl_summary=json.dumps(context.get("pnl_summary", {})),
        rule_perf_text=rule_perf_text,
        sector_perf_text=format_sector_performance(context.get("sector_performance", [])),
        time_pattern_text=format_time_patterns(context.get("time_patterns", [])),
        score_analysis=json.dumps(context.get("score_analysis", {})),
        bracket_analysis=json.dumps(context.get("bracket_analysis", {})),
        current_params=json.dumps(context.get("current_params", {})),
        market_snapshot_text=format_market_snapshot(context.get("market_snapshot", {})),
    )

    return OPTIMIZER_SYSTEM_PROMPT, user_prompt


async def generate_candidate_items(
    context_json: str,
    candidate_config: dict,
) -> dict | None:
    """Call the LLM with a stored context snapshot using candidate model/prompt config.

    Returns parsed AIDecisionPayload dict, or None on failure.
    """
    api_key = cfg.ANTHROPIC_API_KEY
    if not api_key:
        return None

    model = candidate_config.get("model", cfg.AI_MODEL_OPTIMIZER)
    temperature = candidate_config.get("temperature", 0)

    system_prompt, user_prompt = build_candidate_prompt(context_json, candidate_config)

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)

        msg = await client.messages.create(
            model=model,
            max_tokens=2000,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        text = msg.content[0].text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        decisions = json.loads(text)
        decisions["_input_tokens"] = msg.usage.input_tokens
        decisions["_output_tokens"] = msg.usage.output_tokens
        return decisions

    except Exception as exc:
        log.warning("Candidate generation failed: %s", exc)
        return None
