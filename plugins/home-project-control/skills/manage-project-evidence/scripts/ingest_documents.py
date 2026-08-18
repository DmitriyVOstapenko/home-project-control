#!/usr/bin/env python3
"""Preview or apply safe copying of external documents into a prepared project."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path

from index_documents import merge, scan, sha256_file, utc_now
from inspect_project import is_linklike, require_ready_project


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
STRUCTURE = json.loads((PLUGIN_ROOT / "schemas" / "project-structure.json").read_text(encoding="utf-8"))
PROJECT_FOLDERS = tuple(STRUCTURE["folders"])
INTAKE_NAMESPACE = uuid.UUID("cd48ba4a-7f54-47c2-b8b1-39a093dfa43d")


def is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def validate_target_name(value: str) -> str:
    name = value.strip()
    if not name or name in {".", ".."} or Path(name).name != name:
        raise ValueError("--target-name must be a single non-empty file name")
    return name


def build_plan(
    root: Path,
    source_values: list[Path],
    target_folder: str,
    description: str,
    target_name: str | None,
) -> dict[str, object]:
    if target_folder not in PROJECT_FOLDERS:
        raise ValueError(
            "--target-folder must exactly match a managed project folder: "
            + ", ".join(PROJECT_FOLDERS)
        )
    if not description.strip():
        raise ValueError("--description must not be empty")
    if target_name is not None and len(source_values) != 1:
        raise ValueError("--target-name can only be used with one source file")

    target_dir = root / target_folder
    if is_linklike(target_dir) or not target_dir.is_dir():
        raise ValueError(f"Unsafe or missing target folder: {target_folder}")
    resolved_target_dir = target_dir.resolve()
    if not is_within(resolved_target_dir, root):
        raise ValueError(f"Target folder escapes the project: {target_folder}")

    items: list[dict[str, str]] = []
    destinations: set[Path] = set()
    for source_value in source_values:
        candidate = source_value.expanduser()
        if is_linklike(candidate):
            raise ValueError(f"Symbolic links and junctions are not accepted as sources: {candidate}")
        try:
            source = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"Source cannot be resolved: {candidate}: {exc}") from exc
        if not source.is_file():
            raise ValueError(f"Source is not a regular file: {source}")

        name = validate_target_name(target_name) if target_name is not None else source.name
        destination = resolved_target_dir / name
        if destination in destinations:
            raise ValueError(f"Several source files resolve to the same destination: {destination}")
        destinations.add(destination)
        if not is_within(destination, root):
            raise ValueError(f"Destination escapes the project: {destination}")

        source_hash = sha256_file(source)
        if source == destination:
            action = "already_in_place"
        elif is_within(source, root):
            raise ValueError(
                f"Source is already inside this project at {source.relative_to(root)}; "
                "relocating an existing project document requires a separate explicit instruction"
            )
        elif destination.exists() or is_linklike(destination):
            if is_linklike(destination) or not destination.is_file():
                raise ValueError(f"Unsafe destination conflict: {destination}")
            if sha256_file(destination) != source_hash:
                raise ValueError(
                    f"Destination already contains a different file: {destination}. "
                    "Choose an explicit --target-name after confirming it with the user"
                )
            action = "use_existing_identical"
        else:
            action = "copy"

        items.append(
            {
                "source": str(source),
                "source_filename": source.name,
                "source_sha256": source_hash,
                "destination": str(destination),
                "relative_path": destination.relative_to(root).as_posix(),
                "action": action,
            }
        )

    return {
        "project_root": str(root),
        "target_folder": target_folder,
        "description": description.strip(),
        "items": items,
    }


def copy_new_file(source: Path, destination: Path) -> None:
    created = False
    try:
        with source.open("rb") as source_handle, destination.open("xb") as destination_handle:
            created = True
            shutil.copyfileobj(source_handle, destination_handle, length=1024 * 1024)
        shutil.copystat(source, destination)
    except Exception:
        if created and destination.exists() and destination.is_file() and not is_linklike(destination):
            destination.unlink()
        raise


def add_intake_contexts(
    registry: dict[str, object], plan: dict[str, object], recorded_at: str
) -> None:
    raw_items = registry.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("documents.json must contain an items array")
    documents_by_path = {
        item.get("relative_path"): item for item in raw_items if isinstance(item, dict)
    }
    for planned in plan["items"]:
        if not isinstance(planned, dict):
            raise ValueError("Invalid intake plan item")
        relative_path = planned["relative_path"]
        document = documents_by_path.get(relative_path)
        if not isinstance(document, dict):
            raise ValueError(f"Indexed document is missing after intake: {relative_path}")
        document_id = document.get("document_id")
        if not isinstance(document_id, str) or not document_id:
            raise ValueError(f"Indexed document has no document_id: {relative_path}")

        signature = "\0".join(
            (
                document_id,
                str(planned["source_filename"]),
                str(planned["source_sha256"]),
                str(plan["description"]),
            )
        )
        intake_id = str(uuid.uuid5(INTAKE_NAMESPACE, signature))
        contexts = document.setdefault("intake_contexts", [])
        if not isinstance(contexts, list) or any(not isinstance(context, dict) for context in contexts):
            raise ValueError(f"Invalid intake_contexts for document: {relative_path}")
        if any(context.get("intake_id") == intake_id for context in contexts):
            continue
        contexts.append(
            {
                "intake_id": intake_id,
                "recorded_at_utc": recorded_at,
                "source_filename": planned["source_filename"],
                "description": plan["description"],
                "declared_by": "user",
                "verification_status": "unreviewed",
            }
        )


def write_json_atomic(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def apply_plan(root: Path, plan: dict[str, object]) -> dict[str, object]:
    created: list[Path] = []
    try:
        for item in plan["items"]:
            if not isinstance(item, dict) or item.get("action") != "copy":
                continue
            source = Path(str(item["source"]))
            destination = Path(str(item["destination"]))
            copy_new_file(source, destination)
            created.append(destination)
            if sha256_file(destination) != item["source_sha256"]:
                raise OSError(f"Copied file failed checksum verification: {destination}")

        registry_path = root / ".home-control" / "documents.json"
        previous = json.loads(registry_path.read_text(encoding="utf-8"))
        recorded_at = utc_now()
        updated = merge(previous, scan(root), recorded_at)
        add_intake_contexts(updated, plan, recorded_at)
        write_json_atomic(registry_path, updated)
    except Exception:
        for destination in reversed(created):
            if destination.exists() and destination.is_file() and not is_linklike(destination):
                destination.unlink()
        raise

    result = dict(plan)
    result["mode"] = "applied"
    result["indexed"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--source", type=Path, action="append", required=True)
    parser.add_argument("--target-folder", required=True, choices=PROJECT_FOLDERS)
    parser.add_argument("--target-name")
    parser.add_argument("--description", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    root = require_ready_project(args.project_dir)
    plan = build_plan(root, args.source, args.target_folder, args.description, args.target_name)
    if args.apply:
        result = apply_plan(root, plan)
    else:
        result = dict(plan)
        result["mode"] = "preview"
        result["indexed"] = False
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Document intake failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
