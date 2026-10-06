#!/usr/bin/env python3
# license:BSD-3-Clause
# copyright-holders:Mattis Lind
"""Create a writable 8-inch WDC CHD with no-defects startup metadata."""
import argparse
from pathlib import Path
import struct
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--heads', type=int, choices=(1, 3, 4, 5), default=3)
    parser.add_argument('--chdman', type=Path, default=Path('chdman'))
    args = parser.parse_args()
    raw = args.output.with_suffix('.raw')
    if raw.exists() or args.output.exists():
        raise FileExistsError('CHD or intermediate raw already exists')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    data = bytearray(b'\xff' * 512)
    data[2:8] = bytes(6)
    data[504:512] = struct.pack('>HBBHBB', 600, args.heads, 24, 605, args.heads * 24, 0)
    with raw.open('xb') as stream:
        stream.truncate(605 * args.heads * 24 * 512)
        for head in range(args.heads):
            stream.seek(head * 24 * 512)
            stream.write(data)
    subprocess.run([str(args.chdman.resolve()), 'createhd', '-i', str(raw), '-o', str(args.output),
                    '-chs', f'605,{args.heads},24', '-ss', '512', '-c', 'none'], check=True)
    print(f'Created WDC image: 605/{args.heads}/24, 512-byte sectors, 600 logical cylinders')


if __name__ == '__main__':
    main()
