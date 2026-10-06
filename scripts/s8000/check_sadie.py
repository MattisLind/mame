#!/usr/bin/env python3
# license:BSD-3-Clause
# copyright-holders:Mattis Lind
"""Run SADIE MWDCII diagnostics over the console on a disposable disk."""

import argparse
import os
import re
from pathlib import Path
import socket
import subprocess
import tempfile
import time


def check_scan_coverage(output, packet_path, diagnostic):
    """Require the guest's logical packets to cover the whole mounted medium."""
    geometry = re.search(rb"#blocks=(\d+)\s+#cyls=(\d+)\s+#heads=(\d+)\s+#sectors/track=(\d+)", output)
    assert geometry, "scan did not report drive geometry"
    blocks, _, _, sectors = map(int, geometry.groups())
    tracks = (blocks + sectors - 1) // sectors
    repetitions = 3 if diagnostic == "MDCMEDIA" else 1
    kinds = ("read", "write") if diagnostic == "MDCMEDIA" else ("read",)
    for kind in kinds:
        pattern = (rf"unit0-{kind} commands=(\d+) blocks=(\d+) unique=(\d+) first=(\d+) last=(\d+)")
        stats = re.search(pattern, packet_path.read_text())
        assert stats, f"scan has no observed {kind} packets"
        assert tuple(map(int, stats.groups())) == (tracks * repetitions, blocks * repetitions, blocks, 0, blocks - 1), \
            f"{kind} packets did not cover every logical block the expected number of times"
    assert re.search(rb"READ\s+=\s+" + str(tracks * repetitions).encode() + rb"\s", output), "incomplete read count"
    if diagnostic == "MDCMEDIA":
        assert re.search(rb"WRITE\s+=\s+" + str(tracks * repetitions).encode() + rb"\s", output), "incomplete write count"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=Path("s8000d"))
    parser.add_argument("--output", type=Path, default=Path("build/s8000-sadie"))
    parser.add_argument("--disk", type=Path, help="disposable writable image; MDCTEST destroys its contents")
    parser.add_argument("--tape", type=Path, help="boot unmodified SADIE from a three-track TAP through TCC")
    parser.add_argument("--test", type=int, choices=(1, 2), default=1)
    parser.add_argument("--diagnostic", choices=("MDCFMT", "MDCMEDIA", "MDCTEST", "MDCMON", "MDCCRC"), default="MDCTEST")
    parser.add_argument("--format", action="store_true", help="run MDCFMT with five scans and automatic mapping")
    parser.add_argument("--timeout", type=int, default=240, help="wall time limit in seconds; allow 1800 or more for formatting")
    args = parser.parse_args()
    if args.format:
        args.diagnostic = "MDCFMT"
    args.format = args.diagnostic == "MDCFMT"
    if args.format and not args.disk:
        parser.error("--format requires --disk")
    if args.diagnostic != "MDCTEST" and not args.disk:
        parser.error("this diagnostic requires a disposable --disk image")
    number = {"MDCFMT": 2, "MDCMEDIA": 3, "MDCTEST": 4, "MDCMON": 5, "MDCCRC": 6}[args.diagnostic]
    args.output.mkdir(parents=True, exist_ok=True)
    output = bytearray()
    with tempfile.TemporaryDirectory(prefix="control-", dir=args.output) as control_dir, socket.create_server(("127.0.0.1", 0)) as listener:
        listener.settimeout(30)
        port = listener.getsockname()[1]
        command = [str(args.mame.resolve()), "s8000", "-slot_disk", "mwdcii",
                   "-rompath", "roms", "-slot_cpu:cpu_a:sio0:chb:console", "null_modem",
                   "-bitb", f"socket.127.0.0.1:{port}", "-video", "none", "-sound", "none",
                   "-cfg_directory", str(args.output / "cfg"),
                   "-nothrottle", "-skip_gameinfo", "-autoboot_delay", "0",
                   "-autoboot_script", "scripts/s8000/native_tape.lua" if args.tape else "scripts/s8000/sadie.lua", "-seconds_to_run", str(max(600, args.timeout * 10))]
        if args.tape:
            command += ["-slot_tape", "tcc", "-tape1", str(args.tape)]
        if args.disk:
            command += ["-hard1", str(args.disk)]
        stop_file = Path(control_dir).resolve() / "stop"
        with (args.output / "mame.log").open("w") as log, (args.output / "console.txt").open("wb") as transcript:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                       env=dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy", SADIE_STOP_FILE=str(stop_file),
                                                MWDCII_SUMMARY_FILE=str((args.output / "packets.txt").resolve())))
            try:
                with listener.accept()[0] as console:
                    console.settimeout(0.5)
                    deadline, stage, start = time.monotonic() + args.timeout, 0, 0
                    setting_parameter = False
                    tape_booted = False
                    monitor_commands = iter((b"HELP", b"RDDRV", b"DRDY", b"SUNIT 0", b"HOME", b"BMODE", b"SEEK 134",
                        b"FILL 1 0 4 4660", b"WRITE 1 0 4 134", b"READ 1 4096 4 134", b"COMPARE 1 0 1 4096 4",
                        b"RAND 1 0 4", b"WRITE 1 0 4 134", b"READ 1 4096 4 134", b"COMPARE 1 0 1 4096 4",
                        b"RDMAP 1 8192", b"RMODE", b"SEEK 20", b"FILL 1 0 256 23130",
                        b"WRITE 1 0 256 20 0 0", b"READ 1 4096 256 20 0 0", b"COMPARE 1 0 1 4096 256",
                        b"BMODE", b"DINT", b"DRDY", b"EINT", b"DRDY", b"INITMDC", b"RDDRV", b"STATUS 0", b"REC", b"QUIT"))
                    while time.monotonic() < deadline and process.poll() is None:
                        try:
                            data = console.recv(8192)
                        except TimeoutError:
                            continue
                        if not data:
                            break
                        output.extend(data)
                        transcript.write(data)
                        transcript.flush()
                        tail = output[start:]
                        if args.tape and not tape_booted and b"Press START to Load System" in output and output.endswith(b" "):
                            console.sendall(b"Z T\r")
                            tape_booted = True
                            start = len(output)
                            continue
                        if setting_parameter and b"]=>" in tail:
                            console.sendall((b"0" if args.format else str(args.test).encode()) + b"\r")
                            setting_parameter = False
                            start = len(output)
                            continue
                        if b"Enter your choice ]=>" in tail and stage < 3:
                            if stage == 2 and (args.format or (args.diagnostic == "MDCTEST" and args.test == 2)) and b"SET A PARAMETER" not in output:
                                console.sendall(b"3\r" if args.format else b"2\r")
                                setting_parameter = True
                                start = len(output)
                                continue
                            console.sendall((b"T\r", str(number).encode() + b"\r", b"\r")[stage])
                            stage += 1
                            start = len(output)
                        elif stage >= 3 and b"Command?" in tail and args.diagnostic == "MDCMON":
                            reply = next(monitor_commands, None)
                            if reply is not None:
                                console.sendall(reply + b"\r")
                                start = len(output)
                        elif stage >= 3 and b"Press <CR> to continue" in tail:
                            console.sendall(b"\r")
                            start = len(output)
                        elif stage == 3 and b"LOSING ALL data on disk" in tail and output.endswith(b"]=>"):
                            console.sendall(b"Y\r")
                            stage += 1
                            start = len(output)
                        elif b"Hit <CR> to return to COMMAND LEVEL" in tail:
                            break
                        elif args.tape and stage >= 3 and b"*****  COMMAND LEVEL  *****" in tail:
                            break
                    # Keep the socket connected until MAME has processed the
                    # stop request; a closed console can stall its reconnect.
                    if process.poll() is None:
                        stop_file.write_text("stop\n")
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            pass
            finally:
                (args.output / "console.txt").write_bytes(output)
                if process.poll() is None:
                    stop_file.write_text("stop\n")
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
    assert f"This is {args.diagnostic}".encode() in output, "diagnostic did not start"
    if args.diagnostic != "MDCTEST":
        assert f"EXITING {args.diagnostic}".encode() in output, "diagnostic did not complete; inspect logs"
        assert b"ERROR IN:" not in output, "diagnostic reported errors; inspect logs"
        assert re.search(rb"TOTAL ERRORS FOR UNIT 0:\s+0\s", output), "diagnostic did not report zero errors"
    if args.format:
        assert b"Hit <CR> to return to COMMAND LEVEL" in output, "MDCFMT did not complete; inspect logs"
        assert b"ERROR IN:" not in output, "MDCFMT reported an error; inspect logs"
        assert re.search(rb"TOTAL ERRORS FOR UNIT 0:\s+0\s", output), "MDCFMT did not report zero errors"
        assert b"NO BAD SECTORS ON DISK!!" in output, "MDCFMT found bad sectors"
        assert re.search(rb"FMT\s+=\s+[1-9][0-9]*\s", output), "MDCFMT did not format any tracks"
        print(f"PASS: SADIE MDCFMT; transcript in {args.output}")
        return
    if args.diagnostic in ("MDCMEDIA", "MDCCRC"):
        assert re.search(rb"READ\s+=\s+[1-9][0-9]*\s", output), "diagnostic performed no reads"
        if args.diagnostic == "MDCMEDIA":
            assert re.search(rb"WRITE\s+=\s+[1-9][0-9]*\s", output), "media diagnostic performed no writes"
            for pattern in (b"'aaaa'", b"'ffff'", b"'6db6'"):
                assert pattern in output, "media diagnostic skipped a pattern"
        if args.tape:
            check_scan_coverage(output, args.output / "packets.txt", args.diagnostic)
        print(f"PASS: SADIE {args.diagnostic}; transcript in {args.output}")
        return
    if args.diagnostic == "MDCMON":
        for error in (b"BAD COMMAND", b"BAD PARAMETER", b"HARD ERR", b"SOFT ERR", b"WARNING -"):
            assert error not in output, "MDCMON command failed"
        assert output.count(b"COMPARE:") == 3 and b"CMP ERR:" not in output, "monitor did not complete all three comparisons"
        for name in (b"READ", b"WRITE"):
            assert re.search(name + rb"\s+=\s+3\s", output), "monitor did not complete all three transfers"
        print(f"PASS: SADIE MDCMON; transcript in {args.output}")
        return
    if args.disk:
        assert b"ERROR IN:" not in output, "MDCTEST reported an error; inspect logs"
        assert re.search(rb"TOTAL ERRORS FOR UNIT 0:\s+0\s", output), "MDCTEST did not report a completed lap with zero errors"
        commands = 128 if args.test == 1 else 50
        assert f"Now doing test# {args.test}".encode() in output, "wrong MDCTEST subtest ran"
        for name in (b"READ", b"WRITE"):
            assert re.search(name + rb"\s+=\s+" + str(commands).encode() + rb"\s", output), "MDCTEST did not complete the expected transfers"
    else:
        assert b"Power-up Test, Drive Not Selected" in output, "MDCTEST did not reach the expected no-drive result; inspect logs"
    assert b"EXITING MDCTEST" in output or (args.tape and output.count(b"*****  COMMAND LEVEL  *****") >= 2), "MDCTEST did not return"
    print(f"PASS: SADIE MDCTEST {'disk test' if args.disk else 'no-drive initialization'}; transcript in {args.output}")


if __name__ == "__main__":
    main()
