#!/usr/bin/env python3
"""Fetch cam images, compute SHA-1 hashes, update data/cam-status.json.

Schema:
    {
        "<wsrv.nl URL>": {
            "hash": "<sha1>",
            "lastChange": <ms_epoch>,
            "source": "origin" | "bot"
        }
    }

Preferred timestamp source is the origin server's HTTP Last-Modified header
(exact file mtime). When that's unavailable, we fall back to the bot's run
time -- which is imprecise because GitHub Actions cron drifts heavily.
"""
import email.utils
import hashlib
import json
import re
import sys
import time
from datetime import timezone
from pathlib import Path
from urllib.parse import unquote

import requests

ROOT = Path(__file__).resolve().parent.parent
# Scan every HTML file in the repo root. The SRC_PATTERN below only picks up
# wsrv.nl cam URLs, so unrelated HTML files (404 page etc.) are ignored.
HTML_FILES = sorted(ROOT.glob("*.html"))
STATUS_FILE = ROOT / "data" / "cam-status.json"

# Only cams that route through wsrv.nl participate. LSPV cams (Wangen-Lachen)
# use data-cam= and have their own client-side timestamping.
SRC_PATTERN = re.compile(r'data-src="(https://wsrv\.nl/\?url=[^"]+)"')

TIMEOUT = 20
USER_AGENT = "Mozilla/5.0 (compatible; CamStatusBot/1.0)"


def collect_cam_urls():
    urls = set()
    for html_path in HTML_FILES:
        text = html_path.read_text(encoding="utf-8")
        urls.update(SRC_PATTERN.findall(text))
    return sorted(urls)


def origin_url(wsrv_url):
    prefix = "https://wsrv.nl/?url="
    if not wsrv_url.startswith(prefix):
        return wsrv_url
    raw = unquote(wsrv_url[len(prefix):])
    if not raw.startswith(("http://", "https://")):
        raw = "http://" + raw
    return raw


def fetch_meta(url):
    """Return (sha1_hex, last_modified_ms_or_None) or (None, None) on error."""
    try:
        r = requests.get(
            url,
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
            allow_redirects=True,
        )
        r.raise_for_status()
        if not r.content:
            return None, None
        h = hashlib.sha1(r.content).hexdigest()

        lm_ms = None
        lm = r.headers.get("Last-Modified")
        if lm:
            try:
                dt = email.utils.parsedate_to_datetime(lm)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                lm_ms = int(dt.timestamp() * 1000)
            except (TypeError, ValueError, OverflowError):
                pass
        return h, lm_ms
    except Exception as e:
        print(f"[warn] {url}: {e}", file=sys.stderr)
        return None, None


def load_status():
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("[warn] cam-status.json malformed, starting fresh",
                  file=sys.stderr)
    return {}


def save_status(data):
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main():
    now_ms = int(time.time() * 1000)
    status = load_status()
    cam_urls = collect_cam_urls()
    print(f"Checking {len(cam_urls)} cams")

    new_status = {}
    changed = 0

    for wsrv_url in cam_urls:
        origin = origin_url(wsrv_url)
        h, lm_ms = fetch_meta(origin)
        prev = status.get(wsrv_url, {})

        if h is None:
            # Transient error: keep previous entry so we don't lose history.
            if prev:
                new_status[wsrv_url] = prev
            continue

        if prev.get("hash") == h:
            # Same image bytes. Refine lastChange if origin now reports a
            # better (Last-Modified) timestamp than what we stored.
            if lm_ms and prev.get("lastChange") != lm_ms:
                new_status[wsrv_url] = {
                    "hash": h, "lastChange": lm_ms, "source": "origin",
                }
            else:
                new_status[wsrv_url] = prev
        else:
            # Image changed. Prefer origin's Last-Modified; fall back to "now".
            last_change = lm_ms if lm_ms else now_ms
            source = "origin" if lm_ms else "bot"
            new_status[wsrv_url] = {
                "hash": h, "lastChange": last_change, "source": source,
            }
            changed += 1
            print(f"[update/{source}] {wsrv_url}")

        if h is not None and lm_ms is None:
            print(f"[no-last-modified] {origin}", file=sys.stderr)

    save_status(new_status)
    print(f"Done. {changed} entry/entries updated.")


if __name__ == "__main__":
    main()
