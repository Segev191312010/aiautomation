import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parents[1]))
import verify_broker_call_inventory as verifier


class BrokerInventoryTests(unittest.TestCase):
    def test_repository_inventory_passes(self):
        root = Path(__file__).parents[2]
        _, errors = verifier.verify(
            root, root / "docs/release-evidence/manifests/broker-call-inventory-v1.json"
        )
        self.assertEqual(errors, [])

    def test_new_direct_call_fails_closed(self):
        source = Path(__file__).parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "backend").mkdir()
            (root / "backend" / "rogue.py").write_text(
                "def send(ibkr, contract, order):\n    return ibkr.ib.placeOrder(contract, order)\n",
                encoding="utf-8",
            )
            manifest = root / "inventory.json"
            manifest.write_text(json.dumps({"schema_version": 1, "calls": []}), encoding="utf-8")
            _, errors = verifier.verify(root, manifest)
            self.assertTrue(any("unlisted broker call" in error for error in errors))

    def test_stale_entry_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "backend").mkdir()
            manifest = root / "inventory.json"
            manifest.write_text(json.dumps({"schema_version": 1, "calls": [
                {"path": "backend/gone.py", "line": 1, "method": "placeOrder", "callee": "ibkr.ib.placeOrder"}
            ]}), encoding="utf-8")
            _, errors = verifier.verify(root, manifest)
            self.assertTrue(any("stale inventory entry" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
