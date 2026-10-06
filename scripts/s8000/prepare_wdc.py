#!/usr/bin/env python3
# license:BSD-3-Clause
# copyright-holders:Mattis Lind
"""Decode and audit the supplied 8-inch WDC Intel HEX ROM captures."""
import argparse
import hashlib
from pathlib import Path
import zlib
from prepare_mwdcii import decode_hex

ROMS = ((27, '34-0611-01', '2d1c88f3', '6d1882a8445b7aff0400ace0767051d164f0dfb6'),
        (28, '34-0612-01', '0575cfd8', 'fa6c5d6c8126645de6da12590aa02ad11152151c'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('roms/S8000/WDC'))
    parser.add_argument('--destination', type=Path, default=Path('roms/s8k_wdc'))
    args = parser.parse_args()
    decoded = []
    for socket, part, crc, sha in ROMS:
        captures = list(args.source.glob(f'*_U{socket}_*'))
        if len(captures) != 1:
            raise ValueError(f'expected exactly one U{socket} capture')
        data = decode_hex(captures[0].read_bytes())
        if (len(data), f'{zlib.crc32(data):08x}', hashlib.sha1(data).hexdigest()) != (4096, crc, sha):
            raise ValueError(f'U{socket} does not match the documented capture')
        decoded.append((f'wdc_{part}.u{socket}', data))
    if decoded[0][1][:5] != bytes.fromhex('c32a006111'):
        raise ValueError('unexpected Z80 reset vector or firmware identification')
    args.destination.mkdir(parents=True, exist_ok=True)
    for name, data in decoded:
        (args.destination / name).write_bytes(data)
        print(name, len(data), hashlib.sha1(data).hexdigest())


if __name__ == '__main__':
    main()
