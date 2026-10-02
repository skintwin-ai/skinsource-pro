#!/usr/bin/env python3
"""Ingredient specify, supplier qualification, and lot receipt for SkinSource Pro."""

from __future__ import annotations

import json
import re
import sys

CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


def specify_ingredient(args: dict) -> dict:
    ingredient_id = _text(args.get("ingredient_id"), "ingredient_id")
    inci = _text(args.get("inci"), "inci")
    cas = _text(args.get("cas"), "cas")
    if CAS_RE.fullmatch(cas) is None:
        raise ValueError(f"cas {cas} is not a CAS registry number")
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


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


def _positive(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


HANDLERS = {
    "specify_ingredient": specify_ingredient,
    "qualify_supplier": qualify_supplier,
    "receive_lot": receive_lot,
}


def main() -> None:
    request = json.load(sys.stdin)
    command = request.get("command")
    handler = HANDLERS.get(command)
    if handler is None:
        _fail(f"unknown command {command}")
    try:
        artifact = handler(request.get("args") or {})
    except ValueError as exc:
        _fail(str(exc))
    json.dump({"ok": True, "artifact": artifact}, sys.stdout)


def _fail(message: str) -> None:
    json.dump({"ok": False, "error": message}, sys.stdout)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
