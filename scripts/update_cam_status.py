#!/usr/bin/env python3
"""Fetch cam images, compute SHA-1 hashes, update data/cam-status.json.

Schema:
    {
        "<wsrv.nl URL>": {
            "hash": "<sha1>",
            "lastChange": <ms_epoch>
        }
    }

On first observation OR when the hash changes, lastChange is set to "now".
Otherwise the existing entry is preserved unchanged so the timestamp
keeps reflecting the last *real* image change.
"""
import hashlib
import json
import re
import sys
import time
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


def fetch_hash(url):
    try:
        r = requests.get(
            url,
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
            allow_redirects=True,
        )
        r.raise_for_status()
        if not r.content:
            return None
        return hashlib.sha1(r.content).hexdigest()
    except Exception as e:
        print(f"[warn] {url}: {e}", file=sys.stderr)
        return None


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
        h = fetch_hash(origin)
        prev = status.get(wsrv_url, {})

        if h is None:
            # Transient error: keep previous entry so we don't lose history.
            if prev:
                new_status[wsrv_url] = prev
            continue

        if prev.get("hash") == h:
            new_status[wsrv_url] = prev
        else:
            new_status[wsrv_url] = {"hash": h, "lastChange": now_ms}
            changed += 1
            print(f"[update] {wsrv_url}")

    save_status(new_status)
    print(f"Done. {changed} entry/entries updated.")


if __name__ == "__main__":
    main()
