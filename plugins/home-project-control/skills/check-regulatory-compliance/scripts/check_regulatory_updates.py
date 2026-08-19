#!/usr/bin/env python3
"""Check official regulatory source pages and report fingerprint changes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CATALOG = PLUGIN_ROOT / "schemas" / "regulatory-sources-ru.json"
USER_AGENT = "home-project-control-regulatory-watch/1.0"


class VisibleTextParser(HTMLParser):
    """Collect stable visible text while excluding executable and style content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "template"}:
            self.ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "template"} and self.ignored_depth:
            self.ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)


def fingerprint(body: bytes, mode: str, content_type: str) -> tuple[str, int]:
    if mode == "html_visible_text":
        charset_match = re.search(r"charset=([A-Za-z0-9._-]+)", content_type, flags=re.IGNORECASE)
        charset = charset_match.group(1) if charset_match else "utf-8"
        text = body.decode(charset, errors="replace")
        parser = VisibleTextParser()
        parser.feed(text)
        normalized = " ".join(" ".join(parser.parts).split()).encode("utf-8")
        return hashlib.sha256(normalized).hexdigest(), len(normalized)
    if mode != "content_fingerprint":
        raise ValueError(f"Unknown check_mode {mode}")
    return hashlib.sha256(body).hexdigest(), len(body)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_object(path: Path, *, required: bool) -> dict:
    if not path.exists() and not required:
        return {}
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Expected a regular JSON file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def atomic_write(path: Path, content: str) -> None:
    if path.exists() and (not path.is_file() or path.is_symlink()):
        raise ValueError(f"Unsafe output path: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()


def fetch_source(source: dict, prior: dict, checked_at: str, timeout: float) -> tuple[dict, dict | None]:
    source_id = str(source.get("source_id", "")).strip()
    url = str(source.get("url", "")).strip()
    if not source_id or not url:
        raise ValueError("Every catalog source requires source_id and url")
    fingerprint_mode = str(source.get("check_mode", "content_fingerprint")).strip()
    check: dict[str, object] = {
        "source_id": source_id,
        "url": url,
        "checked_at": checked_at,
    }
    try:
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json,*/*"})
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URLs come from a reviewed catalog
            body = response.read()
            status = response.getcode() or 200
            content_type = str(response.headers.get("Content-Type", ""))
            content_sha256, fingerprint_length = fingerprint(body, fingerprint_mode, content_type)
            snapshot = {
                "source_id": source_id,
                "url": url,
                "checked_at": checked_at,
                "http_status": status,
                "content_sha256": content_sha256,
                "content_length": len(body),
                "fingerprint_length": fingerprint_length,
                "fingerprint_mode": fingerprint_mode,
                "etag": str(response.headers.get("ETag", "")),
                "last_modified": str(response.headers.get("Last-Modified", "")),
            }
        previous_hash = str(prior.get("content_sha256", "")).strip()
        if str(prior.get("fingerprint_mode", "")).strip() != fingerprint_mode:
            previous_hash = ""
        change_status = "baseline_created" if not previous_hash else (
            "unchanged" if previous_hash == content_sha256 else "changed"
        )
        check.update(snapshot)
        check["change_status"] = change_status
        return check, snapshot
    except Exception as exc:  # network and protocol failures are evidence, not tracebacks in the report
        check.update({"change_status": "error", "error": f"{type(exc).__name__}: {exc}"})
        return check, None


def markdown_report(catalog: dict, status: str, checks: list[dict], checked_at: str) -> str:
    lines = [
        "# Мониторинг официальных нормативных источников",
        "",
        f"- Юрисдикция: `{catalog.get('jurisdiction', '')}`",
        f"- Проверено: `{checked_at}`",
        f"- Статус: `{status}`",
        "",
        "| Источник | Результат | HTTP | Отпечаток |",
        "| --- | --- | ---: | --- |",
    ]
    for check in checks:
        digest = str(check.get("content_sha256", ""))
        lines.append(
            f"| [{check.get('source_id', '')}]({check.get('url', '')}) | "
            f"`{check.get('change_status', '')}` | {check.get('http_status', '')} | "
            f"`{digest[:16] if digest else 'нет'}` |"
        )
    lines.extend(
        [
            "",
            "Изменение отпечатка означает только изменение страницы. Оно не доказывает изменение конкретного документа, его обязательности или применимости и требует содержательной проверки.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--write-state", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if args.write_state and args.state is None:
        raise ValueError("--write-state requires --state")

    catalog = load_object(args.catalog, required=True)
    if catalog.get("schema_version") != "1.0" or not isinstance(catalog.get("sources"), list):
        raise ValueError("Regulatory source catalog must use schema_version 1.0 and contain sources")
    state = load_object(args.state, required=False) if args.state else {}
    previous_sources = state.get("sources", {}) if isinstance(state.get("sources", {}), dict) else {}
    checked_at = utc_now()
    checks: list[dict] = []
    next_sources: dict[str, dict] = dict(previous_sources)
    for source in catalog["sources"]:
        if not isinstance(source, dict):
            raise ValueError("Catalog sources must be objects")
        source_id = str(source.get("source_id", "")).strip()
        check, snapshot = fetch_source(source, previous_sources.get(source_id, {}), checked_at, args.timeout)
        checks.append(check)
        if snapshot is not None:
            next_sources[source_id] = snapshot

    successes = sum(check.get("change_status") != "error" for check in checks)
    status = "complete" if successes == len(checks) else ("partial" if successes else "blocked")
    changed = any(check.get("change_status") in {"changed", "baseline_created"} for check in checks)
    result = {
        "catalog_id": catalog.get("catalog_id"),
        "checked_at": checked_at,
        "status": status,
        "changed": changed,
        "source_checks": checks,
    }

    state_updated = bool(changed or not state)
    result["state_updated"] = state_updated
    if args.write_state and args.state and state_updated:
        next_state = {
            "schema_version": "1.0",
            "catalog_id": catalog.get("catalog_id"),
            "updated_at": checked_at,
            "sources": next_sources,
        }
        atomic_write(args.state, json.dumps(next_state, ensure_ascii=False, indent=2) + "\n")
    if args.report:
        atomic_write(args.report, markdown_report(catalog, status, checks, checked_at))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Regulatory update check failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
