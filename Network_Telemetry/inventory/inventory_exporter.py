#!/usr/bin/env python3
"""Expose daily inventory snapshots and FROM/TO deltas as Prometheus metrics.

Grafana variables $from_date and $to_date select series labelled with those
dates. Unchanged devices are counted, not labelled row-by-row, so cardinality
stays proportional to change volume rather than fleet size.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlparse

from prometheus_client import CollectorRegistry, Gauge, generate_latest, start_http_server

from inventory_lib import compare, list_dates, load_day

LISTEN_PORT = int(os.environ.get("EXPORTER_PORT", "9824"))
API_PORT = int(os.environ.get("INVENTORY_API_PORT", "9825"))
REFRESH = int(os.environ.get("INVENTORY_REFRESH_SECONDS", "60"))
MAX_PAIR_DAYS = int(os.environ.get("INVENTORY_DELTA_WINDOW_DAYS", "14"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
)
log = logging.getLogger("inventory-exporter")

registry = CollectorRegistry()

snap_date = Gauge(
    "inventory_snapshot_date",
    "1 for each stored UTC snapshot date (used as Grafana $from_date / $to_date)",
    ["snapshot_date"],
    registry=registry,
)
snap_devices = Gauge(
    "inventory_snapshot_devices",
    "Devices in the merged snapshot for this date",
    ["snapshot_date"],
    registry=registry,
)
delta_count = Gauge(
    "inventory_delta_count",
    "Inventory comparison counts between two snapshot dates",
    ["from_date", "to_date", "change"],
    registry=registry,
)
delta_row = Gauge(
    "inventory_delta",
    "1 per added/removed/changed device (or changed attribute). "
    "Unchanged devices are represented only in inventory_delta_count.",
    ["from_date", "to_date", "change", "region", "country", "site_id",
     "device", "source", "ident", "attribute", "previous", "current"],
    registry=registry,
)
exporter_up = Gauge(
    "inventory_exporter_up",
    "1 while the inventory exporter is running",
    registry=registry,
)


def publish() -> None:
    dates = list_dates()
    snap_date.clear()
    snap_devices.clear()
    delta_count.clear()
    delta_row.clear()

    window = dates[-MAX_PAIR_DAYS:] if dates else []
    for day in window:
        snap_date.labels(snapshot_date=day).set(1)
        snap_devices.labels(snapshot_date=day).set(len(load_day(day)))

    # Consecutive pairs plus first/last in the window so FROM/TO in Grafana
    # usually hits a precomputed pair. Arbitrary pairs are served on /delta.
    pairs = set()
    for i in range(len(window) - 1):
        pairs.add((window[i], window[i + 1]))
    if len(window) >= 2:
        pairs.add((window[0], window[-1]))

    for from_day, to_day in sorted(pairs):
        result = compare(from_day, to_day)
        for change, n in result["counts"].items():
            delta_count.labels(from_date=from_day, to_date=to_day, change=change).set(n)
        _emit_rows(from_day, to_day, result)

    log.info("published %d snapshot dates, %d delta pairs", len(window), len(pairs))


def _emit_rows(from_day: str, to_day: str, result: dict, limit: int = 5000) -> None:
    emitted = 0
    for rec in result["added"]:
        if emitted >= limit:
            break
        _row(from_day, to_day, "added", rec, attribute="", previous="", current="")
        emitted += 1
    for rec in result["removed"]:
        if emitted >= limit:
            break
        _row(from_day, to_day, "removed", rec, attribute="", previous="", current="")
        emitted += 1
    for item in result["changed"]:
        rec = item["device"]
        for diff in item["changes"]:
            if emitted >= limit:
                return
            _row(
                from_day, to_day, "changed", rec,
                attribute=diff["attribute"],
                previous=diff["previous"],
                current=diff["current"],
            )
            emitted += 1


def _row(from_day, to_day, change, rec, attribute, previous, current):
    delta_row.labels(
        from_date=from_day,
        to_date=to_day,
        change=change,
        region=rec.get("region") or "unknown",
        country=rec.get("country") or "unknown",
        site_id=rec.get("site_id") or "unknown",
        device=rec.get("device") or "unknown",
        source=rec.get("source") or "unknown",
        ident=rec.get("ident") or "unknown",
        attribute=attribute or "n/a",
        previous=_clip(previous),
        current=_clip(current),
    ).set(1)


def _clip(value: str, n: int = 64) -> str:
    value = value or ""
    return value if len(value) <= n else value[: n - 1] + "…"


class DeltaHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.debug("%s - " + fmt, self.address_string(), *args)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/health", "/"):
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/dates":
            self._json({"dates": list_dates()})
            return
        if parsed.path == "/delta":
            qs = parse_qs(parsed.query)
            dates = list_dates()
            from_day = (qs.get("from") or [None])[0] or (dates[-2] if len(dates) >= 2 else "")
            to_day = (qs.get("to") or [None])[0] or (dates[-1] if dates else "")
            if not from_day or not to_day:
                self._json({"error": "no snapshots yet", "dates": dates}, 404)
                return
            result = compare(from_day, to_day)
            # Omit the unchanged device list from the default JSON payload;
            # counts still include them.
            if (qs.get("include_unchanged") or ["false"])[0].lower() != "true":
                result = dict(result)
                result["unchanged"] = []
            self._json(result)
            return
        self.send_error(404)

    def _json(self, payload, status=200):
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def loop(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            publish()
        except Exception:
            log.exception("inventory publish failed")
        stop.wait(REFRESH)


def main() -> int:
    log.info("inventory exporter :%d metrics, :%d api, dir refresh %ds",
             LISTEN_PORT, API_PORT, REFRESH)
    start_http_server(LISTEN_PORT, registry=registry)
    exporter_up.set(1)
    try:
        publish()
    except Exception:
        log.exception("initial publish failed")

    api = ThreadingHTTPServer(("0.0.0.0", API_PORT), DeltaHandler)
    threading.Thread(target=api.serve_forever, name="inventory-api", daemon=True).start()

    stop = threading.Event()

    def handle(signum, _frame):
        log.info("signal %s", signum)
        stop.set()
        api.shutdown()

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)

    worker = threading.Thread(target=loop, args=(stop,), name="publisher", daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(timeout=1.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
