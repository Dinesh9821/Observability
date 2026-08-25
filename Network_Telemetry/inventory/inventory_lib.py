#!/usr/bin/env python3
"""Daily inventory snapshots and deterministic FROM/TO comparison.

Snapshots are JSON files — no database. Each file is one UTC calendar day
and one source (meraki or vmanage). Comparison is keyed by (source, ident)
where ident is the serial (Meraki) or system-ip (Viptela).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

COMPARE_FIELDS = (
    "device",
    "device_type",
    "vendor",
    "model",
    "serial",
    "management_ip",
    "software_version",
    "status",
    "site_id",
    "region",
    "country",
    "role",
)

SNAPSHOT_DIR = os.environ.get(
    "INVENTORY_SNAPSHOT_DIR", "/var/lib/network-telemetry/inventory"
)
RETENTION_DAYS = int(os.environ.get("INVENTORY_RETENTION_DAYS", "90"))


def utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def device_key(rec: Dict[str, Any]) -> str:
    source = rec.get("source") or "unknown"
    ident = rec.get("ident") or rec.get("serial") or rec.get("device") or "unknown"
    return "%s|%s" % (source, ident)


def normalize_record(rec: Dict[str, Any]) -> Dict[str, str]:
    out = {}
    for field in (
        "source",
        "ident",
        "region",
        "country",
        "site_id",
        "device",
        "device_type",
        "vendor",
        "model",
        "serial",
        "management_ip",
        "software_version",
        "status",
        "role",
        "first_seen",
        "last_seen",
        "snapshot_date",
    ):
        val = rec.get(field)
        out[field] = "" if val is None else str(val)
    return out


def snapshot_path(day: str, source: str) -> str:
    return os.path.join(SNAPSHOT_DIR, "%s-%s.json" % (day, source))


def write_snapshot(source: str, records: Iterable[Dict[str, Any]], day: Optional[str] = None) -> str:
    day = day or utc_today()
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    payload = {
        "snapshot_date": day,
        "source": source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "device_count": 0,
        "devices": [],
    }
    devices = []
    for rec in records:
        item = normalize_record(rec)
        if not item.get("source"):
            item["source"] = source
        item["snapshot_date"] = day
        if not item.get("last_seen"):
            item["last_seen"] = payload["generated_at"]
        devices.append(item)
    payload["devices"] = devices
    payload["device_count"] = len(devices)
    path = snapshot_path(day, source)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    _prune()
    return path


def _prune() -> None:
    if RETENTION_DAYS <= 0 or not os.path.isdir(SNAPSHOT_DIR):
        return
    cutoff = datetime.now(timezone.utc).timestamp() - RETENTION_DAYS * 86400
    for name in os.listdir(SNAPSHOT_DIR):
        if not name.endswith(".json") or name.endswith(".tmp"):
            continue
        path = os.path.join(SNAPSHOT_DIR, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def list_dates() -> List[str]:
    dates = set()
    if not os.path.isdir(SNAPSHOT_DIR):
        return []
    for name in os.listdir(SNAPSHOT_DIR):
        if len(name) < 10 or not name.endswith(".json"):
            continue
        day = name[:10]
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            continue
        dates.add(day)
    return sorted(dates)


def load_day(day: str) -> Dict[str, Dict[str, str]]:
    """Merge every source file for a calendar day, keyed by device_key."""
    merged: Dict[str, Dict[str, str]] = {}
    if not os.path.isdir(SNAPSHOT_DIR):
        return merged
    for name in os.listdir(SNAPSHOT_DIR):
        if not name.startswith(day) or not name.endswith(".json"):
            continue
        path = os.path.join(SNAPSHOT_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            continue
        for rec in payload.get("devices") or []:
            item = normalize_record(rec)
            merged[device_key(item)] = item
    return merged


def compare(from_day: str, to_day: str) -> Dict[str, Any]:
    old = load_day(from_day)
    new = load_day(to_day)
    added, removed, changed, unchanged = [], [], [], []

    for key, rec in new.items():
        if key not in old:
            added.append(rec)
            continue
        prev = old[key]
        diffs = []
        for field in COMPARE_FIELDS:
            a, b = prev.get(field, ""), rec.get(field, "")
            if a != b:
                diffs.append({"attribute": field, "previous": a, "current": b})
        if diffs:
            changed.append({"device": rec, "previous": prev, "changes": diffs})
        else:
            unchanged.append(rec)

    for key, rec in old.items():
        if key not in new:
            removed.append(rec)

    added.sort(key=lambda r: (r.get("site_id", ""), r.get("device", "")))
    removed.sort(key=lambda r: (r.get("site_id", ""), r.get("device", "")))
    changed.sort(key=lambda r: (r["device"].get("site_id", ""), r["device"].get("device", "")))
    unchanged.sort(key=lambda r: (r.get("site_id", ""), r.get("device", "")))

    return {
        "from_date": from_day,
        "to_date": to_day,
        "counts": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "unchanged": len(unchanged),
        },
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged": unchanged,
    }


def meraki_record(labels: Dict[str, Any], firmware: str = "", assigned: bool = True) -> Dict[str, str]:
    status = "assigned" if assigned else "unassigned"
    return normalize_record({
        "source": "meraki",
        "ident": labels.get("serial"),
        "region": labels.get("region"),
        "country": labels.get("country"),
        "site_id": labels.get("site_id"),
        "device": labels.get("device_name"),
        "device_type": labels.get("product_type"),
        "vendor": "cisco-meraki",
        "model": labels.get("model"),
        "serial": labels.get("serial"),
        "management_ip": labels.get("lan_ip") or "",
        "software_version": firmware,
        "status": status,
        "role": labels.get("device_role"),
    })


def vmanage_record(labels: Dict[str, Any], version: str = "", reachability: str = "") -> Dict[str, str]:
    return normalize_record({
        "source": "vmanage",
        "ident": labels.get("system_ip"),
        "region": labels.get("region"),
        "country": labels.get("country"),
        "site_id": labels.get("site_id"),
        "device": labels.get("hostname"),
        "device_type": labels.get("device_type"),
        "vendor": "cisco-viptela",
        "model": labels.get("device_model"),
        "serial": labels.get("serial") or labels.get("uuid") or "",
        "management_ip": labels.get("system_ip"),
        "software_version": version,
        "status": reachability or "unknown",
        "role": labels.get("device_role"),
    })
