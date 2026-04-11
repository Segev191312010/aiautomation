"""Rule Builder API — templates, validation, cloning, import/export."""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_user
from database import get_rules, save_rule, get_rule, delete_rule
from models import Rule, RuleCreate, Condition, TradeAction
from rule_templates import get_templates, get_template, get_categories
from rule_validator import validate_conditions, ValidationResult

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/rules", tags=["rule-builder"], dependencies=[Depends(get_current_user)])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class TemplateApplyRequest(BaseModel):
    template_id: str
    symbol: str
    quantity: int = 10
    order_type: str = "MKT"


class ValidateRequest(BaseModel):
    conditions: list[dict[str, Any]]


class ImportRequest(BaseModel):
    rules: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Template endpoints
# ---------------------------------------------------------------------------

@router.get("/templates")
async def list_templates():
    return {"templates": get_templates(), "categories": get_categories()}


@router.get("/templates/{template_id}")
async def get_template_detail(template_id: str):
    t = get_template(template_id)
    if not t:
        raise HTTPException(404, f"Template '{template_id}' not found")
    return t


@router.post("/from-template", status_code=201)
async def create_from_template(req: TemplateApplyRequest):
    t = get_template(req.template_id)
    if not t:
        raise HTTPException(404, f"Template '{req.template_id}' not found")

    rule_data = RuleCreate(
        name=f"{t['name']} — {req.symbol}",
        symbol=req.symbol.upper(),
        conditions=[Condition(**c) for c in t["entry_conditions"]],
        logic=t.get("logic", "AND"),
        action=TradeAction(type=t["action"]["type"], quantity=req.quantity, order_type=req.order_type),
    )
    rule = Rule(**rule_data.model_dump())
    await save_rule(rule)
    return rule.model_dump()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@router.post("/validate")
async def validate_rule_conditions(req: ValidateRequest):
    result = validate_conditions(req.conditions)
    return {
        "valid": result.valid,
        "errors": [{"field": e.field, "message": e.message, "suggestion": e.suggestion} for e in result.errors],
        "warnings": result.warnings,
    }


# ---------------------------------------------------------------------------
# Clone, export, import
# ---------------------------------------------------------------------------

@router.post("/{rule_id}/clone", status_code=201)
async def clone_rule(rule_id: str):
    original = await get_rule(rule_id)
    if not original:
        raise HTTPException(404, "Rule not found")
    data = original.model_dump()
    data.pop("id", None)
    data["name"] = f"{data['name']} (copy)"
    data["enabled"] = False
    data["last_triggered"] = None
    data["symbol_cooldowns"] = {}
    new_rule = Rule(**data)
    await save_rule(new_rule)
    return new_rule.model_dump()


@router.post("/export")
async def export_rules():
    rules = await get_rules()
    return {"rules": [r.model_dump() for r in rules], "count": len(rules)}


@router.post("/import")
async def import_rules(req: ImportRequest):
    imported = 0
    errors = []
    for i, raw in enumerate(req.rules):
        try:
            raw.pop("id", None)
            raw["enabled"] = False
            raw["last_triggered"] = None
            raw["symbol_cooldowns"] = {}
            rule = Rule(**raw)
            await save_rule(rule)
            imported += 1
        except Exception as e:
            errors.append({"index": i, "error": str(e)})
    return {"imported": imported, "errors": errors}
