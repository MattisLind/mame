-- license:BSD-3-Clause
-- copyright-holders:Mattis Lind
-- Completion arriving during an earlier ISR must survive that ISR's reset.
-- Run with -slot_disk mwdcii -slot_tape tcc; IRQ_PROBE_DEVICE=tcc or mwdcii.
local target = os.getenv("IRQ_PROBE_DEVICE") or "tcc"
assert(target == "tcc" or target == "mwdcii")
local cpu = assert(manager.machine.devices[":slot_cpu:cpu_a:maincpu"])
local mem, ios = cpu.spaces.program, cpu.spaces.io_std
local function request(vector)
    if target == "tcc" then
        ios:write_u16(0x40, vector)
        ios:write_u16(0x42, 0x0b) -- select unit 0; no cartridge needed
    else
        local board = assert(manager.machine.devices[":slot_disk:mwdcii:cpu"]).spaces.io_std
        board:write_u16(0x74, vector)
        board:write_u16(0x72, 0)
        board:write_u16(0x72, 2) -- controller completion request edge
    end
end
local function reset()
    ios:write_u16(target == "tcc" and 0x4e or 0x80f2, target == "tcc" and 3 or 0)
end
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
    mem:write_u32(0x5204, 0x00007200) -- vector E4
    mem:write_u32(0x5208, 0x00007300) -- vector E6
    reset()
    cpu.state.FCW.value = 0x5000
    request(0xe4)
    coroutine.yield()
    assert(cpu.state.IRQV.value == 0xe4, target .. " first completion was not acknowledged")
    request(0xe6)
    coroutine.yield()
    assert(cpu.state.IRQV.value == 0xe4, target .. " next completion bypassed IEO")
    reset()
    cpu.state.FCW.value = 0x5000
    coroutine.yield()
    assert(cpu.state.IRQV.value == 0xe6, target .. " reset discarded the next pending completion")
    reset()
    cpu.state.FCW.value = 0x4000
    request(0xe4)
    coroutine.yield()
    reset()
    cpu.state.FCW.value = 0x5000
    coroutine.yield()
    assert(cpu.state.IRQV.value == 0xe6, target .. " polling reset did not clear its request")
    print("PASS: " .. target .. " preserves the next completion at ISR reset and clears polled requests")
    manager.machine:exit()
end)
emu.register_periodic(function()
    if manager.machine.time:as_double() < 1 or coroutine.status(routine) == "dead" then return end
    local ok, message = coroutine.resume(routine)
    assert(ok, message)
end)
