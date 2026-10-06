-- license:BSD-3-Clause
-- copyright-holders:Mattis Lind
-- Deterministic daisy-chain regression: SMDC RI must expose a pending WDC IRQ.
-- Run with -slot_disk smdc -slot_disk2 wdc; no disk image is required.
local cpu = assert(manager.machine.devices[":slot_cpu:cpu_a:maincpu"])
local mem, ios = cpu.spaces.program, cpu.spaces.io_std
local routine = coroutine.create(function()
    ios:write_u16(0xffc0, 0x0100)
    for address = 0xffa0, 0xffae, 2 do ios:write_u8(address + 1, 3) end
    for _, address in ipairs({0x7000, 0x7200, 0x7300}) do
        mem:write_u16(address, 0x5e08); mem:write_u16(address + 2, address)
    end
    cpu.state.FCW.value, cpu.state.PC.value = 0x4000, 0x7000
    cpu.state.R14.value, cpu.state.R15.value = 0, 0x6000
    cpu.state.PSAPSEG.value, cpu.state.PSAPOFF.value = 0, 0x5000
    mem:write_u16(0x503a, 0x4000)
    mem:write_u32(0x507c, 0x00007200) -- SMDC vector 20
    mem:write_u32(0x5080, 0x00007300) -- WDC vector 22
    ios:write_u8(0x8005, 0x22); ios:write_u8(0x8000, 0x0c)
    coroutine.yield()
    assert(ios:read_u8(0x8010) & 0x80 ~= 0, "WDC vector command did not finish")
    ios:write_u8(0x8010, 0)
    for address = 0x9000, 0x9016, 2 do mem:write_u16(address, 0) end
    for address = 0x9200, 0x921e, 2 do mem:write_u16(address, 0) end
    mem:write_u16(0x9000, 1) -- unit-0 dispatch GO
    mem:write_u32(0x9008, 0x00009200) -- NOP packet
    for _, value in ipairs({0x0080, 0x0004, 0x9005, 0x0006, 0x0001, 0x2007}) do
        ios:write_u16(0x7f00, value)
    end
    cpu.state.FCW.value = 0x5000
    ios:write_u16(0x7f00, 0x0018) -- enable interrupt and wake controller
    coroutine.yield()
    assert(mem:read_u16(0x9000) == 4, "SMDC NOP packet did not complete")
    assert(cpu.state.IRQV.value & 0xff == 0x20 and ios:read_u16(0x7f00) & 2 ~= 0, "SMDC interrupt was not acknowledged")
    ios:write_u8(0x8000, 0x20) -- interrupting NULL, lower-priority WDC
    coroutine.yield()
    assert(ios:read_u8(0x8010) & 0x80 ~= 0, "WDC NULL did not finish")
    cpu.state.FCW.value = 0x5000
    coroutine.yield()
    assert(cpu.state.IRQV.value & 0xff == 0x20, "WDC bypassed SMDC IUS")
    ios:write_u16(0x7f00, 0x0040) -- ZEUS's RI interrupt completion
    coroutine.yield()
    assert(cpu.state.IRQV.value == 0x22 and mem:read_u16(0x5ff0) == 0x22,
           "SMDC RI did not deliver the pending WDC interrupt to the guest")
    -- ZEUS queues the next WDC command before resetting the interrupt at
    -- the end of zdint. Its completion must survive that earlier IRQ's reset.
    ios:write_u8(0x8001, 1)
    ios:write_u8(0x8000, 0x20)
    coroutine.yield()
    assert(ios:read_u8(0x8010) & 0x80 ~= 0, "second WDC NULL did not finish")
    ios:write_u8(0x8010, 0)
    cpu.state.FCW.value = 0x5000
    coroutine.yield()
    assert(cpu.state.IRQV.value == 0x1022,
           "WDC interrupt reset discarded the next command's pending completion")
    ios:write_u8(0x8010, 0)
    cpu.state.FCW.value = 0x4000
    ios:write_u8(0x8001, 2)
    ios:write_u8(0x8000, 0x20)
    coroutine.yield()
    assert(ios:read_u8(0x8010) & 0x80 ~= 0, "polled WDC NULL did not finish")
    ios:write_u8(0x8010, 0)
    cpu.state.FCW.value = 0x5000
    coroutine.yield()
    assert(cpu.state.IRQV.value == 0x1022, "polling reset did not clear an unacknowledged WDC request")
    print("PASS: SMDC RI releases its daisy-chain priority and delivers the pending WDC interrupt")
    print("PASS: WDC interrupt reset preserves a completion queued during its previous handler")
    print("PASS: WDC polling reset clears an unacknowledged completion")
    manager.machine:exit()
end)
emu.register_periodic(function()
    if manager.machine.time:as_double() < 1 or coroutine.status(routine) == "dead" then return end
    local ok, message = coroutine.resume(routine)
    assert(ok, message)
end)
