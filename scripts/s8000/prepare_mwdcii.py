#!/usr/bin/env python3
# license:BSD-3-Clause
# copyright-holders:Mattis Lind
"""Decode the supplied MWDCII Intel HEX captures into audited MAME ROMs."""

import argparse
import hashlib
import json
from pathlib import Path
import zlib


ROMS = (
    (162, "34-0726-00a", 4096, "ce73c522", "89cb7e78c600dc75609d8eaf1d794a076cdb375d"),
    (163, "34-0727-00a", 4096, "ca6d28a9", "dcef61e67815c527cecabe7b06344137a80118e5"),
    (157, "34-00112-00a", 1024, "5924674a", "075063d175bbee92e212e535a44999e542ba1d26"),
    (158, "34-00113-00a", 1024, "74990714", "04f3509b17b93e923c1d2e88d383c43cc729c53b"),
)


def decode_hex(capture):
    """Accept Intel HEX, including the NUL-prefixed bipolar PROM captures."""
    start = capture.find(b":")
    if start < 0 or capture[:start].strip(b"\0\r\n\t "):
        raise ValueError("not an Intel HEX capture")
    data = {}
    base = 0
    ended = False
    for line_number, line in enumerate(capture[start:].splitlines(), 1):
        line = line.strip(b"\0\r\n\t ")
        if not line:
            continue
        if ended or not line.startswith(b":"):
            raise ValueError(f"unexpected data at line {line_number}")
        record = bytes.fromhex(line[1:].decode("ascii"))
        if len(record) < 5 or len(record) != record[0] + 5 or sum(record) & 255:
            raise ValueError(f"bad length/checksum at line {line_number}")
        count, address, kind = record[0], int.from_bytes(record[1:3], "big"), record[3]
        payload = record[4:-1]
        if kind == 0:
            for i, value in enumerate(payload):
                location = base + address + i
                if location in data:
                    raise ValueError(f"overlapping data at {location:x}")
                data[location] = value
        elif kind == 1 and count == 0 and address == 0:
            ended = True
        elif kind in (2, 4) and count == 2 and address == 0:
            base = int.from_bytes(payload, "big") << (4 if kind == 2 else 16)
        else:
            raise ValueError(f"unsupported record at line {line_number}")
    if not ended or not data:
        raise ValueError("missing EOF or data")
    if min(data) != 0 or len(data) != max(data) + 1:
        raise ValueError("capture does not cover a contiguous ROM starting at zero")
    return bytes(data[i] for i in range(len(data)))


def prepare(source, destination):
    decoded = {}
    report = []
    for socket, part, size, crc, sha1 in ROMS:
        matches = list(source.glob(f"*_U{socket}_*"))
        if len(matches) != 1:
            raise ValueError(f"expected one capture for U{socket}, found {len(matches)}")
        content = decode_hex(matches[0].read_bytes())
        actual_crc = f"{zlib.crc32(content):08x}"
        actual_sha1 = hashlib.sha1(content).hexdigest()
        if (len(content), actual_crc, actual_sha1) != (size, crc, sha1):
            raise ValueError(f"U{socket} does not match the documented dump")
        decoded[socket] = content
        report.append(dict(source=matches[0].name, name=f"mwdc_{part}.u{socket}",
                           size=size, crc32=crc, sha1=sha1))

    firmware = bytes(value for pair in zip(decoded[163], decoded[162]) for value in pair)
    # Firmware 01c8..01ec sums bytes up to 154c, then adds a big-endian word.
    checksum = (sum(firmware[:0x154c]) + int.from_bytes(firmware[0x154c:0x154e], "big")) & 65535
    if firmware[:8] != bytes.fromhex("0000c00000000070") or checksum:
        raise ValueError("bad interleaving, reset vector or firmware checksum")
    destination.mkdir(parents=True, exist_ok=True)
    for entry, (socket, *_rest) in zip(report, ROMS):
        (destination / entry["name"]).write_bytes(decoded[socket])
    return dict(roms=report, reset_fcw="c000", reset_pc="000070", checksum=checksum,
                identification=firmware[0x48:0x70].decode("ascii"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("roms/S8000/MWDCII"))
    parser.add_argument("--destination", type=Path, default=Path("roms/s8k_mwdcii"))
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.destination), indent=2))


if __name__ == "__main__":
    main()
