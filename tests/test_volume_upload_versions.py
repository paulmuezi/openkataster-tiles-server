import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import main
from fastapi import HTTPException


PMTILES_BYTES = b"PMTiles" + (b"\x00" * 120)
FEATURES_BYTES = b"SQLite format 3\x00features"
SEARCH_BYTES = b"SQLite format 3\x00search"


class JsonRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class StreamRequest:
    def __init__(self, data, split_at=None):
        self.data = data
        self.split_at = split_at

    async def stream(self):
        if self.split_at:
            yield self.data[: self.split_at]
            yield self.data[self.split_at :]
        else:
            yield self.data


def declarations(files):
    return [
        {"filename": filename, "size_bytes": len(data)}
        for filename, data in files.items()
    ]


class VolumeUploadVersionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.root_patch = mock.patch.object(main, "ACTIVE_VOLUME_ROOT", self.root)
        self.root_patch.start()
        self.state_slug = "nordrhein-westfalen"

    def tearDown(self):
        self.root_patch.stop()
        self.tmp.cleanup()

    def write_version(self, version, files=None):
        version_dir = self.root / "versions" / self.state_slug / version
        version_dir.mkdir(parents=True)
        for filename, data in (files or {
            "alkis.pmtiles": PMTILES_BYTES,
            "features.sqlite": FEATURES_BYTES,
            "search.sqlite": SEARCH_BYTES,
        }).items():
            (version_dir / filename).write_bytes(data)
        return version_dir

    async def create_session(self, version, files, base_version=None):
        payload = {"files": declarations(files)}
        if base_version is not None:
            payload["base_version"] = base_version
        return await main.create_volume_upload_session(
            state_slug=self.state_slug,
            _="admin",
            request=JsonRequest(payload),
            version=version,
            bundesland="Nordrhein-Westfalen",
            base_version=None,
        )

    async def upload_files(self, version, files):
        for filename, data in files.items():
            await main.upload_volume_part(
                request=StreamRequest(data, split_at=max(1, len(data) // 2)),
                state_slug=self.state_slug,
                _="admin",
                version=version,
                filename=filename,
                start=0,
                end=len(data),
                total_size=len(data),
            )

    async def complete(self, version, files, base_version=None):
        payload = {"files": declarations(files)}
        if base_version is not None:
            payload["base_version"] = base_version
        return await main.complete_volume_upload(
            state_slug=self.state_slug,
            _="admin",
            request=JsonRequest(payload),
            version=version,
            bundesland="Nordrhein-Westfalen",
            base_version=None,
        )

    async def test_pmtiles_only_upload_inherits_databases_and_records_provenance(self):
        base_dir = self.write_version("base-v1")
        replacement = {"alkis.pmtiles": PMTILES_BYTES + b"new"}

        session = await self.create_session("pmtiles-v2", replacement, base_version="base-v1")
        self.assertEqual(session["mode"], "partial")
        self.assertEqual(session["base_version"], "base-v1")
        self.assertEqual([item["filename"] for item in session["files"]], ["alkis.pmtiles"])

        sessions = main.list_volume_upload_sessions(self.state_slug, "admin")["sessions"]
        self.assertEqual(sessions[0]["mode"], "partial")
        self.assertEqual(sessions[0]["base_version"], "base-v1")
        self.assertEqual(sessions[0]["expected_sizes"], {"alkis.pmtiles": len(replacement["alkis.pmtiles"])})

        await self.upload_files("pmtiles-v2", replacement)
        result = await self.complete("pmtiles-v2", replacement, base_version="base-v1")

        version_dir = self.root / "versions" / self.state_slug / "pmtiles-v2"
        self.assertEqual((version_dir / "alkis.pmtiles").read_bytes(), replacement["alkis.pmtiles"])
        self.assertEqual((version_dir / "features.sqlite").read_bytes(), FEATURES_BYTES)
        self.assertEqual((version_dir / "search.sqlite").read_bytes(), SEARCH_BYTES)
        self.assertTrue((version_dir / "features.sqlite").samefile(base_dir / "features.sqlite"))
        self.assertTrue((version_dir / "search.sqlite").samefile(base_dir / "search.sqlite"))
        self.assertFalse((version_dir / main.VOLUME_UPLOAD_SESSION_MANIFEST).exists())
        self.assertEqual(result["uploaded_files"], ["alkis.pmtiles"])
        self.assertEqual(result["inherited_files"], ["features.sqlite", "search.sqlite"])

        manifest = json.loads((version_dir / "state_upload_manifest.json").read_text())
        self.assertEqual(manifest["mode"], "partial")
        self.assertEqual(manifest["base_version"], "base-v1")
        inherited = {item["filename"]: item for item in manifest["file_details"] if item["source"] == "inherited"}
        self.assertEqual(set(inherited), {"features.sqlite", "search.sqlite"})
        self.assertTrue(all(item["source_version"] == "base-v1" for item in inherited.values()))
        self.assertTrue(all(item["storage"] == "hardlink" for item in inherited.values()))

        version_files = main.list_volume_version_files(self.state_slug, "admin", "pmtiles-v2")
        self.assertTrue(version_files["complete"])
        self.assertTrue(all(item["present"] for item in version_files["files"]))

    async def test_full_three_file_upload_remains_compatible(self):
        files = {
            "alkis.pmtiles": PMTILES_BYTES,
            "features.sqlite": FEATURES_BYTES,
            "search.sqlite": SEARCH_BYTES,
        }
        session = await self.create_session("full-v1", files)
        self.assertEqual(session["mode"], "full")
        self.assertIsNone(session["base_version"])

        await self.upload_files("full-v1", files)
        result = await self.complete("full-v1", files)
        self.assertEqual(result["mode"], "full")
        self.assertEqual(result["uploaded_files"], sorted(files))
        self.assertEqual(result["inherited_files"], [])
        version_dir = self.root / "versions" / self.state_slug / "full-v1"
        for filename, data in files.items():
            self.assertEqual((version_dir / filename).read_bytes(), data)

    async def test_session_rejects_name_size_and_completion_mismatches(self):
        self.write_version("base-v1")
        replacement = {"alkis.pmtiles": PMTILES_BYTES + b"new"}
        await self.create_session("mismatch-v2", replacement, base_version="base-v1")

        with self.assertRaises(HTTPException) as size_error:
            await main.upload_volume_part(
                request=StreamRequest(replacement["alkis.pmtiles"]),
                state_slug=self.state_slug,
                _="admin",
                version="mismatch-v2",
                filename="alkis.pmtiles",
                start=0,
                end=len(replacement["alkis.pmtiles"]),
                total_size=len(replacement["alkis.pmtiles"]) + 1,
            )
        self.assertEqual(size_error.exception.status_code, 409)

        with self.assertRaises(HTTPException) as name_error:
            await main.upload_volume_part(
                request=StreamRequest(FEATURES_BYTES),
                state_slug=self.state_slug,
                _="admin",
                version="mismatch-v2",
                filename="features.sqlite",
                start=0,
                end=len(FEATURES_BYTES),
                total_size=len(FEATURES_BYTES),
            )
        self.assertEqual(name_error.exception.status_code, 409)

        wrong_files = {"alkis.pmtiles": replacement["alkis.pmtiles"] + b"wrong"}
        with self.assertRaises(HTTPException) as complete_error:
            await self.complete("mismatch-v2", wrong_files, base_version="base-v1")
        self.assertEqual(complete_error.exception.status_code, 409)

    async def test_partial_session_rejects_incomplete_base_version(self):
        self.write_version("legacy-v1", {
            "alkis.pmtiles": PMTILES_BYTES,
            "features.sqlite": FEATURES_BYTES,
        })
        with self.assertRaises(HTTPException) as error:
            await self.create_session(
                "partial-v2",
                {"alkis.pmtiles": PMTILES_BYTES + b"new"},
                base_version="legacy-v1",
            )
        self.assertEqual(error.exception.status_code, 400)
        self.assertIn("full upload", str(error.exception.detail))

    async def test_existing_target_is_never_overwritten(self):
        existing = self.write_version("existing-v1")
        sentinel = existing / "sentinel"
        sentinel.write_text("keep")
        full_files = {
            "alkis.pmtiles": PMTILES_BYTES,
            "features.sqlite": FEATURES_BYTES,
            "search.sqlite": SEARCH_BYTES,
        }
        with self.assertRaises(HTTPException) as create_error:
            await self.create_session("existing-v1", full_files)
        self.assertEqual(create_error.exception.status_code, 409)
        self.assertEqual(sentinel.read_text(), "keep")

        await self.create_session("race-v1", full_files)
        await self.upload_files("race-v1", full_files)
        race_target = self.write_version("race-v1")
        (race_target / "sentinel").write_text("keep")
        with self.assertRaises(HTTPException) as complete_error:
            await self.complete("race-v1", full_files)
        self.assertEqual(complete_error.exception.status_code, 409)
        self.assertEqual((race_target / "sentinel").read_text(), "keep")
        self.assertTrue((self.root / ".incoming" / self.state_slug / "race-v1").is_dir())

    async def test_delete_removes_only_the_incoming_session(self):
        self.write_version("base-v1")
        replacement = {"alkis.pmtiles": PMTILES_BYTES + b"new"}
        await self.create_session("delete-v2", replacement, base_version="base-v1")
        upload_dir = self.root / ".incoming" / self.state_slug / "delete-v2"
        self.assertTrue(upload_dir.is_dir())

        result = main.delete_volume_upload_session(self.state_slug, "admin", "delete-v2")
        self.assertTrue(result["deleted"])
        self.assertFalse(upload_dir.exists())
        self.assertTrue((self.root / "versions" / self.state_slug / "base-v1").is_dir())

        with self.assertRaises(HTTPException) as missing_error:
            main.delete_volume_upload_session(self.state_slug, "admin", "delete-v2")
        self.assertEqual(missing_error.exception.status_code, 404)

    def test_inherit_falls_back_to_copy_when_hardlink_is_unavailable(self):
        source = self.root / "source.sqlite"
        destination = self.root / "destination.sqlite"
        source.write_bytes(FEATURES_BYTES)
        with mock.patch.object(main.os, "link", side_effect=OSError("cross-device")):
            storage = main._inherit_volume_file(source, destination)
        self.assertEqual(storage, "copy")
        self.assertEqual(destination.read_bytes(), FEATURES_BYTES)
        self.assertFalse(destination.samefile(source))


if __name__ == "__main__":
    unittest.main()
