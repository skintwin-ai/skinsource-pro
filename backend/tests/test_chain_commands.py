import unittest

from src.chain_commands import ingredient_create_guard, respond


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

    def test_supply_chain_route_rejects_an_unknown_command(self) -> None:
        body, status = respond({"command": "manufacture", "args": {}})
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])


if __name__ == "__main__":
    unittest.main()
