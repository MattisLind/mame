#!/usr/bin/env python3
# license:BSD-3-Clause
# copyright-holders:Mattis Lind
"""Prepare CPU ROMs and SADIE logical files from a pinned Tpaxia snapshot."""

import argparse
import hashlib
import json
from pathlib import Path
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path, help="local Zilog_S8000 checkout")
    source.add_argument("--download", action="store_true", help="fetch pinned files from GitHub")
    parser.add_argument("--destination", type=Path, default=Path("roms"))
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("media.json"), help="pinned media manifest (zeus-media.json for installation tape)")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    root = f"https://raw.githubusercontent.com/{manifest['repository']}/{manifest['revision']}/"
    for entry in manifest["files"]:
        target = args.destination / entry["destination"]

        def valid(data):
            blob = b"blob " + str(len(data)).encode() + b"\0" + data
            return len(data) == entry["size"] and hashlib.sha1(blob).hexdigest() == entry["blob"]

        if target.exists() and valid(target.read_bytes()):
            continue
        if args.source:
            data = (args.source / entry["source"]).read_bytes()
        else:
            with urllib.request.urlopen(root + entry["source"], timeout=60) as response:
                data = response.read()
        if not valid(data):
            raise ValueError(f"size or Git blob hash mismatch: {entry['source']}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        print(target)
    print(f"Verified {len(manifest['files'])} files at {manifest['revision']}")


if __name__ == "__main__":
    main()
