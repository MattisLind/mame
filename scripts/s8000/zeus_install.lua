-- license:BSD-3-Clause
-- copyright-holders:Mattis Lind
-- Original ZEUS 3.21 standalone loader and utilities, logical tape transport.
-- Only ct entry points are replaced; disk drivers and installer remain original.
local cpu = assert(manager.machine.devices[":slot_cpu:cpu_a:maincpu"])
local mem, data, ios = cpu.spaces.program, cpu.spaces.data, cpu.spaces.io_std
local media = os.getenv("ZEUS_INSTALL_MEDIA") or "roms/zeus-install"
local stop_file = os.getenv("SADIE_STOP_FILE")
local serial_file = os.getenv("ZEUS_CONSOLE_7E1_FILE")
local console_prefix = ":slot_cpu:cpu_a:sio0:chb:console:" .. (os.getenv("ZEUS_CONSOLE_DEVICE") or "null_modem") .. ":"
local serial_changed = false
local serial_frames = 0
-- Wait for the input-port callbacks to apply 7E1 before accepting host input.
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
local loaded, current, cursor = false, nil, 0
local last_report = 0
local file_number = 0
local entries = {
    [1] = {0x26fa, 0x275c, 0x2770, 0x2796},
    [2] = {0x2416, 0x2478, 0x248c, 0x24b2},
    [3] = {0x20da, 0x213c, 0x2150, 0x2176},
    [4] = {0x2de6, 0x2e48, 0x2e5c, 0x2e82}
}
local function file_bytes(number)
    local suffix = number >= 5 and "dump" or "bin"
    local f = io.open(string.format("%s/file-%03d.%s", media, number, suffix), "rb")
    if not f then return nil end
    local bytes = f:read("a")
    f:close()
    if entries[number] then
        local base = number == 1 and 0 or 40
        for index, address in ipairs(entries[number]) do
            local patch = string.char(0x3b, 0x26, 0x90, (index - 1) * 2, 0x9e, 0x08)
            local offset = base + address
            bytes = bytes:sub(1, offset) .. patch .. bytes:sub(offset + #patch + 1)
        end
    end
    return bytes
end
local function open_file(number)
    file_number, current, cursor = number, file_bytes(number), 0
    print(string.format("ZEUS tape open file %d: %s", number, current and #current or "missing"))
    return current and 0 or 0xffff
end
local taps = {}
local trace = os.getenv("ZEUS_TRACE") == "1"
if os.getenv("ZEUS_TRACE_HOST") == "1" then
    taps.host = ios:install_write_tap(0x80f0, 0x80f5, "zeus-mwdcii-host", function(offset, value, mask)
        print(string.format("ZEUS MWDC host pc=%06x port=%04x value=%04x mask=%04x", cpu.state.PC.value, offset, value, mask))
    end)
end
if trace then
    local reports = 0
    local function syscall_report(name)
        if reports >= 40 then return end
        reports = reports + 1
        local component = {}
        for i = 0, 13 do
            local value = data:read_u8(0x3ef440 + i)
            if value == 0 then break end
            component[#component + 1] = string.char(value)
        end
        print(string.format("ZEUS %s error=%d component=%q retval=%04x arg=%08x", name,
            data:read_u8(0x3ef415), table.concat(component), data:read_u16(0x3ef428),
            data:read_u32(0x3ef4cc)))
    end
    taps.open_return = mem:install_read_tap(0x00f92e, 0x00f92f, "zeus-open-return", function()
        syscall_report("open return")
    end)
    taps.exec_return = mem:install_read_tap(0x0205dc, 0x0205dd, "zeus-exec-return", function()
        syscall_report("exec return")
    end)
    taps.exit = mem:install_read_tap(0x0211ee, 0x0211ef, "zeus-exit", function()
        syscall_report("exit")
        print(string.format("ZEUS exit r7=%04x", cpu.state.R7.value))
    end)
    taps.signal = mem:install_read_tap(0x008cec, 0x008ced, "zeus-signal", function()
        print(string.format("ZEUS signal proc=%04x:%04x signal=%d", cpu.state.R6.value, cpu.state.R7.value, cpu.state.R5.value))
    end)
end
_G.zeus_install_taps = taps
-- Standalone pointer arguments are offsets in the active nonsegmented data segment.
taps.open = ios:install_write_tap(0x9000, 0x9001, "zeus-tape-open", function()
    local segment = cpu.state.PC.value & 0xff0000
    local iob = segment | cpu.state.R7.value
    cpu.state.R2.value = open_file(data:read_u32(iob + 0x52))
end)
taps.close = ios:install_write_tap(0x9002, 0x9003, "zeus-tape-close", function()
    cpu.state.R2.value = 0
end)
taps.read = ios:install_write_tap(0x9004, 0x9005, "zeus-tape-read", function()
    local segment = cpu.state.PC.value & 0xff0000
    local iob = segment | cpu.state.R7.value
    local dest, requested = data:read_u16(iob + 0x62), data:read_u16(iob + 0x64)
    local count = current and cpu.state.R6.value == 1 and math.min(requested, #current - cursor) or 0
    for i = 1, count do data:write_u8(segment | (dest + i - 1), current:byte(cursor + i)) end
    if cursor == 0 or cursor + count == #current then
        print(string.format("ZEUS tape read file=%d cursor=%d count=%d", file_number, cursor, count))
    end
    cursor = cursor + count
    cpu.state.R2.value = current and count or 0xffff
end)
taps.ioctl = ios:install_write_tap(0x9006, 0x9007, "zeus-tape-command", function()
    local command = cpu.state.R6.value
    print(string.format("ZEUS tape command %04x", command))
    if command & 0xff == 6 then
        cpu.state.R2.value = open_file(file_number + (command >> 8))
    else cpu.state.R2.value = 0xffff end
end)
emu.register_periodic(function()
    if stop_file then
        local f = io.open(stop_file, "rb")
        if f then f:close(); manager.machine:exit(); return end
    end
    if serial_file and not serial_changed then
        local request = io.open(serial_file, "rb")
        if request then
            request:close()
            local ports = manager.machine.ioport.ports
            local prefix = console_prefix
            ports[prefix .. "RS232_DATABITS"].fields["Data Bits"].user_value = 2
            ports[prefix .. "RS232_PARITY"].fields["Parity"].user_value = 2
            serial_changed = true
            serial_frames = 2
        end
    end
    local now = manager.machine.time:as_double()
    if trace and loaded and now - last_report >= 10 then
        last_report = now
        print(string.format("ZEUS state t=%d pc=%06x fcw=%04x", now, cpu.state.PC.value, cpu.state.FCW.value))
        if cpu.state.FCW.value & 0x4000 ~= 0 and now >= 30 then
            for index = 0, 7 do
                local address = 0x0491b0 + index * 44
                local status = data:read_u8(address)
                if status ~= 0 then
                    print(string.format("ZEUS proc %d status=%d pid=%d ppid=%d addr=%04x wait=%08x", index, status, data:read_u16(address + 14), data:read_u16(address + 16), data:read_u16(address + 18), data:read_u32(address + 22)))
                end
            end
        end
    end
    if loaded or now < 2 then return end
    loaded = true
    local boot = assert(file_bytes(1), "Missing ZEUS secondary bootstrap")
    assert(#boot == 23040 and boot:sub(1, 4) == string.char(0x21, 0, 0x40, 0), "Unexpected bootstrap")
    ios:write_u16(0xffc0, 0x0100)
    for i = 1, #boot do mem:write_u8(i - 1, boot:byte(i)) end
    cpu.state.FCW.value = 0x4000
    -- Match CPU-A's tape-entry register environment.  The loader passes
    -- R5 through to the kernel as its licensed serial-user limit.
    cpu.state.R7.value, cpu.state.R5.value, cpu.state.R4.value = 0, 0x10, 1
    cpu.state.PC.value = 0
    print(string.format("ZEUS startup r7=%04x scr=%04x", cpu.state.R7.value, ios:read_u16(0xffc0)))
    print("ZEUS 3.21 original secondary bootstrap; logical-file tape transport")
end)
