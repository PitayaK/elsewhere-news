"""Distribution checks using harmless client bytes in an isolated source tree."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest
import zipfile


SOURCE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("native_package_builder", SOURCE / "scripts/build_plugin.py")
assert SPEC and SPEC.loader
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class PackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="elsewhere-package-check-")
        self.root = Path(self.temporary.name).resolve() / "source"
        self.root.mkdir()
        self.old_root = BUILDER.ROOT
        BUILDER.ROOT = self.root
        self.client = b'#!/usr/bin/env python3\nprint("harmless packaging fixture")\n'
        for target in ["plugin.json", "agents/openai.yaml"]:
            path = self.root / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((SOURCE / target).read_bytes())
        skill = (SOURCE / "SKILL.md").read_text()
        skill = re.sub(r"^Client bytes: .+$", f"Client bytes: {len(self.client)}", skill, flags=re.M)
        skill = re.sub(r"^Client SHA-256: .+$", f"Client SHA-256: {hashlib.sha256(self.client).hexdigest()}", skill, flags=re.M)
        (self.root / "SKILL.md").write_text(skill)
        path = self.root / "scripts/elsewhere.py"
        path.parent.mkdir()
        path.write_bytes(self.client)

    def tearDown(self) -> None:
        BUILDER.ROOT = self.old_root
        self.temporary.cleanup()

    def set_artifact(self, url: str) -> None:
        path = self.root / "SKILL.md"
        value, count = re.subn(r"^Reviewed client artifact: `[^`\n]+`$",
                              f"Reviewed client artifact: `{url}`", path.read_text(), flags=re.M)
        self.assertEqual(count, 1)
        path.write_text(value)

    def test_release_and_immutable_same_repository_source_are_valid_downloads(self) -> None:
        urls = [
            "https://elsewhere.news/.well-known/elsewhere-skill/scripts/elsewhere-3.1.1.py",
            "https://raw.githubusercontent.com/PitayaK/elsewhere-news/0123456789abcdef0123456789abcdef01234567/scripts/elsewhere.py",
        ]
        for index, url in enumerate(urls):
            with self.subTest(url=url):
                self.set_artifact(url)
                result = BUILDER.build(Path(self.temporary.name) / f"valid-{index}.zip")
                with zipfile.ZipFile(result["archive"]) as package:
                    self.assertEqual(package.read("skills/elsewhere-news/scripts/elsewhere.py"), self.client)
                    self.assertIn(url.encode(), package.read("skills/elsewhere-news/SKILL.md"))

    def test_mutable_foreign_or_modified_download_urls_are_rejected(self) -> None:
        base = "https://raw.githubusercontent.com/PitayaK/elsewhere-news/"
        commit = "0123456789abcdef0123456789abcdef01234567"
        immutable = base + commit + "/scripts/elsewhere.py"
        bad_urls = [
            base + "main/scripts/elsewhere.py",
            base + "refs/heads/main/scripts/elsewhere.py",
            base + "v3.1.0/scripts/elsewhere.py",
            base + commit[:-1] + "/scripts/elsewhere.py",
            base + commit + "0/scripts/elsewhere.py",
            base + commit.upper() + "/scripts/elsewhere.py",
            immutable.replace("/PitayaK/", "/AnotherOwner/"),
            immutable.replace("/elsewhere-news/", "/another-repository/"),
            immutable.replace("raw.githubusercontent.com", "raw.githubusercontent.com.evil.invalid"),
            immutable.replace("raw.githubusercontent.com", "raw.githubusercontent.com@evil.invalid"),
            immutable.replace("https:", "http:"),
            immutable.replace("/scripts/elsewhere.py", "/scripts/another.py"),
            immutable + "?download=1",
            immutable + "#L1",
            immutable + "/",
            " " + immutable,
            "https://elsewhere.news/.well-known/elsewhere-skill/scripts/elsewhere-3.0.2.py",
        ]
        for index, url in enumerate(bad_urls):
            with self.subTest(url=url):
                self.set_artifact(url)
                with self.assertRaisesRegex(ValueError, "Wrong reviewed client artifact"):
                    BUILDER.build(Path(self.temporary.name) / f"invalid-{index}.zip")

    def test_immutable_source_url_does_not_bypass_reviewed_client_integrity(self) -> None:
        self.set_artifact("https://raw.githubusercontent.com/PitayaK/elsewhere-news/0123456789abcdef0123456789abcdef01234567/scripts/elsewhere.py")
        (self.root / "scripts/elsewhere.py").write_bytes(self.client + b"# changed\n")
        with self.assertRaisesRegex(ValueError, "Client differs"):
            BUILDER.build(Path(self.temporary.name) / "bad-immutable.zip")

    def test_exact_bundle_is_deterministic_and_excludes_private_or_native_sources(self) -> None:
        private_marker = "synthetic-private-context-do-not-distribute"
        (self.root / "elsewhere.md").write_text(private_marker)
        (self.root / ".env").write_text(private_marker)
        (self.root / "tests").mkdir()
        (self.root / "tests/not-shipped.py").write_text(private_marker)
        for location in ["credentials.json", ".local/state/elsewhere-news/production.json",
                         "private-home/.elsewhere-news/authorization.json"]:
            private = self.root / location
            private.parent.mkdir(parents=True, exist_ok=True)
            private.write_text(private_marker)
        legacy = self.root / "integrations/native-mcp/mcp.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(private_marker)
        first = BUILDER.build(Path(self.temporary.name) / "first.zip")
        second = BUILDER.build(Path(self.temporary.name) / "second.zip")
        self.assertEqual(first["sha256"], second["sha256"])
        with zipfile.ZipFile(first["archive"]) as package:
            self.assertEqual(package.namelist(), sorted(BUILDER.FILES))
            self.assertEqual(len(package.namelist()), 4)
            self.assertNotIn("mcp.json", package.namelist())
            self.assertEqual(package.read("skills/elsewhere-news/scripts/elsewhere.py"), self.client)
            for item in package.namelist():
                self.assertNotIn(private_marker.encode(), package.read(item))

    def test_unreviewed_client_change_is_rejected(self) -> None:
        (self.root / "scripts/elsewhere.py").write_bytes(self.client + b"# changed\n")
        with self.assertRaisesRegex(ValueError, "Client differs"):
            BUILDER.build(Path(self.temporary.name) / "bad.zip")

    def test_development_pin_is_rejected(self) -> None:
        path = self.root / "SKILL.md"
        path.write_text(path.read_text().replace(f"Client bytes: {len(self.client)}", "Client bytes: PENDING_CLIENT_BYTES"))
        with self.assertRaisesRegex(ValueError, "pin is missing or unresolved"):
            BUILDER.build(Path(self.temporary.name) / "bad.zip")

    def test_client_symlink_is_rejected(self) -> None:
        path = self.root / "scripts/elsewhere.py"
        path.unlink()
        outside = Path(self.temporary.name) / "outside.py"
        outside.write_bytes(self.client)
        path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            BUILDER.build(Path(self.temporary.name) / "bad.zip")

    def test_automatic_mcp_binding_is_rejected(self) -> None:
        (self.root / "mcp.json").write_text('{"mcpServers":{}}\n')
        with self.assertRaisesRegex(ValueError, "auto-discovered connection"):
            BUILDER.build(Path(self.temporary.name) / "bad.zip")

    def test_dependency_metadata_is_rejected(self) -> None:
        path = self.root / "agents/openai.yaml"
        path.write_text(path.read_text() + "dependencies:\n  tools: []\n")
        with self.assertRaisesRegex(ValueError, "Unexpected display metadata or host dependency"):
            BUILDER.build(Path(self.temporary.name) / "bad.zip")


if __name__ == "__main__":
    unittest.main()
