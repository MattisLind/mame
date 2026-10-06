# System 8000 MWDCII

For native cartridge TAP boot and installation, see [the TCC notes](s8000-tcc.md).
The `tcc` tape-slot option now provides record-level cartridge transport;
the older logical-file Lua helpers remain available for isolated disk work.
For the 8-inch Winchester controller and its SADIE tests, see
[the WDC notes](s8000-wdc.md).
For simultaneous SMDC, WDC and MWDCII drives under ZEUS, see
[the multi-user stress setup](s8000-multiuser-stress.md).

## Current result

The `mwdcii` disk-slot option runs the supplied board firmware on a Z8001,
with local RAM, a Z8036 CIO, a host command latch and access to host RAM.
Four `harddisk_image_device` drives provide image-backed sector reads/writes.
The complete five-program SADIE suite has passed through native TCC boot on
a fresh type-0 disk, including both MDCTEST subtests. Both MDCTEST subtests
also pass on drive types 1..3. Full scan coverage and saved-sector contents
are checked in the [native suite results](#native-diagnostic-suite-2026-10-04).
SADIE 3.5 loads MDCTEST 4.2, and both its tests complete one lap with zero
errors on a type-0 image: test 1 performs 128 random single-sector writes and
128 reads; test 2 performs 50 random-length write/read cycles and checks for
memory corruption outside the DMA buffers. A separate firmware probe verifies
multi-sector transfers across cylinder and 64 KiB DMA boundaries, destination
guards, and persistence across emulator restarts. Firmware reports `0203`.
Four simultaneously attached images of types 0, 1, 2 and 3 also pass firmware
startup and independent write/read probes on units 0, 1, 2 and 3 respectively.
An adapter write to an absent drive raises the firmware's write-fault ISR.

Without an image, MDCTEST still reports `Power-up Test, Drive Not Selected`.
The original ZEUS 3.21 `sawbz`, `mkfs` and `sarestor` utilities have written
block zero, created root and `/usr`, and restored tape files 5..8 on the
SADIE-formatted type-3 image. The installed kernel loads through `md` and
reaches the original single-user shell. After the vendor post-install setup,
the completed image also enters multi-user mode and accepts the default
`zeus`/`jupiter` login; the shell executes `uname`, lists all four mounted
filesystems and syncs. External memory accesses currently
delegate directly to ZBI RAM without modeling bus arbitration. The confirmed
20 MHz oscillator uses provisional CPU/CIO dividers.

## Disk abstraction and comparison with SMDC

SMDC implements its command processor in C++ because its microcode has not
been dumped. It converts CHS addresses to LBA, moves data through ZBI RAM and
uses `harddisk_image_device::read/write` for sector payloads. MWDCII uses the
same image device and LBA calculation, while retaining its available Z8001
firmware to process host commands, defect maps, DMA and completion status.

MWDCII's sector adapter supplies canonical cylinder/head/sector ID words at
I/O `007a`, then transfers the corresponding 512-byte image sector. The write
path skips the firmware's 22-byte gap/sync/address-mark prefix and stores the
512-byte payload through the image device. Seeks complete immediately, and
header searches cycle through sector IDs without modeling spindle rotation.
The WD1100, magnetic encoding, flux transitions and PROM sequencer do not
need to be simulated for this abstraction.

Track-format ID/gap streams are accepted without changing sector payloads;
IDs remain canonical, with no stored interleave or malformed headers. The
original SADIE MDCFMT 4.3 completed formatting a blank type-3 image with
five scans and automatic mapping: 4,980 format commands, 29,880 reads,
24,912 writes, zero errors and no bad sectors. Custom drive types 4..7 are
not validated. CRC/ECC corruption and correction are outside the intended
sector-image abstraction. Image read failures
raise the firmware's error flag rather than returning fabricated sector data.

The older `sadie.lua` launcher injects the original executive into RAM and replaces its
tape read/position entries with Lua-assisted logical-file transfers. It leaves
the disk diagnostics and MWDCII firmware intact. It is a development tool,
not an emulated TCC or a demonstration of an unmodified tape boot. The native
diagnostic runs below instead boot the unmodified three-track TAP through TCC.

## Reproduce

Run commands from the MAME repository root. Build the S8000 target normally:

```sh
make SUBTARGET=s8000 SOURCES=src/mame/zilog/s8k.cpp TOOLS=1 -j8
python3 scripts/s8000/prepare_mwdcii.py
python3 scripts/s8000/prepare_sadie.py --download
```

Alternatively, pass `--source /path/to/Zilog_S8000` to prepare media from a
local checkout. `media.json` pins Tpaxia's repository to revision
`5c874c92c7968812dd1a1368e8053159726341fa` and records Git blob hashes for
the 13 CPU ROMs and 62 extracted SADIE files. Existing matching files are
reused. Binary ROMs and media stay under `roms`, outside the source changes.

The MWDCII converter accepts the Intel HEX EPROM captures and the NUL-padded
Intel HEX PROM captures under `roms/S8000/MWDCII`. It verifies record checksums,
coverage, decoded sizes, CRC32/SHA1, ROM interleaving and the firmware checksum.
Its output goes into `roms/s8k_mwdcii`.

Examples below use the locally built debug executable `s8000d`; substitute
your executable name as needed.

```sh
./s8000d s8k_mwdcii -rompath roms -verifyroms
./s8000d s8000 -slot_disk mwdcii -rompath roms -skip_gameinfo \
    -autoboot_delay 0 -autoboot_script scripts/s8000/sadie.lua
```

To attach a disk, first create a disposable image or supply an existing image
with the correct geometry and MWDCII metadata:

```sh
python3 scripts/s8000/create_mwdcii_image.py build/mwdcii.chd --chdman ./chdman
./s8000d s8000 -slot_disk mwdcii -hard1 build/mwdcii.chd -rompath roms \
    -skip_gameinfo -autoboot_delay 0 -autoboot_script scripts/s8000/sadie.lua
```

The supported image geometries are 216/8/17, 306/6/17, 645/5/17 and
830/6/17 (cylinders/heads/sectors), all with 512-byte sectors. Firmware drive
types 0..3 are selected from the mounted geometry. Other geometries are
rejected. Use CHD metadata to specify geometry; raw-image geometry guessing
in MAME's disk device is generally unsuitable for these drives. The other
three units are `-hard2`, `-hard3` and `-hard4`.

The image creator refuses to overwrite existing files and retains its `.raw`
source. It places an empty defect-map terminator (physical sector count,
big endian) in cylinder zero, sector zero of every head. Firmware reads this
metadata during startup; an all-zero image does not pass drive initialization.
The firmware handles logical-to-physical mapping itself, including the
reserved cylinder and defect remapping. Type 0 exposes 28,968 logical sectors.
This image is empty and contains no ZEUS installation.

MAME normally writes compressed CHDs through a differencing image in `diff`.
Keep the parent CHD and its matching `.dif` together to retain writes.

At SADIE's command menu select `T`, then test `4` (MDCTEST), then accept the
parameters. The diagnostic asks for destructive-test confirmation. Select
`Y` only for a disposable disk. In the optional parameters menu, choose `2`
and set parameter 2 to `2` to run the random-length test. Test `5` is MDCMON;
the tape loader also provides the other extracted logical files. Native TAP
diagnostic coverage is recorded below.

Automated checks:

```sh
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy ./s8000d s8000 \
    -slot_disk mwdcii -rompath roms -video none -sound none -nothrottle \
    -skip_gameinfo -autoboot_delay 0 \
    -autoboot_script scripts/s8000/probe_mwdcii.lua -seconds_to_run 10
python3 scripts/s8000/check_sadie.py --mame ./s8000d
python3 scripts/s8000/check_sadie.py --mame ./s8000d --disk build/mwdcii.chd \
    --output build/s8000-sadie-test1
python3 scripts/s8000/check_sadie.py --mame ./s8000d --disk build/mwdcii.chd \
    --test 2 --output build/s8000-sadie-test2
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy ./s8000d s8000 \
    -slot_disk mwdcii -hard1 build/mwdcii.chd -rompath roms -video none \
    -sound none -nothrottle -skip_gameinfo -autoboot_delay 0 \
    -autoboot_script scripts/s8000/probe_mwdcii_disk.lua -seconds_to_run 20
```

The Lua probe requires firmware version `0203` in both controller RAM and
host RAM. The console check uses a local TCP socket, loads MDCTEST through
SADIE's menus, and requires either the expected no-drive error or a completed
lap with zero errors and the expected read/write counts. It writes
`console.txt` and `mame.log` in its output directory, then asks MAME to shut
down cleanly. `--disk` authorizes destructive diagnostic writes to that image.
The Lua disk probe also overwrites four logical sectors at block 134. Running
it again with `MWDCII_PROBE_VERIFY_ONLY=1` verifies persistence without writing.
`MWDCII_PROBE_UNIT=0..3` chooses the probe drive. With only drive zero attached,
`MWDCII_PROBE_WRITE_FAULT=1` also verifies that a failed adapter write to absent
drive one enters the real firmware's write-fault ISR. CIO transition-pattern
matching is currently incomplete, so the adapter raises its port-A pending
flag through the CIO command register on a write failure; it does not patch
the firmware's status. The controller interrupt acknowledge callback preserves
the full vector rather than treating `intack_r`'s integer result as one bit.
The existing front-panel layout emits an invalid screen-index warning when
its console is a null modem.

The logical-file tape shim supports positioning at record zero, sequential
byte reads and EOF. It does not reproduce physical record boundaries or honor
the maximum record count beyond detecting zero. Nonzero-record positioning,
tape writes and raw tape commands fail. `SADIE_MEDIA` overrides `roms/sadie`.

## Native diagnostic suite (2026-10-04)

The complete five-program SADIE suite passed through native `Z T`
boot from `roms/tapes/sadie-3.5.tap`, with the supplied MWDCII firmware
executing on its Z8001. No guest instructions are patched. The initial disk,
`build/s8000-mwdcii-suite-blank.chd`, was created with:

```sh
./chdman createhd -o build/s8000-mwdcii-suite-blank.chd \
  -chs 216,8,17 -ss 512 -c none
```

This was a completely blank type-0 drive, including an empty cylinder zero;
it did not have a pre-seeded defect map. MDCFMT created the map itself.
The random tests and read-only scan used separate copies of the formatted
disk, avoiding simultaneous writers. MDCMON used a separate empty type-0
image with factory no-defects metadata. Existing ZEUS images were not used.

| Diagnostic | Coverage | Result | Log directory under `build/s8000-mwdcii-suite/` |
|---|---|---|---|
| MDCFMT 4.3 | Five scans and automatic mapping; 1,728 formats, 10,368 reads, 8,656 writes | Zero errors; no bad sectors | `format` |
| MDCMEDIA 4.2 | All 1,704 logical tracks with three patterns; 5,112 writes and 5,112 reads | Zero errors | `media` |
| MDCTEST 4.2, test 1 | 128 random single-sector write/read pairs | Zero errors | `test1` |
| MDCTEST 4.2, test 2 | 50 random-length write/read pairs and memory corruption checks | Zero errors | `test2` |
| MDCCRC 4.2 | All 1,704 logical tracks, 28,968 sectors | Zero errors | `crc` |
| MDCMON 4.2 | Block/raw transfers and comparisons, HOME/SEEK, drive parameters/ready, map read, polling/interrupt modes and controller reinitialization | Zero errors | `monitor-final` |

The harness writes `console.txt` and `mame.log`. Native runs also write
`packets.txt`: a passive Lua observer records logical read/write host packets,
including commands, sector totals, distinct sectors and first/last sector.
MDCCRC's observed reads covered every logical block `0..28967` exactly once.
MDCMEDIA observed 86,904 sectors transferred in each direction across its
three patterns, covering all 28,968 logical sectors. After MAME exited, the
CHD was extracted to `media-final.raw`: every logical track contained the
exact final `6db6db` pattern, and all eight defect-map copies were intact.
`results.json` records the completed suite and geometry-specific random runs.
For full scans the harness requires complete logical coverage and expected
command/sector counts, as well as a completed lap with zero reported errors.
The MDCCRC result checks healthy sector reads; physical CRC/ECC fault injection
and correction remain outside the image adapter.

MDCMON performs two four-block transfers (fixed and random patterns), including
a cylinder boundary, and one 256-word transfer using raw CHS arguments. All
three comparisons must complete without errors. In BMODE, FILL/COMPARE/RAND
lengths are blocks; in RMODE they are words. This unit change is not explicit
in every HELP entry. DINT/DRDY exercises polling, EINT/DRDY exercises interrupts,
and INITMDC/RDDRV verifies firmware restart and geometry reload.

Both MDCTEST subtests also passed with zero errors on empty factory-metadata
images for types 1, 2 and 3, on unit 0. Each type completed 128 write/read
pairs in test 1 and 50 in test 2, including the memory corruption checks.
Their logs are `type1-test1`, `type1-test2`, and corresponding names
for types 2 and 3. Earlier four-sector probes cover units 1..3 separately.

To repeat the suite on a new disposable image, use the `createhd` command
above with a new filename, then:

```sh
python3 scripts/s8000/check_sadie.py --diagnostic MDCFMT \
  --tape roms/tapes/sadie-3.5.tap --disk build/s8000-mwdcii-suite-blank.chd \
  --output build/mdc-format --timeout 1800
python3 scripts/s8000/check_sadie.py --diagnostic MDCMEDIA \
  --tape roms/tapes/sadie-3.5.tap --disk build/s8000-mwdcii-suite-blank.chd \
  --output build/mdc-media --timeout 1800
python3 scripts/s8000/check_sadie.py --test 1 \
  --tape roms/tapes/sadie-3.5.tap --disk build/s8000-mwdcii-suite-blank.chd \
  --output build/mdc-test1
python3 scripts/s8000/check_sadie.py --test 2 \
  --tape roms/tapes/sadie-3.5.tap --disk build/s8000-mwdcii-suite-blank.chd \
  --output build/mdc-test2 --timeout 600
python3 scripts/s8000/check_sadie.py --diagnostic MDCCRC \
  --tape roms/tapes/sadie-3.5.tap --disk build/s8000-mwdcii-suite-blank.chd \
  --output build/mdc-crc --timeout 900
python3 scripts/s8000/check_sadie.py --diagnostic MDCMON \
  --tape roms/tapes/sadie-3.5.tap --disk build/s8000-mwdcii-suite-blank.chd \
  --output build/mdc-monitor
```

Run these sequentially when using the same disk. MDCFMT, MDCMEDIA, MDCTEST
and the selected MDCMON commands overwrite disk data.

## Formatting and ZEUS installation work

A full SADIE MDCFMT run on a fresh uncompressed CHD is recorded in
`build/s8000-mwdcii-format/console.txt`. Reproduce on a disposable image:

```sh
./chdman createhd -o build/mwdcii-install.chd -chs 830,6,17 -ss 512 -c none
python3 scripts/s8000/check_sadie.py --format --timeout 1800 \
    --disk build/mwdcii-install.chd --output build/mwdcii-format
```

This exercises the original format program and firmware, including creating
its defect-map metadata; the blank CHD has no pre-seeded map.

Prepare the pinned original ZEUS 3.21 installation files with:

```sh
python3 scripts/s8000/prepare_sadie.py --download \
    --manifest scripts/s8000/zeus-media.json
python3 scripts/s8000/install_zeus.py --stage install \
    --disk build/mwdcii-install.chd --output build/mwdcii-zeus-install
python3 scripts/s8000/install_zeus.py --stage boot --timeout 180 \
    --disk build/mwdcii-install.chd --output build/mwdcii-zeus-boot
```

`--stage install` runs the original `sawbz`, `mkfs` and `sarestor` on the
formatted disk and restores both filesystems, then boots ZEUS and runs the
vendor `/etc/makenewfs` procedure. It replaces their contents. That final
procedure creates the virtual device aliases and initializes `/tmp` and `/z`;
restoring root and `/usr` alone is insufficient for multi-user startup.
`--stage boot` loads `md(0,15200)zeus`, requires the original single-user
shell to execute marker commands, `uname -a` and `sync`, and then exits MAME.
This path uses the logical-file tape shim for the secondary bootstrap and
standalone utilities; the kernel and userspace run from the MWDCII image.

To finish an image made by the earlier restore-only harness, use a copy:

```sh
cp build/s8000-mwdcii-zeus.chd build/mwdcii-complete.chd
python3 scripts/s8000/install_zeus.py --stage setup --timeout 900 \
    --disk build/mwdcii-complete.chd --output build/mwdcii-setup
python3 scripts/s8000/install_zeus.py --stage multiuser --timeout 900 \
    --disk build/mwdcii-complete.chd --output build/mwdcii-multiuser
```

`--stage setup` is for an unfinished installation: it recreates `/tmp` and
`/z` and removes unused SMD/ZD device nodes. It preserves root and `/usr`
contents while adding aliases and labels. Do not use it on an image whose
auxiliary filesystems contain data to retain. `--stage restore` only restores
root and `/usr`, without the post-install procedure.

The original `datem` utility predates 2000. These checks use the historical
date `10/03/84` and time `12:00`, configurable with `--date` and `--time`.
The multi-user check uses the tape's default account `zeus`, password
`jupiter`, executes commands and syncs before exiting MAME.

The 2026-10-03 shutdown investigation checked a copy of
`build/s8000-mwdcii-zeus.chd`: native ZEUS `fsck -n` found no inconsistencies
in root (270 files, 5,285 blocks, 473 free) or `/usr` (1,106 files, 10,793
blocks, 725 free). All 193 root and 1,033 `/usr` regular files represented
in the pinned tape tree matched their original Git blob hashes, with none
missing or changed. The errors reproduced as `Can't stat /dev/root`,
`/dev/rusr`, `/dev/rtmp` and `/dev/rz`. All aliases were absent, and the
`/tmp` and `/z` superblocks were zero. This was incomplete post-install
setup, not evidence of corruption or missing distribution files. The
original CHD is retained unchanged; the completed copy is
`build/s8000-mwdcii-zeus-ready.chd`.

Start that completed image interactively from the repository root:

```sh
./s8000d s8000 -slot_disk mwdcii \
    -hard1 build/s8000-mwdcii-zeus-ready.chd -rompath roms \
    -cfg_directory build/s8000-mwdcii-zeus-boot/cfg \
    -slot_cpu:cpu_a:sio0:chb:console terminal -autoboot_delay 0 \
    -autoboot_script scripts/s8000/zeus_install.lua -skip_gameinfo -window
```

At `Boot`, enter `md(0,15200)zeus`; at the single-user shell enter
`/etc/INIT 2`. Answer the date/time prompts using a historical date supported
by the original utility. At the login prompt, change the console's **Data
Bits** to **7** and **Parity** to **Even** under MAME's **Machine
Configuration** menu (9600 baud, one stop bit). Boot and the single-user shell
use 8N1; getty switches to 7E1. Restore 8 data bits/no parity before the next
boot if MAME saved those settings. The automated multi-user check switches
its null modem at the first login prompt. Before exiting MAME, use `sync`
and `/etc/halt` from the logged-in shell.

The completed-image checks are recorded in
`build/s8000-mwdcii-zeus-setup/console.txt` (all four filesystems clean),
`build/s8000-mwdcii-zeus-setup-auto/console.txt` (automated vendor setup), and
`build/s8000-mwdcii-zeus-multiuser/console.txt` (multi-user login and commands).

The installer includes a native `md` layout for the type-3 drive: 84,354
usable blocks, `/usr` at 0 (12,000 blocks), swap at 12,000 (3,200), root at
15,200 (6,000), `/tmp` at 21,200 (6,000), and `/z` at 27,200 (57,154).
Root and swap use major 10, minors 2 and 1 respectively.

The standalone utilities require the Z8010 descriptor selector to wrap after
its last byte. This checkout now includes the two-line correction documented
in Tpaxia's `serial_installer/mame-z8010-descriptor-wrap.patch`.

## Evidence and recovered map

The board photographs identify board 09-0241 revision B. The owner confirms
a 20 MHz oscillator. CPU `/4` and CIO `/8` divider ratios are provisional;
the oscillator frequency alone does not establish either device clock.
The owner also identifies two 74LS161 counters and a 74S74 nearby. Their
connections have not been traced, so these markings do not establish which
outputs, if any, supply the CPU and CIO clocks.

U163 supplies the high byte, U162 the low byte. The reset words are
`0000 c000 0000 0070`; the firmware string at `0048` is
`Copyright 1984 Zilog Inc., mWDC Firmware`. Code at `01c8..01ec` sums bytes
through `154b`, adds the big-endian word at `154c` and expects zero modulo
65536. Both 1 KiB bipolar PROM dumps are registered but not interpreted yet.

| Controller address | Recovered purpose |
| --- | --- |
| `000000..001fff` | Firmware ROM |
| `004000..007fff` | 16 KiB RAM, shared program/data |
| `400000..7fffff` (data space) | Host RAM, physical address minus `400000` |
| I/O `0070` | Drive selection, step/direction, head, sector-transfer gates |
| I/O `0072` | Board control latch, host completion interrupt request |
| I/O `0074` | Host interrupt vector latch |
| I/O `0076` | Host mailbox read; consumes its full flag |
| I/O `0078` | Zero ECC syndrome (bit-level correction unimplemented) |
| I/O `007a` | Sector ID and image-data word input |
| I/O `007c` | Four three-bit drive-type settings, inferred from images |
| I/O `007e` | Sector-data word output |
| I/O `0080..00df` | Z8036 registers |
| Host I/O `80f0` | One-word controller command latch |
| Host I/O `80f2`, `80f4` | Host completion acknowledgement |

The CIO is mapped to the high byte lane: the firmware uses word I/O to odd
ports, and the Z8000 core swaps the word lanes for these accesses. This detail
is necessary for its register initialization and mailbox polling to work.

The version probe writes `f1f1`, then host destination high word `0000`, low
word `8000`, then command `1d1d`. The firmware performs its own startup path
and writes `0203` to physical host RAM at `008000`.

Firmware 2.03 requests completion interrupts through bit 8 of the command
word. Its interrupt vector carries packet status; SADIE's handler acknowledges
it through the CPU/ZBI daisy chain. Interrupts start enabled: SADIE only uses
the separate `80f2/80f4` ports for firmware 2.04 and later. ZEUS 3.21 writes
`80f2` at the end of its MD interrupt handler; this acknowledges completion
and must allow subsequent interrupt requests. Both MDCTEST tests exercise
the 2.03 completion path.

Port A observations from firmware: bits 0/1 select drive 0/1, bit 2 ready,
bit 3 seek complete, bit 5 track zero and bit 7 host IRQ acknowledgement.
Port B: bits 0/3 select drive 2/3, bit 1 mailbox full; CRC/ECC status also
uses this port. The image adapter supplies logical status without modeling
the physical signal timing.

Drive control recovered from firmware and validated by the disk tests:
bit 15 pulses step; bit 14 changes seek direction; bits 11/12 choose unit;
bits 8..10 choose head; bit 13 enables write precompensation for these drive
types; bit 7 gates a transfer; bit 6 distinguishes data from header reads;
bit 5 gates writing; bit 4 identifies track-format streams; bit 3 resets
stream logic.
The header path reads two words (cylinder, head/sector), then the data path
reads 256 words. Default drive type appears to use 216 cylinders, eight
heads and 17 sectors per track. The startup routine also examines on-disk
defect-map metadata, so supplying ready/selected status alone cannot make a
blank disk pass initialization.

## Remaining work

Validate custom drive parameters, error recovery and remapping with nonempty
defect maps. Validate
extended multi-user workloads. Confirm CPU/CIO clock divider ratios
if timing-sensitive behavior needs investigation. Bus arbitration timing and
physical CRC/ECC fault injection remain outside the current image adapter.

The owner identifies the MWDCII PCB connectors as bus J11..J16 and
J21..J26; left Euro connectors J1/J2; top Euro connector J9; tape J8;
ten-pin J3; lower twenty-pin J17/J18; upper twenty-pin J4/J5; and power
J10. The obscured thirty-four-pin connectors are provisionally J6/J7.
J19/J20 have not been located.

## Tpaxia CPU/MMU patch audit (2026-10-03)

The local base `3f748f459de` already contains upstream
[PR #15866](https://github.com/mamedev/mame/pull/15866), merged as
`ab41620cf2d4bace0497922b03cb48e1d5169b50`, including its Z8001 instruction,
trap/vector and Z8010 fixes. The M20 fixes in PR #15883 are also present.
Reapplying either complete series would duplicate existing changes.

Tpaxia's newer branches contain additional fixes absent from that base.
The following have been reviewed and applied locally in dependency order:

| Commit | Change |
| --- | --- |
| [5749bbff](https://github.com/tpaxia/mame/commit/5749bbff007ea720899c4bf1fb63abcd7fbd20dc) | Wrap the Z8010 descriptor counter after its fourth byte; already applied during standalone installer work. |
| [3656a875](https://github.com/tpaxia/mame/commit/3656a8750b86b9881c236ab805efe1a8bf83857e) | Distinguish an MMU driving an address from suppressing an access. |
| [6def1140](https://github.com/tpaxia/mame/commit/6def11408cf87b44ef6ef3d82cc880944f3504ee) | Retain CPU suppression through the violating instruction; keep DMA independent. |
| [42d55926](https://github.com/tpaxia/mame/commit/42d559263b07e45a0789a77f37161de70ab84ea1) | Release suppression at VI/NVI acknowledgement as well as NMI acknowledgement. |
| [dc29a123](https://github.com/tpaxia/mame/commit/dc29a12317d81af712af716843e238a0a57b739f) | Bind acknowledgement callbacks to the CPU board. |
| [85388727](https://github.com/tpaxia/mame/commit/853887278a513a046f4bb36eee46b2e422acec79) | Keep MMU protection active when the board bypasses address translation. |
| [4055011b](https://github.com/tpaxia/mame/commit/4055011b77704660da3316977b8ce080ac50f439) | Prevent debugger accesses from changing bus-observation and suppression state. |
| [674fa2ed](https://github.com/tpaxia/mame/commit/674fa2edfd7a59baa739d463439ae4c08f10cf08) | Preserve bit 15 of the loaded PC segment word in LDAR and pushed return addresses, based on physical Z8001 captures. |

The last fix is also present under a different commit ID in
`z8001_pcseg_bit15` and `m40_z8010_sup_test`; it is applied once. That M40
branch adds no further Z8001/Z8010 core changes beyond these. Other changes
on `sadie` concern SMDC completion, SCC loopback/status and RAM parity/local
RAM diagnostic coverage; they have not been imported into the MWDCII work.
Only S8000 uses the changed Z8010 translation API in this checkout.

The S8000 target rebuilds and passes MAME validation with these changes.
SADIE MDCTEST test 2 completes a lap with zero errors, including its checks
for DMA corruption outside the buffers.

The initial repeat boot still stalled after the kernel banner. Tracing showed
successful exec of `/bin/sh`, followed by `ENXIO` opening `/dev/console`.
The logical-tape boot harness had supplied R5=0; the loader passes that value
to the kernel as its serial-user limit. Matching the documented CPU-A tape
entry with R5=`0x10` fixes this: the kernel reports 16 users, runs `/etc/rc`
and reaches the original single-user shell. Thus the missing CPU/MMU changes
were real, but the zero-user boot environment caused this particular console
failure. See [Tpaxia's tape-entry register documentation](https://github.com/tpaxia/Zilog_S8000/blob/5c874c92c7968812dd1a1368e8053159726341fa/serial_installer/README.md).

Source media and SADIE ABI research: [Tpaxia's Zilog_S8000 repository](https://github.com/tpaxia/Zilog_S8000/tree/5c874c92c7968812dd1a1368e8053159726341fa),
especially its `sadie` tools and extracted `sadie-3.5` tape files.
