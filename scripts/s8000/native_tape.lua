-- license:BSD-3-Clause
-- copyright-holders:Mattis Lind
-- Harness lifecycle and console settings only. No injected or patched code.
-- Boot from the monitor with Z T. Images are mounted on the tcc slot device.
local stop_file = os.getenv("SADIE_STOP_FILE")
local serial_file = os.getenv("ZEUS_CONSOLE_7E1_FILE")
local console_prefix = ":slot_cpu:cpu_a:sio0:chb:console:" .. (os.getenv("ZEUS_CONSOLE_DEVICE") or "null_modem") .. ":"
local changed = false
local serial_frames = 0
-- user_value changes reach device callbacks on a frame update. Acknowledge
-- only after two frame notifications, so input cannot race the reconfiguration.
_G.zeus_console_ready_notifier = emu.add_machine_frame_notifier(function()
    if serial_frames > 0 then
        serial_frames = serial_frames - 1
        if serial_frames == 0 then
            local ports = manager.machine.ioport.ports
            local prefix = console_prefix
            assert(ports[prefix .. "RS232_DATABITS"]:read() == 2)
            assert(ports[prefix .. "RS232_PARITY"]:read() == 2)
            local ready = assert(io.open(serial_file .. ".ready", "wb"))
            ready:write("9600 7E1\n"); ready:close()
            print("ZEUS console switched to 9600 baud, 7 data bits, even parity")
        end
    end
end)
local mount_file = os.getenv("SADIE_TAPE_MOUNT_FILE")
-- Observe host packets without changing guest code or controller state.
local mdc_summary = os.getenv("MWDCII_SUMMARY_FILE")
local mdc_counts = {}
if mdc_summary then
    local cpu = manager.machine.devices[":slot_cpu:cpu_a:maincpu"]
    local mem, ios = cpu.spaces.program, cpu.spaces.io_std
    local phase, high, base = 0, 0, nil
    _G.mdc_packets = ios:install_write_tap(0x80f0, 0x80f1, "mdc-packets", function(offset, value, mask)
        if value == 0xf1f1 then phase = 1; base = nil
        elseif phase == 1 then high = value; phase = 2
        elseif phase == 2 then base = (high << 16) | value; phase = 0
        elseif value == 0 and base then
            local pending = mem:read_u16(base)
            for unit = 0, 3 do
                if pending & (1 << (unit * 2)) ~= 0 then
                    local packet = base + 2 + unit * 16
                    local command = mem:read_u16(packet) & 0xff
                    if command == 0x10 or command == 0x11 then
                        local key = string.format("unit%d-%s", unit, command == 0x10 and "read" or "write")
                        local stats = mdc_counts[key] or {commands = 0, blocks = 0, seen = {}}
                        local block = mem:read_u16(packet + 4) | (mem:read_u16(packet + 6) << 16)
                        local count = mem:read_u16(packet + 8)
                        stats.commands = stats.commands + 1; stats.blocks = stats.blocks + count
                        for b = block, block + count - 1 do stats.seen[b] = true end
                        mdc_counts[key] = stats
                    end
                end
            end
        end
    end)
end
if os.getenv("TCC_TRACE") == "1" or os.getenv("ZEUS_TRACE") == "1" then
    local cpu = manager.machine.devices[":slot_cpu:cpu_a:maincpu"]
    local ios = cpu.spaces.io_std
    _G.tcc_trace = ios:install_write_tap(0x0042, 0x0043, "tcc-trace", function(offset, value, mask)
        print(string.format("TCC pc=%06x cmd=%04x mask=%04x addr=%04x:%04x count=%04x", cpu.state.PC.value,
            value, mask, ios:read_u16(0x46), ios:read_u16(0x44), ios:read_u16(0x48)))
    end)
    _G.tcc_irq_trace = ios:install_write_tap(0x004e, 0x004f, "tcc-irq-trace", function(offset, value, mask)
        print(string.format("TCC IRQ pc=%06x control=%04x mask=%04x vector=%04x", cpu.state.PC.value,
            value, mask, ios:read_u16(0x40)))
    end)
end
if os.getenv("WDC_TRACE") == "1" then
    local cpu = manager.machine.devices[":slot_cpu:cpu_a:maincpu"]
    local ios = cpu.spaces.io_std
    _G.wdc_trace = ios:install_write_tap(0x8000, 0x8001, "wdc-trace", function(offset, value, mask)
        if mask & 0xff00 ~= 0 then
            print(string.format("WDC pc=%06x cmd=%02x unit=%d CHS=%d/%d/%d count=%04x addr=%02x:%04x", cpu.state.PC.value,
                value >> 8, ios:read_u8(0x8001), ios:read_u8(0x8003) | (ios:read_u8(0x8004) << 8),
                ios:read_u8(0x8002), ios:read_u8(0x8005), ios:read_u8(0x8008) | (ios:read_u8(0x8009) << 8),
                ios:read_u8(0x800c), ios:read_u8(0x800a) | (ios:read_u8(0x800b) << 8)))
        end
    end)
end
-- Native tape boots use the same kernel as zeus_install.lua. Keep --trace
-- useful here too: report sleeping processes and their kernel wait channels.
if os.getenv("ZEUS_TRACE") == "1" then
    io.stdout:setvbuf("no")
    local cpu = manager.machine.devices[":slot_cpu:cpu_a:maincpu"]
    local data = cpu.spaces.data
    local last = -30
    emu.register_periodic(function()
        local now = manager.machine.time:as_double()
        if now < 30 or now - last < 30 or cpu.state.FCW.value & 0x4000 == 0 then return end
        last = now
        print(string.format("ZEUS state t=%.0f pc=%06x fcw=%04x", now, cpu.state.PC.value, cpu.state.FCW.value))
        local count = math.min(data:read_u16(0x010726), 70) -- ZEUS 3.21 _Nproc
        for index = 0, count - 1 do
            local address = 0x0491b0 + index * 44
            local status = data:read_u8(address)
            if status >= 1 and status <= 6 then
                print(string.format("ZEUS proc %d status=%d pid=%d ppid=%d addr=%04x wait=%08x", index,
                    status, data:read_u16(address + 14), data:read_u16(address + 16),
                    data:read_u16(address + 18), data:read_u32(address + 22)))
            end
        end
    end)
end
emu.register_periodic(function()
    if mount_file then
        local f = io.open(mount_file, "rb")
        if f then
            local path = f:read("*l"); f:close(); os.remove(mount_file)
            local image = manager.machine.images[":slot_tape:tcc:tape0"]
            assert(image, "TCC tape0 image missing")
            if os.getenv("SADIE_TAPE_SMALL") == "1" then
                manager.machine.ioport.ports[":slot_tape:tcc:tape0:CAPACITY"].fields["Capacity per track"].user_value = 3
            end
            local error = image:load(path)
            assert(not error, error)
            local ready = assert(io.open(mount_file .. ".ready", "wb"))
            ready:write("mounted\n"); ready:close()
        end
    end
    if stop_file then
        local f = io.open(stop_file, "rb")
        if f then
            f:close()
            if mdc_summary then
                local summary = assert(io.open(mdc_summary, "wb"))
                for key, stats in pairs(mdc_counts) do
                    local unique, first, last = 0, nil, nil
                    for b in pairs(stats.seen) do
                        unique = unique + 1
                        first = first and math.min(first, b) or b
                        last = last and math.max(last, b) or b
                    end
                    summary:write(string.format("%s commands=%d blocks=%d unique=%d first=%d last=%d\n",
                        key, stats.commands, stats.blocks, unique, first or 0, last or 0))
                end
                summary:close()
            end
            manager.machine:exit(); return
        end
    end
    if serial_file and not changed then
        local f = io.open(serial_file, "rb")
        if f then
            f:close()
            local ports = manager.machine.ioport.ports
            local prefix = console_prefix
            ports[prefix .. "RS232_DATABITS"].fields["Data Bits"].user_value = 2
            ports[prefix .. "RS232_PARITY"].fields["Parity"].user_value = 2
            changed = true
            serial_frames = 2
        end
    end
end)
