-- license:BSD-3-Clause
-- copyright-holders:Mattis Lind
-- Real firmware round trip: multiple sectors, head/cylinder/64 KiB boundaries.
-- Requires a writable image created by create_mwdcii_image.py.
local host = assert(manager.machine.devices[":slot_cpu:cpu_a:maincpu"])
local controller = assert(manager.machine.devices[":slot_disk:mwdcii:cpu"])
local mem, ios = host.spaces["program"], host.spaces["io_std"]
local step, deadline = 0, 0
local count = 2048
local source, target = 0x1ff00, 0x2ff00
local unit = tonumber(os.getenv("MWDCII_PROBE_UNIT") or "0")
assert(unit >= 0 and unit <= 3 and unit == math.floor(unit), "invalid probe unit")
local packet_address = 0x8002 + unit * 16
local verify_only = os.getenv("MWDCII_PROBE_VERIFY_ONLY") == "1"
local check_fault = os.getenv("MWDCII_PROBE_WRITE_FAULT") == "1"
local function pattern(i) return (i * 37 + (i >> 8) * 11) & 255 end
local function submit(command, block, destination)
    local packet = {command, 0, block, 0, count // 512, destination & 0xffff, destination >> 16, 0}
    for i, value in ipairs(packet) do mem:write_u16(packet_address + (i - 1) * 2, value) end
    mem:write_u16(0x8000, 1 << (unit * 2))
    ios:write_u16(0x80f0, 0)
    deadline = manager.machine.time:as_double() + 5
end
local function finished()
    local status = mem:read_u16(0x8000)
    if status ~= 0 then
        assert(manager.machine.time:as_double() < deadline, "firmware command timeout")
        return false
    end
    assert((mem:read_u16(packet_address + 14) & 0x1f00) == 0, string.format("disk status %04x", mem:read_u16(packet_address + 14)))
    return true
end
emu.register_periodic(function()
    local t = manager.machine.time:as_double()
    if step == 0 and t >= 1 then ios:write_u16(0x80f0, 0xf1f1); step = 1
    elseif step == 1 and t >= 1.5 then ios:write_u16(0x80f0, 0); step = 2
    elseif step == 2 and t >= 2 then ios:write_u16(0x80f0, 0x8000); step = 3
    elseif step == 3 and t >= 4 then
        assert((mem:read_u16(packet_address + 14) & 0x1f00) == 0, string.format("power-up status %04x", mem:read_u16(packet_address + 14)))
        for i = 0, count - 1 do mem:write_u8(source + i, pattern(i)); mem:write_u8(target + i, 0xa5) end
        mem:write_u16(target - 2, 0xcafe)
        mem:write_u16(target + count, 0xf00d)
        -- Logical block 134 crosses a cylinder for type 0 (136 sectors/cyl).
        submit(verify_only and 0x0010 or 0x0011, 134, verify_only and target or source)
        step = verify_only and 5 or 4
    elseif step == 4 and finished() then submit(0x0010, 134, target); step = 5
    elseif step == 5 and finished() then
        for i = 0, count - 1 do
            assert(mem:read_u8(target + i) == pattern(i), string.format("round-trip mismatch at byte %x", i))
        end
        assert(mem:read_u16(target - 2) == 0xcafe and mem:read_u16(target + count) == 0xf00d, "DMA wrote beyond its destination")
        print("PASS: MWDCII unit " .. unit .. " firmware/image 4-sector " .. (verify_only and "persistent read" or "round trip") .. " across head and DMA segment boundaries")
        if check_fault then
            -- Exercise an image-write failure through the sector adapter:
            -- drive 1 is absent. The real firmware must take its fault ISR.
            local cio = controller.spaces["io_std"]
            cio:write_u16(0x70, 0x08e4)
            for i = 1, 267 do cio:write_u16(0x7e, 0) end
            deadline = t + 0.1
            step = 6
        else
            manager.machine:exit()
            step = 7
        end
    elseif step == 6 and t >= deadline then
        step = 7
        assert(controller.spaces["data"]:read_u16(0x40d4) == 0x0400,
            string.format("image write failure: status %04x, PC %x, FCW %04x, PA %04x, CSR %04x, vector %04x",
                controller.spaces["data"]:read_u16(0x40d4), controller.state["PC"].value, controller.state["FCW"].value,
                controller.spaces["io_std"]:read_u16(0x9a), controller.spaces["io_std"]:read_u16(0x90), controller.state["IRQV"].value))
        print("PASS: image write failure reaches MWDCII firmware fault ISR")
        manager.machine:exit()
        step = 7
    end
end)
