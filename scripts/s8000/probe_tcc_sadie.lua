-- license:BSD-3-Clause
-- copyright-holders:Mattis Lind
-- Compare native TAP transfers with independently extracted SADIE files.
local cpu = assert(manager.machine.devices[":slot_cpu:cpu_a:maincpu"])
local mem, ios = cpu.spaces.program, cpu.spaces.io_std
local function command(value)
    ios:write_u16(0x42, value)
    local deadline = manager.machine.time:as_double() + 1
    repeat
        coroutine.yield()
        assert(manager.machine.time:as_double() < deadline, "TCC timeout")
    until ios:read_u16(0x40) & 0x200 == 0
end
local routine = coroutine.create(function()
    ios:write_u16(0xffc0, 0x0100)
    mem:write_u16(0x7000, 0x5e08); mem:write_u16(0x7002, 0x7000)
    cpu.state.FCW.value, cpu.state.PC.value = 0x4000, 0x7000
    command(9); command(0x010f)
    for _, pair in ipairs({{0, 1}, {1, 0}, {2, 0}, {2, 1}, {2, 2}}) do
        local track, file = table.unpack(pair)
        local f = assert(io.open(string.format("roms/sadie/track-%d/file-%03d.bin", track, file), "rb"))
        local expected = f:read("a"); f:close()
        command((track << 8) | 0x0e)
        if file > 0 then command((file << 8) | 6) end
        local base = 0x1f000
        ios:write_u16(0x44, base & 0xffff); ios:write_u16(0x46, (base >> 16) << 8)
        mem:write_u16(base - 2, 0xcafe); mem:write_u16(base + #expected, 0xf00d)
        local size = 0
        while true do
            ios:write_u16(0x48, 0x4000); command(1)
            local status = ios:read_u16(0x4a) & 255
            if status & 2 ~= 0 then break end
            assert(status == 0, string.format("SADIE tape status %04x", status))
            local count = ios:read_u16(0x48)
            assert(count > 0 and size + count <= #expected, "unexpected record length")
            size = size + count
        end
        assert(size == #expected, "logical file length differs from original extract")
        for i = 1, #expected do assert(mem:read_u8(base + i - 1) == expected:byte(i), "SADIE file payload mismatch") end
        assert(mem:read_u16(base - 2) == 0xcafe and mem:read_u16(base + #expected) == 0xf00d, "DMA outside file")
        print(string.format("PASS: SADIE TAP track %d file %d, %d bytes match independent extract", track, file, size))
    end
    manager.machine:exit()
end)
emu.register_periodic(function()
    if manager.machine.time:as_double() < 1 or coroutine.status(routine) == "dead" then return end
    local ok, message = coroutine.resume(routine)
    assert(ok, message)
end)
