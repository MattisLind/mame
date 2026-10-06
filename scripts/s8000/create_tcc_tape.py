#!/usr/bin/env python3
# license:BSD-3-Clause
# copyright-holders:Mattis Lind
"""Create an empty four-track S8000 cartridge TAP without replacing a file."""

import argparse
from pathlib import Path
import struct


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    with args.output.open("xb") as tape:
        tape.write(struct.pack("<5I", 0x70000000, 0x70000001, 0x70000002, 0x70000003, 0xffffffff))
    print(f"Created blank cartridge: {args.output}")


if __name__ == "__main__":
    main()
