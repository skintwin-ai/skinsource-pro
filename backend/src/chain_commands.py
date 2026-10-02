"""Ingredient supply-chain commands used by the API and the hub ledger."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

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
    return _commit(body, ({"ok": True, "artifact": artifact}, 200))


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


def _commit(request: dict, result: tuple[dict, int]) -> tuple[dict, int]:
    body, status = result
    if status != 200 or os.environ.get("SKINTWIN_CHAIN_SKIP_DISPATCH") == "1":
        return result
    ledger = os.environ.get("SKINTWIN_CHAIN_LEDGER")
    if not ledger:
        return result
    hub = _hub_root()
    if hub is None:
        return {"ok": False, "error": "supply-chain hub is not present"}, 400
    completed = subprocess.run(
        [sys.executable, "-m", "domain.ledger"],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        cwd=hub,
        check=False,
    )
    if completed.returncode != 0:
        message = _ledger_error(completed.stdout, completed.stderr)
        return {"ok": False, "error": message}, 400
    return body, status


def use_shared_ledger() -> None:
    """Point this process at the hub ledger when an API request records a stage."""
    hub = _hub_root()
    if hub is None:
        return
    os.environ.setdefault("SKINTWIN_HUB_ROOT", str(hub))
    os.environ.setdefault("SKINTWIN_CHAIN_LEDGER", str(hub / "var" / "supply-chain.jsonl"))


def _hub_root() -> Path | None:
    override = os.environ.get("SKINTWIN_HUB_ROOT")
    candidates = [Path(override)] if override else []
    candidates.extend(
        [
            Path("/agent/repos/skintwin-ecosystem-design"),
            Path("/workspace/repos/skintwin-ecosystem-design"),
        ]
    )
    for candidate in candidates:
        if (candidate / "domain" / "ledger.py").is_file():
            return candidate
    return None


def _ledger_error(stdout: str, stderr: str) -> str:
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    return str(payload.get("error") or stderr or "ledger rejected the command")
