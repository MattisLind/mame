// license:BSD-3-Clause
// copyright-holders:Mattis Lind
/*
 * System 8000 Cartridge Tape Controller, board 09-0208.
 *
 * Record-level emulation of the host interface, like the SMDC.  The Z80B
 * firmware and discrete TTL formatter are not executed.  ROMs are retained
 * for auditing and future firmware-driven emulation.  No flux, NRZ framing
 * or hardware CRC is modelled.  Timing is provisional.
 *
 * Media: SIMH TAP with Tpaxia's standalone 0x70000000 | track markers.
 * Unmarked images occupy track 0.  Independent tracks must not be confused
 * with tape files (file marks separate files within each track).
 */

#include "emu.h"
#include "s8k_tcc.h"
#include "imagedev/magtape.h"
#include "multibyte.h"

#define LOG_COMMAND (1U << 1)
#include "logmacro.h"

class s8k_tcc_image_device;
DECLARE_DEVICE_TYPE(S8K_TCC_IMAGE, s8k_tcc_image_device)

class s8k_tcc_image_device : public microtape_image_device
{
public:
	s8k_tcc_image_device(machine_config const &config, char const *tag, device_t *owner, u32 clock)
		: microtape_image_device(config, S8K_TCC_IMAGE, tag, owner, clock), m_capacity(*this, "CAPACITY") { }

	struct record { u64 offset; u32 size; bool error; };
	std::vector<record> const &records(unsigned track) const { return m_records[track]; }
	u32 &position(unsigned track) { return m_position[track]; }
	bool read(record const &record, std::vector<u8> &bytes)
	{
		bytes.resize(record.size);
		return !fseek(record.offset, SEEK_SET) && fread(bytes.data(), bytes.size()) == bytes.size();
	}
	bool at_eot(unsigned track, u32 extra = 0) const;
	bool write(unsigned track, std::vector<u8> const &bytes, bool error = false, bool truncate = true, bool advance = true);
	virtual std::pair<std::error_condition, std::string> call_create(int, util::option_resolution *) override;
	void rewind() { std::fill(std::begin(m_position), std::end(m_position), 0); }

	virtual bool is_writeable() const noexcept override { return true; }
	virtual bool is_creatable() const noexcept override { return true; }
	virtual const char *image_interface() const noexcept override { return "s8000_tape"; }
	virtual const char *file_extensions() const noexcept override { return "tap"; }
	virtual const char *image_type_name() const noexcept override { return "tape"; }
	virtual const char *image_brief_type_name() const noexcept override { return "tap"; }
	virtual std::pair<std::error_condition, std::string> call_load() override;
	virtual void call_unload() override
	{
		for (auto &track : m_records) track.clear();
		rewind();
	}

protected:
	virtual ioport_constructor device_input_ports() const override ATTR_COLD;
	virtual void device_config_complete() override ATTR_COLD { add_format("tap", "S8000 cartridge tape", "tap", ""); }
	virtual void device_start() override ATTR_COLD { save_item(NAME(m_position)); }

private:
	required_ioport m_capacity;
	std::vector<record> m_records[4];
	u32 m_position[4] = {};
};

DEFINE_DEVICE_TYPE(S8K_TCC_IMAGE, s8k_tcc_image_device, "s8k_tcc_image", "S8000 cartridge tape image")

static INPUT_PORTS_START(tcc_image)
	PORT_START("CAPACITY")
	// Virtual EOT limits, not board switches or calibrated cartridge capacities.
	// Payload-only accounting omits gaps/framing; the small option accelerates SADIE TEX.
	// See docs/research/s8000-tcc.md for DEI 3400 cartridge length/capacity evidence.
	PORT_CONFNAME(0x03, 0x02, "Capacity per track")
	PORT_CONFSETTING(0x00, "1 MiB")
	PORT_CONFSETTING(0x01, "4 MiB")
	PORT_CONFSETTING(0x02, "16 MiB")
	PORT_CONFSETTING(0x03, "256 KiB (diagnostic scratch)")
INPUT_PORTS_END

ioport_constructor s8k_tcc_image_device::device_input_ports() const { return INPUT_PORTS_NAME(tcc_image); }

bool s8k_tcc_image_device::at_eot(unsigned track, u32 extra) const
{
	static constexpr u64 capacities[] = { 0x100000, 0x400000, 0x1000000, 0x40000 };
	u64 used = extra;
	for (unsigned i = 0; i < m_position[track]; ++i) used += std::max<u32>(m_records[track][i].size, 1);
	return used >= capacities[m_capacity->read() & 3];
}

std::pair<std::error_condition, std::string> s8k_tcc_image_device::call_load()
{
	call_unload();
	auto invalid = [this](char const *message) {
		call_unload();
		return std::make_pair(std::error_condition(image_error::INVALIDIMAGE), std::string(message));
	};
	u64 offset = 0;
	unsigned track = 0;
	u8 seen = 0;
	bool marked = false;
	while (offset < length())
	{
		u8 word[4];
		if (length() - offset < 4 || fseek(offset, SEEK_SET) || fread(word, 4) != 4)
			return invalid("Truncated TAP marker");
		u32 const marker = get_u32le(word);
		offset += 4;
		if (marker == 0xffffffff)
		{
			if (offset != length()) return invalid("Data follows TAP end of medium");
			return { std::error_condition(), std::string() };
		}
		if ((marker & 0xfffffffcu) == 0x70000000)
		{
			track = marker & 3;
			if ((seen & (1 << track)) || (!marked && !m_records[0].empty()))
				return invalid("Repeated track or misplaced TAP track marker");
			seen |= 1 << track;
			marked = true;
			continue;
		}
		if (!marker)
		{
			m_records[track].push_back({ offset, 0, false });
			continue;
		}
		// SIMH error flag is preserved; other special markers are unsupported.
		u32 const size = marker & 0x00ffffff;
		if ((marker & 0x7f000000) || !size)
			return invalid("Unsupported TAP marker");
		u64 const trailer = offset + size + (size & 1);
		if (trailer > length() || length() - trailer < 4 || fseek(trailer, SEEK_SET) || fread(word, 4) != 4)
			return invalid("Truncated TAP record");
		if (get_u32le(word) != marker) return invalid("TAP record length trailer mismatch");
		m_records[track].push_back({ offset, size, bool(marker & 0x80000000) });
		offset = trailer + 4;
	}
	// A clean record boundary at EOF is valid for ordinary SIMH images.
	return { std::error_condition(), std::string() };
}

// A write replaces the remainder of the selected track.  Rebuild offsets
// after serialization so other independently addressed tracks remain intact.
// Flush each command: unloading or terminating MAME must not lose tape writes.
bool s8k_tcc_image_device::write(unsigned track, std::vector<u8> const &bytes, bool damaged, bool truncate, bool advance)
{
	if (is_readonly()) return false;
	std::vector<u8> tape;
	std::vector<record> index[4];
	auto marker = [&tape](u32 value) {
		for (unsigned i = 0; i != 4; ++i) tape.push_back(value >> (8 * i));
	};
	for (unsigned t = 0; t != 4; ++t)
	{
		marker(0x70000000 | t);
		unsigned const entries = t == track && truncate ? m_position[t] + 1 : m_records[t].size();
		for (unsigned i = 0; i < entries; ++i)
		{
			std::vector<u8> data;
			bool error = false;
			if (t == track && i == m_position[t]) { data = bytes; error = damaged; }
			else
			{
				auto const &entry = m_records[t][i];
				if (!read(entry, data)) return false;
				error = entry.error;
			}
			u32 const size = data.size();
			u32 const header = size | (error ? 0x80000000 : 0);
			marker(header);
			index[t].push_back({ tape.size(), size, error });
			if (size)
			{
				tape.insert(tape.end(), data.begin(), data.end());
				if (size & 1) tape.push_back(0);
				marker(header);
			}
		}
	}
	marker(0xffffffff);
	if (fseek(0, SEEK_SET) || fwrite(tape.data(), tape.size()) != tape.size()
		|| image_core_file().truncate(tape.size()) || image_core_file().flush()) return false;
	for (unsigned t = 0; t != 4; ++t) m_records[t] = std::move(index[t]);
	if (advance) ++m_position[track];
	return true;
}

std::pair<std::error_condition, std::string> s8k_tcc_image_device::call_create(int, util::option_resolution *)
{
	call_unload();
	u8 bytes[20];
	for (unsigned t = 0; t != 4; ++t) put_u32le(bytes + t * 4, 0x70000000 | t);
	put_u32le(bytes + 16, 0xffffffff);
	if (fwrite(bytes, sizeof(bytes)) != sizeof(bytes) || image_core_file().flush())
		return { image_error::UNSPECIFIED, "Unable to create cartridge tape" };
	return { std::error_condition(), std::string() };
}

namespace {

class zbi_s8k_tcc_device : public device_t, public device_zbi_card_interface
{
public:
	zbi_s8k_tcc_device(machine_config const &config, char const *tag, device_t *owner, u32 clock)
		: device_t(config, ZBI_S8K_TCC, tag, owner, clock)
		, device_zbi_card_interface(config, *this)
		, m_drives(*this, "tape%u", 0U) { }

protected:
	virtual void device_add_mconfig(machine_config &config) override ATTR_COLD;
	virtual tiny_rom_entry const *device_rom_region() const override ATTR_COLD;
	virtual void device_resolve_objects() override ATTR_COLD { m_bus->add_to_daisy_chain(tag()); }
	virtual void device_start() override ATTR_COLD;
	virtual void device_reset() override ATTR_COLD;
	virtual int z80daisy_irq_state() override { return m_irq_service ? Z80_DAISY_IEO : (m_irq_pending ? Z80_DAISY_INT : 0); }
	virtual int z80daisy_irq_ack() override;
	virtual void z80daisy_irq_reti() override;

private:
	enum : u16 { NOTAP=0x0001, FMDET=0x0002, HWERR=0x0004, INVAL=0x0008,
		INAP=0x0010, BPARM=0x0040, BLKTAP=0x0080, PROT=0x0100, LBOT=0x0200, LEOT=0x0400 };
	u16 host_r(offs_t offset);
	void host_w(offs_t offset, u16 data, u16 mem_mask = ~0);
	TIMER_CALLBACK_MEMBER(command_done);
	void execute();
	void update_irq();
	u16 media_status() const;
	bool step(bool reverse, bool &filemark);

	required_device_array<s8k_tcc_image_device, 4> m_drives;
	emu_timer *m_timer = nullptr;
	u16 m_registers[8] = {};
	u8 m_unit = 0;
	u8 m_track[4] = {};
	bool m_loaded[4] = {};
	bool m_independent = false;
	u8 m_max_retries = 10;
	bool m_irq_pending = false;
	bool m_irq_service = false;
	bool m_diagnostic = false;
	u8 m_diagnostic_vector = 0;
	u16 m_diagnostic_control = 0;
};

u16 zbi_s8k_tcc_device::media_status() const
{
	auto const &image = *m_drives[m_unit];
	unsigned const track = m_track[m_unit];
	u16 status = (image.exists() && image.is_readonly() ? PROT : 0) | (m_unit << 12) | (track << 14);
	if (!image.exists()) return status | NOTAP;
	if (!m_loaded[m_unit] || (!m_drives[m_unit]->position(track) && (m_independent || !track))) status |= LBOT;
	if (m_loaded[m_unit] && image.at_eot(track)
		&& (m_independent || track == 3)) status |= LEOT;
	return status;
}

u16 zbi_s8k_tcc_device::host_r(offs_t offset)
{
	return m_registers[offset];
}

void zbi_s8k_tcc_device::host_w(offs_t offset, u16 data, u16 mem_mask)
{
	if (m_diagnostic)
	{
		COMBINE_DATA(&m_registers[offset]);
		// The monitor tests registers in ascending order, while TCUMON
		// tests them in descending order.  The firmware waits for every
		// byte to become AA, not merely a write to the last address.
		bool complete = true;
		for (u16 value : m_registers) complete &= value == 0xaaaa;
		// Leave time for the host to read back its final write before the
		// firmware clears the test pattern and releases BUSY.
		if (complete) m_timer->adjust(attotime::from_usec(100));
		return;
	}
	if (offset == 1 && (m_registers[0] & 0x0200)) return;
	u16 const controller_status = m_registers[0] & 0xff00;
	COMBINE_DATA(&m_registers[offset]);
	if (offset == 0)
		m_registers[0] = controller_status | (m_registers[0] & 0xff); // high byte is read-only
	else if (offset == 7)
	{
		// The ZEUS CT ISR acknowledges the board by rewriting control=3.
		// Acknowledge already consumed the serviced request. A command
		// started inside the ISR can complete before this reset, so retain
		// that new request when the interrupt enables are still set.
		if (!(m_registers[7] & 0x40))
		{
			if (!m_irq_service || (m_registers[7] & 3) != 3) m_irq_pending = false;
			m_irq_service = false;
			if (m_irq_pending) m_registers[7] |= 0x40;
		}
		update_irq();
	}
	else if (offset == 1 && ACCESSING_BITS_0_7 && (m_registers[1] & 0xff))
	{
		m_registers[0] |= 0x0200;
		m_registers[7] &= ~0xc0;
		// Keep completion asynchronous so both polling and interrupt users
		// observe BUSY.  Transport timing is not yet calibrated to hardware.
		m_timer->adjust(attotime::from_usec(100));
	}
}

bool zbi_s8k_tcc_device::step(bool reverse, bool &filemark)
{
	auto &image = *m_drives[m_unit];
	u8 &track = m_track[m_unit];
	if (reverse)
	{
		while (!image.position(track))
		{
			if (m_independent || !track) return false;
			--track;
			image.position(track) = image.records(track).size();
		}
		filemark = !image.records(track)[--image.position(track)].size;
	}
	else
	{
		while (image.position(track) >= image.records(track).size())
		{
			if (m_independent || track == 3) return false;
			image.position(++track) = 0;
		}
		filemark = !image.records(track)[image.position(track)++].size;
	}
	return true;
}

void zbi_s8k_tcc_device::execute()
{
	u8 const command = m_registers[1];
	u8 const operand = m_registers[1] >> 8;
	auto &image = *m_drives[m_unit];
	u16 status = 0;
	u16 controller_error = 0;
	m_registers[6] = 0;
	LOGMASKED(LOG_COMMAND, "TCC cmd=%04x unit=%u track=%u pos=%u DMA=%04x:%04x count=%04x\n",
		m_registers[1], m_unit, m_track[m_unit], image.position(m_track[m_unit]), m_registers[3], m_registers[2], m_registers[4]);
	if (command > 0x10) status = INVAL;
	else if (command == 0x0b)
	{
		if (operand > 3) status = INVAL;
		else m_unit = operand;
	}
	else if (command == 0x0c)
	{
		if (operand > 15) status = INVAL;
		else m_max_retries = operand;
	}
	else if (command == 0x0f)
	{
		if (operand > 1) status = INVAL;
		else m_independent = operand;
	}
	else if (command == 0x10)
	{
		if (operand) status = INVAL;
		else
		{
			// Firmware DIAG tests its host latches using 5555/AAAA.  Expose
			// that handshake; this is not a test of the unmodelled TTL FIFO.
			m_diagnostic_vector = m_registers[0];
			m_diagnostic_control = m_registers[7];
			std::fill(std::begin(m_registers), std::end(m_registers), 0x5555);
			m_diagnostic = true;
			return;
		}
	}
	else if (!image.exists()) status = NOTAP;
	else if (command == 9)
	{
		if (operand) status = INVAL;
		else { image.rewind(); m_track[m_unit] = 0; m_loaded[m_unit] = true; }
	}
	else if (command == 0x0a)
	{
		if (operand) status = INVAL;
		else if (!m_loaded[m_unit]) status = INAP;
		else { image.rewind(); m_track[m_unit] = 0; m_loaded[m_unit] = false; }
	}
	else if (!m_loaded[m_unit]) status = INAP;
	else if (command == 0x0e)
	{
		if (operand > 3) status = INVAL;
		else { m_track[m_unit] = operand; image.position(operand) = 0; }
	}
	else if (command == 3)
	{
		if (operand) status = INVAL;
		else if (m_independent) image.position(m_track[m_unit]) = 0;
		else { image.rewind(); m_track[m_unit] = 0; }
	}
	else if (command >= 4 && command <= 7)
	{
		bool const reverse = command & 1;
		bool const files = command >= 6;
		unsigned skipped = 0;
		// A forward file search starts at the file mark currently under
		// the head after SKBF found it (SADIE TCOM module 5).
		unsigned const track = m_track[m_unit];
		unsigned const position = image.position(track);
		if (command == 7 && operand && position && !image.records(track)[position - 1].size)
			--image.position(track);
		if (command == 6 && operand && (m_registers[5] & FMDET) && position
			&& !image.records(track)[position - 1].size) { ++skipped; status = FMDET; }
		while (skipped < operand)
		{
			bool mark;
			if (!step(reverse, mark))
			{
				status |= reverse ? LBOT : LEOT;
				controller_error = 0x1000; // skip operation not completed
				break;
			}
			if (mark || !files) ++skipped;
			status = mark ? FMDET : 0;
		}
		if (command == 7 && skipped && (status & FMDET)) ++image.position(m_track[m_unit]);
		m_registers[6] = skipped << 8;
	}
	else if (command == 1 || command == 2)
	{
		// The high address byte is on D15..D8 at 46H; D7..D0 are
		// address bits 24..31, which the firmware requires to be zero.
		u32 address = (u32(m_registers[3] >> 8) << 16) | m_registers[2];
		// Firmware 0333..033f sets HL to the original count, then 018f
		// subtracts BC (the remaining count).  48H returns bytes transferred,
		// as confirmed by the unmodified standalone ct driver at 2f90.
		u16 const count = m_registers[4];
		if (operand) status = INVAL;
		else if ((address & 1) || (m_registers[3] & 0xff) || !count || count > 0x8000
			|| (count & 1) || address + count > 0xffffff) status = BPARM;
		else if (command == 2)
		{
			if (image.is_readonly()) status = INAP;
			else
			{
				if (image.at_eot(m_track[m_unit], count - 1) && !m_independent && m_track[m_unit] < 3)
					image.position(++m_track[m_unit]) = 0;
				if (image.at_eot(m_track[m_unit], count - 1))
				{
					status = LEOT | INAP;
					m_registers[4] = 0;
				}
				else
				{
					std::vector<u8> bytes(count);
					for (unsigned i = 0; i < count; i += 2)
					{
						u16 const value = m_bus->ram16_r(address + i);
						bytes[i] = value >> 8;
						bytes[i + 1] = value;
					}
					if (!image.write(m_track[m_unit], bytes)) { status = HWERR; m_registers[4] = 0; }
					else
					{
						address += count;
						m_registers[2] = address;
						m_registers[3] = (address >> 16) << 8;
					}
				}
			}
		}
		else
		{
			bool mark;
			if (!step(false, mark)) { status = LEOT | BLKTAP; m_registers[4] = 0; }
			else if (mark) { status = FMDET | INAP; m_registers[4] = 0; }
			else
			{
				auto const &record = image.records(m_track[m_unit])[image.position(m_track[m_unit]) - 1];
				std::vector<u8> bytes;
				if (!image.read(record, bytes)) status = HWERR;
				else if (record.error)
				{
					controller_error = 0x0800; // exhausted data-error retries
					m_registers[6] = m_max_retries;
					if (m_max_retries) status |= 0x0800; // retries attempted
					m_registers[4] = 0;
				}
				else
				{
					unsigned const transfer = std::min<unsigned>(count, bytes.size());
					for (unsigned i = 0; i < transfer; i += 2)
					{
						u16 const mask = i + 1 < transfer ? 0xffff : 0xff00;
						u16 const value = (bytes[i] << 8) | (i + 1 < transfer ? bytes[i + 1] : 0);
						m_bus->ram16_w((address + i) & 0xffffff, value, mask);
					}
					address = (address + ((transfer + 1) & ~1U)) & 0xffffff;
					m_registers[2] = address;
					m_registers[3] = (address >> 16) << 8;
					m_registers[4] = transfer;
				}
			}
		}
	}
	else if (command == 8)
	{
		if (operand) status = INVAL;
		else if (image.is_readonly()) status = INAP;
		else
		{
			if (image.at_eot(m_track[m_unit]) && !m_independent && m_track[m_unit] < 3)
				image.position(++m_track[m_unit]) = 0;
			if (image.at_eot(m_track[m_unit])) status = LEOT | INAP;
			else if (!image.write(m_track[m_unit], {})) status = HWERR;
		}
	}
	else if (command == 0x0d)
	{
		if (operand) status = INVAL;
		else if (image.is_readonly()) status = INAP;
		else
		{
			// At record level an erase gap damages the next recorded block.
			// It neither inserts a file mark nor consumes a logical block.
			unsigned const track = m_track[m_unit];
			unsigned const position = image.position(track);
			if (position < image.records(track).size())
			{
				std::vector<u8> bytes;
				if (!image.read(image.records(track)[position], bytes)) status = HWERR;
				else
				{
					if (bytes.empty()) bytes.resize(2); // a damaged mark must remain a valid TAP record
					if (!image.write(track, bytes, true, false, false)) status = HWERR;
				}
			}
		}
	}
	else status = INVAL;
	if ((command != 1 && command != 2) || (status & (NOTAP | HWERR | INVAL | INAP | BPARM))) m_registers[4] = 0;
	m_registers[5] = status | media_status();
	m_registers[1] = 0;
	m_registers[0] = (m_registers[0] & 0x00ff) | controller_error;
	if (status & (NOTAP | HWERR | INVAL | INAP | BPARM)) m_registers[0] |= 0x0400;
	if (status & NOTAP) { m_registers[0] |= 0x0100; m_registers[5] |= INAP; }
	if (status & BLKTAP) m_registers[0] |= 0x0800;
	if ((m_registers[7] & 3) == 3)
	{
		m_registers[7] |= 0x40;
		m_irq_pending = true;
	}
	else m_registers[7] |= 0x80;
	update_irq();
}

TIMER_CALLBACK_MEMBER(zbi_s8k_tcc_device::command_done)
{
	if (m_diagnostic)
	{
		std::fill(std::begin(m_registers), std::end(m_registers), 0);
		// Firmware 0a36..0a3a restores the saved interrupt control/vector.
		m_registers[0] = m_diagnostic_vector;
		m_registers[7] = m_diagnostic_control;
		m_registers[5] = media_status();
		m_diagnostic = m_irq_service = false;
		m_irq_pending = (m_registers[7] & 3) == 3;
		m_registers[7] |= m_irq_pending ? 0x40 : 0x80;
		update_irq();
	}
	else execute();
}

void zbi_s8k_tcc_device::update_irq() { m_bus->vi_w(m_irq_pending ? ASSERT_LINE : CLEAR_LINE); }
int zbi_s8k_tcc_device::z80daisy_irq_ack()
{
	m_irq_pending = false;
	m_irq_service = true;
	return m_registers[0] & 0xff;
}
void zbi_s8k_tcc_device::z80daisy_irq_reti() { m_irq_service = false; update_irq(); }

void zbi_s8k_tcc_device::device_start()
{
	m_timer = timer_alloc(FUNC(zbi_s8k_tcc_device::command_done), this);
	save_item(NAME(m_registers));
	save_item(NAME(m_unit));
	save_item(NAME(m_track));
	save_item(NAME(m_loaded));
	save_item(NAME(m_independent));
	save_item(NAME(m_max_retries));
	save_item(NAME(m_irq_pending));
	save_item(NAME(m_irq_service));
	save_item(NAME(m_diagnostic));
	save_item(NAME(m_diagnostic_vector));
	save_item(NAME(m_diagnostic_control));
}

void zbi_s8k_tcc_device::device_reset()
{
	m_timer->adjust(attotime::never);
	std::fill(std::begin(m_registers), std::end(m_registers), 0);
	std::fill(std::begin(m_track), std::end(m_track), 0);
	std::fill(std::begin(m_loaded), std::end(m_loaded), false);
	for (auto &drive : m_drives) drive->rewind();
	m_unit = 0;
	m_independent = m_irq_pending = m_irq_service = m_diagnostic = false;
	m_diagnostic_vector = m_diagnostic_control = 0;
	m_max_retries = 10;
	m_registers[5] = media_status();
	m_bus->iospace()->install_readwrite_handler(0x0040, 0x004f,
		read16sm_delegate(*this, FUNC(zbi_s8k_tcc_device::host_r)),
		write16s_delegate(*this, FUNC(zbi_s8k_tcc_device::host_w)));
}

void zbi_s8k_tcc_device::device_add_mconfig(machine_config &config)
{
	for (auto &drive : m_drives) S8K_TCC_IMAGE(config, drive, 0);
}

ROM_START(s8k_tcc)
	ROM_REGION(0x1000, "firmware", 0)
	ROM_LOAD("tcc_34-0607-01a.u97", 0x0000, 0x0800, CRC(106a23a2) SHA1(3b615f9b9ddac6685ff79f0676249c30f1fbaad4))
	ROM_LOAD("tcc_34-0608-01a.u96", 0x0800, 0x0800, CRC(ebc69875) SHA1(1cd3cb1121e01440487165da753932812dada8bf))
ROM_END

tiny_rom_entry const *zbi_s8k_tcc_device::device_rom_region() const { return ROM_NAME(s8k_tcc); }

} // anonymous namespace

DEFINE_DEVICE_TYPE_PRIVATE(ZBI_S8K_TCC, device_zbi_card_interface, zbi_s8k_tcc_device, "s8k_tcc", "Zilog S8000 Cartridge Tape Controller")
