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
    command(0x0009)
    command(0x010f)
    for track = 0, 3 do
        command((track << 8) | 0x0e)
        assert(ios:read_u16(0x4a) & 0xc200 == ((track << 14) | 0x0200), "track/BOT status")
        dma(0x1ff00, 1536)
        mem:write_u16(0x1fefe, 0xcafe); mem:write_u8(0x20301, 0xa5)
        command(1)
        verify(0x1ff00, 1025, track)
        assert(mem:read_u16(0x1fefe) == 0xcafe and mem:read_u8(0x20301) == 0xa5, "odd record DMA overrun")
        assert(ios:read_u16(0x48) == 1025, "odd record transfer count")
        assert(ios:read_u16(0x46) == 0x0200 and ios:read_u16(0x44) == 0x0302, "DMA 64KiB carry")
        dma(0x8000, 1536)
        command(1)
        verify(0x8000, 20, track)
        assert(ios:read_u16(0x48) == 20, "short record transfer count")
        ios:write_u16(0x48, 1536); command(1, 0x12)
        assert(ios:read_u16(0x48) == 0, "file mark transfer count")
        dma(0x9000, 512)
        command(1)
        verify(0x9000, 512, track)
        assert(ios:read_u16(0x48) == 512, "truncated record transfer count")
        command(1, 0x12) -- extra bytes of previous record were consumed
        ios:write_u16(0x48, 512)
        command(1, 0x80)
        assert(ios:read_u16(0x4a) & 0x0400 ~= 0, "track EOT")
        command(3)
        command(0x0106, 2)
        assert(ios:read_u16(0x4c) == 0x0100, "skip-file count")
        dma(0xa000, 2048); command(1); verify(0xa000, 2048, track)
        command(0x0105); dma(0xa800, 2048); command(1); verify(0xa800, 2048, track)
        command(0x0107, 2); dma(0xb000, 2048); command(1); verify(0xb000, 2048, track) -- reverse finds the beginning of the file
    end
    for unit = 0, 3 do
        command((unit << 8) | 0x0b)
        command(9); command(0x010f)
        assert(ios:read_u16(0x4a) & 0x3000 == unit << 12, "drive selection")
        dma(0xb000, 1024); command(1); verify(0xb000, 1024, 0)
    end
    dma(0x8001, 512); command(1, 0x40)
    dma(0x8000, 513); command(1, 0x40)
    dma(0x8000, 0); command(1, 0x40)
    dma(0x8000, 0x8002); command(1, 0x40)
    dma(0x8000, 512); ios:write_u16(0x46, 1); command(1, 0x40)
    dma(0xfffffe, 512); command(1, 0x40)
    command(0x040e, 8); command(0x020f, 8); command(0x100c, 8)
    dma(0x8000, 512)
    command(2, 0x10); command(8, 0x10)
    -- Interrupt control must work for repeated completions, not only once.
    ios:write_u16(0x40, 0xa2)
    for i = 1, 2 do
        ios:write_u16(0x4e, 3); command(3)
        assert(ios:read_u16(0x4e) & 0x40 ~= 0, "completion interrupt flag")
        assert(ios:read_u16(0x40) & 255 == 0xa2, "vector overwritten")
        ios:write_u16(0x4e, 0)
        assert(ios:read_u16(0x4e) & 0x40 == 0, "interrupt flag not cleared")
    end
    command(0x10)
    for port = 0x40, 0x4e, 2 do assert(ios:read_u16(port) == 0x5555, "DIAG handshake") end
    for port = 0x40, 0x4e, 2 do ios:write_u16(port, 0xaaaa) end
    assert(ios:read_u16(0x4e) == 0xaaaa, "DIAG final write readback")
    repeat coroutine.yield() until ios:read_u16(0x40) & 0x200 == 0
    assert(ios:read_u16(0x4a) & 4 == 0, "DIAG host latch test")
    command(0x10)
    for port = 0x4e, 0x40, -2 do
        ios:write_u16(port, 0xaaaa)
        assert(ios:read_u16(port) == 0xaaaa, "DIAG reverse-order write readback")
    end
    repeat coroutine.yield() until ios:read_u16(0x40) & 0x200 == 0
    assert(ios:read_u16(0x4a) & 4 == 0, "DIAG reverse-order host latch test")
    assert(ios:read_u16(0x40) & 255 == 0xa2, "DIAG did not restore vector")
    ios:write_u16(0x4e, 3)
    command(0x10)
    for port = 0x4e, 0x40, -2 do ios:write_u16(port, 0xaaaa) end
    repeat coroutine.yield() until ios:read_u16(0x40) & 0x200 == 0
    assert(ios:read_u16(0x4e) & 0x43 == 0x43, "DIAG did not restore interrupt enables and complete")
    ios:write_u16(0x4e, 0)
    print("PASS: TCC four tracks/drives, DMA carry/odd/truncated records, transfer counts, marks, reverse seeks, parameter errors, write protection and repeated interrupts")
    manager.machine:exit()
end)
emu.register_periodic(function()
    if manager.machine.time:as_double() < 1 or coroutine.status(routine) == "dead" then return end
    local ok, message = coroutine.resume(routine)
    assert(ok, message)
end)
