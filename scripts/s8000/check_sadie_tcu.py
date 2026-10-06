#!/usr/bin/env python3
# license:BSD-3-Clause
# copyright-holders:Mattis Lind
"""Run SADIE's cartridge diagnostics on a disposable copy of its TAP."""

import argparse
import os
import re
from pathlib import Path
import shutil
import socket
import subprocess
import struct
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostic", choices=("TCUMON", "TCOM", "TEX"), default="TCUMON")
    parser.add_argument("--tape", type=Path, default=Path("roms/tapes/sadie-3.5.tap"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    tape = args.output / "scratch-sadie.tap"
    shutil.copyfile(args.tape, tape)
    output = bytearray()
    with tempfile.TemporaryDirectory(prefix="control-", dir=args.output) as control, socket.create_server(("127.0.0.1", 0)) as server:
        server.settimeout(30)
        stop = Path(control).resolve() / "stop"
        mount = Path(control).resolve() / "mount"
        command = [str(Path("s8000d").resolve()), "s8000", "-slot_disk", "mwdcii", "-slot_tape", "tcc",
                   "-tape1", str(tape), "-rompath", "roms", "-cfg_directory", str(args.output / "cfg"),
                   "-slot_cpu:cpu_a:sio0:chb:console", "null_modem",
                   "-bitb", f"socket.127.0.0.1:{server.getsockname()[1]}", "-video", "none", "-sound", "none",
                   "-nothrottle", "-skip_gameinfo", "-autoboot_delay", "0",
                   "-autoboot_script", "scripts/s8000/native_tape.lua", "-seconds_to_run", str(args.timeout * 10)]
        with (args.output / "mame.log").open("w") as log, (args.output / "console.txt").open("wb") as transcript:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                       env=dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy",
                                                SADIE_STOP_FILE=str(stop), SADIE_TAPE_MOUNT_FILE=str(mount), SADIE_TAPE_SMALL="1" if args.diagnostic == "TEX" else "0", TCC_TRACE="1"))
            try:
                with server.accept()[0] as console:
                    console.settimeout(0.2)
                    deadline, stage, start = time.monotonic() + args.timeout, 0, 0
                    setting_parameter = False
                    parameter_set = False
                    number = {"TCUMON": 17, "TCOM": 18, "TEX": 19}[args.diagnostic]
                    monitor_commands = iter((b"HELP", b"DIAG1", b"LOAD", b"MODE 1",
                        b"STRK 0", b"READ 1 8000 4000", b"STRK 1", b"READ 1 8000 4000",
                        b"STRK 2", b"READ 1 8000 4000", b"SKBF 1", b"SKBR 1",
                        b"READ 1 8000 4000", b"REWIND", b"SKFF 1", b"READ 1 8000 4000",
                        b"STAT", b"Q"))
                    while time.monotonic() < deadline and process.poll() is None:
                        data = b""
                        try:
                            data = console.recv(8192)
                        except TimeoutError:
                            pass
                        if data:
                            output.extend(data); transcript.write(data); transcript.flush()
                        pending = args.output / "input.txt"
                        if pending.exists():
                            console.sendall(pending.read_bytes().replace(b"\n", b"\r")); pending.unlink()
                            start = len(output)
                        tail = output[start:]
                        reply = None
                        if setting_parameter and b"]=>" in tail:
                            reply = b"10a"  # one read pass, zero write retries, ten read retries
                            setting_parameter = False
                            parameter_set = True
                        elif stage == 3 and args.diagnostic == "TEX" and not parameter_set and b"Enter your choice ]=>" in tail:
                            reply = b"2"
                            setting_parameter = True
                        elif stage == 0 and b"Press START to Load System" in tail and output.endswith(b" "):
                            reply, stage = b"Z T", 1
                        elif stage <= 3 and b"Enter your choice ]=>" in tail and output.endswith(b"]=>"):
                            reply = {1: b"T", 2: str(number).encode(), 3: b""}[stage]
                            stage += 1
                        elif stage >= 4 and b"Press <RETURN> to continue" in tail:
                            reply = b""
                        elif stage >= 4 and b"THE TAPE WILL BE OVERWRITTEN!" in tail and output.endswith(b"]=>"):
                            scratch = (args.output / "scratch-blank.tap").resolve()
                            scratch.write_bytes(struct.pack("<5I", 0x70000000, 0x70000001, 0x70000002, 0x70000003, 0xffffffff))
                            mount.write_text(str(scratch) + "\n")
                            while not Path(str(mount) + ".ready").exists():
                                assert process.poll() is None and time.monotonic() < deadline, "scratch mount failed"
                                time.sleep(0.05)
                            reply = b"Y"
                        elif args.diagnostic == "TCUMON" and stage >= 4 and b"CMD?" in tail:
                            reply = next(monitor_commands, None)
                        elif stage >= 4 and b"Hit <CR> to return to COMMAND LEVEL" in tail:
                            break
                        elif stage >= 4 and b"*****  COMMAND LEVEL  *****" in tail:
                            break
                        if args.diagnostic == "TCOM" and b"Please write enable it and re-insert" in tail:
                            break
                        if reply is not None:
                            console.sendall(reply + b"\r")
                            start = len(output)
                    stop.write_text("stop\n")
                    process.wait(timeout=5)
            finally:
                if process.poll() is None:
                    stop.write_text("stop\n")
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
    assert f"This is {args.diagnostic}".encode() in output, "diagnostic did not start; inspect transcript"
    if args.diagnostic == "TCUMON":
        assert b"Exiting TCUMON" in output, "TCUMON command sequence did not finish"
        for error in (b"would not initialize", b"Incorrect initialization", b"POWER-ON ERROR", b"BAD COMMAND", b"BAD PARAMETER",
                      b"command rejected", b"bad dma parameters", b"invalid command", b"inappropriate command"):
            assert error not in output, "TCUMON reported an error; inspect transcript"
        print(f"PASS: TCUMON power-on, DIAG1, track reads and positioning; transcript in {args.output / 'console.txt'}")
    else:
        assert b"write protected" not in output, "scratch tape unexpectedly protected"
        assert (b"EXITING TCOM" if args.diagnostic == "TCOM" else b"Exiting TEX") in output, "diagnostic did not finish"
        for error in (b"would not initialize", b"Incorrect initialization", b"POWER-ON ERROR", b"ERROR IN:"):
            assert error not in output, "diagnostic reported an error; inspect transcript"
        if args.diagnostic == "TCOM":
            assert b"LAP 1 COMPLETE, ERROR COUNT = 0" in output, "TCOM lap did not pass"
            for module in range(1, 7):
                assert f"This is module {module}.".encode() in output, "TCOM omitted a module"
        else:
            assert b"track= 3" in output, "TEX did not exercise the last track"
            totals = re.findall(rb"hard write=\s*(\d+)\s+hard read=\s*(\d+)\s+data compare errors=\s*(\d+)", output)
            soft = re.findall(rb"soft write=\s*(\d+)\s+soft read=\s*(\d+)", output)
            assert totals and soft and all(int(n) == 0 for row in totals + soft for n in row), "TEX reported media errors"
        print(f"PASS: {args.diagnostic} completed with zero errors; transcript in {args.output / 'console.txt'}")


if __name__ == "__main__":
    main()
