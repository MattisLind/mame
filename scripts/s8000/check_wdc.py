#!/usr/bin/env python3
# license:BSD-3-Clause
# copyright-holders:Mattis Lind
"""Check WDC DMA, persistent CHD sectors, errors and all four drive geometries."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mame', type=Path, default=Path('s8000d'))
    parser.add_argument('--chdman', type=Path, default=Path('chdman'))
    parser.add_argument('--output', type=Path, default=Path('build/s8000-wdc-probe'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    images = []
    for heads in (1, 3, 4, 5):
        image = args.output / f'heads-{heads}.chd'
        subprocess.run([sys.executable, 'scripts/s8000/create_wdc_image.py', str(image),
                        '--heads', str(heads), '--chdman', str(args.chdman)], check=True)
        images.append(image)
    archive = args.output / 'protected.zip'
    with zipfile.ZipFile(archive, 'w') as zipped:
        zipped.write(images[3], images[3].name)
    images[3] = archive / images[3].name
    command = [str(args.mame.resolve()), 's8000', '-slot_disk', 'wdc', '-rompath', 'roms',
               '-video', 'none', '-sound', 'none', '-nothrottle', '-skip_gameinfo',
               '-cfg_directory', str(args.output / 'cfg'), '-diff_directory', str(args.output / 'diff'),
               '-seconds_to_run', '30', '-autoboot_delay', '0', '-autoboot_script', 'scripts/s8000/probe_wdc.lua']
    env = dict(os.environ, SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy')
    for stage, options, drives in (('write', {}, images), ('verify', {'WDC_VERIFY_ONLY': '1'}, images),
                                  ('absent', {'WDC_ABSENT': '1'}, images[:1])):
        result = subprocess.run(command + [arg for unit, image in enumerate(drives, 1) for arg in (f'-hard{unit}', str(image))],
                                env=dict(env, **options), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        (args.output / f'{stage}.log').write_bytes(result.stdout)
        assert result.returncode == 0 and b'PASS: WDC' in result.stdout, f'{stage} probe failed; inspect {args.output}/{stage}.log'
        print(result.stdout.decode().split('PASS:', 1)[1].strip())
    for unit, heads in enumerate((1, 3, 4)):
        raw = args.output / f'verified-{heads}.raw'
        subprocess.run([str(args.chdman.resolve()), 'extracthd', '-i', str(images[unit]), '-o', str(raw)], check=True)
        offset = ((21 * heads + heads - 1) * 24 + 23) * 512
        with raw.open('rb') as stream:
            stream.seek(offset)
            actual = stream.read(777 * 2)
            tail = stream.read(512 - (777 * 2) % 512)
        expected = bytes((i * 37 + (i >> 8) * 11 + unit * 53) & 255 for i in range(777 * 2))
        assert actual == expected, 'persisted CHD sectors differ from DMA data'
        assert tail == bytes(len(tail)), 'short write changed the unused part of its final sector'
    print('PASS: extracted CHDs contain the exact expected bytes after restart')
    invalid = args.output / 'invalid-geometry.chd'
    shutil.copyfile(images[0], invalid)
    subprocess.run([str(args.chdman.resolve()), 'addmeta', '-i', str(invalid), '-t', 'GDDD', '-ix', '0',
                    '-vt', 'CYLS:600,HEADS:1,SECS:24,BPS:512'], check=True)
    result = subprocess.run(command + ['-hard1', str(invalid)], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=15)
    (args.output / 'invalid-geometry.log').write_bytes(result.stdout)
    assert result.returncode != 0 and b'Invalid image' in result.stdout, 'incompatible WDC geometry was accepted'
    print('PASS: incompatible CHD geometry rejected')


if __name__ == '__main__':
    main()
