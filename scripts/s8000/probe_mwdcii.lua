-- license:BSD-3-Clause
-- copyright-holders:Mattis Lind
-- Reset the real firmware, establish its DMA destination and request version.
local host = assert(manager.machine.devices[":slot_cpu:cpu_a:maincpu"])
local controller = assert(manager.machine.devices[":slot_disk:mwdcii:cpu"])
local ios = host.spaces["io_std"]
local step = 0
emu.register_periodic(function()
    local t = manager.machine.time:as_double()
    if t < 1 + step * 0.5 then return end
    if step == 0 then ios:write_u16(0x80f0, 0xf1f1)
    elseif step == 1 then ios:write_u16(0x80f0, 0x0000)
    elseif step == 2 then ios:write_u16(0x80f0, 0x8000)
    elseif step == 6 then ios:write_u16(0x80f0, 0x1d1d)
    elseif step == 10 then
        assert(controller.spaces["data"]:read_u16(0x4000) == 0x0203, "controller did not return firmware version")
        assert(host.spaces["program"]:read_u16(0x8000) == 0x0203, "controller DMA did not reach host RAM")
        print("PASS: MWDCII firmware 2.03 mailbox and DMA response")
        manager.machine:exit()
    end
    step = step + 1
end)
