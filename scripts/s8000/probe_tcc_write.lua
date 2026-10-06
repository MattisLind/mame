-- license:BSD-3-Clause
-- copyright-holders:Mattis Lind
-- Exercise the TCC register interface against check_tcc.py's known records.
local cpu = assert(manager.machine.devices[":slot_cpu:cpu_a:maincpu"])
local mem, ios = cpu.spaces.program, cpu.spaces.io_std
local function command(value, error_bits)
    ios:write_u16(0x42, value)
    assert(ios:read_u16(0x40) & 0x200 ~= 0, "BUSY not asserted")
    local deadline = manager.machine.time:as_double() + 1
    repeat
        coroutine.yield()
        assert(manager.machine.time:as_double() < deadline, "TCC command timeout")
    until ios:read_u16(0x40) & 0x200 == 0
    if value ~= 0x10 then
        assert(ios:read_u16(0x4a) & 0xff == (error_bits or 0), string.format("command %04x status %04x", value, ios:read_u16(0x4a)))
    end
end
local function dma(address, count)
    ios:write_u16(0x44, address & 0xffff)
    ios:write_u16(0x46, (address >> 16) << 8)
    ios:write_u16(0x48, count)
end
local function verify(address, count, track, first)
    for i = 0, count - 1 do
        local expected = ((i + (first or 0)) * 37 + track * 53) & 255
        assert(mem:read_u8(address + i) == expected, string.format("DMA mismatch track=%d byte=%x", track, i))
    end
end
local routine = coroutine.create(function()
    ios:write_u16(0xffc0, 0x0100)
    mem:write_u16(0x7000, 0x5e08); mem:write_u16(0x7002, 0x7000)
    cpu.state.FCW.value, cpu.state.PC.value = 0x4000, 0x7000
    local image = assert(manager.machine.images[":slot_tape:tcc:tape0"])
    command(9); command(0x010f)
    assert(ios:read_u16(0x4a) & 0x100 == 0, "writable image protected")
    -- Overwrite track 1, including DMA carry and consecutive file marks.
    command(0x010e)
    for i = 0, 511 do mem:write_u8(0x1ff00 + i, (i * 17 + 23) & 255) end
    dma(0x1ff00, 512); command(2)
    assert(ios:read_u16(0x44) == 0x0100 and ios:read_u16(0x46) == 0x0200, "write DMA carry")
    assert(ios:read_u16(0x48) == 512, "write transfer count")
    command(8); command(8)
    command(0x010e); dma(0x8000, 512); command(1)
    for i = 0, 511 do assert(mem:read_u8(0x8000 + i) == ((i * 17 + 23) & 255), "write/read mismatch") end
    dma(0x8000, 512); command(1, 0x12); dma(0x8000, 512); command(1, 0x12)
    -- Replace the second file mark and verify truncation after reloading.
    command(0x0105, 2)
    for i = 0, 19 do mem:write_u8(0x9000 + i, (i * 37 + 53) & 255) end
    dma(0x9000, 20); command(2)
    local path = image.filename
    image:unload(); assert(not image:load(path), "reload failed")
    command(9); command(0x010e); command(0x0204, 2)
    dma(0xa000, 20); command(1); verify(0xa000, 20, 1)
    dma(0xa000, 20); command(1, 0x80)
    for _, track in ipairs({0, 2, 3}) do
        command((track << 8) | 0x0e); dma(0xb000, 1536); command(1)
        verify(0xb000, 1025, track)
    end
    -- Creation through the same interface exposed by MAME's file manager.
    local created = assert(os.getenv("TCC_CREATE_FILE"))
    image:unload(); assert(not image:create(created), "create failed")
    command(9); command(0x010f); command(0x030e)
    dma(0x9000, 20); command(2); command(8)
    image:unload(); assert(not image:load(created), "created tape reload failed")
    command(9); command(0x030e); dma(0xc000, 20); command(1); verify(0xc000, 20, 1)
    dma(0xc000, 20); command(1, 0x12)
    print("PASS: TCC writable DMA, file marks, overwrite truncation, independent track preservation, persistence and cartridge creation")
    manager.machine:exit()
end)
emu.register_periodic(function()
    if manager.machine.time:as_double() < 1 or coroutine.status(routine) == "dead" then return end
    local ok, message = coroutine.resume(routine)
    assert(ok, message)
end)
