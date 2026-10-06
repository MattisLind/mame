-- license:BSD-3-Clause
-- copyright-holders:Mattis Lind
-- Regression: the host must not see a 7E1 acknowledgement before the
-- null modem's input-port callbacks have applied the new configuration.
local request = assert(os.getenv("ZEUS_CONSOLE_7E1_FILE"))
local script = os.getenv("ZEUS_CONSOLE_SCRIPT") or "scripts/s8000/native_tape.lua"
local ports = manager.machine.ioport.ports
local prefix = ":slot_cpu:cpu_a:sio0:chb:console:" .. (os.getenv("ZEUS_CONSOLE_DEVICE") or "null_modem") .. ":"
local databits, parity = ports[prefix .. "RS232_DATABITS"], ports[prefix .. "RS232_PARITY"]
assert(databits:read() == 3 and parity:read() == 0, "probe must start in 8N1")
dofile(script)
local file = assert(io.open(request, "wb"))
file:write("9600 7E1\n"); file:close()
local saw_pending, finished = false, false
emu.register_periodic(function()
    if finished then return end
    local applied = databits:read() == 2 and parity:read() == 2
    if databits.fields["Data Bits"].user_value == 2 and not applied then
        saw_pending = true
    end
    local ready = io.open(request .. ".ready", "rb")
    if ready then
        ready:close()
        finished = true
        if not applied then
            print("FAIL: console acknowledged 7E1 before its input ports updated")
        elseif not saw_pending then
            print("FAIL: console probe did not exercise pending input-port changes")
        else
            print("PASS: console acknowledged 7E1 after input-port updates (" .. script .. ")")
        end
        manager.machine:exit()
    end
end)
