import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parents[1]))
import verify_runtime_file_manifest as verifier


class RuntimeManifestTests(unittest.TestCase):
    def _repo(self, files):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        for rel, content in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.email=x@y", "-c", "user.name=x", "commit", "-qm", "init"], check=True)
        return root

    def _manifest(self, root, digest):
        path = root / "manifest.json"
        path.write_text(json.dumps({
            "schema_version": 1,
            "hash_algorithm": "sha256-path-length-bytes-v1",
            "runtime_roots": ["backend"],
            "excluded_paths": [],
            "ignored_runtime_patterns": ["backend/*.db"],
            "tree_sha256": digest,
        }), encoding="utf-8")
        return path

    def test_snapshot_passes_and_mutation_fails(self):
        root = self._repo({"backend/main.py": "print(1)\n"})
        digest = verifier._tree_hash(root, ["backend/main.py"])
        manifest = self._manifest(root, digest)
        self.assertEqual(verifier.verify(root, manifest), [])
        (root / "backend/main.py").write_text("print(2)\n", encoding="utf-8")
        self.assertTrue(any("digest mismatch" in e for e in verifier.verify(root, manifest)))

    def test_dirty_ignored_runtime_path_fails(self):
        root = self._repo({"backend/main.py": "print(1)\n"})
        digest = verifier._tree_hash(root, ["backend/main.py"])
        manifest = self._manifest(root, digest)
        (root / "backend/state.db").write_text("runtime", encoding="utf-8")
        self.assertTrue(any("dirty ignored runtime path" in e for e in verifier.verify(root, manifest)))


if __name__ == "__main__":
    unittest.main()
