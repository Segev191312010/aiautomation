"""Shared evaluation metric helpers for Stage 10 slice scoring."""
from __future__ import annotations


def empty_slice_metrics() -> dict:
    return {
        "count": 0,
        "scored_count": 0,
        "hit_rate": None,
        "net_pnl": None,
        "expectancy": None,
        "max_drawdown": None,
        "coverage": None,
        "abstain_rate": None,
        "avg_confidence": None,
        "calibration_error": None,
    }


def compute_hit_rate(pnls: list[float]) -> float | None:
    if not pnls:
        return None
    wins = sum(1 for pnl in pnls if pnl > 0)
    return wins / len(pnls)


def compute_net_pnl(pnls: list[float]) -> float | None:
    return sum(pnls) if pnls else None


def compute_expectancy(pnls: list[float], *, min_samples: int = 3) -> float | None:
    if len(pnls) < min_samples:
        return None

    wins = sum(1 for pnl in pnls if pnl > 0)
    avg_win = sum(pnl for pnl in pnls if pnl > 0) / max(wins, 1)
    losses_count = sum(1 for pnl in pnls if pnl <= 0)
    avg_loss = abs(sum(pnl for pnl in pnls if pnl <= 0) / max(losses_count, 1))
    hit_rate = compute_hit_rate(pnls) or 0.0
    return (hit_rate * avg_win) - ((1 - hit_rate) * avg_loss)


def compute_max_drawdown_pct_from_pnls(pnls: list[float]) -> float | None:
    if not pnls:
        return None

    max_dd = 0.0
    cumulative = 0.0
    peak = 0.0
    for pnl in pnls:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        if peak > 0:
            drawdown = ((peak - cumulative) / peak) * 100.0
            if drawdown > max_dd:
                max_dd = drawdown

    return max_dd if max_dd else None


def compute_coverage(total_count: int, scored_count: int) -> float | None:
    return (scored_count / total_count) if total_count else None


def compute_abstain_rate(total_count: int, abstain_count: int) -> float | None:
    return (abstain_count / total_count) if total_count else None


def compute_avg_confidence(items: list[dict]) -> float | None:
    confidences = [float(item["confidence"]) for item in items if item.get("confidence") is not None]
    return (sum(confidences) / len(confidences)) if confidences else None


def compute_calibration_error(scored_items: list[dict], hit_rate: float | None) -> float | None:
    scored_confidences = [
        float(item["confidence"]) for item in scored_items if item.get("confidence") is not None
    ]
    if scored_confidences and hit_rate is not None:
        avg_scored_confidence = sum(scored_confidences) / len(scored_confidences)
        return abs(avg_scored_confidence - hit_rate)
    return None


def bucket_confidence(confidence: float | None) -> str:
    conf = confidence or 0.0
    bucket_idx = min(int(conf * 10), 9)
    lower = bucket_idx / 10
    upper = (bucket_idx + 1) / 10
    return f"{lower:.1f}-{upper:.1f}"


def make_confidence_buckets(items: list[dict]) -> dict[str, list[dict]]:
    """Bucket decision items by confidence into 0.0-0.1, 0.1-0.2, ... 0.9-1.0."""
    buckets: dict[str, list[dict]] = {}
    for item in items:
        key = bucket_confidence(item.get("confidence"))
        buckets.setdefault(key, []).append(item)
    return buckets
