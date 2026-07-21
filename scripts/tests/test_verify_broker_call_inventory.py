import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parents[1]))
import verify_broker_call_inventory as verifier


def _write_manifest(path: Path, calls: list[dict]) -> None:
    path.write_text(
        json.dumps({"schema_version": verifier.SCHEMA_VERSION, "calls": calls}),
        encoding="utf-8",
    )


def _reviewed(call: dict, *, classification: str | None = None,
              disposition: str | None = None) -> dict:
    policy = verifier.METHOD_POLICY.get(call["method"])
    default_classification = policy[0] if policy else "admin"
    default_disposition = sorted(policy[1])[0] if policy else "review-required"
    return {
        **call,
        "classification": classification or default_classification,
        "disposition": disposition or default_disposition,
    }


class BrokerInventoryTests(unittest.TestCase):
    def test_repository_inventory_passes(self):
        root = Path(__file__).parents[2]
        actual, errors = verifier.verify(
            root, root / "docs/release-evidence/manifests/broker-call-inventory-v1.json"
        )
        self.assertEqual(len(actual), 98)
        self.assertEqual(errors, [])

    def test_new_known_direct_call_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "backend").mkdir()
            (root / "backend" / "rogue.py").write_text(
                "def send(ibkr, contract, order):\n    return ibkr.ib.placeOrder(contract, order)\n",
                encoding="utf-8",
            )
            manifest = root / "inventory.json"
            _write_manifest(manifest, [])
            _, errors = verifier.verify(root, manifest)
            self.assertTrue(any("unlisted broker call" in error for error in errors))

    def test_unknown_method_is_discovered_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "backend").mkdir()
            (root / "backend" / "rogue.py").write_text(
                "ibkr.ib.newBrokerMutation()\n", encoding="utf-8"
            )
            call = verifier.discover(root)[0]
            manifest = root / "inventory.json"
            _write_manifest(manifest, [_reviewed(call)])
            _, errors = verifier.verify(root, manifest)
            self.assertTrue(any("unclassified broker method" in error for error in errors))

    def test_stale_entry_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "backend").mkdir()
            manifest = root / "inventory.json"
            call = {
                "path": "backend/gone.py", "line": 1, "column": 0,
                "method": "placeOrder", "callee": "ibkr.ib.placeOrder",
            }
            _write_manifest(manifest, [_reviewed(call)])
            _, errors = verifier.verify(root, manifest)
            self.assertTrue(any("stale inventory entry" in error for error in errors))

    def test_wrong_classification_and_disposition_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "backend").mkdir()
            (root / "backend" / "rogue.py").write_text(
                "ibkr.ib.placeOrder(contract, order)\n", encoding="utf-8"
            )
            call = verifier.discover(root)[0]
            manifest = root / "inventory.json"
            _write_manifest(manifest, [_reviewed(
                call, classification="read", disposition="market-data-read"
            )])
            _, errors = verifier.verify(root, manifest)
            self.assertTrue(any("classification mismatch" in error for error in errors))
            self.assertTrue(any("disposition mismatch" in error for error in errors))

    def test_same_line_calls_are_distinguished_by_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "backend").mkdir()
            (root / "backend" / "same_line.py").write_text(
                "left = ib.portfolio() if flag else ib.portfolio()\n", encoding="utf-8"
            )
            calls = verifier.discover(root)
            self.assertEqual(len(calls), 2)
            self.assertEqual(len({call["column"] for call in calls}), 2)

    def test_unrelated_disconnect_is_not_broker_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "backend").mkdir()
            (root / "socket.py").write_text("ws.disconnect()\n", encoding="utf-8")
            self.assertEqual(verifier.discover(root), [])


if __name__ == "__main__":
    unittest.main()
