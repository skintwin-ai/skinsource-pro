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


def ingredient_identity(data: dict) -> dict | None:
    """Map an ingredient create payload onto a specify command, when it has identity."""
    if not data.get("inci_name") and not data.get("cas_number"):
        return None
    return {
        "command": "specify_ingredient",
        "args": {
            "ingredient_id": data.get("name") or "",
            "inci": data.get("inci_name") or "",
            "cas": data.get("cas_number") or "",
        },
    }


def ingredient_create_guard(data: dict) -> dict | None:
    """Validate INCI and CAS on the existing ingredient create payload."""
    command = ingredient_identity(data)
    if command is None:
        return None
    return specify_ingredient(command["args"])


def record_created_ingredient(data: dict) -> tuple[dict, int] | None:
    """Accept the ingredient on the shared ledger before the product database write."""
    command = ingredient_identity(data)
    if command is None:
        return None
    return respond(command)


def record_supplier_qualification(data: dict) -> tuple[dict, int]:
    """Qualify a supplier for an ingredient before the offering is stored."""
    return respond(
        {
            "command": "qualify_supplier",
            "args": {
                "qualification_id": data.get("qualification_id") or "",
                "supplier_name": data.get("supplier_name") or "",
                "ingredient_id": data.get("ingredient_id") or "",
            },
        }
    )


def kilograms_to_milligrams(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StageRejection("quantity_needed must be kilograms")
    milligrams = int(round(float(value) * 1_000_000))
    if milligrams < 1:
        raise StageRejection("milligrams must be a positive integer")
    return milligrams


def record_received_lot(data: dict) -> tuple[dict, int]:
    """Receive a lot when a procurement request is completed."""
    milligrams = data.get("milligrams")
    if not isinstance(milligrams, int) or isinstance(milligrams, bool):
        try:
            milligrams = kilograms_to_milligrams(data.get("quantity_kg"))
        except StageRejection as exc:
            return {"ok": False, "error": str(exc)}, 400
    return respond(
        {
            "command": "receive_lot",
            "args": {
                "lot_id": data.get("lot_id") or "",
                "ingredient_id": data.get("ingredient_id") or "",
                "qualification_id": data.get("qualification_id") or "",
                "milligrams": milligrams,
            },
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
    locator = _locator()
    if locator is not None:
        locator.bind_ledger()


_LOCATOR = None


def _locator():
    global _LOCATOR
    if _LOCATOR is False:
        return None
    if _LOCATOR is not None:
        return _LOCATOR
    import importlib.util

    script = _locate_script()
    if script is None:
        _LOCATOR = False
        return None
    spec = importlib.util.spec_from_file_location("skintwin_chain_locate", script)
    if spec is None or spec.loader is None:
        _LOCATOR = False
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _LOCATOR = module
    return module


def _locate_script() -> Path | None:
    override = os.environ.get("SKINTWIN_HUB_ROOT")
    if override:
        script = Path(override) / "domain" / "locate.py"
        if script.is_file() and (Path(override) / "domain" / "org-ecosystem.json").is_file():
            return script
    start = Path(__file__).resolve()
    for parent in [start, *start.parents]:
        if not (parent / ".git").exists():
            continue
        try:
            children = list(parent.parent.iterdir())
        except OSError:
            return None
        for child in children:
            script = child / "domain" / "locate.py"
            if script.is_file() and (child / "domain" / "org-ecosystem.json").is_file():
                return script
        return None
    return None


def _hub_root() -> Path | None:
    locator = _locator()
    if locator is None:
        return None
    found = locator.find_hub()
    return Path(found) if found else None


def _ledger_error(stdout: str, stderr: str) -> str:
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    return str(payload.get("error") or stderr or "ledger rejected the command")
