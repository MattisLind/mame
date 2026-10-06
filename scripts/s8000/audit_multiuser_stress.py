#!/usr/bin/env python3
# license:BSD-3-Clause
# copyright-holders:Mattis Lind
"""Verify saved three-controller stress files and the cartridge archive outside MAME."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import tarfile
import tempfile


class Filesystem:
    """Read the original big-endian ZEUS 512-byte System III filesystem."""

    def __init__(self, raw):
        self.raw = raw

    def block(self, number):
        return self.raw[number * 512:(number + 1) * 512]

    def content(self, number):
        offset = 1024 + (number - 1) * 64
        inode = self.raw[offset:offset + 64]
        size = int.from_bytes(inode[8:12], "big")
        addresses = [int.from_bytes(inode[12 + i * 3:15 + i * 3], "big") for i in range(13)]
        blocks = addresses[:10]

        def indirect(block, depth):
            if not block:
                return []
            numbers = struct.unpack(">128I", self.block(block))
            return list(numbers) if depth == 1 else [value for n in numbers for value in indirect(n, depth - 1)]

        for depth in range(1, 4):
            if len(blocks) * 512 >= size:
                break
            blocks += indirect(addresses[9 + depth], depth)
        data = b"".join(self.block(n) if n else bytes(512) for n in blocks)[:size]
        assert len(data) == size, "incomplete inode block list"
        return data

    def file(self, path):
        inode = 2
        for part in path.strip("/").split("/"):
            directory = self.content(inode)
            entries = {directory[i + 2:i + 16].split(b"\0")[0].decode("ascii"):
                       int.from_bytes(directory[i:i + 2], "big") for i in range(0, len(directory), 16)}
            inode = entries[part]
            assert inode, f"missing file: {path}"
        return self.content(inode)


def first_tape_file(path):
    tape = path.read_bytes()
    offset, track, records = 0, 0, []
    while offset < len(tape):
        marker, = struct.unpack_from("<I", tape, offset)
        offset += 4
        if marker == 0xffffffff:
            break
        if marker & 0xfffffffc == 0x70000000:
            track = marker & 3
            continue
        if not marker:
            if track == 0:
                break
            continue
        assert not marker & 0xff000000, "error or unsupported TAP record"
        record = tape[offset:offset + marker]
        assert len(record) == marker
        offset += marker + (marker & 1)
        trailer, = struct.unpack_from("<I", tape, offset)
        assert trailer == marker, "TAP trailer mismatch"
        offset += 4
        if track == 0:
            records.append(record)
    assert records, "missing track-0 archive"
    return b"".join(records), len(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media", type=Path, required=True, help="directory produced by prepare_multiuser_stress.py")
    parser.add_argument("--chdman", type=Path, default=Path("chdman"))
    args = parser.parse_args()
    filesystems = {}
    with tempfile.TemporaryDirectory(prefix="audit-", dir=args.media) as temporary:
        for name, start, size in (("md", 27302, 57154), ("smd", 100, 20000), ("wd", 196, 20000)):
            raw = Path(temporary) / f"{name}.raw"
            subprocess.run([str(args.chdman.resolve()), "extracthd", "-i", str(args.media / f"{name}.chd"),
                            "-o", str(raw), "-isb", str(start * 512), "-ib", str(size * 512)], check=True)
            filesystems[name] = Filesystem(raw.read_bytes())
    source = filesystems["md"].file("stress/payload")
    assert len(source) > 128 * 1024, "payload should exceed the guest's buffer cache"
    matched = []
    for name, paths in (("md", ("stress/fromwd", "stress/fromsmd")),
                        ("smd", ("fwd", "rev", "restore/payload")),
                        ("wd", ("fwd", "rev", "tape"))):
        for path in paths:
            assert filesystems[name].file(path) == source, f"saved file differs: {name}/{path}"
            matched.append(f"{name}/{path}")
    seed = filesystems["md"].file("stress/seed")
    assert filesystems["smd"].file("restore/seed") == seed
    archive, records = first_tape_file(args.media / "scratch.tap")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tape:
        for name, expected in (("seed", seed), ("payload", source)):
            assert tape.extractfile(name).read() == expected, f"saved tape archive differs: {name}"
    report = {"payload_bytes": len(source), "payload_sha256": hashlib.sha256(source).hexdigest(),
              "matching_disk_files": matched, "tape_archive_bytes": len(archive), "tape_records": records,
              "matching_archive_files": ["seed", "payload"]}
    (args.media / "audit.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"PASS: saved CHD and TAP bytes match; report in {args.media / 'audit.json'}")


if __name__ == "__main__":
    main()
