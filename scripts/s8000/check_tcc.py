#!/usr/bin/env python3
# license:BSD-3-Clause
# copyright-holders:Mattis Lind
"""Test TCC positioning, DMA and TAP validation with known cartridge records."""

import argparse
import os
from pathlib import Path
import struct
import subprocess
import zipfile


def record(payload, error=False):
    marker = struct.pack("<I", len(payload) | (0x80000000 if error else 0))
    return marker + payload + (b"\0" if len(payload) & 1 else b"") + marker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=Path("s8000d"))
    parser.add_argument("--output", type=Path, default=Path("build/s8000-tcc-probe"))
    parser.add_argument("--sadie", type=Path, help="also compare all three SADIE TAP tracks with prepared logical files")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    tape = bytearray()
    for track in range(4):
        tape.extend(struct.pack("<I", 0x70000000 | track))
        for size in (1025, 20, 0, 2048, 0):
            tape.extend(record(bytes((i * 37 + track * 53) & 255 for i in range(size))) if size else bytes(4))
    tape.extend(b"\xff" * 4)
    fixture = args.output / "four-tracks.tap"
    fixture.write_bytes(tape)
    archive = args.output / "protected.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.write(fixture, "four-tracks.tap")
    writable = fixture
    fixture = archive / "four-tracks.tap"
    command = [str(args.mame.resolve()), "s8000", "-slot_tape", "tcc", "-rompath", "roms",
               "-video", "none", "-sound", "none", "-nothrottle", "-skip_gameinfo",
               "-cfg_directory", str(args.output / "cfg"), "-seconds_to_run", "20"]
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    result = subprocess.run(command + [arg for unit in range(1, 5) for arg in (f"-tape{unit}", str(fixture))]
                            + ["-autoboot_delay", "0", "-autoboot_script", "scripts/s8000/probe_tcc.lua"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, timeout=60)
    (args.output / "mame.log").write_bytes(result.stdout)
    assert result.returncode == 0 and b"PASS: TCC" in result.stdout, "controller probe failed; inspect mame.log"
    print(result.stdout.decode().split("PASS:", 1)[1].strip())
    result = subprocess.run(command + ["-tape1", str(writable), "-autoboot_delay", "0",
                            "-autoboot_script", "scripts/s8000/probe_tcc_write.lua"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=dict(env, TCC_CREATE_FILE=str((args.output / "created.tap").resolve())), timeout=60)
    (args.output / "write.log").write_bytes(result.stdout)
    assert result.returncode == 0 and b"PASS: TCC writable" in result.stdout, "write probe failed; inspect write.log"
    expected = bytearray()
    for track in range(4):
        expected.extend(struct.pack("<I", 0x70000000 | track))
        if track == 1:
            expected.extend(record(bytes((i * 17 + 23) & 255 for i in range(512))))
            expected.extend(bytes(4))
            expected.extend(record(bytes((i * 37 + 53) & 255 for i in range(20))))
        else:
            for size in (1025, 20, 0, 2048, 0):
                expected.extend(record(bytes((i * 37 + track * 53) & 255 for i in range(size))) if size else bytes(4))
    expected.extend(b"\xff" * 4)
    assert writable.read_bytes() == expected, "persisted TAP differs from expected records/tracks"
    assert (args.output / "created.tap").read_bytes() == (
        struct.pack("<4I", 0x70000000, 0x70000001, 0x70000002, 0x70000003)
        + record(bytes((i * 37 + 53) & 255 for i in range(20))) + bytes(4) + b"\xff" * 4)
    print("PASS: writable tape DMA, overwrite, file marks, creation and exact persisted TAP bytes")
    malformed = {
        "truncated-marker": b"\0\0",
        "truncated-record": struct.pack("<I", 20) + b"short",
        "bad-trailer": struct.pack("<I", 2) + b"ab" + struct.pack("<I", 3),
        "duplicate-track": struct.pack("<II", 0x70000000, 0x70000000),
        "invalid-track": struct.pack("<I", 0x70000004),
        "unsupported-marker": struct.pack("<I", 0xfffffffe),
        "data-after-eom": b"\xff" * 4 + bytes(4),
    }
    for name, data in malformed.items():
        path = args.output / f"{name}.tap"
        path.write_bytes(data)
        result = subprocess.run(command + ["-tape1", str(path)], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=env, timeout=15)
        (args.output / f"{name}.log").write_bytes(result.stdout)
        assert result.returncode != 0 and b"TAP" in result.stdout, f"invalid media accepted: {name}"
    print("PASS: malformed/truncated TAP records and track markers rejected")
    if args.sadie:
        result = subprocess.run(command + ["-tape1", str(args.sadie), "-autoboot_delay", "0",
                                "-autoboot_script", "scripts/s8000/probe_tcc_sadie.lua"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, timeout=60)
        (args.output / "sadie-tracks.log").write_bytes(result.stdout)
        assert result.returncode == 0 and result.stdout.count(b"PASS: SADIE TAP") == 5, "SADIE track checks failed; inspect sadie-tracks.log"
        print("PASS: all three SADIE tracks match independently extracted files")


if __name__ == "__main__":
    main()
