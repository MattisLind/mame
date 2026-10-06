#!/usr/bin/env python3
# license:BSD-3-Clause
# copyright-holders:Mattis Lind
"""Decode the supplied cartridge tape controller ROMs for MAME."""

import argparse
import hashlib
import json
from pathlib import Path
import zlib

from prepare_mwdcii import decode_hex


ROMS = (
    (97, "tcc_34-0607-01a.u97", "106a23a2", "3b615f9b9ddac6685ff79f0676249c30f1fbaad4"),
    (96, "tcc_34-0608-01a.u96", "ebc69875", "1cd3cb1121e01440487165da753932812dada8bf"),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("roms/S8000/TCU"))
    parser.add_argument("--destination", type=Path, default=Path("roms/s8k_tcc"))
    args = parser.parse_args()
    images, report = [], []
    for socket, name, crc, sha in ROMS:
        matches = list(args.source.glob(f"*_U{socket}_*"))
        if len(matches) != 1:
            raise ValueError(f"Expected one capture for U{socket}, found {len(matches)}")
        content = decode_hex(matches[0].read_bytes())
        if (len(content), f"{zlib.crc32(content):08x}", hashlib.sha1(content).hexdigest()) != (2048, crc, sha):
            raise ValueError(f"U{socket} does not match the documented dump")
        images.append((name, content))
        report.append(dict(name=name, source=matches[0].name, size=len(content), crc32=crc, sha1=sha))
    if sum(b"".join(content for _, content in images)) & 255:
        raise ValueError("Firmware self-test checksum failed")
    args.destination.mkdir(parents=True, exist_ok=True)
    for name, content in images:
        (args.destination / name).write_bytes(content)
    print(json.dumps(dict(roms=report, checksum=0), indent=2))


if __name__ == "__main__":
    main()
