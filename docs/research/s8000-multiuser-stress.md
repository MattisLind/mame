# System 8000: three disk controllers and cartridge tape

`s8000`, `s8000v1` and `s8000s2` now have two optional disk slots,
`slot_disk2` and `slot_disk3`, in addition to the existing `slot_disk`.
Leave them empty for an existing single-controller configuration. Use only
one instance of each controller: their I/O addresses are fixed.

The tested configuration boots ZEUS 3.21 from MWDCII and attaches one drive
each to SMDC and the 8-inch WDC, plus a TCC cartridge drive:

| Slot | Controller | Unit-0 image option | ZEUS device | I/O address |
| --- | --- | --- | --- | --- |
| `slot_disk` | `mwdcii` | `-hard1` | `md` | `80f0` |
| `slot_disk2` | `smdc` | `-hard5` | `smd` | `7f00` |
| `slot_disk3` | `wdc` | `-hard9` | `zd` | `8000` |
| `slot_tape` | `tcc` | `-tape1` | `ct` | `0040` |

Each disk controller exposes four image devices, so the next controller's
unit zero starts four image numbers later. These numbers depend on the
populated slots; confirm with `-listmedia` when changing their order.
The original ZEUS kernel contains all four drivers. Its vendor setup removes
unused disk device nodes; the stress setup recreates the necessary nodes on
the disposable system copy.

## Verified result, 2026-10-04

The complete combined suite passed in ZEUS 3.21 multi-user mode with the
default 1 MB RAM configuration. Two background workers each completed five
rounds of three disk copies, with a byte comparison after every copy:
30 copies of a 222,508-byte payload, totaling 6,675,240 copied bytes. The
kernel has 150 sector buffers, totaling 76,800 bytes, so the payload exceeds
its disk buffer cache.

| Check | Result |
| --- | --- |
| Both five-round disk copy/compare workers | Pass |
| MWDCII → raw tape → WDC, byte comparison | Pass |
| MWDCII → tape archive → SMDC, both files compared | Pass |
| Unmount and read-only SMDC/WDC filesystem checks | Pass |
| Host-side saved CHD/TAP byte audit | Pass |
| Fresh multi-user boot and four saved-file comparisons | Pass |
| All six filesystems checked after reboot | Clean |
| Focused SMDC interrupt-release regression | Pass |

Artifacts are under `build/s8000-three-irq-fixed`: `results.json` contains the
combined result, `audit.json` the independently verified data hash and file
list, and `run/console.txt` and `reboot/console.txt` the guest transcripts.
The three usable images are `md.chd`, `smd.chd` and `wd.chd`. The original
installed MWDCII image and installation TAP were preserved. The scratch
TAP now contains the two-file archive.

Further interrupt-reset regressions found lost-completion races in MWDCII,
WDC and TCC. After fixing all three, the complete combined suite and saved
CHD/TAP byte audit passed again on separate copies of the user's stopped-run
media, including filesystem labels and per-round progress:

| Mode | Measured wall time | Artifacts |
| --- | --- | --- |
| Windowed, passive console | 857.8 s (14.3 min) | `build/s8000-stress-all-irq-fixed` |
| Headless | 811.1 s (13.5 min) | `build/s8000-stress-all-irq-fixed-headless` |

Each directory contains `run/console.txt` and `audit.json`; the windowed
directory also contains `results.json` summarizing both runs. All three
completion-reset probes passed, as did native SADIE TCUMON, TCOM (zero
errors), and MDCMON. The original `build/three-disks` media was untouched.
The reset issue and regression commands are described below.

## Prepare and run

Start with the completed type-3 MWDCII installation, not the earlier image
that still needed the vendor post-install setup. The preparation script reads
the source image and creates a new directory, refusing to replace existing
media. The system disk and installation TAP are copied. The two extra disks
are blank images with ZEUS block-zero metadata and a 20,000-block scratch
partition starting at logical block 100. SMDC geometry is 823/8/32; WDC is
605/4/24, with its no-defects metadata in physical cylinder zero.
Preparation alone does not create device nodes in the copied system disk or
filesystems on the extra disks. The automated stress run below performs
those steps; an interactive run needs the first-time setup described later.

```sh
python3 scripts/s8000/prepare_multiuser_stress.py \
    --system-disk build/s8000-mwdcii-zeus-ready.chd \
    --output build/three-disks

python3 scripts/s8000/install_zeus.py --stage multiuser \
    --disk build/three-disks/md.chd \
    --smd-disk build/three-disks/smd.chd \
    --wdc-disk build/three-disks/wd.chd \
    --tape build/three-disks/scratch.tap \
    --check-three-disks --output build/three-disks/run \
    --timeout 2400 --live-console --window

python3 scripts/s8000/audit_multiuser_stress.py --media build/three-disks
```

Run the preparation command only once for a directory. If it already exists,
reuse its media with `install_zeus.py`, or choose a new directory for a fresh
set. Preparation refuses to replace existing disks and tapes.

The stress runner now boots from a protected copy of
`roms/tapes/zeus-3.21-install.tap`, then automatically mounts `--tape` when
the original secondary loader reaches its `Boot` prompt. The kernel still
loads from the MWDCII disk. Override the installation tape with
`--boot-tape /path/to/zeus-install.tap` if needed. This lets subsequent stress runs use
the existing scratch cartridge, even after it contains an archive. An older
runner tried to boot that archive and stopped at `BOOTING FROM TAPE`.

For another run on the same media, skip preparation and use a new log
directory to retain the earlier transcript:

```sh
python3 scripts/s8000/install_zeus.py --stage multiuser \
    --disk build/three-disks/md.chd \
    --smd-disk build/three-disks/smd.chd \
    --wdc-disk build/three-disks/wd.chd \
    --tape build/three-disks/scratch.tap \
    --check-three-disks --output build/three-disks/run-fixed \
    --timeout 2400 --live-console --window
```

This rerun still formats the two extra scratch partitions and overwrites the
scratch tape. It does not recreate the system disk or alter the original
installation tape.

The focused regression under `build/s8000-tape-reboot-check` verified boot
with an archive on the scratch tape, automatic multi-user login, a kernel
tape write/read comparison, and another boot after overwriting that tape.
All three disk controllers were attached. `results.json` records the checks;
the original installation TAP and the user's scratch TAP hashes were unchanged.
This boot fix was checked on copied media without rerunning the full disk
stress suite. Tape-check commands are now sent in short lines to fit the
historical console input buffer.

Allow roughly **15–20 minutes** for the default 222 KB payload on the
development Mac. The latest measured runs took **14.3 minutes windowed** and
**13.5 minutes headless**, including final sync waits. Host load and
emulation speed affect the duration. `--timeout 2400` is a **40-minute wall
time limit**, not a required run time; the runner exits once checks finish.
New runs print their measured elapsed wall time on success.

The commands above include `--window` to watch the console in a MAME window.
The runner passes `-window -ui_active -video auto` to MAME, supplies all input
and switches parity automatically. The window's terminal display ignores
keyboard input. `--live-console` can
be used at the same time to stream stdout. The window closes when the run
finishes. This option uses the `null_modem_terminal` serial device and
requires rebuilding `s8000d` after adding that device. Leave the emulator
running without pausing, resetting or changing its configuration during
automation. Omit `--window` for a headless run. The runner uses `./s8000d`
by default; no separate UI or parity command-line options are needed.
Windowed multi-user boot, automatic parity/login, shell commands and ignored
monitor keyboard input were verified on copied disks. The result and console
snapshot are in `build/s8000-window-console-check/window.json` and
`build/s8000-window-console-check/console.png`.

`--live-console` streams received serial bytes immediately to stdout while
also saving `<output>/console.txt`. The copy/compare workers report each
completed round, with silent stretches during the copies and comparisons.
To watch the rerun above from another host terminal,
use:

```sh
tail -f build/three-disks/run-fixed/console.txt
```

ZEUS clears the terminal at login. To show the entire transcript and render
control sequences visibly instead of clearing the host terminal, use
`tail -n +1 -f build/three-disks/run-fixed/console.txt | cat -v`.

The runner logs in automatically. If it stays at `ZEUS login:` after
`Login incorrect`, the stress commands have not started. The console switch
now acknowledges 7E1 only after MAME's frame updates apply the new serial
settings; an earlier acknowledgement could drop characters from the login
name. Login rejection now stops the runner with an explicit error rather
than waiting for its full timeout. Restart a run started before this fix.

The stress option **formats the two extra partitions and overwrites the
attached tape**. Always use disposable media. It enters multi-user mode,
logs in as `zeus`, and then performs:

* Two concurrent five-round copy/compare loops: MWDCII → SMDC → WDC →
  MWDCII, and MWDCII → WDC → SMDC → MWDCII. There are 30 full-file copies
  and 30 corresponding byte comparisons.
* A larger raw tape write/read using `dd`, followed by a byte comparison.
* A two-file `tar` archive to tape and extraction onto SMDC, with both
  extracted files compared to their MWDCII originals.
* Sync, unmount of both extra disks, and read-only filesystem checks.

The host audit separately reads the saved CHD filesystem data and TAP archive
and verifies exact file contents. It writes `audit.json`. This checks
persistence outside the guest's buffer cache. The console transcript and MAME
log remain in `run`. Shell commands are kept short enough for the historical
console input buffer. The harness waits after sync so the ZEUS kernel can
finish queued writes before MAME closes the images.

The default payload concatenates six installed executables, approximately
222 KB in the supplied installation. This exceeds the guest's disk buffer
cache. Add `--stress-repeat 8` for an approximately 1.78 MB payload and
allow a longer timeout. Historical byte-by-byte comparison utilities make
that run substantially slower.

## Start interactively

ZEUS 3.21 requires **Support Segmented OS (v2.2+ Only)** set to **Yes**.
The default is **No**; with that setting, the secondary loader prints the
kernel segment sizes but ZEUS does not reach its startup banner. The
automated runner enables the jumper in its own configuration. A newly
prepared directory has no such saved setting.

Seed a separate interactive configuration with the segmented OS jumper,
the 5.25-inch primary boot selection and an 8N1 terminal:

```sh
mkdir -p build/three-disks/interactive-cfg
cp scripts/s8000/zeus_interactive.cfg build/three-disks/interactive-cfg/s8000.cfg
```

Then launch all three disks with:

```sh
./s8000d s8000 -rompath roms \
    -slot_disk mwdcii -hard1 build/three-disks/md.chd \
    -slot_disk2 smdc -hard5 build/three-disks/smd.chd \
    -slot_disk3 wdc -hard9 build/three-disks/wd.chd \
    -slot_tape tcc -tape1 roms/tapes/zeus-3.21-install.tap \
    -slot_cpu:cpu_a:sio0:chb:console terminal \
    -cfg_directory build/three-disks/interactive-cfg -skip_gameinfo -ui_active -window
```

To fix an existing interactive session manually, open **Tab → Machine
Configuration**, set **Support Segmented OS (v2.2+ Only)** to **Yes**, then
reset the machine and boot again. The jumper is sampled on reset. Under
**DIP Switches**, select **5.25-inch Disk** for **Primary Boot Device** to
match the tested configuration.

Use `Z T` at the monitor and `md(0,15200)zeus` at the secondary boot prompt.
Enter `/etc/INIT 2` in single-user mode, then log in with `zeus` / `jupiter`.
The console changes from 8N1 during boot to 7E1 at multi-user login. With
`-ui_active`, press **Tab** to open MAME's menu, then select **Machine
Configuration** and set the console's **Data Bits** to **7** and **Parity**
to **Even**, keeping both baud rates at **9600** and **Stop Bits** at **1**.
Make this change at the login prompt; boot and the single-user shell use
**8** data bits and **None** for parity. Restore those boot settings before
restarting if MAME saved the 7E1 settings. Toggle MAME's UI controls off
before typing into the emulated console.

The automated stress runner switches its null modem to 7E1 at the first
login prompt automatically and does not need `-ui_active`.

For a freshly prepared `build/three-disks` directory, create the four missing
device nodes at the ZEUS shell prompt:

```sh
/etc/mknod /dev/smd0 b 8 0
/etc/mknod /dev/rsmd0 c 2 0
/etc/mknod /dev/zd0 b 0 0
/etc/mknod /dev/rzd0 c 32 0
```

Then initialize the two blank 20,000-block scratch partitions and create
their mount directories. **The two `mkfs` commands erase the partitions.**
Run them only for the blank extra disks from the preparation script. Skip
this initialization if the automated stress run has already formatted them
or they contain files to keep. Existing device nodes and mount directories
also do not need to be recreated.

```sh
/etc/mkfs /dev/rsmd0 20000 16 256
/etc/labelit /dev/rsmd0 /z/smd SMD0
/etc/mkfs /dev/rzd0 20000 12 96
/etc/labelit /dev/rzd0 /z/wd WDC0
mkdir /z/smd /z/wd
sync
```

These setup steps persist across reboots. Mount the initialized extra disks
manually each time:

```sh
/etc/mount /dev/smd0 /z/smd
/etc/mount /dev/zd0 /z/wd
```

ZEUS `mkfs` has no label arguments, so the setup uses `labelit` immediately
after formatting, before mounting. The filesystem names `/z/smd` and `/z/wd`
match their mount directories; the volume names are `SMD0` and `WDC0`.
Both fields have a six-character limit. The automated setup performs the
same labeling and rejects label errors or mount-name warnings.
The focused check in `build/s8000-filesystem-label-check/labels.json`
verified the saved labels, warning-free mounts and clean filesystem checks
on copied images.

Earlier runs omitted `labelit` and printed
`WARNING!! - mounting: <> as </z/smd>` and its `/z/wd` equivalent because
their filesystem names were empty. Those mounts still succeeded; the saved
2026-10-04 verified images retain their original empty labels. Existing
filesystems can be labeled with the two `labelit` commands while unmounted,
without running `mkfs` or erasing their files.

The stress tape now contains test data, so the interactive example uses the
original installation TAP for the bootstrap. Mount the disposable tape after
boot if continuing tape writes. Never run two MAME instances writing the
same media. Unmount the extra disks and allow queued sync writes to finish
before exiting.

## Rerun the disk stress interactively

After the automated stress run, the system disk retains
`/z/stress/forward.sh`, `/z/stress/reverse.sh` and their input payload.
The verified images under `build/s8000-three-irq-fixed` already contain these
files. Preparing blank extra disks and creating their device nodes manually
does not create the stress scripts.

With SMDC mounted at `/z/smd` and WDC at `/z/wd`, run these commands at the
multi-user login shell prompt:

```sh
rm -f /z/stress/forward.ok /z/stress/reverse.ok
/bin/sh /z/stress/forward.sh &
/bin/sh /z/stress/reverse.sh &
wait
cat /z/stress/forward.ok /z/stress/reverse.ok
sync
```

Both `FORWARD_PASS` and `REVERSE_PASS` must appear. Removing the old result
files before each run prevents an earlier success from masking a new
failure. The two workers repeat the five-round, three-disk copy/compare
test, using the saved payload size. They overwrite their previous scratch
output files and do not format the disks.

The tape round trips and final filesystem checks are currently sent by the
host command file `scripts/s8000/stress_three_disks.commands`; there is no
saved guest script that reruns the entire suite. That host file also includes
the initial formatting commands and uses the login shell's syntax, so it
must not be run wholesale with `/bin/sh`.

## WDC word I/O

SADIE exercises byte-register access. The original ZEUS WDC driver also uses
Z8001 `OUT` word instructions to write these byte-wide registers, including
odd-numbered ports. The ordinary aligned I/O-space access lost the original
port's A0 bit and wrote an adjacent register, potentially starting a command
when selecting a unit or programming its interrupt vector.

Optional Z8000 standard word-I/O callbacks now preserve the original port
address. On the System 8000 bus the WDC consumes the low byte at that single
register. Other bus cards use the existing aligned word access and byte swap;
other Z8000 systems and the MWDCII firmware CPU retain their existing path.
The WDC probe executes real guest `OUT` instructions at even and odd ports
and checks neighbouring registers. Its existing DMA, persistence, parameter
error and write-protection checks also pass. Native SADIE WDCTEST test 2
passes after this change.

## SMDC interrupt completion

ZEUS ends the SMDC interrupt handler with an `RI` write to `7f00`. The
existing device cleared its pending/service bits but did not recompute the
shared VI line. A lower-priority controller that had already requested an
interrupt while SMDC was in service could remain masked after the handler
returned. SMDC now updates the bus interrupt state after command writes,
including interrupt reset and enable/disable changes.

`probe_disk_irq.lua` exercises this with real guest interrupt acknowledgement:
SMDC completes a NOP packet, a WDC completion becomes pending while SMDC
is in service, and `RI` exposes WDC's interrupt. The probe checks the vector
delivered to the CPU and its guest stack. No disk image is needed:

```sh
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy ./s8000d s8000 \
    -slot_disk smdc -slot_disk2 wdc -rompath roms \
    -video none -sound none -nothrottle -skip_gameinfo -seconds_to_run 15 \
    -autoboot_delay 0 -autoboot_script scripts/s8000/probe_disk_irq.lua
```

## Completion interrupt reset and stress progress

ZEUS's `zdint` starts the next queued WDC command before writing the
interrupt reset register at `8010`. With the emulated controller's short
command delay, the new completion can arrive while the previous interrupt
is still in service. The reset previously cleared both states, losing that
new completion. It now releases the serviced interrupt while preserving
an independently pending completion. A reset without an interrupt in
service still clears a pending request for polling clients such as SADIE.

The same `probe_disk_irq.lua` now completes another WDC command while its
previous interrupt is in service and verifies that reset delivers its
unit-specific vector. This assertion fails with the previous implementation
and passes with the fix.

The MWDCII host reset at `80f2` and TCC control reset at `004e` had the same
pending/service race. Both now preserve the next completion when releasing
the previous serviced interrupt. The TCC also retains its request flag in
the control register; disabling its interrupt enables still clears the
request. A windowed run with only the WDC fix stalled in the archive writer,
whose process was asleep on TCC's command buffer (`0400c7a2`).

`probe_completion_irq.lua` reproduces the lost completion separately on
MWDCII and TCC, checks the guest's interrupt vector, and verifies that a
polling reset still cancels an unacknowledged request. Both devices fail
the pending-completion assertion before their fixes:

```sh
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy IRQ_PROBE_DEVICE=tcc \
    ./s8000d s8000 -slot_disk mwdcii -slot_tape tcc -rompath roms \
    -video none -sound none -nothrottle -skip_gameinfo -seconds_to_run 15 \
    -autoboot_delay 0 -autoboot_script scripts/s8000/probe_completion_irq.lua
```

Repeat with `IRQ_PROBE_DEVICE=mwdcii` to check the disk controller. No media
is needed: the tape test selects a unit and the MWDCII test drives its
controller-side request/vector latches.

Each stress run removes old `forward.ok` and `reverse.ok` files before
starting the workers. The serial transcript reports `FORWARD_ROUND=1`
through `5` and `REVERSE_ROUND=1` through `5` after each complete copy/compare
round. A timeout reports its elapsed wall time, last command and log paths.
At `wait`, the foreground tape checks have finished and the login shell is
waiting for both disk workers. For kernel diagnosis, `--trace` also reports
process states and wait channels when booting through the native TCC.

This is logical image emulation. It does not establish physical controller
timing, mechanical throughput, flux encoding or CRC/ECC correction behavior.
