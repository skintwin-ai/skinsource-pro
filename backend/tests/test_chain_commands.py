import json
import os
import tempfile
import unittest
from pathlib import Path

from src.chain_commands import ingredient_create_guard, record_created_ingredient, respond


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
            os.environ["SKINTWIN_HUB_ROOT"] = "/agent/repos/skintwin-ecosystem-design"
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

    def test_supply_chain_route_rejects_an_unknown_command(self) -> None:
        body, status = respond({"command": "manufacture", "args": {}})
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])


if __name__ == "__main__":
    unittest.main()
