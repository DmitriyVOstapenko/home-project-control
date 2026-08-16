#!/usr/bin/env python3
"""Index local project documents without modifying source files."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path


NAMESPACE = uuid.UUID("7c7619f5-b284-42be-b0f1-0cf6f504c87a")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def validate_project(path: Path) -> Path:
    root = path.expanduser().resolve()
    if root == Path(root.anchor) or root == Path.home().resolve():
        raise ValueError("Unsafe project directory")
    registry = root / ".home-control" / "documents.json"
    if not registry.is_file():
        raise FileNotFoundError(f"Project registry not found: {registry}")
    return root


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan(root: Path) -> list[dict]:
    results = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] == ".home-control":
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
    old_by_path = {item["relative_path"]: item for item in previous.get("items", [])}
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
            versions = list(record.get("versions", []))
            if old.get("sha256") != current["sha256"]:
                versions.append({"version": len(versions) + 1, **current, "indexed_at_utc": indexed_at})
            record["versions"] = versions
        merged.append(record)
    for rel, old in old_by_path.items():
        if rel not in seen:
            record = dict(old)
            record["status"] = "missing"
            record["last_indexed_at_utc"] = indexed_at
            merged.append(record)
    merged.sort(key=lambda item: item["relative_path"].casefold())
    return {"schema_version": "1.0", "indexed_at_utc": indexed_at, "items": merged}


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
        temp_path = registry_path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(registry_path)
    prefix = "Would index" if args.dry_run else "Indexed"
    print(f"{prefix}: {active} active, {missing} missing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
