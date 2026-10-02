"""Ingredient supply-chain commands used by the API and the hub ledger."""

from __future__ import annotations

import json
import os
import re
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
        "milligrams": _positive(_whole_count(args.get("milligrams")), "milligrams"),
    }


_IDENTITY_KEYS = (
    "name",
    "ingredientId",
    "ingredient_id",
    "inciName",
    "inci_name",
    "inci",
    "casNumber",
    "cas_number",
    "cas",
)


def _named(record: object, *keys: str) -> str:
    """The first non-blank string wins. A blank value falls through to the next key."""
    if not isinstance(record, dict):
        return ""
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_value(record: object, *keys: str) -> object:
    """The first present value wins. A blank string falls through to the next key."""
    if not isinstance(record, dict):
        return None
    for key in keys:
        if key not in record:
            continue
        value = record[key]
        if isinstance(value, str):
            if not value.strip():
                continue
            return value.strip()
        if value is None:
            continue
        return value
    return None


def ingredient_column_values(data: dict) -> dict:
    """INCI and CAS stored on the ingredient row, using the names the ledger reads."""
    if not isinstance(data, dict):
        return {}
    stored: dict[str, str] = {}
    inci = _named(data, "inciName", "inci_name", "inci")
    cas = _named(data, "casNumber", "cas_number", "cas")
    if inci:
        stored["inci_name"] = inci
    if cas:
        stored["cas_number"] = cas
    return stored


def ingredient_identity(data: dict) -> dict | None:
    """Map an ingredient create payload onto a specify command, when it has identity."""
    if not isinstance(data, dict):
        return None
    inci = _named(data, "inciName", "inci_name", "inci")
    cas = _named(data, "casNumber", "cas_number", "cas")
    if not inci and not cas:
        return None
    return {
        "command": "specify_ingredient",
        "args": {
            "ingredient_id": _named(data, "ingredientId", "ingredient_id", "name"),
            "inci": inci,
            "cas": cas,
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


def record_updated_ingredient(existing: dict, data: dict) -> tuple[dict, int] | None:
    """Specify an ingredient when an update first gives it an INCI name and CAS number."""
    if not isinstance(existing, dict) or not isinstance(data, dict):
        return None
    if not any(key in data for key in _IDENTITY_KEYS):
        return None
    merged = dict(existing)
    for key in _IDENTITY_KEYS:
        if key in data:
            merged[key] = data[key]
    command = ingredient_identity(merged)
    if command is None:
        return None
    try:
        args = specify_ingredient(command["args"])
    except StageRejection as exc:
        return {"ok": False, "error": str(exc)}, 400
    prior = _recorded_ingredient(args["ingredient_id"])
    if prior == args:
        return {"ok": True, "count": 0}, 200
    return respond({"command": "specify_ingredient", "args": args})


def ledger_ingredient_id(name: object, inci: object, cas: object) -> str:
    """The ledger id for this INCI name and CAS number.

    A create payload can record ``ascorbic`` while the product row keeps the
    display name ``Ascorbic Acid``. Later qualification and receipt must use
    the ledger id. When the ledger has no single match, the display name remains
    the id.
    """
    fallback = name.strip() if isinstance(name, str) else ""
    inci_text = inci.strip() if isinstance(inci, str) else ""
    cas_text = cas.strip() if isinstance(cas, str) else ""
    if not inci_text or not cas_text:
        return fallback
    raw = os.environ.get("SKINTWIN_CHAIN_LEDGER")
    if not raw:
        return fallback
    path = Path(raw)
    if not path.is_file():
        return fallback
    found: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("command") != "specify_ingredient":
            continue
        args = record.get("args") or {}
        if args.get("inci") != inci_text or args.get("cas") != cas_text:
            continue
        ingredient_id = args.get("ingredient_id")
        if isinstance(ingredient_id, str) and ingredient_id not in found:
            found.append(ingredient_id)
    if len(found) == 1:
        return found[0]
    return fallback


def _recorded_ingredient(ingredient_id: str) -> dict | None:
    raw = os.environ.get("SKINTWIN_CHAIN_LEDGER")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_file():
        return None
    found = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("command") != "specify_ingredient":
            continue
        args = record.get("args") or {}
        if args.get("ingredient_id") == ingredient_id:
            found = args
    return found


def record_supplier_qualification(data: dict) -> tuple[dict, int]:
    """Qualify a supplier for an ingredient before the offering is stored."""
    return respond(
        {
            "command": "qualify_supplier",
            "args": {
                "qualification_id": _named(data, "qualificationId", "qualification_id"),
                "supplier_name": _named(data, "supplierName", "supplier_name"),
                "ingredient_id": _named(data, "ingredientId", "ingredient_id"),
            },
        }
    )


def offering_qualification(data: dict, supplier_name: str, ingredient_name: str) -> dict:
    """Ledger fields for an offering. The ingredient name is the ledger id."""
    payload = data if isinstance(data, dict) else {}
    qualification_id = _named(payload, "qualificationId", "qualification_id")
    supplier = supplier_name.strip() if isinstance(supplier_name, str) else ""
    ingredient = ingredient_name.strip() if isinstance(ingredient_name, str) else ""
    if not qualification_id:
        qualification_id = f"{supplier}:{ingredient}"
    return {
        "qualification_id": qualification_id,
        "supplier_name": supplier,
        "ingredient_id": ingredient,
    }


def kilograms_to_milligrams(value: object) -> int:
    """A numeric kilogram string is the same quantity. A blank or word is not."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise StageRejection("quantity_needed must be kilograms")
        try:
            value = float(text)
        except ValueError as exc:
            raise StageRejection("quantity_needed must be kilograms") from exc
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StageRejection("quantity_needed must be kilograms")
    milligrams = int(round(float(value) * 1_000_000))
    if milligrams < 1:
        raise StageRejection("milligrams must be a positive integer")
    return milligrams


def _whole_count(value: object) -> object:
    """A digit string is that integer. Anything else is left as written."""
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


def receive_package(args: dict) -> dict:
    pieces = _whole_count(args.get("pieces"))
    if isinstance(pieces, bool) or not isinstance(pieces, int) or pieces < 1:
        raise StageRejection("pieces must be a positive integer")
    return {
        "component_id": _text(args.get("component_id"), "component_id"),
        "name": _text(args.get("name"), "package name"),
        "lot_id": _text(args.get("lot_id"), "lot_id"),
        "supplier_name": _text(args.get("supplier_name"), "supplier_name"),
        "pieces": pieces,
    }


def record_received_package(data: dict) -> tuple[dict, int]:
    """Receive a packaging lot before the procurement record is stored."""
    component_id = _named(data, "componentId", "component_id")
    return respond(
        {
            "command": "receive_package",
            "args": {
                "component_id": component_id,
                "name": _named(data, "name") or component_id,
                "lot_id": _named(data, "lotId", "lot_id"),
                "supplier_name": _named(data, "supplierName", "supplier_name"),
                "pieces": data.get("pieces"),
            },
        }
    )


def record_received_lot(data: dict) -> tuple[dict, int]:
    """Receive a lot when a procurement request is completed."""
    milligrams = _whole_count(data.get("milligrams"))
    if not isinstance(milligrams, int) or isinstance(milligrams, bool):
        try:
            milligrams = kilograms_to_milligrams(_first_value(data, "quantityKg", "quantity_kg"))
        except StageRejection as exc:
            return {"ok": False, "error": str(exc)}, 400
    return respond(
        {
            "command": "receive_lot",
            "args": {
                "lot_id": _named(data, "lotId", "lot_id"),
                "ingredient_id": _named(data, "ingredientId", "ingredient_id"),
                "qualification_id": _named(data, "qualificationId", "qualification_id"),
                "milligrams": milligrams,
            },
        }
    )


def completed_procurement_receipt(
    data: dict,
    ingredient_name: str,
    request_key: str,
    quantity_kg: object = None,
) -> tuple[dict, int] | None:
    """A procurement created or updated as completed receives its lot or package."""
    if not isinstance(data, dict) or data.get("status") != "completed":
        return None
    component_id = _named(data, "componentId", "component_id")
    if component_id and data.get("pieces") is not None:
        package = dict(data)
        if not _named(data, "name"):
            package["name"] = component_id
        if not _named(data, "lotId", "lot_id"):
            package["lot_id"] = f"pack-{request_key}"
        return record_received_package(package)
    resolved_quantity = _first_value(data, "quantityKg", "quantity_kg")
    if resolved_quantity is None:
        resolved_quantity = quantity_kg if quantity_kg is not None else data.get("quantity_needed")
    lot = dict(data)
    lot["ingredient_id"] = ingredient_name or ""
    lot.pop("ingredientId", None)
    lot.pop("quantityKg", None)
    lot["quantity_kg"] = resolved_quantity
    if not _named(data, "lotId", "lot_id"):
        lot["lot_id"] = f"lot-{request_key}"
    return record_received_lot(lot)


def respond(body: dict) -> tuple[dict, int]:
    command = body.get("command")
    handler = HANDLERS.get(command)
    if handler is None:
        return {"ok": False, "error": f"unknown command {command}"}, 400
    try:
        artifact = handler(body.get("args") or {})
    except StageRejection as exc:
        return {"ok": False, "error": str(exc)}, 400
    # The ledger stores this body. A numeric string must be the integer it names.
    normalized = {"command": command, "args": artifact}
    return _commit(normalized, ({"ok": True, "artifact": artifact}, 200))


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
    "receive_package": receive_package,
}


def _commit(request: dict, result: tuple[dict, int]) -> tuple[dict, int]:
    _body, status = result
    if status != 200 or os.environ.get("SKINTWIN_CHAIN_SKIP_DISPATCH") == "1":
        return result
    if not os.environ.get("SKINTWIN_CHAIN_LEDGER"):
        return result
    locator = _locator()
    if locator is None:
        return {"ok": False, "error": "supply-chain hub is not present"}, 400
    error = locator.commit_command(request)
    if error:
        return {"ok": False, "error": error}, 400
    return result


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


def _recorded_hub(directory: Path, file_name: str) -> Path | None:
    directory = directory.resolve()
    registry_path = directory / "domain" / "org-ecosystem.json"
    script = directory / "domain" / file_name
    if not registry_path.is_file() or not (directory / "domain" / "supply-chain.json").is_file():
        return None
    if not script.is_file():
        return None
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    hub = data.get("hub") if isinstance(data, dict) else None
    name = hub.get("name") if isinstance(hub, dict) else None
    if name != directory.name:
        return None
    return script


def _locate_script() -> Path | None:
    override = os.environ.get("SKINTWIN_HUB_ROOT")
    if override:
        found = _recorded_hub(Path(override), "locate.py")
        if found is not None:
            return found
    start = Path(__file__).resolve()
    for parent in [start, *start.parents]:
        if not (parent / ".git").exists():
            continue
        try:
            children = list(parent.parent.iterdir())
        except OSError:
            return None
        for child in children:
            found = _recorded_hub(child, "locate.py")
            if found is not None:
                return found
        return None
    return None
