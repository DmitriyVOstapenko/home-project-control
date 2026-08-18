#!/usr/bin/env python3
"""Index local project documents without modifying source files."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from inspect_project import is_linklike, require_ready_project


NAMESPACE = uuid.UUID("7c7619f5-b284-42be-b0f1-0cf6f504c87a")
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
STRUCTURE = json.loads((PLUGIN_ROOT / "schemas" / "project-structure.json").read_text(encoding="utf-8"))
DOCUMENT_SCHEMA_VERSION = STRUCTURE["json_files"][".home-control/documents.json"]["schema_version"]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def validate_project(path: Path) -> Path:
    return require_ready_project(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan(root: Path) -> list[dict]:
    results = []
    for path in sorted(root.rglob("*")):
        if is_linklike(path) or not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] == ".home-control":
            continue
        resolved = path.resolve()
        if resolved != root and root not in resolved.parents:
            continue
        stat = path.stat()
        rel_text = relative.as_posix()
        results.append({
            "relative_path": rel_text,
            "filename": path.name,
            "extension": path.suffix.lower(),
            "size_bytes": stat.st_size,
            "modified_at_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).replace(microsecond=0).isoformat(),
            "sha256": sha256_file(path),
        })
    return results


def merge(previous: dict, scanned: list[dict], indexed_at: str) -> dict:
    if not isinstance(previous, dict) or not isinstance(previous.get("items"), list):
        raise ValueError("documents.json must be an object with an items array")
    old_by_path: dict[str, dict] = {}
    document_ids: set[str] = set()
    for item in previous["items"]:
        if not isinstance(item, dict):
            raise ValueError("Every documents.json item must be an object")
        relative_path = item.get("relative_path")
        document_id = item.get("document_id")
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ValueError("Every documents.json item must have a non-empty relative_path")
        if not isinstance(document_id, str) or not document_id.strip():
            raise ValueError(f"Document {relative_path} must have a non-empty document_id")
        normalized_document_id = document_id.strip()
        if relative_path in old_by_path:
            raise ValueError(f"Duplicate document relative_path: {relative_path}")
        if normalized_document_id in document_ids:
            raise ValueError(f"Duplicate document_id: {normalized_document_id}")
        raw_versions = item.get("versions", [])
        if not isinstance(raw_versions, list) or any(
            not isinstance(version, dict)
            or not isinstance(version.get("version"), int)
            or version["version"] < 1
            for version in raw_versions
        ):
            raise ValueError(f"Invalid version history for document path: {relative_path}")
        version_numbers = [version["version"] for version in raw_versions]
        if len(version_numbers) != len(set(version_numbers)):
            raise ValueError(f"Duplicate version number for document path: {relative_path}")
        document_ids.add(normalized_document_id)
        old_by_path[relative_path] = item
    merged = []
    seen = set()
    for current in scanned:
        rel = current["relative_path"]
        seen.add(rel)
        old = old_by_path.get(rel)
        if old is None:
            record = {
                "document_id": str(uuid.uuid5(NAMESPACE, "local:" + rel)),
                "provider": "local",
                "status": "active",
                "first_indexed_at_utc": indexed_at,
                "last_indexed_at_utc": indexed_at,
                "versions": [{"version": 1, **current, "indexed_at_utc": indexed_at}],
                **current,
            }
        else:
            record = dict(old)
            record.update(current)
            record["status"] = "active"
            record["last_indexed_at_utc"] = indexed_at
            raw_versions = record.get("versions", [])
            versions = list(raw_versions)
            if old.get("sha256") != current["sha256"]:
                next_version = max((version["version"] for version in versions), default=0) + 1
                versions.append({"version": next_version, **current, "indexed_at_utc": indexed_at})
            record["versions"] = versions
        merged.append(record)
    for rel, old in old_by_path.items():
        if rel not in seen:
            record = dict(old)
            record["status"] = "missing"
            record["last_indexed_at_utc"] = indexed_at
            merged.append(record)
    merged.sort(key=lambda item: item["relative_path"].casefold())
    result = dict(previous)
    result.update(
        {"schema_version": DOCUMENT_SCHEMA_VERSION, "indexed_at_utc": indexed_at, "items": merged}
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = validate_project(args.project_dir)
    registry_path = root / ".home-control" / "documents.json"
    previous = json.loads(registry_path.read_text(encoding="utf-8"))
    indexed_at = utc_now()
    updated = merge(previous, scan(root), indexed_at)
    active = sum(1 for item in updated["items"] if item["status"] == "active")
    missing = sum(1 for item in updated["items"] if item["status"] == "missing")
    if not args.dry_run:
        temp_path = registry_path.with_name(f".{registry_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temp_path.replace(registry_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
    prefix = "Would index" if args.dry_run else "Indexed"
    print(f"{prefix}: {active} active, {missing} missing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
