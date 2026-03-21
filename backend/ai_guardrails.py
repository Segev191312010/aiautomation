"""
AI Guardrails — uncertainty-aware enforcement for all AI-initiated changes.

Every AI change goes through GuardrailEnforcer.execute_with_audit().
Confidence-scaled limits: max_allowed = base_limit * confidence.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from database import get_db
from api_contracts import GuardrailConfigResponse

log = logging.getLogger(__name__)


# ── Database helpers ─────────────────────────────────────────────────────────

async def _load_guardrails_from_db() -> GuardrailConfigResponse:
    """Load guardrails config from DB. Returns defaults if not found."""
    try:
        async with get_db() as db:
            cur = await db.execute(
                "SELECT data FROM ai_guardrails WHERE id = 'default'"
            )
            row = await cur.fetchone()
            if row:
                return GuardrailConfigResponse(**json.loads(row[0]))
    except Exception as e:
        log.warning("Failed to load guardrails from DB: %s", e)
    return GuardrailConfigResponse()


async def save_guardrails_to_db(config: GuardrailConfigResponse) -> None:
    """Upsert guardrails config to DB."""
    now = datetime.now(timezone.utc).isoformat()
    data = config.model_dump_json()
    try:
        async with get_db() as db:
            await db.execute(
                "INSERT OR REPLACE INTO ai_guardrails (id, user_id, data, updated_at) "
                "VALUES ('default', 'demo', ?, ?)",
                (data, now),
            )
            await db.commit()
    except Exception as e:
        log.error("Failed to save guardrails to DB: %s", e)
        raise


async def log_ai_action(
    action_type: str,
    category: str,
    description: str,
    old_value: object = None,
    new_value: object = None,
    reason: str = "",
    confidence: float = 0.5,
    status: str = "applied",
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    param_type: str | None = None,
) -> int:
    """Insert an entry into ai_audit_log. Returns the new row ID."""
    now = datetime.now(timezone.utc).isoformat()
    old_json = json.dumps(old_value) if old_value is not None else None
    new_json = json.dumps(new_value) if new_value is not None else None
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO ai_audit_log "
            "(timestamp, action_type, category, description, old_value, new_value, "
            " reason, confidence, status, input_tokens, output_tokens, param_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now, action_type, category, description, old_json, new_json,
             reason, confidence, status, input_tokens, output_tokens, param_type),
        )
        await db.commit()
        return cur.lastrowid or 0


async def get_ai_audit_log(limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """Return paginated audit log entries (newest first) and total count."""
    async with get_db() as db:
        cur = await db.execute("SELECT COUNT(*) FROM ai_audit_log")
        total = (await cur.fetchone())[0]
        cur = await db.execute(
            "SELECT id, timestamp, action_type, category, description, "
            "old_value, new_value, reason, confidence, "
            "decision_confidence_avg, parameter_uncertainty_width, "
            "input_tokens, output_tokens, status, reverted_at "
            "FROM ai_audit_log ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = await cur.fetchall()
    entries = []
    for r in rows:
        entries.append({
            "id": r[0], "timestamp": r[1], "action_type": r[2],
            "category": r[3], "description": r[4],
            "old_value": r[5], "new_value": r[6],
            "reason": r[7], "confidence": r[8],
            "decision_confidence_avg": r[9],
            "parameter_uncertainty_width": r[10],
            "input_tokens": r[11], "output_tokens": r[12],
            "status": r[13], "reverted_at": r[14],
        })
    return entries, total


async def save_param_snapshot(
    param_type: str, symbol: str | None, data: object, source: str = "ai",
) -> None:
    """Save a parameter snapshot for rollback."""
    now = datetime.now(timezone.utc).isoformat()
    data_json = json.dumps(data) if not isinstance(data, str) else data
    async with get_db() as db:
        await db.execute(
            "INSERT INTO ai_parameter_snapshots "
            "(timestamp, param_type, symbol, data, source) VALUES (?, ?, ?, ?, ?)",
            (now, param_type, symbol, data_json, source),
        )
        await db.commit()


async def log_shadow_decision(
    param_type: str,
    symbol: str | None,
    ai_suggested: object,
    actual_used: object,
    market_condition: str | None = None,
    delta_value: float | None = None,
    confidence: float | None = None,
    regime: str | None = None,
) -> None:
    """Log a shadow-mode decision (AI suggested but not applied)."""
    now = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        await db.execute(
            "INSERT INTO ai_shadow_decisions "
            "(timestamp, param_type, symbol, ai_suggested_value, "
            "actual_value_used, market_condition, delta_value, confidence, regime) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now, param_type, symbol,
             json.dumps(ai_suggested), json.dumps(actual_used),
             market_condition, delta_value, confidence, regime),
        )
        await db.commit()


async def get_shadow_decisions(
    limit: int = 50,
    offset: int = 0,
    param_type: str | None = None,
    symbol: str | None = None,
    regime: str | None = None,
    min_confidence: float | None = None,
) -> tuple[list[dict], int]:
    """Query shadow decisions with filters, paginated, newest first."""
    async with get_db() as db:
        where_clauses = []
        params: list = []
        if param_type:
            where_clauses.append("param_type = ?")
            params.append(param_type)
        if symbol:
            where_clauses.append("symbol = ?")
            params.append(symbol)
        if regime:
            where_clauses.append("regime = ?")
            params.append(regime)
        if min_confidence is not None:
            where_clauses.append("confidence >= ?")
            params.append(min_confidence)

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        cur = await db.execute(f"SELECT COUNT(*) FROM ai_shadow_decisions{where_sql}", params)
        total = (await cur.fetchone())[0]

        cur = await db.execute(
            f"SELECT id, timestamp, param_type, symbol, ai_suggested_value, "
            f"actual_value_used, market_condition, hypothetical_outcome, "
            f"delta_value, confidence, regime "
            f"FROM ai_shadow_decisions{where_sql} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        rows = await cur.fetchall()

    entries = []
    for r in rows:
        entries.append({
            "id": r[0], "timestamp": r[1], "param_type": r[2],
            "symbol": r[3], "ai_suggested_value": r[4],
            "actual_value_used": r[5], "market_condition": r[6],
            "hypothetical_outcome": r[7], "delta_value": r[8],
            "confidence": r[9], "regime": r[10],
        })
    return entries, total


async def analyze_shadow_performance(
    min_trades_per_window: int = 20,
) -> dict:
    """Compute shadow mode effectiveness using trade-count windows."""
    import bisect
    from database import get_db as _get_db

    async with _get_db() as db:
        # Get all shadow decisions
        cur = await db.execute(
            "SELECT id, timestamp, param_type, symbol, ai_suggested_value, "
            "actual_value_used, delta_value, confidence, regime "
            "FROM ai_shadow_decisions ORDER BY timestamp ASC"
        )
        decisions = await cur.fetchall()

        if not decisions:
            return {
                "total_decisions": 0, "decisions_with_data": 0,
                "overall_hit_rate": None, "overall_effect_size_avg": None,
                "active_days": 0, "regimes_covered": {},
                "by_param_type": {}, "gating_conditions": [],
                "ready_for_live": False, "ready_reasons": [],
            }

        # B2 FIX: Bounded trade query — only load trades around the decision range
        earliest_ts = decisions[0][1]
        cur = await db.execute(
            "SELECT id, symbol, timestamp, data FROM trades "
            "WHERE timestamp < ? ORDER BY timestamp DESC LIMIT ?",
            (earliest_ts, min_trades_per_window),
        )
        pre_trades = list(reversed(await cur.fetchall()))
        latest_ts = decisions[-1][1]  # last decision timestamp
        cur = await db.execute(
            "SELECT id, symbol, timestamp, data FROM trades "
            "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC "
            "LIMIT ?",
            (earliest_ts, latest_ts, len(decisions) * min_trades_per_window + min_trades_per_window),
        )
        post_trades = await cur.fetchall()
        all_trades_raw = pre_trades + post_trades

    # B6 FIX: Parse trades with correct PnL extraction
    trades = []
    for t in all_trades_raw:
        try:
            tdata = json.loads(t[3]) if t[3] else {}
            raw_pnl = tdata.get("pnl")
            pnl = float(raw_pnl) if raw_pnl is not None else 0.0
            trades.append({
                "id": t[0], "symbol": t[1], "timestamp": t[2],
                "pnl": pnl,
                "rule_id": tdata.get("rule_id", ""),
            })
        except Exception:
            continue

    # Pre-compute sorted trade timestamps for bisect (B17 FIX)
    trade_timestamps = [t["timestamp"] for t in trades]

    # Evaluate each decision
    hits = 0
    misses = 0
    effect_sizes = []
    by_param: dict[str, dict] = {}
    by_regime: dict[str, dict] = {}
    unique_days: set[str] = set()

    for d in decisions:
        d_id, d_ts, d_param, d_symbol, d_suggested, d_actual, d_delta, d_conf, d_regime = d

        # B21 FIX: Skip decisions with NULL timestamp
        if not d_ts:
            log.warning("Shadow decision id=%d has NULL timestamp, skipping", d_id)
            continue
        unique_days.add(d_ts[:10])

        # Initialize param bucket
        if d_param not in by_param:
            by_param[d_param] = {"count": 0, "hits": 0, "effect_sizes": [], "confidences": []}
        by_param[d_param]["count"] += 1
        if d_conf:
            by_param[d_param]["confidences"].append(d_conf)

        # Initialize regime bucket
        if d_regime and d_regime not in by_regime:
            by_regime[d_regime] = {"decisions": 0, "hits": 0}
        if d_regime:
            by_regime[d_regime]["decisions"] += 1

        # B17 FIX: Use bisect for index-based window slicing (handles same-timestamp)
        idx = bisect.bisect_left(trade_timestamps, d_ts)
        trades_before = trades[max(0, idx - min_trades_per_window):idx]
        trades_after = trades[idx:idx + min_trades_per_window]

        if len(trades_before) < min_trades_per_window or len(trades_after) < min_trades_per_window:
            continue  # insufficient data

        pnl_pre = sum(t["pnl"] for t in trades_before)
        pnl_post = sum(t["pnl"] for t in trades_after)
        effect = (pnl_post - pnl_pre) / max(abs(pnl_pre), 1.0)

        # Conservative change = delta > 0 (tightening)
        is_conservative = (d_delta or 0) > 0
        if is_conservative:
            hit = pnl_post >= pnl_pre
        else:
            hit = pnl_post > pnl_pre * 1.05

        if hit:
            hits += 1
            by_param[d_param]["hits"] += 1
            if d_regime and d_regime in by_regime:
                by_regime[d_regime]["hits"] += 1
        else:
            misses += 1

        effect_sizes.append(effect)
        by_param[d_param]["effect_sizes"].append(effect)

    total = len(decisions)
    scored = hits + misses
    active_days = len(unique_days)

    # B7 FIX: Removed dead pt_scored variable
    param_metrics = {}
    for pt, pdata in by_param.items():
        param_metrics[pt] = {
            "count": pdata["count"],
            "hit_rate": pdata["hits"] / max(1, len(pdata["effect_sizes"])) if pdata["effect_sizes"] else None,
            "effect_size_avg": sum(pdata["effect_sizes"]) / len(pdata["effect_sizes"]) if pdata["effect_sizes"] else None,
            "avg_confidence": sum(pdata["confidences"]) / len(pdata["confidences"]) if pdata["confidences"] else None,
        }

    # Regime coverage
    regime_metrics = {}
    for regime, rdata in by_regime.items():
        regime_metrics[regime] = {
            "decisions": rdata["decisions"],
            "hit_rate": rdata["hits"] / max(1, rdata["decisions"]),
        }

    overall_hit_rate = hits / scored if scored > 0 else None
    overall_effect = sum(effect_sizes) / len(effect_sizes) if effect_sizes else None

    # B5 FIX: Read gating thresholds from DB guardrails, not env vars
    guardrails = await _load_guardrails_from_db()
    gating = [
        {"name": "min_decisions", "met": scored >= guardrails.shadow_to_live_min_decisions,
         "actual": float(scored), "required": float(guardrails.shadow_to_live_min_decisions)},
        {"name": "min_days", "met": active_days >= guardrails.shadow_to_live_min_days,
         "actual": float(active_days), "required": float(guardrails.shadow_to_live_min_days)},
        {"name": "hit_rate", "met": (overall_hit_rate or 0) >= guardrails.shadow_to_live_hit_rate_threshold,
         "actual": float(overall_hit_rate or 0), "required": guardrails.shadow_to_live_hit_rate_threshold},
        {"name": "effect_size", "met": (overall_effect or 0) >= guardrails.shadow_to_live_effect_size_threshold,
         "actual": float(overall_effect or 0), "required": guardrails.shadow_to_live_effect_size_threshold},
    ]
    ready = all(g["met"] for g in gating)
    reasons = [f"{g['name']}: {g['actual']:.2f} {'≥' if g['met'] else '<'} {g['required']:.2f}" for g in gating]

    return {
        "total_decisions": total,
        "decisions_with_data": scored,
        "overall_hit_rate": overall_hit_rate,
        "overall_effect_size_avg": overall_effect,
        "active_days": active_days,
        "regimes_covered": regime_metrics,
        "by_param_type": param_metrics,
        "gating_conditions": gating,
        "ready_for_live": ready,
        "ready_reasons": reasons,
    }


# ── Guardrail Enforcer ───────────────────────────────────────────────────────

class GuardrailEnforcer:
    """Wraps every AI-initiated change with guardrail checks.

    Confidence-scaled limits: max_allowed = base_limit * confidence.
    Conservative actions (disable rule): use upper bound of estimate.
    Aggressive actions (increase size): use lower bound of estimate.
    """

    async def load_config(self) -> GuardrailConfigResponse:
        return await _load_guardrails_from_db()

    async def save_config(self, config: GuardrailConfigResponse) -> None:
        await save_guardrails_to_db(config)

    async def can_execute(
        self,
        action_type: str,
        proposed_change: dict,
        confidence: float = 0.5,
    ) -> tuple[bool, str]:
        """Check if proposed change is within guardrails."""
        config = await self.load_config()

        if config.emergency_stop:
            return False, "Emergency stop is active"
        if not config.ai_autonomy_enabled:
            return False, "AI autonomy is not enabled"

        today_count = await self._count_today_changes()
        if today_count >= config.max_changes_per_day:
            return False, f"Daily budget exhausted ({today_count}/{config.max_changes_per_day})"

        last = await self._last_change_at()
        if last:
            elapsed_h = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            if elapsed_h < config.min_hours_between_changes:
                return False, f"Cooldown active ({elapsed_h:.1f}h < {config.min_hours_between_changes}h)"

        return self._check_action_limits(action_type, proposed_change, confidence, config)

    def _check_action_limits(
        self,
        action_type: str,
        change: dict,
        confidence: float,
        config: GuardrailConfigResponse,
    ) -> tuple[bool, str]:
        """Check parameter-specific limits, scaled by confidence."""
        conf = max(0.1, min(1.0, confidence))

        if action_type == "rule_disable":
            if change.get("disabled_today", 0) >= config.max_rules_disabled_per_day:
                return False, f"Max rule disables/day reached ({config.max_rules_disabled_per_day})"

        elif action_type == "rule_enable":
            if change.get("enabled_today", 0) >= config.max_rules_enabled_per_day:
                return False, f"Max rule enables/day reached ({config.max_rules_enabled_per_day})"

        elif action_type == "weight_change":
            delta = abs(change.get("delta_pct", 0))
            limit = config.max_weight_change_pct * conf
            if delta > limit:
                return False, f"Weight change {delta:.1f}% exceeds limit {limit:.1f}%"

        elif action_type == "exit_param_change":
            delta = abs(change.get("delta", 0))
            limit = config.max_atr_mult_change * conf
            if delta > limit:
                return False, f"ATR mult change {delta:.2f} exceeds limit {limit:.2f}"

        elif action_type == "score_threshold":
            new_score = change.get("new_score", 50)
            if new_score < config.min_score_floor:
                return False, f"Score {new_score} below floor {config.min_score_floor}"
            if new_score > config.min_score_ceiling:
                return False, f"Score {new_score} above ceiling {config.min_score_ceiling}"

        elif action_type == "risk_adjust":
            increase_pct = change.get("increase_pct", 0)
            limit = config.max_position_size_increase_pct * conf
            if increase_pct > limit:
                return False, f"Size increase {increase_pct:.1f}% exceeds limit {limit:.1f}%"

        elif action_type in ("rule_boost", "rule_reduce"):
            # Sizing multiplier must stay within bounds (hard-clamped in ai_params too)
            new_sizing = change.get("new", 1.0)
            if isinstance(new_sizing, (int, float)):
                if new_sizing > 3.0:
                    return False, f"Sizing multiplier {new_sizing:.2f} exceeds max 3.0x"
                if new_sizing < 0.1:
                    return False, f"Sizing multiplier {new_sizing:.2f} below min 0.1x"

        return True, "OK"

    async def execute_with_audit(
        self,
        action_type: str,
        category: str,
        description: str,
        old_value: object,
        new_value: object,
        reason: str,
        confidence: float,
        apply_fn,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> dict:
        """Execute a change within guardrails, logging to audit table."""
        proposed: dict = {"old": old_value, "new": new_value}
        if action_type in ("rule_disable", "rule_enable"):
            # Count how many of this type have already been applied today
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            async with get_db() as db:
                cur = await db.execute(
                    "SELECT COUNT(*) FROM ai_audit_log "
                    "WHERE status = 'applied' AND action_type = ? AND timestamp >= ?",
                    (action_type, today),
                )
                count = (await cur.fetchone())[0]
            key = "disabled_today" if action_type == "rule_disable" else "enabled_today"
            proposed[key] = count
        elif action_type == "weight_change":
            if isinstance(old_value, (int, float)) and isinstance(new_value, (int, float)):
                proposed["delta_pct"] = abs(new_value - old_value)
        elif action_type == "exit_param_change":
            if isinstance(old_value, (int, float)) and isinstance(new_value, (int, float)):
                proposed["delta"] = abs(new_value - old_value)
        elif action_type == "risk_adjust" and isinstance(new_value, dict):
            proposed["increase_pct"] = new_value.get("increase_pct", 0)
        elif action_type == "score_threshold" and isinstance(new_value, (int, float)):
            proposed["new_score"] = new_value

        allowed, block_reason = await self.can_execute(action_type, proposed, confidence)

        if not allowed:
            entry_id = await log_ai_action(
                action_type=action_type, category=category,
                description=description, old_value=old_value,
                new_value=new_value, reason=f"BLOCKED: {block_reason}",
                confidence=confidence, status="blocked",
                input_tokens=input_tokens, output_tokens=output_tokens,
            )
            log.info("AI action BLOCKED: %s — %s", description, block_reason)
            return {"applied": False, "reason": block_reason, "entry_id": entry_id}

        # Snapshot before change
        await save_param_snapshot(action_type, None, old_value, "ai_pre_change")

        # Apply
        try:
            await apply_fn()
        except Exception as e:
            log.error("AI action FAILED: %s — %s", description, e)
            await log_ai_action(
                action_type=action_type, category=category,
                description=f"FAILED: {description}",
                old_value=old_value, new_value=new_value,
                reason=str(e), confidence=confidence, status="failed",
            )
            return {"applied": False, "reason": str(e)}

        entry_id = await log_ai_action(
            action_type=action_type, category=category,
            description=description, old_value=old_value,
            new_value=new_value, reason=reason,
            confidence=confidence, status="applied",
            input_tokens=input_tokens, output_tokens=output_tokens,
        )
        log.info("AI action APPLIED: %s (confidence=%.2f)", description, confidence)
        return {"applied": True, "entry_id": entry_id, "description": description}

    async def revert_action(self, entry_id: int) -> dict:
        """Revert a specific AI-initiated change by ID.

        Marks the audit log entry as 'reverted' and logs a new 'revert' entry
        that records the restoration of the old value. Full parameter restoration
        requires the Phase 3 AIParameterStore (which reads snapshots on startup).
        For rule enable/disable, the revert is applied immediately via the DB.
        """
        async with get_db() as db:
            cur = await db.execute(
                "SELECT id, action_type, old_value, new_value, status, reverted_at "
                "FROM ai_audit_log WHERE id = ?",
                (entry_id,),
            )
            row = await cur.fetchone()
            if not row:
                return {"reverted": False, "reason": f"Entry {entry_id} not found"}
            if row[4] == "reverted":
                return {"reverted": False, "reason": "Already reverted"}
            if row[4] != "applied":
                return {"reverted": False, "reason": f"Cannot revert status '{row[4]}'"}

            action_type = row[1]
            old_value = row[2]

            # Mark as reverted
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                "UPDATE ai_audit_log SET status = 'reverted', reverted_at = ? WHERE id = ?",
                (now, entry_id),
            )
            await db.commit()

        # For rule changes, apply the revert immediately
        if action_type in ("rule_disable", "rule_enable") and old_value:
            try:
                import json as _json
                old_data = _json.loads(old_value)
                rule_id = old_data.get("rule_id") if isinstance(old_data, dict) else None
                if rule_id:
                    from database import get_rules, save_rule
                    rules = await get_rules()
                    for rule in rules:
                        if rule.id == rule_id:
                            rule.enabled = action_type == "rule_disable"  # reverse the action
                            await save_rule(rule)
                            log.info("Reverted rule %s to enabled=%s", rule_id, rule.enabled)
                            break
            except Exception as e:
                log.warning("Could not auto-revert rule change: %s", e)

        # Save a snapshot of the restored value for Phase 3 AIParameterStore
        if old_value:
            await save_param_snapshot(action_type, None, old_value, "revert")

        # Log the revert as its own audit entry
        await log_ai_action(
            action_type=f"revert_{action_type}",
            category="revert",
            description=f"Reverted entry #{entry_id}",
            old_value=None,
            new_value=old_value,
            reason="Manual revert by user",
            confidence=1.0,
            status="applied",
        )

        log.info("AI action REVERTED: entry_id=%d, type=%s", entry_id, action_type)
        return {"reverted": True, "entry_id": entry_id}

    async def _count_today_changes(self) -> int:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with get_db() as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM ai_audit_log "
                "WHERE status = 'applied' AND timestamp >= ?",
                (today,),
            )
            return (await cur.fetchone())[0]

    async def _last_change_at(self) -> datetime | None:
        async with get_db() as db:
            cur = await db.execute(
                "SELECT timestamp FROM ai_audit_log "
                "WHERE status = 'applied' ORDER BY timestamp DESC LIMIT 1"
            )
            row = await cur.fetchone()
            if row:
                try:
                    return datetime.fromisoformat(row[0].replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    return None
        return None


# ── AI Status helper ─────────────────────────────────────────────────────────

async def get_ai_status_dict() -> dict:
    """Build the AI status response dict."""
    config = await _load_guardrails_from_db()
    enforcer = GuardrailEnforcer()
    changes_today = await enforcer._count_today_changes()
    last_change = await enforcer._last_change_at()

    return {
        "autonomy_active": config.ai_autonomy_enabled and not config.emergency_stop,
        "shadow_mode": config.shadow_mode,  # B1 FIX: read from DB, not env var
        "emergency_stop": config.emergency_stop,
        "last_action_at": last_change.isoformat() if last_change else None,
        "changes_today": changes_today,
        "next_optimization_at": None,
        "daily_budget_remaining": max(0, config.max_changes_per_day - changes_today),
        "last_optimization_at": None,
        "optimizer_running": False,
    }
