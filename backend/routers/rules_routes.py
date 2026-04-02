"""Rules CRUD routes — /api/rules/* (CRUD only, not templates/validate/export/import)"""
from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from database import get_rules, get_rule, save_rule, delete_rule
from models import Rule, RuleCreate, RuleUpdate

router = APIRouter(
    prefix="/api/rules",
    tags=["rules"],
    dependencies=[Depends(get_current_user)],
)


@router.get("")
async def list_rules():
    return [r.model_dump() for r in await get_rules()]


@router.get("/{rule_id}")
async def get_rule_route(rule_id: str):
    if rule_id in ("templates", "validate", "from-template", "export", "import"):
        raise HTTPException(404, "Use the specific endpoint")
    rule = await get_rule(rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")
    return rule.model_dump()


@router.post("", status_code=201)
async def create_rule(body: RuleCreate):
    rule = Rule(**body.model_dump())
    await save_rule(rule)
    return rule.model_dump()


@router.put("/{rule_id}")
async def update_rule_route(rule_id: str, body: RuleUpdate):
    rule = await get_rule(rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")
    updated = rule.model_copy(update=body.model_dump(exclude_none=True))
    await save_rule(updated)
    return updated.model_dump()


@router.delete("/{rule_id}")
async def delete_rule_route(rule_id: str):
    if not await delete_rule(rule_id):
        raise HTTPException(404, "Rule not found")
    return {"deleted": True}


@router.post("/{rule_id}/toggle")
async def toggle_rule(rule_id: str):
    rule = await get_rule(rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")
    rule.enabled = not rule.enabled
    await save_rule(rule)
    return {"id": rule_id, "enabled": rule.enabled}
