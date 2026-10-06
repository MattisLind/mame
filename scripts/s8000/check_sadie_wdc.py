#!/usr/bin/env python3
# license:BSD-3-Clause
# copyright-holders:Mattis Lind
"""Run unmodified SADIE Winchester diagnostics through TCC on a scratch disk."""

import argparse
import os
import re
from pathlib import Path
import socket
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=Path("s8000d"))
    parser.add_argument("--output", type=Path, default=Path("build/s8000-wdc-sadie"))
    parser.add_argument("--disk", type=Path, help="disposable writable image; diagnostics destroy its contents")
    parser.add_argument("--tape", type=Path, default=Path("roms/tapes/sadie-3.5.tap"), help="boot unmodified SADIE from a three-track TAP through TCC")
    parser.add_argument("--diagnostic", choices=("WDCFMT", "WDCMEDIA", "WDCTEST", "WDCMON", "WDCCRC"), default="WDCTEST")
    parser.add_argument("--test", type=int, choices=(1, 2), default=1)
    parser.add_argument("--timeout", type=int, default=240, help="wall time limit in seconds; allow 1800 or more for a full WDCMEDIA lap")
    args = parser.parse_args()
    if not args.disk:
        parser.error("--disk must name a disposable writable WDC image")
    number = {"WDCFMT": 12, "WDCMEDIA": 13, "WDCTEST": 14, "WDCMON": 15, "WDCCRC": 16}[args.diagnostic]
    args.output.mkdir(parents=True, exist_ok=True)
    output = bytearray()
    with tempfile.TemporaryDirectory(prefix="control-", dir=args.output) as control_dir, socket.create_server(("127.0.0.1", 0)) as listener:
        listener.settimeout(30)
        port = listener.getsockname()[1]
        command = [str(args.mame.resolve()), "s8000", "-slot_disk", "wdc",
                   "-rompath", "roms", "-slot_cpu:cpu_a:sio0:chb:console", "null_modem",
                   "-bitb", f"socket.127.0.0.1:{port}", "-video", "none", "-sound", "none",
                   "-nothrottle", "-skip_gameinfo", "-autoboot_delay", "0",
                   "-autoboot_script", "scripts/s8000/native_tape.lua" if args.tape else "scripts/s8000/sadie.lua", "-seconds_to_run", str(max(600, args.timeout * 10))]
        if args.tape:
            command += ["-slot_tape", "tcc", "-tape1", str(args.tape)]
        if args.disk:
            command += ["-hard1", str(args.disk)]
        stop_file = Path(control_dir).resolve() / "stop"
        with (args.output / "mame.log").open("w") as log, (args.output / "console.txt").open("wb") as transcript:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                       env=dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy", SADIE_STOP_FILE=str(stop_file), WDC_TRACE="1"))
            try:
                with listener.accept()[0] as console:
                    console.settimeout(0.5)
                    deadline, stage, start = time.monotonic() + args.timeout, 0, 0
                    setting_parameter = False
                    tape_booted = False
                    monitor_commands = iter((b"HELP", b"FIRMID", b"EXTID", b"SUNIT 0", b"NULL", b"HOME", b"SEEK 20",
                        b"FILL 1 0 256 4660", b"WRITE 1 0 256 20 0 0", b"READ 1 1024 256 20 0 0",
                        b"COMPARE 1 0 1 1024 256", b"FRD 1 2048 261 20 0 0", b"FWR 20 0 0 0", b"CEMODE", b"RDDT", b"EXITCE", b"STAT", b"QUIT"))
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
                            console.sendall((str(args.test).encode()) + b"\r")
                            setting_parameter = False
                            start = len(output)
                            continue
                        if b"Enter your choice ]=>" in tail and stage < 3:
                            if stage == 2 and (args.diagnostic == "WDCTEST" and args.test == 2) and b"SET A PARAMETER" not in output:
                                console.sendall(b"2\r")
                                setting_parameter = True
                                start = len(output)
                                continue
                            console.sendall((b"T\r", str(number).encode() + b"\r", b"\r")[stage])
                            stage += 1
                            start = len(output)
                        elif stage >= 3 and b"Command?" in tail and args.diagnostic == "WDCMON":
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
    assert b"no controller response" not in output and b"DISK NOT RESPONDING" not in output, "WDC initialization failed"
    assert b"ERROR IN:" not in output, "diagnostic reported errors"
    assert f"EXITING {args.diagnostic}".encode() in output, "diagnostic did not complete"
    if args.diagnostic != "WDCMON":
        assert re.search(rb"TOTAL ERRORS:\s+0\s", output), "completed lap did not report zero errors"
        geometry = re.search(rb"#cyls\s*=\s*(\d+)\s+#heads\s*=\s*(\d+)", output)
        assert geometry, "drive geometry was not reported"
        tracks = int(geometry[1]) * int(geometry[2])
        expected = {"WDCFMT": (1, 0), "WDCCRC": (tracks, 0), "WDCMEDIA": (tracks * 3, tracks * 3),
                    "WDCTEST": (128, 128) if args.test == 1 else (10, 10)}[args.diagnostic]
        counters = re.search(rb"READS\s*=\s*(\d+)\s+WRITES\s*=\s*(\d+)", output)
        assert counters and tuple(map(int, counters.groups())) == expected, "diagnostic did not exercise the expected transfers"
        if args.diagnostic == "WDCTEST":
            assert f"Now doing test# {args.test}".encode() in output, "wrong WDCTEST subtest ran"
        elif args.diagnostic == "WDCFMT":
            assert re.search(rb"FMT\s*=\s*1\s", output), "format command did not run"
    else:
        for error in (b"BAD COMMAND", b"BAD PARAMETER", b"HARD ERR", b"SOFT ERR", b"CAN'T", b"Completion Status = 8H", b"Completion Status = 1H"):
            assert error not in output, "WDCMON reported an error"
        assert b"ERROR COUNT = 0" in output, "monitor did not verify comparison"
    print(f"PASS: SADIE {args.diagnostic}; transcript in {args.output}")


if __name__ == "__main__":
    main()
