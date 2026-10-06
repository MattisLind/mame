#!/usr/bin/env python3
# license:BSD-3-Clause
# copyright-holders:Mattis Lind
"""Create an empty MWDCII sector image with its no-defects startup metadata."""

import argparse
from pathlib import Path
import struct
import subprocess


GEOMETRIES = ((216, 8), (306, 6), (645, 5), (830, 6))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new CHD filename")
    parser.add_argument("--type", type=int, choices=range(4), default=0)
    parser.add_argument("--chdman", type=Path, default=Path("chdman"))
    args = parser.parse_args()
    cylinders, heads = GEOMETRIES[args.type]
    sectors = cylinders * heads * 17
    raw = args.output.with_suffix(".raw")
    if args.output.exists() or raw.exists():
        raise FileExistsError("output CHD or intermediate raw image already exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with raw.open("xb") as stream:
        stream.truncate(sectors * 512)
        # Cylinder zero contains one defect-map copy on each head. A single
        # big-endian entry equal to physical capacity terminates an empty map.
        for head in range(heads):
            stream.seek(head * 17 * 512)
            stream.write(struct.pack(">I", sectors))
    subprocess.run([str(args.chdman.resolve()), "createhd", "-i", str(raw),
                    "-o", str(args.output), "-chs", f"{cylinders},{heads},17",
                    "-ss", "512"], check=True)
    print(f"Created type {args.type} image ({cylinders}/{heads}/17); raw source retained at {raw}")


if __name__ == "__main__":
    main()
