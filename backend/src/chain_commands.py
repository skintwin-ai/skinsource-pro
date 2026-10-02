"""Ingredient supply-chain commands used by the API and the hub ledger."""

from __future__ import annotations

import re

CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


class StageRejection(ValueError):
    pass


def specify_ingredient(args: dict) -> dict:
    ingredient_id = _text(args.get("ingredient_id"), "ingredient_id")
    inci = _text(args.get("inci"), "inci")
    cas = _text(args.get("cas"), "cas")
    if CAS_RE.fullmatch(cas) is None:
        raise StageRejection(f"cas {cas} is not a CAS registry number")
    return {"ingredient_id": ingredient_id, "inci": inci, "cas": cas}


def qualify_supplier(args: dict) -> dict:
    return {
        "qualification_id": _text(args.get("qualification_id"), "qualification_id"),
        "supplier_name": _text(args.get("supplier_name"), "supplier_name"),
        "ingredient_id": _text(args.get("ingredient_id"), "ingredient_id"),
    }


def receive_lot(args: dict) -> dict:
    return {
        "lot_id": _text(args.get("lot_id"), "lot_id"),
        "ingredient_id": _text(args.get("ingredient_id"), "ingredient_id"),
        "qualification_id": _text(args.get("qualification_id"), "qualification_id"),
        "milligrams": _positive(args.get("milligrams"), "milligrams"),
    }


def ingredient_create_guard(data: dict) -> dict | None:
    """Validate INCI and CAS on the existing ingredient create payload."""
    if not data.get("inci_name") and not data.get("cas_number"):
        return None
    return specify_ingredient(
        {
            "ingredient_id": data.get("name") or "",
            "inci": data.get("inci_name") or "",
            "cas": data.get("cas_number") or "",
        }
    )


def respond(body: dict) -> tuple[dict, int]:
    command = body.get("command")
    handler = HANDLERS.get(command)
    if handler is None:
        return {"ok": False, "error": f"unknown command {command}"}, 400
    try:
        artifact = handler(body.get("args") or {})
    except StageRejection as exc:
        return {"ok": False, "error": str(exc)}, 400
    return {"ok": True, "artifact": artifact}, 200


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StageRejection(f"{label} is required")
    return value.strip()


def _positive(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise StageRejection(f"{label} must be a positive integer")
    return value


HANDLERS = {
    "specify_ingredient": specify_ingredient,
    "qualify_supplier": qualify_supplier,
    "receive_lot": receive_lot,
}
