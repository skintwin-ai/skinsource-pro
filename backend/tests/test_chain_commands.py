import json
import os
import tempfile
import unittest
from pathlib import Path

from src.chain_commands import (
    completed_procurement_receipt,
    ingredient_create_guard,
    record_created_ingredient,
    record_received_lot,
    record_updated_ingredient,
    record_received_package,
    record_supplier_qualification,
    respond,
)


class ChainCommandTests(unittest.TestCase):
    def test_ingredient_create_rejects_a_bad_cas_before_persistence(self) -> None:
        with self.assertRaises(Exception) as caught:
            ingredient_create_guard(
                {"name": "Water", "inci_name": "Aqua", "cas_number": "not-a-cas"}
            )
        self.assertIn("CAS", str(caught.exception))

    def test_supply_chain_route_accepts_a_lot(self) -> None:
        body, status = respond(
            {
                "command": "receive_lot",
                "args": {
                    "lot_id": "lot-1",
                    "ingredient_id": "ascorbic",
                    "qualification_id": "qual-1",
                    "milligrams": 1000,
                },
            }
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["artifact"]["lot_id"], "lot-1")

    def test_ingredient_create_records_identity_on_the_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "supply-chain.jsonl"
            previous = os.environ.get("SKINTWIN_CHAIN_LEDGER")
            os.environ["SKINTWIN_CHAIN_LEDGER"] = str(ledger)
            try:
                body, status = record_created_ingredient(
                    {
                        "name": "ascorbic",
                        "inci_name": "Ascorbic Acid",
                        "cas_number": "50-81-7",
                    }
                )
                self.assertEqual(status, 200)
                self.assertEqual(body["artifact"]["ingredient_id"], "ascorbic")
                recorded = json.loads(ledger.read_text().strip())
                self.assertEqual(recorded["command"], "specify_ingredient")
                again, again_status = record_created_ingredient(
                    {
                        "name": "ascorbic",
                        "inci_name": "Ascorbic Acid",
                        "cas_number": "50-81-7",
                    }
                )
                self.assertEqual(again_status, 400)
                self.assertFalse(again["ok"])
            finally:
                if previous is None:
                    os.environ.pop("SKINTWIN_CHAIN_LEDGER", None)
                else:
                    os.environ["SKINTWIN_CHAIN_LEDGER"] = previous

    def test_supplier_qualification_against_an_empty_ledger_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "supply-chain.jsonl"
            previous = os.environ.get("SKINTWIN_CHAIN_LEDGER")
            os.environ["SKINTWIN_CHAIN_LEDGER"] = str(ledger)
            try:
                body, status = record_supplier_qualification(
                    {
                        "qualification_id": "qual-ascorbic",
                        "supplier_name": "Cape Acids",
                        "ingredient_id": "ascorbic",
                    }
                )
            finally:
                if previous is None:
                    os.environ.pop("SKINTWIN_CHAIN_LEDGER", None)
                else:
                    os.environ["SKINTWIN_CHAIN_LEDGER"] = previous
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertFalse(ledger.exists())

    def test_package_receipt_accepts_a_positive_piece_count(self) -> None:
        previous = os.environ.pop("SKINTWIN_CHAIN_LEDGER", None)
        try:
            body, status = record_received_package(
                {
                    "component_id": "bottle-30",
                    "name": "30 ml bottle",
                    "lot_id": "lot-bottle",
                    "supplier_name": "Cape Glass",
                    "pieces": 4,
                }
            )
        finally:
            if previous is not None:
                os.environ["SKINTWIN_CHAIN_LEDGER"] = previous
        self.assertEqual(status, 200)
        self.assertEqual(body["artifact"]["pieces"], 4)
        self.assertEqual(body["artifact"]["supplier_name"], "Cape Glass")
        rejected, rejected_status = respond(
            {
                "command": "receive_package",
                "args": {
                    "component_id": "bottle-30",
                    "name": "30 ml bottle",
                    "lot_id": "lot-bottle",
                    "supplier_name": "Cape Glass",
                    "pieces": 0,
                },
            }
        )
        self.assertEqual(rejected_status, 400)
        self.assertFalse(rejected["ok"])

    def test_completed_procurement_receives_kilograms_as_milligrams(self) -> None:
        previous = os.environ.pop("SKINTWIN_CHAIN_LEDGER", None)
        try:
            body, status = record_received_lot(
                {
                    "lot_id": "lot-ascorbic",
                    "ingredient_id": "ascorbic",
                    "qualification_id": "qual-ascorbic",
                    "quantity_kg": 0.05,
                }
            )
        finally:
            if previous is not None:
                os.environ["SKINTWIN_CHAIN_LEDGER"] = previous
        self.assertEqual(status, 200)
        self.assertEqual(body["artifact"]["milligrams"], 50_000)

    def test_lot_receipt_against_an_empty_ledger_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "supply-chain.jsonl"
            previous = os.environ.get("SKINTWIN_CHAIN_LEDGER")
            os.environ["SKINTWIN_CHAIN_LEDGER"] = str(ledger)
            try:
                body, status = record_received_lot(
                    {
                        "lot_id": "lot-ascorbic",
                        "ingredient_id": "ascorbic",
                        "qualification_id": "qual-ascorbic",
                        "milligrams": 50_000,
                    }
                )
            finally:
                if previous is None:
                    os.environ.pop("SKINTWIN_CHAIN_LEDGER", None)
                else:
                    os.environ["SKINTWIN_CHAIN_LEDGER"] = previous
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertFalse(ledger.exists())

    def test_an_ingredient_update_specifies_identity_once(self) -> None:
        existing = {"name": "glycerin", "inci_name": None, "cas_number": None}
        self.assertIsNone(record_updated_ingredient(existing, {"description": "humectant"}))
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "supply-chain.jsonl"
            previous = os.environ.get("SKINTWIN_CHAIN_LEDGER")
            os.environ["SKINTWIN_CHAIN_LEDGER"] = str(ledger)
            try:
                rejected, rejected_status = record_updated_ingredient(
                    existing, {"inci_name": "Glycerin", "cas_number": "bad"}
                )
                self.assertEqual(rejected_status, 400)
                self.assertFalse(ledger.exists())
                body, status = record_updated_ingredient(
                    existing, {"inci_name": "Glycerin", "cas_number": "56-81-5"}
                )
                self.assertEqual(status, 200)
                self.assertEqual(body["artifact"]["ingredient_id"], "glycerin")
                text = ledger.read_text(encoding="utf-8")
                self.assertIn("56-81-5", text)
                again, again_status = record_updated_ingredient(
                    {"name": "glycerin", "inci_name": "Glycerin", "cas_number": "56-81-5"},
                    {"description": "humectant", "inci_name": "Glycerin"},
                )
                self.assertEqual(again_status, 200)
                self.assertEqual(again["count"], 0)
                self.assertEqual(ledger.read_text(encoding="utf-8"), text)
                changed, changed_status = record_updated_ingredient(
                    {"name": "glycerin", "inci_name": "Glycerin", "cas_number": "56-81-5"},
                    {"cas_number": "50-81-7"},
                )
                self.assertEqual(changed_status, 400)
                self.assertFalse(changed["ok"])
                self.assertEqual(ledger.read_text(encoding="utf-8"), text)
            finally:
                if previous is None:
                    os.environ.pop("SKINTWIN_CHAIN_LEDGER", None)
                else:
                    os.environ["SKINTWIN_CHAIN_LEDGER"] = previous

    def test_a_completed_procurement_receives_the_lot_once(self) -> None:
        self.assertIsNone(
            completed_procurement_receipt(
                {"status": "draft", "quantity_needed": 0.05},
                "glycerin",
                "1",
            )
        )
        previous = os.environ.pop("SKINTWIN_CHAIN_LEDGER", None)
        try:
            body, status = completed_procurement_receipt(
                {
                    "status": "completed",
                    "quantity_needed": 0.05,
                    "lot_id": "lot-glycerin",
                    "qualification_id": "qual-glycerin",
                },
                "glycerin",
                "1",
            )
        finally:
            if previous is not None:
                os.environ["SKINTWIN_CHAIN_LEDGER"] = previous
        self.assertEqual(status, 200)
        self.assertEqual(body["artifact"]["milligrams"], 50_000)
        packaged, packaged_status = completed_procurement_receipt(
            {
                "status": "completed",
                "component_id": "bottle-30",
                "name": "30 ml bottle",
                "supplier_name": "Cape Glass",
                "pieces": 4,
                "lot_id": "lot-bottle",
            },
            "glycerin",
            "2",
        )
        self.assertEqual(packaged_status, 200)
        self.assertEqual(packaged["artifact"]["pieces"], 4)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "supply-chain.jsonl"
            previous_ledger = os.environ.get("SKINTWIN_CHAIN_LEDGER")
            os.environ["SKINTWIN_CHAIN_LEDGER"] = str(ledger)
            try:
                missing, missing_status = completed_procurement_receipt(
                    {
                        "status": "completed",
                        "lot_id": "lot-glycerin",
                        "qualification_id": "qual-glycerin",
                        "milligrams": 50_000,
                    },
                    "glycerin",
                    "1",
                )
                self.assertEqual(missing_status, 400)
                self.assertFalse(missing["ok"])
                self.assertFalse(ledger.exists())
                specified, specified_status = respond(
                    {
                        "command": "specify_ingredient",
                        "args": {"ingredient_id": "glycerin", "inci": "Glycerin", "cas": "56-81-5"},
                    }
                )
                self.assertEqual(specified_status, 200)
                qualified, qualified_status = respond(
                    {
                        "command": "qualify_supplier",
                        "args": {
                            "qualification_id": "qual-glycerin",
                            "supplier_name": "Inland Humectants",
                            "ingredient_id": "glycerin",
                        },
                    }
                )
                self.assertEqual(qualified_status, 200)
                received, received_status = completed_procurement_receipt(
                    {
                        "status": "completed",
                        "lot_id": "lot-glycerin",
                        "qualification_id": "qual-glycerin",
                        "quantity_needed": 0.05,
                    },
                    "glycerin",
                    "1",
                )
                self.assertEqual(received_status, 200)
                text = ledger.read_text(encoding="utf-8")
                self.assertIn("lot-glycerin", text)
                again, again_status = completed_procurement_receipt(
                    {
                        "status": "completed",
                        "lot_id": "lot-glycerin",
                        "qualification_id": "qual-glycerin",
                        "quantity_needed": 0.05,
                    },
                    "glycerin",
                    "1",
                )
                self.assertEqual(again_status, 400)
                self.assertFalse(again["ok"])
                self.assertEqual(ledger.read_text(encoding="utf-8"), text)
            finally:
                if previous_ledger is None:
                    os.environ.pop("SKINTWIN_CHAIN_LEDGER", None)
                else:
                    os.environ["SKINTWIN_CHAIN_LEDGER"] = previous_ledger

    def test_supply_chain_route_rejects_an_unknown_command(self) -> None:
        body, status = respond({"command": "manufacture", "args": {}})
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])


if __name__ == "__main__":
    unittest.main()
