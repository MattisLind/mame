#!/usr/bin/env python3
# license:BSD-3-Clause
# copyright-holders:Mattis Lind
"""Prepare disposable three-controller media from a completed type-3 ZEUS disk."""

import argparse
from pathlib import Path
import shutil
import struct
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system-disk", type=Path, required=True, help="completed MWDCII 830/6/17 system image; source is only read")
    parser.add_argument("--tape", type=Path, default=Path("roms/tapes/zeus-3.21-install.tap"))
    parser.add_argument("--output", type=Path, required=True, help="new directory; existing directories are refused")
    parser.add_argument("--chdman", type=Path, default=Path("chdman"))
    args = parser.parse_args()
    for source in (args.system_disk, args.tape):
        if not source.is_file():
            parser.error(f"missing source: {source}")
    if args.output.exists():
        parser.error(f"output already exists: {args.output}; reuse its media with install_zeus.py, or choose a new --output directory for fresh disks")
    tool = str(args.chdman.resolve())
    info = subprocess.check_output([tool, "info", "-i", str(args.system_disk)], text=True)
    if "CYLS:830,HEADS:6,SECS:17,BPS:512" not in info:
        parser.error("system disk must have MWDCII type-3 geometry, 830/6/17, 512-byte sectors")
    args.output.mkdir(parents=True, exist_ok=False)
    # A compressed source must become a directly writable CHD, rather than
    # silently putting subsequent guest writes in an external .dif overlay.
    subprocess.run([tool, "copy", "-i", str(args.system_disk), "-o", str(args.output / "md.chd"),
                    "-c", "none"], check=True)
    shutil.copyfile(args.tape, args.output / "scratch.tap")
    header = args.output / "system-block0.bin"
    subprocess.run([tool, "extracthd", "-i", str(args.system_disk), "-o", str(header),
                    "-isb", str(102 * 512), "-ib", "512"], check=True)
    template = header.read_bytes()
    if template[:4] != bytes.fromhex("deadbabe"):
        raise ValueError("source does not contain a ZEUS block-zero configuration")
    for name, cylinders, heads, sectors, first in (("smd", 823, 8, 32, 0), ("wd", 605, 4, 24, 96)):
        raw = args.output / f"{name}.raw"
        block0 = bytearray(template)
        block0[34:290] = bytes(256)
        # Keep block zero outside the filesystem, and give each extra disk
        # one 10 MB scratch partition. No boot-device aliases are changed.
        struct.pack_into(">II8s", block0, 34, 100, 20000, name.encode())
        with raw.open("xb") as disk:
            disk.truncate(cylinders * heads * sectors * 512)
            if name == "wd":
                defect = bytearray(b"\xff" * 512)
                defect[2:8] = bytes(6)
                defect[504:512] = struct.pack(">HBBHBB", 600, heads, sectors, cylinders, heads * sectors, 0)
                for head in range(heads):
                    disk.seek(head * sectors * 512)
                    disk.write(defect)
            disk.seek(first * 512)
            disk.write(block0)
        subprocess.run([tool, "createhd", "-i", str(raw), "-o", str(args.output / f"{name}.chd"),
                        "-chs", f"{cylinders},{heads},{sectors}", "-ss", "512", "-c", "none"], check=True)
    print(f"Prepared disposable MWDCII, SMDC, WDC and tape images in {args.output}")


if __name__ == "__main__":
    main()
