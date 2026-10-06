#!/usr/bin/env python3
# license:BSD-3-Clause
# copyright-holders:Mattis Lind
"""Run the original ZEUS standalone installation utilities on a formatted MWDCII CHD."""

import argparse
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import zipfile


def check_three_disks(output):
    for marker in (b"FORWARD_PASS", b"REVERSE_PASS", b"TAPE_CMP_STATUS=0",
                   b"FORWARD_CMP_STATUS=0", b"REVERSE_CMP_STATUS=0",
                   b"ARCHIVE_SEED_STATUS=0", b"ARCHIVE_PAYLOAD_STATUS=0", b"THREE_STRESS_DONE"):
        assert b"\r\n" + marker + b"\r\n" in output, f"missing successful stress result: {marker.decode()}"
    for error in (b"I/O error", b"BAD SUPER BLOCK", b"FILE SYSTEM WAS MODIFIED", b"No space", b"not found", b"cannot open",
                  b"labelit:", b"labelit cannot", b"Usage: labelit", b"WARNING!! - mounting:"):
        assert error not in output, f"stress test reported {error.decode()}; inspect logs"
    assert output.count(b"** Phase 5 - Check Free List") >= 6, "filesystem checks did not finish"


def check_tape_write(output):
    source = re.search(rb"\r\nTCC_SOURCE\r\n(?:#[0-9]+ od -x /tmp/w\r\n)?(.*?)\r\n0000200\r\n", output, re.S)
    readback = re.search(rb"\r\nTCC_READ\r\n(?:#[0-9]+ od -x /tmp/r\r\n)?(.*?)\r\n0000200\r\n", output, re.S)
    assert source and readback and source[1] == readback[1], "kernel CT write/read dumps differ"
    assert output.count(b"1+0 records in\r\n1+0 records out") >= 3, "kernel CT write/read transfers did not complete"
    for error in (b"I/O error", b"read error", b"write error", b"cannot open", b"fatal error"):
        assert error not in output, "kernel CT driver reported an error"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=Path("s8000d"))
    parser.add_argument("--output", type=Path, default=Path("build/s8000-zeus-install"))
    parser.add_argument("--disk", type=Path, required=True, help="writable image; installation replaces its filesystems")
    parser.add_argument("--smd-disk", type=Path, help="attach an SMDC in slot_disk2 with this unit-0 image")
    parser.add_argument("--wdc-disk", type=Path, help="attach an 8-inch WDC in slot_disk3 with this unit-0 image")
    parser.add_argument("--command-file", type=Path, help="additional shell commands, one per prompt, after boot or multi-user login")
    parser.add_argument("--check-three-disks", action="store_true", help="in multiuser, FORMAT disposable SMDC/WDC partitions, run concurrent disk/tape stress tests and check results")
    parser.add_argument("--stress-repeat", type=int, choices=range(1, 9), default=1, help="with --check-three-disks, concatenate this many seed copies (about 222 KB each) per payload")
    parser.add_argument("--stage", choices=("install", "restore", "setup", "boot", "multiuser"), default="install")
    parser.add_argument("--tape", type=Path, help="TCC cartridge; used for boot/install unless --boot-tape or --check-three-disks selects a separate boot tape")
    parser.add_argument("--boot-tape", type=Path, help="with boot/multiuser --tape, boot from this protected TAP, then mount --tape as the working cartridge; three-disk stress defaults to the original installation TAP")
    parser.add_argument("--check-tape", action="store_true", help="with --stage boot/multiuser --tape, read a record using the kernel CT driver")
    parser.add_argument("--check-tape-write", action="store_true", help="with boot/multiuser, overwrite a disposable --tape and verify a kernel CT write/read round trip")
    parser.add_argument("--date", default="10/03/84", help="ZEUS setup date (MM/DD/YY); use a date its original utilities support")
    parser.add_argument("--time", default="12:00", help="ZEUS setup time (HH:MM)")
    parser.add_argument("--trace", action="store_true", help="log kernel calls and process state for boot diagnosis")
    parser.add_argument("--live-console", action="store_true", help="stream the serial transcript to stdout as it arrives; also save console.txt")
    parser.add_argument("--window", action="store_true", help="show a passive MAME console window while the script supplies all serial input")
    parser.add_argument("--timeout", type=int, default=1800, help="wall time limit in seconds")
    args = parser.parse_args()
    if args.check_three_disks:
        if args.stage != "multiuser" or not (args.smd_disk and args.wdc_disk and args.tape) or args.command_file:
            parser.error("--check-three-disks requires --stage multiuser, --smd-disk, --wdc-disk, a disposable --tape, and no --command-file")
        args.command_file = Path("scripts/s8000/stress_three_disks.commands")
    if args.command_file and args.stage not in ("boot", "multiuser"):
        parser.error("--command-file requires --stage boot/multiuser")
    if (args.check_tape or args.check_tape_write) and (not args.tape or args.stage not in ("boot", "multiuser")):
        parser.error("tape checks require --stage boot/multiuser and --tape")
    if args.check_three_disks and not args.boot_tape:
        args.boot_tape = Path("roms/tapes/zeus-3.21-install.tap")
    if args.boot_tape:
        if not args.tape or args.stage not in ("boot", "multiuser"):
            parser.error("--boot-tape requires --stage boot/multiuser and a working --tape")
        if args.check_tape:
            parser.error("--check-tape expects the installation bootstrap on the working tape; omit it with --boot-tape")
        if args.boot_tape.resolve() == args.tape.resolve():
            parser.error("--boot-tape and working --tape must be different files")
        for source in (args.boot_tape, args.tape):
            if not source.is_file():
                parser.error(f"missing tape image: {source}")
        if not os.access(args.tape, os.W_OK):
            parser.error(f"working tape is not writable: {args.tape}")
    tape_check = b"dd if=/dev/rct0a of=/tmp/tcc-boot bs=512 count=1; od -x /tmp/tcc-boot; rm /tmp/tcc-boot; " if args.check_tape else b""
    if args.check_tape_write:
        tape_check += (b"dd if=/zeus of=/tmp/w count=1; dd if=/tmp/w of=/dev/rct0a count=1; "
                       b"dd if=/dev/rct0a of=/tmp/r count=1; echo TCC_SOURCE; od -x /tmp/w; echo TCC_READ; od -x /tmp/r; rm /tmp/w /tmp/r; ")
    plan = []
    if args.stage == "install":
        plan = [(b"Boot\r\n: ", b"ct(0,2)"), (b"standard configuration", b"y")]
        for size, device in [(b"6000", b"md(0,15200)"), (b"12000", b"md(0,0)")]:
            plan += [(b"Boot\r\n: ", b"ct(0,3)"), (b"file sys size: ", size),
                     (b"file system: ", device), (b"interleaving factor (m n): ", b"7 102")]
    if args.stage in ("install", "restore"):
        plan += [(b"Boot\r\n: ", b"ct(0,4)"), (b"factory-supplied Zilog release tape", b"y"),
                 (b"want instructions", b"n"), (b"configuration information in block 0", b"y"),
                 (b"restor the root filesystem", b"y"), (b"restor the /usr filesystem", b"y"),
                 (b"md type disk: ", b""), (b"disk unit 0: ", b""), (b"offset 15200: ", b""),
                 (b"md type disk: ", b""), (b"disk unit 0: ", b""), (b"offset 0: ", b""),
                 (b"Tape unit number? ", b"0"), (b"OK to restor", b"y")]
    if args.stage in ("install", "setup"):
        plan += [(b"Boot\r\n: ", b"md(0,15200)zeus"),
                 (b"Single-User Mode", b"chmod 700 /etc/makenewfs; /etc/makenewfs"),
                 (b"Enter Date (MM/DD/YY or <cr>): ", args.date.encode()),
                 (b"Enter Time (HH:MM): ", args.time.encode()),
                 (b"Do you have any SMD disk drives ? (y or n) ", b"n"),
                 (b"Do you have any  ZD disk drives ? (y or n) ", b"n"),
                 (b"Do you want to proceed? (y or n) ", b"y"),
                 (b"This file has been made non-executable.", b"sync; echo MWDCII_SETUP_DONE")]
    elif args.stage == "boot":
        plan = [(b"Boot\r\n: ", b"md(0,15200)zeus"),
                (b"Single-User Mode", b"echo MWDCII_BOOT_OK; uname -a")]
    elif args.stage == "multiuser":
        plan = [(b"Boot\r\n: ", b"md(0,15200)zeus"),
                (b"Single-User Mode", b"/etc/INIT 2"),
                (b"Enter Date (MM/DD/YY or <cr>): ", args.date.encode()),
                (b"Enter Time (HH:MM): ", args.time.encode()),
                (b"login: ", b"zeus"), (b"Password:", b"jupiter"),
                (b"", b"echo MWDCII_LOGIN_OK; uname -a; /etc/mount")]
    if args.stage in ("boot", "multiuser"):
        # The historical console rejects long input lines. Keep tape checks
        # separate from login verification and send one command per prompt.
        plan += [(b"", item.strip()) for item in tape_check.split(b";") if item.strip()]
        marker = b"MWDCII_BOOT_CHECK_DONE" if args.stage == "boot" else b"MWDCII_MULTIUSER_DONE"
        plan.append((b"", b"sync; echo " + marker))
    if args.command_file:
        marker = b"MWDCII_BOOT_CHECK_DONE" if args.stage == "boot" else b"MWDCII_MULTIUSER_DONE"
        plan[-1] = (plan[-1][0], plan[-1][1].replace(b"sync; echo " + marker, b"sync"))
        plan += [(b"", line.encode()) for line in args.command_file.read_text().splitlines()
                 if line.strip() and not line.lstrip().startswith("#")]
        if args.check_three_disks:
            repeated = b"cat /z/stress/seed >> /z/stress/payload"
            left = args.stress_repeat - 1
            selected = []
            for item in plan:
                if item[1] == repeated:
                    if not left:
                        continue
                    left -= 1
                selected.append(item)
            plan = selected
        # sync queues writes in this kernel. Give the guest time to finish
        # them before the harness exits MAME and closes the disk images.
        plan.append((b"", b"sync; sleep 5; sync; sleep 5; echo " + marker))
    args.output.mkdir(parents=True, exist_ok=True)
    output = bytearray()
    timed_out = False
    last_command = "waiting for the console connection"
    with tempfile.TemporaryDirectory(prefix="control-", dir=args.output) as control_dir, socket.create_server(("127.0.0.1", 0)) as listener:
        listener.settimeout(30)
        port = listener.getsockname()[1]
        console_device = "null_modem_terminal" if args.window else "null_modem"
        command = [str(args.mame.resolve()), "s8000", "-slot_disk", "mwdcii",
                   "-rompath", "roms", "-slot_cpu:cpu_a:sio0:chb:console", console_device,
                   "-bitb", f"socket.127.0.0.1:{port}", "-sound", "none",
                   "-nothrottle", "-skip_gameinfo", "-autoboot_delay", "0",
                   "-autoboot_script", "scripts/s8000/native_tape.lua" if args.tape else "scripts/s8000/zeus_install.lua", "-seconds_to_run", str(args.timeout * 10)]
        command += ["-window", "-ui_active", "-video", "auto"] if args.window else ["-video", "none"]
        if args.tape:
            initial_tape = args.tape
            if args.boot_tape:
                # ZIP members mount read-only. The original installation TAP
                # stays protected even if the guest attempts to write it.
                archive = Path(control_dir).resolve() / "boot.zip"
                with zipfile.ZipFile(archive, "w") as boot:
                    boot.write(args.boot_tape, "boot.tap")
                initial_tape = archive / "boot.tap"
                print(f"Booting from protected {args.boot_tape}; working tape: {args.tape}", flush=True)
            command += ["-slot_tape", "tcc", "-tape1", str(initial_tape)]
        if args.disk:
            command += ["-hard1", str(args.disk)]
        if args.smd_disk:
            command += ["-slot_disk2", "smdc", "-hard5", str(args.smd_disk)]
        if args.wdc_disk:
            command += ["-slot_disk3", "wdc", "-hard9" if args.smd_disk else "-hard5", str(args.wdc_disk)]
        cfg = args.output / "cfg"
        cfg.mkdir(exist_ok=True)
        (cfg / "s8000.cfg").write_text('<mameconfig version="10"><system name="s8000"><input><port tag=":slot_cpu:cpu_a:SEGJP" type="CONFIG" mask="1" defvalue="0" value="1"/><port tag=":slot_cpu:cpu_a:DIPSW" type="DIPSWITCH" mask="192" defvalue="64" value="128"/></input></system></mameconfig>')
        command += ["-cfg_directory", str(cfg)]
        stop_file = Path(control_dir).resolve() / "stop"
        serial_file = Path(control_dir).resolve() / "console-7e1"
        mount_file = Path(control_dir).resolve() / "mount-tape"
        with (args.output / "mame.log").open("w") as log, (args.output / "console.txt").open("wb") as transcript:
            run_started = time.monotonic()
            env = dict(os.environ, SDL_AUDIODRIVER="dummy", SADIE_STOP_FILE=str(stop_file),
                       ZEUS_CONSOLE_7E1_FILE=str(serial_file), ZEUS_CONSOLE_DEVICE=console_device,
                       ZEUS_TRACE="1" if args.trace else os.environ.get("ZEUS_TRACE", "0"))
            if args.boot_tape:
                env["SADIE_TAPE_MOUNT_FILE"] = str(mount_file)
                env["SADIE_TAPE_SMALL"] = "0"
            if not args.window:
                env["SDL_VIDEODRIVER"] = "dummy"
            elif env.get("SDL_VIDEODRIVER") == "dummy":
                del env["SDL_VIDEODRIVER"]
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
            try:
                with listener.accept()[0] as console:
                    console.settimeout(0.5)
                    deadline, stage, start = time.monotonic() + args.timeout, 0, 0
                    tape_booted = False
                    while time.monotonic() < deadline and process.poll() is None:
                        try:
                            data = console.recv(8192)
                        except TimeoutError:
                            pending = args.output / "input.txt"
                            if pending.exists():
                                command_input = pending.read_bytes()
                                pending.unlink()
                                console.sendall(command_input.replace(b"\n", b"\r"))
                            continue
                        if not data:
                            break
                        # Getty changes the console from 8N1 to 7E1.  Until
                        # its first prompt, the old receiver sees parity as
                        # bit 7; decode that prompt before switching the link.
                        if args.stage == "multiuser" and stage == 4:
                            data = bytes(value & 0x7f for value in data)
                        output.extend(data)
                        transcript.write(data)
                        transcript.flush()
                        if args.live_console:
                            sys.stdout.buffer.write(data)
                            sys.stdout.buffer.flush()
                        tail = output[start:]
                        if args.stage == "multiuser" and stage in (5, 6) and b"Login incorrect" in tail:
                            raise RuntimeError(f"ZEUS automatic login failed; inspect {args.output / 'console.txt'}")
                        if args.tape and not tape_booted and b"Press START to Load System" in output and output.endswith(b" "):
                            console.sendall(b"Z T\r")
                            tape_booted = True
                            start = len(output)
                            continue
                        if stage < len(plan) and plan[stage][0] in tail:
                            # Wait until the entire prompt has arrived before sending input.
                            shell_prompt = re.search(rb"#[0-9]+ $", output)
                            prompt_ending = output.endswith((b": ", b":", b"? ", b"(y or n) "))
                            if shell_prompt or (plan[stage][0] and prompt_ending):
                                if args.boot_tape and stage == 0:
                                    # The original secondary loader is now in
                                    # memory. Swap before loading ZEUS from disk,
                                    # so kernel tape operations use scratch media.
                                    mount_file.write_text(str(args.tape.resolve()) + "\n")
                                    mount_deadline = time.monotonic() + 10
                                    while not Path(str(mount_file) + ".ready").exists():
                                        if process.poll() is not None or time.monotonic() > mount_deadline:
                                            raise RuntimeError("working tape did not mount; inspect mame.log")
                                        time.sleep(0.01)
                                    print(f"Mounted working tape: {args.tape}", flush=True)
                                if args.stage == "multiuser" and stage == 4:
                                    serial_file.write_text("9600 7E1\n")
                                    serial_deadline = time.monotonic() + 5
                                    while not Path(str(serial_file) + ".ready").exists():
                                        if process.poll() is not None or time.monotonic() > serial_deadline:
                                            raise RuntimeError("console did not switch to 7E1; inspect logs")
                                        time.sleep(0.01)
                                console.sendall(plan[stage][1] + b"\r")
                                last_command = plan[stage][1].decode(errors="replace")
                                stage += 1
                                start = len(output)
                        tail = output[start:]
                        if stage == len(plan) and args.stage == "restore" and b"Boot\r\n: " in tail:
                            break
                        if stage == len(plan) and args.stage in ("install", "setup") and b"\r\nMWDCII_SETUP_DONE\r\n" in tail:
                            break
                        if stage == len(plan) and args.stage == "boot" and b"\r\nMWDCII_BOOT_CHECK_DONE\r\n" in tail:
                            break
                        if stage == len(plan) and args.stage == "multiuser" and b"\r\nMWDCII_MULTIUSER_DONE\r\n" in tail:
                            break
                        pending = args.output / "input.txt"
                        if pending.exists():
                            command_input = pending.read_bytes()
                            pending.unlink()
                            console.sendall(command_input.replace(b"\n", b"\r"))
                    timed_out = time.monotonic() >= deadline
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
            elapsed = time.monotonic() - run_started

    if timed_out:
        raise RuntimeError(f"ZEUS timed out after {elapsed:.1f} seconds; last command: {last_command!r}; "
                           f"inspect {args.output / 'console.txt'} and {args.output / 'mame.log'}")

    if args.stage in ("install", "restore"):
        assert b"Restoring / filesystem.\r\nDone." in output, "root restore did not complete; inspect logs"
        assert b"Restoring /usr filesystem.\r\nDone." in output, "/usr restore did not complete; inspect logs"
        for error in (b"disk write error", b"fatal error", b"tape read error", b"Can't open disk"):
            assert error not in output, "installer reported an error; inspect logs"
        print(f"PASS: ZEUS original root and /usr restore; transcript in {args.output}")
    if args.stage in ("install", "setup"):
        assert b"The /z and /tmp filesystems are initialized" in output, "auxiliary filesystem setup did not complete; inspect logs"
        assert b"\r\nMWDCII_SETUP_DONE\r\n" in output, "post-install setup did not complete and sync; inspect logs"
        for error in (b"Cannot determine model", b"Can't stat", b"cannot mount", b"I/O error"):
            assert error not in output, "post-install setup reported an error; inspect logs"
        print(f"PASS: ZEUS device aliases and /tmp, /z setup; transcript in {args.output}")
    elif args.stage == "boot":
        assert b"Single-User Mode" in output, "ZEUS did not reach single-user startup; inspect logs"
        assert b"\r\nMWDCII_BOOT_OK\r\n" in output, "ZEUS shell did not execute the boot check; inspect logs"
        assert b"\r\nMWDCII_BOOT_CHECK_DONE\r\n" in output, "ZEUS shell did not complete uname/sync; inspect logs"
        print(f"PASS: ZEUS MWDCII single-user boot and shell commands; transcript in {args.output}")
    elif args.stage == "multiuser":
        assert b"Multi-user Startup" in output, "ZEUS did not enter multi-user startup; inspect logs"
        assert re.search(rb"\r\n(?:#[0-9]+ )?MWDCII_LOGIN_OK\r\n", output), "ZEUS multi-user login did not execute commands; inspect logs"
        assert b"\r\nMWDCII_MULTIUSER_DONE\r\n" in output, "ZEUS multi-user check did not complete and sync; inspect logs"
        for error in (b"Can't stat", b"cannot mount", b"BAD SUPER BLOCK", b"I/O error", b"Login incorrect"):
            assert error not in output, "multi-user startup reported an error; inspect logs"
        print(f"PASS: ZEUS MWDCII multi-user login and shell commands; transcript in {args.output}")
    if args.check_tape:
        assert b"1+0 records in\r\n1+0 records out" in output and b"\r\n0000200\r\n" in output, "kernel CT read did not return a 512-byte record"
        assert re.search(rb"\b2100\s+4000\s+7d0a\s+2102\b", output), "kernel CT read does not match the TAP primary bootstrap"
        for error in (b"I/O error", b"read error", b"cannot open", b"fatal error"):
            assert error not in output, "kernel CT driver reported an error"
        print(f"PASS: ZEUS kernel CT driver read original TAP bootstrap; transcript in {args.output}")

    if args.check_tape_write:
        check_tape_write(output)
        print(f"PASS: ZEUS kernel CT write/read round trip; transcript in {args.output}")
    if args.check_three_disks:
        check_three_disks(output)
        print(f"PASS: ZEUS multi-user concurrent SMDC/WDC/MWDCII copies, tape and archive round trips, and filesystem checks; transcript in {args.output}")
    print(f"Elapsed wall time: {elapsed:.1f} seconds ({elapsed / 60:.1f} minutes)")


if __name__ == "__main__":
    main()
