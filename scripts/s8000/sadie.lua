-- license:BSD-3-Clause
-- copyright-holders:Mattis Lind
-- Debugger-assisted SADIE 3.5 startup, using extracted logical tape files.
-- This bypasses the tape transport; it does not patch controller diagnostics.
-- Run from the repository root. SADIE_MEDIA overrides roms/sadie.

local cpu = assert(manager.machine.devices[":slot_cpu:cpu_a:maincpu"], "CPU-A required")
local ios = cpu.spaces["io_std"]
local mem = cpu.spaces["program"]
local media = os.getenv("SADIE_MEDIA") or "roms/sadie"
local loaded = false
local stop_file = os.getenv("SADIE_STOP_FILE")
local current = nil
local cursor = 0

local function file_bytes(track, file)
    local name = string.format("%s/track-%d/file-%03d.bin", media, track, file)
    local handle = io.open(name, "rb")
    if not handle then return nil end
    local bytes = handle:read("a")
    handle:close()
    return bytes
end

-- Retain taps for the lifetime of the script (otherwise Lua GC removes them).
local taps = {}
_G.sadie_taps = taps
taps.position = ios:install_write_tap(0x9002, 0x9003, "sadie-position", function()
    local track, file = cpu.state["R7"].value, cpu.state["R6"].value
    print(string.format("SADIE position %d/%d record %d", track, file, cpu.state["R5"].value))
    -- Extracted logical files contain no physical record boundaries.
    current = cpu.state["R5"].value == 0 and file_bytes(track, file) or nil
    cursor = 0
    cpu.state["R2"].value = current and 0 or 0xffff
    if current then
        mem:write_u16(0x4936, track)
        mem:write_u16(0x4938, file)
    end
end)
taps.read = ios:install_write_tap(0x9000, 0x9001, "sadie-read", function()
    local count = 0
    if current and cpu.state["R3"].value ~= 0 then
        count = math.min(cpu.state["R5"].value, #current - cursor)
        local dest = (cpu.state["R7"].value << 16) | cpu.state["R6"].value
        for i = 1, count do mem:write_u8(dest + i - 1, current:byte(cursor + i)) end
        cursor = cursor + count
    end
    cpu.spaces["data"]:write_u16(cpu.state["R4"].value, count)
    cpu.state["R2"].value = current and 0 or 0xffff
end)

emu.register_periodic(function()
    if stop_file then
        local stop = io.open(stop_file, "rb")
        if stop then
            stop:close()
            manager.machine:exit()
            return
        end
    end
    if loaded or manager.machine.time:as_double() < 2 then return end
    loaded = true
    local executive = assert(file_bytes(0, 1), "Missing SADIE executive; run prepare_sadie.py")
    assert(#executive == 27648, "Unexpected SADIE executive size")
    -- Check the entry points before replacing any instructions.
    assert(executive:sub(1, 4) == string.char(0x5e, 0x08, 0x00, 0xb2), "Unexpected SADIE entry")
    ios:write_u16(0xffc0, 0x0100) -- physical RAM, CPU board ROM/MMU disabled
    for i = 1, #executive do mem:write_u8(i - 1, executive:byte(i)) end
    local function patch(address, bytes)
        for i = 1, #bytes do mem:write_u8(address + i - 1, bytes:byte(i)) end
    end
    patch(0x2874, string.char(0x3b, 0x26, 0x90, 0x00, 0x9e, 0x08)) -- OUT #9000,r2; RET
    patch(0x2a88, string.char(0x3b, 0x26, 0x90, 0x02, 0x9e, 0x08)) -- position
    patch(0x29b8, string.char(0x21, 0x02, 0xff, 0xff, 0x9e, 0x08)) -- write unsupported
    patch(0x2c00, string.char(0x21, 0x02, 0xff, 0xff, 0x9e, 0x08)) -- raw command unsupported
    cpu.state["FCW"].value = 0x4000
    cpu.state["R7"].value = 0 -- CPU-A console configuration
    cpu.state["PC"].value = 0
    print("SADIE 3.5: debugger-assisted startup; logical-file tape transport")
end)
