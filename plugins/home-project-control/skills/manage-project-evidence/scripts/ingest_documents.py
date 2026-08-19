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
BATCH_NAMESPACE = uuid.UUID("9e72260e-6808-4fbf-a56c-474445ca32ae")
MANIFEST_SCHEMA_VERSION = "1.0"


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


def load_manifest(path: Path) -> dict[str, object]:
    if is_linklike(path) or not path.is_file():
        raise ValueError("Manifest path must be a regular non-linked JSON file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"Intake manifest schema_version must be {MANIFEST_SCHEMA_VERSION}")
    unknown = set(value) - {"schema_version", "batch_description", "items"}
    if unknown:
        raise ValueError("Unknown intake manifest fields: " + ", ".join(sorted(unknown)))
    items = value.get("items")
    if not isinstance(items, list) or not items or any(not isinstance(item, dict) for item in items):
        raise ValueError("Intake manifest items must be a non-empty array of objects")
    return value


def build_manifest_plan(root: Path, manifest: dict[str, object]) -> dict[str, object]:
    planned_items: list[dict[str, str]] = []
    destinations: set[str] = set()
    for number, raw_item in enumerate(manifest["items"], 1):
        if not isinstance(raw_item, dict):
            raise ValueError(f"Manifest item {number} must be an object")
        unknown = set(raw_item) - {"source", "target_folder", "description", "target_name"}
        if unknown:
            raise ValueError(
                f"Manifest item {number} has unknown fields: " + ", ".join(sorted(unknown))
            )
        source = raw_item.get("source")
        target_folder = raw_item.get("target_folder")
        description = raw_item.get("description")
        target_name = raw_item.get("target_name")
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"Manifest item {number} must have a non-empty source")
        if not isinstance(target_folder, str) or not target_folder.strip():
            raise ValueError(f"Manifest item {number} must have a non-empty target_folder")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"Manifest item {number} must have a non-empty description")
        if target_name is not None and (not isinstance(target_name, str) or not target_name.strip()):
            raise ValueError(f"Manifest item {number} target_name must be a non-empty string")
        item_plan = build_plan(
            root,
            [Path(source)],
            target_folder,
            description,
            target_name,
        )
        planned = dict(item_plan["items"][0])
        planned["target_folder"] = target_folder
        planned["description"] = description.strip()
        relative_path = planned["relative_path"]
        if relative_path in destinations:
            raise ValueError(f"Several manifest items resolve to the same destination: {relative_path}")
        destinations.add(relative_path)
        planned_items.append(planned)

    batch_description = manifest.get("batch_description", "")
    if not isinstance(batch_description, str):
        raise ValueError("batch_description must be a string")
    signature_items = [
        {
            "source_filename": item["source_filename"],
            "source_sha256": item["source_sha256"],
            "relative_path": item["relative_path"],
            "description": item["description"],
        }
        for item in planned_items
    ]
    signature = json.dumps(
        {"batch_description": batch_description.strip(), "items": signature_items},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "project_root": str(root),
        "intake_batch_id": "INT-" + str(uuid.uuid5(BATCH_NAMESPACE, signature)),
        "batch_description": batch_description.strip(),
        "items": planned_items,
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
                str(planned["description"]),
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
                "intake_batch_id": plan["intake_batch_id"],
                "recorded_at_utc": recorded_at,
                "source_filename": planned["source_filename"],
                "description": planned["description"],
                "declared_by": "user",
                "verification_status": "unreviewed",
            }
        )


def build_batch_record(
    registry: dict[str, object], plan: dict[str, object], recorded_at: str
) -> dict[str, object]:
    raw_items = registry.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("documents.json must contain an items array")
    documents_by_path = {
        item.get("relative_path"): item for item in raw_items if isinstance(item, dict)
    }
    recorded_items: list[dict[str, object]] = []
    for planned in plan["items"]:
        if not isinstance(planned, dict):
            raise ValueError("Invalid intake plan item")
        document = documents_by_path.get(planned["relative_path"])
        if not isinstance(document, dict):
            raise ValueError(f"Indexed document is missing after intake: {planned['relative_path']}")
        recorded_items.append(
            {
                "document_id": document["document_id"],
                "source_filename": planned["source_filename"],
                "source_sha256": planned["source_sha256"],
                "relative_path": planned["relative_path"],
                "action": planned["action"],
                "description": planned["description"],
                "verification_status": "unreviewed",
            }
        )
    return {
        "intake_batch_id": plan["intake_batch_id"],
        "applied_at_utc": recorded_at,
        "status": "applied",
        "batch_description": plan.get("batch_description", ""),
        "items": recorded_items,
    }


def replace_files_atomically(replacements: list[tuple[Path, str]]) -> None:
    staged: list[tuple[Path, Path, Path]] = []
    applied: list[tuple[Path, Path]] = []
    committed = False
    try:
        for target, content in replacements:
            if is_linklike(target) or not target.is_file():
                raise ValueError(f"Unsafe managed registry: {target.name}")
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            backup = target.with_name(f".{target.name}.{uuid.uuid4().hex}.bak")
            temporary.write_text(content, encoding="utf-8")
            staged.append((target, temporary, backup))
        for target, temporary, backup in staged:
            target.replace(backup)
            applied.append((target, backup))
            temporary.replace(target)
        committed = True
    except Exception:
        for target, backup in reversed(applied):
            if target.exists() and not is_linklike(target):
                target.unlink()
            if backup.exists() and not is_linklike(backup):
                backup.replace(target)
        raise
    finally:
        for _, temporary, backup in staged:
            if temporary.exists() and not is_linklike(temporary):
                temporary.unlink()
            if committed and backup.exists() and not is_linklike(backup):
                try:
                    backup.unlink()
                except OSError:
                    pass


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

        control = root / ".home-control"
        registry_path = control / "documents.json"
        batches_path = control / "document_intake_batches.jsonl"
        previous = json.loads(registry_path.read_text(encoding="utf-8"))
        recorded_at = utc_now()
        updated = merge(previous, scan(root), recorded_at)
        add_intake_contexts(updated, plan, recorded_at)
        batch_record = build_batch_record(updated, plan, recorded_at)
        existing_batches = batches_path.read_text(encoding="utf-8")
        matching_batch: dict[str, object] | None = None
        for line_number, raw in enumerate(existing_batches.splitlines(), 1):
            if not raw.strip():
                continue
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError(f"document_intake_batches.jsonl:{line_number}: expected an object")
            if value.get("intake_batch_id") == plan["intake_batch_id"]:
                matching_batch = value
                break
        if matching_batch is None:
            suffix = "" if not existing_batches or existing_batches.endswith("\n") else "\n"
            updated_batches = existing_batches + suffix + json.dumps(batch_record, ensure_ascii=False) + "\n"
        else:
            def stable_batch(value: dict[str, object]) -> dict[str, object]:
                stable = {
                    "intake_batch_id": value.get("intake_batch_id"),
                    "status": value.get("status"),
                    "batch_description": value.get("batch_description", ""),
                    "items": [],
                }
                raw_items = value.get("items", [])
                if not isinstance(raw_items, list):
                    return stable
                stable["items"] = [
                    {key: item.get(key) for key in (
                        "document_id",
                        "source_filename",
                        "source_sha256",
                        "relative_path",
                        "description",
                        "verification_status",
                    )}
                    for item in raw_items
                    if isinstance(item, dict)
                ]
                return stable

            if stable_batch(matching_batch) != stable_batch(batch_record):
                raise ValueError(f"Existing intake batch {plan['intake_batch_id']} has different content")
            updated_batches = existing_batches
        replace_files_atomically(
            [
                (registry_path, json.dumps(updated, ensure_ascii=False, indent=2) + "\n"),
                (batches_path, updated_batches),
            ]
        )
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
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--source", type=Path, action="append")
    source_group.add_argument("--manifest", type=Path)
    parser.add_argument("--target-folder", choices=PROJECT_FOLDERS)
    parser.add_argument("--target-name")
    parser.add_argument("--description")
    parser.add_argument("--expected-batch-id")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    root = require_ready_project(args.project_dir)
    if args.manifest is not None:
        if args.target_folder is not None or args.target_name is not None or args.description is not None:
            raise ValueError("--manifest cannot be combined with target or description arguments")
        plan = build_manifest_plan(root, load_manifest(args.manifest))
    else:
        if args.target_folder is None or args.description is None:
            raise ValueError("--source requires --target-folder and --description")
        legacy_plan = build_plan(root, args.source, args.target_folder, args.description, args.target_name)
        legacy_plan["items"] = [
            {**item, "target_folder": args.target_folder, "description": args.description.strip()}
            for item in legacy_plan["items"]
        ]
        signature = json.dumps(
            {
                "batch_description": "",
                "items": [
                    {
                        "source_filename": item["source_filename"],
                        "source_sha256": item["source_sha256"],
                        "relative_path": item["relative_path"],
                        "description": item["description"],
                    }
                    for item in legacy_plan["items"]
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        legacy_plan["intake_batch_id"] = "INT-" + str(uuid.uuid5(BATCH_NAMESPACE, signature))
        legacy_plan["batch_description"] = ""
        plan = legacy_plan
    if args.expected_batch_id is not None and not args.apply:
        raise ValueError("--expected-batch-id is only valid with --apply")
    if args.manifest is not None and args.apply and not args.expected_batch_id:
        raise ValueError("Manifest apply requires --expected-batch-id from the approved preview")
    if args.expected_batch_id is not None and args.expected_batch_id != plan["intake_batch_id"]:
        raise ValueError("Current intake batch differs from the approved preview")
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
