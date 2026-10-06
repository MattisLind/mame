-- license:BSD-3-Clause
-- copyright-holders:Mattis Lind
-- Register-level checks on disposable CHDs prepared by check_wdc.py.
local cpu = assert(manager.machine.devices[":slot_cpu:cpu_a:maincpu"])
local mem, ios = cpu.spaces.program, cpu.spaces.io_std
local verify_only = os.getenv("WDC_VERIFY_ONLY") == "1"
local absent = os.getenv("WDC_ABSENT") == "1"
local heads = {1, 3, 4, 5}
local source, target, words = 0x1ff00, 0x2ff00, 777
local function pattern(unit, i) return (i * 37 + (i >> 8) * 11 + unit * 53) & 255 end
local function position(unit, cylinder, head, sector, count, address)
    ios:write_u8(0x8001, unit)
    ios:write_u8(0x8002, head)
    ios:write_u8(0x8003, cylinder & 255)
    ios:write_u8(0x8004, cylinder >> 8)
    ios:write_u8(0x8005, sector)
    ios:write_u8(0x8008, count & 255)
    ios:write_u8(0x8009, count >> 8)
    ios:write_u8(0x800a, address & 255)
    ios:write_u8(0x800b, (address >> 8) & 255)
    ios:write_u8(0x800c, address >> 16)
end
local function command(code, completion, error)
    completion, error = completion or 0, error or 0
    ios:write_u8(0x8010, 0)
    local unit = ios:read_u8(0x8001)
    cpu.state.FCW.value = 0x5000 -- system mode, vectored interrupts enabled
    cpu.state.R14.value, cpu.state.R15.value, cpu.state.PC.value = 0, 0x6000, 0x7000
    ios:write_u8(0x8000, code | 0x20)
    assert(ios:read_u8(0x8010) & 0x80 == 0, "READY did not clear")
    local deadline = manager.machine.time:as_double() + 1
    repeat
        coroutine.yield()
        assert(manager.machine.time:as_double() < deadline, "WDC command timeout")
    until ios:read_u8(0x8010) & 0x80 ~= 0
    local expected = (((unit < 4 and unit or 0) << 4) | completion) << 8 | 0x20
    assert(cpu.state.IRQV.value == expected, string.format("command %02x IRQ tag: got %04x, expected %04x", code, cpu.state.IRQV.value, expected))
    assert(mem:read_u16(0x5ff8) == expected, "interrupt tag was not delivered to the guest stack")
    local status = 0x98 | (error ~= 0 and 4 or 0) | (completion == 8 and 2 or 1)
    assert(ios:read_u8(0x8010) == status, "completion status mismatch")
    ios:write_u8(0x8010, 0)
end
local routine = coroutine.create(function()
    ios:write_u16(0xffc0, 0x0100)
    for address = 0xffa0, 0xffae, 2 do ios:write_u8(address + 1, 3) end -- reset CPU-board CTCs
    mem:write_u16(0x7000, 0x5e08); mem:write_u16(0x7002, 0x7000)
    mem:write_u16(0x7100, 0x5e08); mem:write_u16(0x7102, 0x7100)
    cpu.state.FCW.value, cpu.state.PC.value = 0x4000, 0x7000
    -- Exercise actual Z8001 OUT instructions, including odd port addresses.
    -- ZEUS mixes these with OUTB; a word write must touch just one WDC register.
    mem:write_u16(0x7200, 0x3f76) -- out @r7,r6
    mem:write_u16(0x7202, 0x5e08); mem:write_u16(0x7204, 0x7202)
    for _, pair in ipairs({{0x8001, 2}, {0x8002, 3}, {0x8004, 1}, {0x8005, 0x20}, {0x8008, 0}, {0x8009, 1}}) do
        cpu.state.R7.value, cpu.state.R6.value = pair[1], pair[2]
        cpu.state.PC.value = 0x7200
        coroutine.yield()
        assert(ios:read_u8(pair[1]) == pair[2], "word OUT selected the wrong WDC register")
    end
    assert(ios:read_u8(0x8000) == 0 and ios:read_u8(0x8003) == 0, "word OUT changed an adjacent register")
    cpu.state.PC.value = 0x7000
    cpu.state.PSAPSEG.value, cpu.state.PSAPOFF.value = 0, 0x5000
    mem:write_u16(0x503a, 0x4000)
    mem:write_u32(0x507c, 0x00007100) -- vector 20H
    -- SETVECTOR completes with its newly programmed vector.
    position(0, 0, 0, 0x20, 0, 0x8000); command(0x0c)
    if absent then
        position(1, 0, 0, 0, 256, target); command(1, 1, 0x10)
        command(3)
        assert(mem:read_u8(target + 4) == 0x10, "absent drive error was not retained")
        print("PASS: WDC absent-drive completion and detailed status")
        manager.machine:exit(); return
    end
    position(0, 0, 0, 0, 0, target); command(0x15)
    assert(mem:read_u32(target) == 0x6111000c, "firmware identification")
    command(0x16)
    for unit = 0, 3 do
        local address = target + unit * 6
        assert(mem:read_u16(address) == 600 and mem:read_u8(address + 2) == heads[unit + 1]
            and mem:read_u8(address + 3) == 24 and mem:read_u16(address + 4) == 605, "drive geometry")
    end
    for unit = 0, 2 do
        for i = 0, words * 2 - 1 do mem:write_u8(source + i, pattern(unit, i)); mem:write_u8(target + i, 0xa5) end
        mem:write_u16(target - 2, 0xcafe); mem:write_u16(target + words * 2, 0xf00d)
        -- Cross the last head/cylinder boundary and a DMA 64 KiB boundary.
        if not verify_only then position(unit, 20, heads[unit + 1] - 1, 23, words, source); command(2) end
        position(unit, 20, heads[unit + 1] - 1, 23, words, target); command(1)
        for i = 0, words * 2 - 1 do assert(mem:read_u8(target + i) == pattern(unit, i), "disk/DMA round-trip mismatch") end
        assert(mem:read_u16(target - 2) == 0xcafe and mem:read_u16(target + words * 2) == 0xf00d, "DMA overrun")
    end
    position(0, 0, 0, 0, 261, target); command(0x0f); command(0x0b)
    assert(mem:read_u32(target) == 0x19000000 and mem:read_u16(target + 8) == 0xffff, "CE mode header/defect map")
    assert(mem:read_u16(target + 512) == 600 and mem:read_u16(target + 516) == 605, "format-read geometry")
    position(0, 604, 0, 23, 256, target); command(1) -- last physical cylinder
    command(0x14); command(1, 8) -- logical cylinder 604 is invalid
    position(0, 599, 0, 23, 257, target); command(1, 8) -- end-of-drive overflow
    position(0, 0, 1, 0, 256, target); command(1, 8)
    position(0, 0, 0, 24, 256, target); command(1, 8)
    position(0, 0, 0, 0, 256, target + 1); command(1, 8)
    position(0, 0, 0, 0, 256, 0xfffffe); command(1, 8)
    position(4, 0, 0, 0, 256, target); command(1, 8)
    position(0, 0, 0, 0, 256, target); command(0x1f, 8)
    position(3, 0, 0, 0, 256, source); command(2, 1, 4)
    position(3, 0, 0, 0, 3, target); command(3)
    assert(mem:read_u8(target + 4) == 4, "write-protection error was not retained")
    print("PASS: WDC four geometries, " .. (verify_only and "persistent reads" or "writes") .. ", DMA boundaries, CE/format reads, parameter errors, write protection and guest interrupt tags")
    manager.machine:exit()
end)
emu.register_periodic(function()
    if manager.machine.time:as_double() < 1 or coroutine.status(routine) == "dead" then return end
    local ok, message = coroutine.resume(routine)
    assert(ok, message)
end)
