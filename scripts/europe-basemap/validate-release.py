#!/usr/bin/env python3
"""Create and validate schema-1 manifests for the Europe PMTiles runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


VERSION_RE = re.compile(
    r"^europe(?P<regional>-de-at)?-(?P<build_date>20\d{6})-z15$"
)
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
DE_AT_COVERAGE_PROFILE = "de-at-buffer-v1"
DE_AT_BOUNDS = (5.0, 45.5, 18.0, 55.75)
LEGACY_COVERAGE_PROFILE = "legacy-europe-v1"
LEGACY_BOUNDS = (-25.0, 34.0, 45.0, 72.0)
BOUNDS_TOLERANCE = 1e-6
EXPECTED_MIN_ZOOM = 0
EXPECTED_MAX_ZOOM = 15
EXPECTED_VECTOR_LAYERS = {
    "boundaries",
    "buildings",
    "earth",
    "landcover",
    "landuse",
    "places",
    "pois",
    "roads",
    "water",
}
ATTRIBUTION = (
    "© OpenStreetMap contributors · "
    "© ESA WorldCover project 2020 / Contains modified Copernicus Sentinel "
    "data (2020) processed by ESA WorldCover consortium"
)
DATA_LICENSES = [
    {
        "id": "openstreetmap",
        "license": "ODbL-1.0",
        "url": "https://www.openstreetmap.org/copyright",
        "attribution": "© OpenStreetMap contributors",
    },
    {
        "id": "esa-worldcover-2020",
        "license": "CC-BY-4.0",
        "url": "https://esa-worldcover.org/",
        "attribution": (
            "© ESA WorldCover project 2020 / Contains modified Copernicus "
            "Sentinel data (2020) processed by ESA WorldCover consortium"
        ),
        "via": "Daylight Landcover / Overture Maps",
    },
]


class ValidationError(ValueError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"not a regular JSON file: {path}")
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ValidationError(f"JSON file is unexpectedly large: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"JSON root must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file(path: Path, *, nonempty: bool = True) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"not a regular file: {path}")
    if nonempty and path.stat().st_size <= 0:
        raise ValidationError(f"empty file: {path}")


def _validate_version(version: str) -> str:
    return _version_contract(version)[0]


def _version_contract(
    version: str,
) -> tuple[str, str, tuple[float, float, float, float]]:
    match = VERSION_RE.fullmatch(version)
    if match is None:
        raise ValidationError(f"invalid version: {version}")
    if match.group("regional"):
        return (
            match.group("build_date"),
            DE_AT_COVERAGE_PROFILE,
            DE_AT_BOUNDS,
        )
    return (
        match.group("build_date"),
        LEGACY_COVERAGE_PROFILE,
        LEGACY_BOUNDS,
    )


def _number(value: object, *, label: str) -> float:
    if isinstance(value, bool):
        raise ValidationError(f"{label} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label} must be numeric") from exc
    if not (-1e12 < number < 1e12):
        raise ValidationError(f"{label} is outside the accepted range")
    return number


def _validated_archive(
    header: dict[str, Any],
    metadata: dict[str, Any],
    *,
    expected_bounds: tuple[float, float, float, float],
    expected_build_date: str | None = None,
) -> tuple[list[float], str]:
    if str(header.get("tile_type", "")).lower() != "mvt":
        raise ValidationError("PMTiles tile_type must be mvt")
    if str(header.get("tile_compression", "")).lower() != "gzip":
        raise ValidationError("PMTiles tile_compression must be gzip")
    if int(header.get("minzoom", -1)) != EXPECTED_MIN_ZOOM:
        raise ValidationError("PMTiles minzoom must be 0")
    if int(header.get("maxzoom", -1)) != EXPECTED_MAX_ZOOM:
        raise ValidationError("PMTiles maxzoom must be 15")

    raw_bounds = header.get("bounds")
    if not isinstance(raw_bounds, list) or len(raw_bounds) != 4:
        raise ValidationError("PMTiles header must contain four bounds")
    bounds = [_number(value, label=f"bounds[{index}]") for index, value in enumerate(raw_bounds)]
    if any(
        abs(actual - expected) > BOUNDS_TOLERANCE
        for actual, expected in zip(bounds, expected_bounds)
    ):
        raise ValidationError(
            f"PMTiles bounds {bounds!r} differ from the configured exact "
            f"release bbox {expected_bounds!r}"
        )

    metadata_name = str(metadata.get("name", ""))
    metadata_version = str(metadata.get("version", ""))
    if "protomaps" not in metadata_name.lower():
        raise ValidationError("PMTiles metadata is not a Protomaps Basemap")
    if not metadata_version.startswith("4."):
        raise ValidationError(
            f"unsupported Protomaps schema version: {metadata_version!r}"
        )
    raw_layers = metadata.get("vector_layers")
    if not isinstance(raw_layers, list):
        raise ValidationError("PMTiles metadata has no vector_layers list")
    layer_ids = {
        str(layer.get("id"))
        for layer in raw_layers
        if isinstance(layer, dict) and layer.get("id")
    }
    missing = sorted(EXPECTED_VECTOR_LAYERS - layer_ids)
    unexpected = sorted(layer_ids - EXPECTED_VECTOR_LAYERS)
    if missing or unexpected:
        raise ValidationError(
            "PMTiles vector-layer inventory changed; a source/license review "
            f"is required (missing={missing!r}, unexpected={unexpected!r})"
        )
    metadata_attribution = str(metadata.get("attribution", ""))
    if "openstreetmap" not in metadata_attribution.lower():
        raise ValidationError("PMTiles metadata has no OpenStreetMap attribution")
    if expected_build_date is not None:
        replication_time = str(
            metadata.get("planetiler:osm:osmosisreplicationtime", "")
        )
        expected_iso_date = (
            f"{expected_build_date[0:4]}-"
            f"{expected_build_date[4:6]}-"
            f"{expected_build_date[6:8]}"
        )
        if not replication_time.startswith(expected_iso_date):
            raise ValidationError(
                "PMTiles OSM replication date does not match the pinned daily build: "
                f"{replication_time!r} != {expected_iso_date!r}"
            )
    return bounds, metadata_version


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, path)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def create_manifest(args: argparse.Namespace) -> None:
    pmtiles = args.pmtiles.resolve(strict=True)
    _regular_file(pmtiles)
    build_date, coverage_profile, expected_bounds = _version_contract(args.version)
    if args.build_date != build_date:
        raise ValidationError(
            f"version build date {build_date} does not match {args.build_date}"
        )
    expected_source_url = (
        f"https://build.protomaps.com/{args.build_date}.pmtiles"
    )
    if args.source_url != expected_source_url:
        raise ValidationError(
            f"source URL is not pinned to the build date: {args.source_url!r}"
        )
    if args.pmtiles_cli_version != "1.31.2":
        raise ValidationError("pmtiles CLI version must be 1.31.2")
    if args.pmtiles_cli_sha256 != (
        "a7e9ae10184d109c83f456ccdf6df4f3e2a64ba6cf69d9ed0f9f1840305055c1"
    ):
        raise ValidationError("unexpected pmtiles CLI SHA-256")
    header = _load_json(args.header_json)
    metadata = _load_json(args.metadata_json)
    bounds, schema_version = _validated_archive(
        header,
        metadata,
        expected_bounds=expected_bounds,
        expected_build_date=args.build_date,
    )
    size_bytes = pmtiles.stat().st_size
    manifest = {
        "schema_version": 1,
        "version": args.version,
        "pmtiles": "basemap.pmtiles",
        "sha256": _sha256(pmtiles),
        "size_bytes": size_bytes,
        "minzoom": EXPECTED_MIN_ZOOM,
        "maxzoom": EXPECTED_MAX_ZOOM,
        "bounds": list(expected_bounds),
        "attribution": ATTRIBUTION,
        "source": f"Protomaps Basemap v4 daily build {args.build_date}",
        "provenance": {
            "build_date": args.build_date,
            "coverage_profile": coverage_profile,
            "source_url": args.source_url,
            "source_schema": schema_version,
            "extract_bbox": list(expected_bounds),
            "extract_minzoom": EXPECTED_MIN_ZOOM,
            "extract_maxzoom": EXPECTED_MAX_ZOOM,
            "pmtiles_cli_version": args.pmtiles_cli_version,
            "pmtiles_cli_sha256": args.pmtiles_cli_sha256,
            "vector_layers": sorted(EXPECTED_VECTOR_LAYERS),
            "data_licenses": DATA_LICENSES,
        },
    }
    _atomic_json_write(args.manifest, manifest)
    print(
        json.dumps(
            {
                "version": args.version,
                "sha256": manifest["sha256"],
                "size_bytes": size_bytes,
                "manifest": str(args.manifest),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def _validated_manifest(release_dir: Path, *, skip_hash: bool) -> dict[str, Any]:
    if release_dir.is_symlink() or not release_dir.is_dir():
        raise ValidationError(f"release directory is not a regular directory: {release_dir}")
    entries = {entry.name for entry in release_dir.iterdir()}
    expected_entries = {"basemap.pmtiles", "manifest.json"}
    if entries != expected_entries:
        raise ValidationError(
            "release directory inventory differs from "
            f"{sorted(expected_entries)!r}: {sorted(entries)!r}"
        )
    manifest_path = release_dir / "manifest.json"
    pmtiles_path = release_dir / "basemap.pmtiles"
    manifest = _load_json(manifest_path)
    _regular_file(pmtiles_path)
    build_date, coverage_profile, expected_bounds = _version_contract(
        release_dir.name
    )
    if manifest.get("schema_version") != 1:
        raise ValidationError("manifest schema_version must be 1")
    if manifest.get("version") != release_dir.name:
        raise ValidationError("manifest version does not match directory name")
    if manifest.get("pmtiles") != "basemap.pmtiles":
        raise ValidationError("manifest pmtiles must be basemap.pmtiles")
    digest = str(manifest.get("sha256", "")).lower()
    if SHA256_RE.fullmatch(digest) is None:
        raise ValidationError("manifest contains an invalid SHA-256")
    size_bytes = int(manifest.get("size_bytes", 0))
    if size_bytes <= 0 or size_bytes != pmtiles_path.stat().st_size:
        raise ValidationError("manifest size_bytes does not match basemap.pmtiles")
    if not skip_hash and _sha256(pmtiles_path) != digest:
        raise ValidationError("manifest SHA-256 does not match basemap.pmtiles")
    if manifest.get("minzoom") != EXPECTED_MIN_ZOOM:
        raise ValidationError("manifest minzoom must be 0")
    if manifest.get("maxzoom") != EXPECTED_MAX_ZOOM:
        raise ValidationError("manifest maxzoom must be 15")
    raw_bounds = manifest.get("bounds")
    if not isinstance(raw_bounds, list) or len(raw_bounds) != 4:
        raise ValidationError("manifest bounds must contain four numbers")
    bounds = tuple(
        _number(value, label=f"manifest bounds[{index}]")
        for index, value in enumerate(raw_bounds)
    )
    if any(
        abs(actual - expected) > BOUNDS_TOLERANCE
        for actual, expected in zip(bounds, expected_bounds)
    ):
        raise ValidationError(f"manifest bounds differ from {expected_bounds!r}")
    if manifest.get("attribution") != ATTRIBUTION:
        raise ValidationError(
            "manifest attribution must name OpenStreetMap and ESA WorldCover"
        )
    if not str(manifest.get("source", "")).startswith("Protomaps Basemap v4"):
        raise ValidationError("manifest source must identify Protomaps Basemap v4")
    provenance = manifest.get("provenance")
    if not isinstance(provenance, dict):
        raise ValidationError("manifest provenance is missing")
    if provenance.get("pmtiles_cli_version") != "1.31.2":
        raise ValidationError("manifest was not built with pmtiles CLI 1.31.2")
    if provenance.get("pmtiles_cli_sha256") != (
        "a7e9ae10184d109c83f456ccdf6df4f3e2a64ba6cf69d9ed0f9f1840305055c1"
    ):
        raise ValidationError("manifest contains an unexpected pmtiles CLI hash")
    if provenance.get("vector_layers") != sorted(EXPECTED_VECTOR_LAYERS):
        raise ValidationError(
            "manifest vector-layer inventory requires a source/license review"
        )
    if provenance.get("data_licenses") != DATA_LICENSES:
        raise ValidationError("manifest data-license inventory is incomplete")
    if provenance.get("build_date") != build_date:
        raise ValidationError("manifest provenance build_date differs from version")
    if provenance.get("source_url") != (
        f"https://build.protomaps.com/{build_date}.pmtiles"
    ):
        raise ValidationError("manifest provenance source_url is not pinned")
    manifest_profile = provenance.get("coverage_profile")
    if coverage_profile == LEGACY_COVERAGE_PROFILE:
        if manifest_profile not in (None, LEGACY_COVERAGE_PROFILE):
            raise ValidationError("legacy manifest coverage_profile is unexpected")
    elif manifest_profile != coverage_profile:
        raise ValidationError("regional manifest coverage_profile is missing or unexpected")
    if provenance.get("extract_bbox") != list(expected_bounds):
        raise ValidationError("manifest provenance extract_bbox is unexpected")
    if provenance.get("extract_minzoom") != EXPECTED_MIN_ZOOM:
        raise ValidationError("manifest provenance extract_minzoom is unexpected")
    if provenance.get("extract_maxzoom") != EXPECTED_MAX_ZOOM:
        raise ValidationError("manifest provenance extract_maxzoom is unexpected")
    if not str(provenance.get("source_schema", "")).startswith("4."):
        raise ValidationError("manifest provenance source_schema is not Protomaps v4")
    return manifest


def check_release(args: argparse.Namespace) -> None:
    release_dir = args.release_dir.resolve(strict=True)
    manifest = _validated_manifest(release_dir, skip_hash=args.skip_hash)
    print(
        json.dumps(
            {
                "version": manifest["version"],
                "sha256": manifest["sha256"],
                "size_bytes": manifest["size_bytes"],
                "hash_checked": not args.skip_hash,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def inspect_release(args: argparse.Namespace) -> None:
    release_dir = args.release_dir.resolve(strict=True)
    manifest = _validated_manifest(release_dir, skip_hash=args.skip_hash)
    header = _load_json(args.header_json)
    metadata = _load_json(args.metadata_json)
    build_date, _coverage_profile, expected_bounds = _version_contract(
        release_dir.name
    )
    bounds, schema_version = _validated_archive(
        header,
        metadata,
        expected_bounds=expected_bounds,
        expected_build_date=build_date,
    )
    if any(
        abs(float(actual) - float(expected)) > 0.02
        for actual, expected in zip(bounds, manifest["bounds"])
    ):
        raise ValidationError("PMTiles header bounds differ from manifest")
    provenance = manifest["provenance"]
    if provenance.get("source_schema") != schema_version:
        raise ValidationError("PMTiles metadata schema differs from manifest")
    print(
        json.dumps(
            {
                "version": manifest["version"],
                "source_schema": schema_version,
                "archive_checked": True,
                "hash_checked": not args.skip_hash,
            },
            sort_keys=True,
        )
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    subparsers = root.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="validate an archive and write manifest schema 1")
    create.add_argument("--pmtiles", required=True, type=Path)
    create.add_argument("--header-json", required=True, type=Path)
    create.add_argument("--metadata-json", required=True, type=Path)
    create.add_argument("--manifest", required=True, type=Path)
    create.add_argument("--version", required=True)
    create.add_argument("--build-date", required=True)
    create.add_argument("--source-url", required=True)
    create.add_argument("--pmtiles-cli-version", required=True)
    create.add_argument("--pmtiles-cli-sha256", required=True)
    create.set_defaults(handler=create_manifest)

    check = subparsers.add_parser("check", help="validate a published release and its manifest")
    check.add_argument("--release-dir", required=True, type=Path)
    check.add_argument("--skip-hash", action="store_true")
    check.set_defaults(handler=check_release)

    inspect = subparsers.add_parser(
        "inspect",
        help="validate a published release against pmtiles show output",
    )
    inspect.add_argument("--release-dir", required=True, type=Path)
    inspect.add_argument("--header-json", required=True, type=Path)
    inspect.add_argument("--metadata-json", required=True, type=Path)
    inspect.add_argument("--skip-hash", action="store_true")
    inspect.set_defaults(handler=inspect_release)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        args.handler(args)
    except (OSError, ValidationError, ValueError, TypeError) as exc:
        print(f"validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
