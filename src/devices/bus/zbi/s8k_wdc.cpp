// license:BSD-3-Clause
// copyright-holders:Mattis Lind
/*
 * System 8000 8-inch Winchester Disk Controller.
 * Host-command emulation recovered from ROM 34-0611/0612 and SADIE.
 * Like SMDC, this uses logical sectors; the Z80/DMA/TTL formatter is
 * abstracted. ROMs are retained for auditing. Timing is provisional.
 */
#include "emu.h"
#include "s8k_wdc.h"
#include "imagedev/harddriv.h"
#include "multibyte.h"

namespace {
class zbi_s8k_wdc_device : public device_t, public device_zbi_card_interface
{
public:
	zbi_s8k_wdc_device(machine_config const &config, char const *tag, device_t *owner, u32 clock)
		: device_t(config, ZBI_S8K_WDC, tag, owner, clock)
		, device_zbi_card_interface(config, *this)
		, m_drives(*this, "wd%u", 0U)
	{
	}

protected:
	virtual void device_start() override ATTR_COLD;
	virtual void device_reset() override ATTR_COLD;
	virtual void device_resolve_objects() override ATTR_COLD { m_bus->add_to_daisy_chain(tag()); }
	virtual void device_add_mconfig(machine_config &config) override ATTR_COLD;
	virtual tiny_rom_entry const *device_rom_region() const override ATTR_COLD;
	virtual bool card_io_word_r(offs_t port, uint16_t &data) override;
	virtual bool card_io_word_w(offs_t port, uint16_t data) override;
	virtual int z80daisy_irq_state() override { return m_irq_service ? Z80_DAISY_IEO : (m_irq_pending ? Z80_DAISY_INT : 0); }
	virtual int z80daisy_irq_ack() override { m_irq_pending = false; m_irq_service = true; return m_vector | (u16(m_completion) << 8); }
	virtual void z80daisy_irq_reti() override { m_irq_service = false; m_bus->vi_w(CLEAR_LINE); }
private:
	u8 host_r(offs_t offset);
	void host_w(offs_t offset, u8 data);
	TIMER_CALLBACK_MEMBER(execute);
	std::error_condition image_load(device_image_interface &image);
	void dma_write(u32 address, std::vector<u8> const &bytes);
	u8 dma_read(u32 address) { u16 v = m_bus->ram16_r(address & ~1U); return address & 1 ? v : v >> 8; }
	unsigned physical_cylinders(unsigned unit) const { return m_drives[unit]->exists() ? m_drives[unit]->get_info().cylinders : 0; }
	unsigned logical_cylinders(unsigned unit) const { return physical_cylinders(unit) > 5 ? physical_cylinders(unit) - 5 : 0; }
	required_device_array<harddisk_image_device, 4> m_drives;
	emu_timer *m_timer = nullptr;
	u8 m_regs[16] = {};
	u8 m_status = 0x80;
	u8 m_vector = 0;
	u8 m_completion = 0;
	bool m_busy = false, m_irq_pending = false, m_irq_service = false, m_ce = false;
	u16 m_cylinder[4] = {};
	u8 m_detail[4][6] = {};
};

std::error_condition zbi_s8k_wdc_device::image_load(device_image_interface &image)
{
	auto const &info = downcast<harddisk_image_device &>(image).get_info();
	if (info.sectorbytes != 512 || info.sectors != 24 || info.cylinders != 605
		|| !(info.heads == 1 || info.heads == 3 || info.heads == 4 || info.heads == 5))
	{
		logerror("WDC requires 605 cylinders, 1/3/4/5 heads, 24 sectors and 512-byte sectors\n");
		return image_error::INVALIDIMAGE;
	}
	return {};
}

u8 zbi_s8k_wdc_device::host_r(offs_t offset)
{
	return offset == 16 ? m_status : m_regs[offset];
}

bool zbi_s8k_wdc_device::card_io_word_r(offs_t port, uint16_t &data)
{
	if (port < 0x8000 || port > 0x8010) return false;
	data = host_r(port - 0x8000);
	return true;
}

bool zbi_s8k_wdc_device::card_io_word_w(offs_t port, uint16_t data)
{
	if (port < 0x8000 || port > 0x8010) return false;
	// ZEUS uses OUT as well as OUTB. This byte-wide board latches the
	// low eight bits at the addressed register, without a second write
	// to its neighbour. In particular, OUT 8001 must not start command 0.
	host_w(port - 0x8000, data);
	return true;
}

void zbi_s8k_wdc_device::host_w(offs_t offset, u8 data)
{
	if (offset == 16)
	{
		// IRQ acknowledge already consumed the serviced completion. ZEUS
		// starts the next command before ending its interrupt handler, so
		// that command can complete while IEO is still asserted. Release
		// IEO without discarding its new request. Polling resets, which have
		// no acknowledged interrupt in service, still clear the request.
		if (!m_irq_service) m_irq_pending = false;
		m_irq_service = false;
		m_bus->vi_w(CLEAR_LINE);
		return;
	}
	if (m_busy) return;
	m_regs[offset] = data;
	if (!offset)
	{
		m_busy = true;
		m_status &= ~0x8f;
		m_timer->adjust(attotime::from_usec(100));
	}
}

void zbi_s8k_wdc_device::dma_write(u32 address, std::vector<u8> const &bytes)
{
	for (u8 byte : bytes)
	{
		m_bus->ram16_w(address & ~1U, address & 1 ? byte : u16(byte) << 8, address & 1 ? 0x00ff : 0xff00);
		++address;
	}
}

TIMER_CALLBACK_MEMBER(zbi_s8k_wdc_device::execute)
{
	u8 const command = m_regs[0] & 0x1f;
	unsigned const unit = m_regs[1];
	u32 const address = (u32(m_regs[12]) << 16) | get_u16le(&m_regs[10]);
	u16 const count = get_u16le(&m_regs[8]); // words, not bytes
	unsigned const cylinder = get_u16le(&m_regs[3]);
	unsigned const head = m_regs[2];
	unsigned const sector = m_regs[5] & 0x1f;
	u8 completion = 0, error = 0;
	std::vector<u8> bytes;
	unsigned const dma_length = command == 3 ? 6 : command == 0x15 ? 4 : command == 0x16 ? 24 : command == 11 ? 522 : command < 3 ? unsigned(count) * 2 : 0;
	if (dma_length && ((address & 1) || address + dma_length > 0x1000000)) completion = 8;
	else if (command == 0 || command == 0x0e || command == 0x0c) { if (command == 0x0c) m_vector = m_regs[5]; }
	else if (command == 0x15)
	{
		bytes = { 0x61, 0x11, 0x00, 0x0c }; // firmware 6.1, board 1.1, 24-byte EXTID
		dma_write(address, bytes);
	}
	else if (command == 0x16)
	{
		for (unsigned u = 0; u < 4; ++u)
		{
			u16 const phys = physical_cylinders(u), logical = logical_cylinders(u);
			bytes.insert(bytes.end(), { u8(logical >> 8), u8(logical), u8(m_drives[u]->exists() ? m_drives[u]->get_info().heads : 0),
				u8(m_drives[u]->exists() ? 24 : 0), u8(phys >> 8), u8(phys) });
		}
		dma_write(address, bytes);
	}
	else if (unit > 3 || command >= 0x18 || command == 8 || command == 9 || command == 10 || command == 13 || command == 16 || command == 17)
		completion = 8;
	else if (command == 3)
	{
		std::copy(std::begin(m_detail[unit]), std::end(m_detail[unit]), std::back_inserter(bytes));
		dma_write(address, bytes);
	}
	else if (!m_drives[unit]->exists()) { completion = 1; error = 0x10; }
	else if (command == 4) m_cylinder[unit] = 0;
	else if (command == 6)
	{
		if (cylinder >= (m_ce ? physical_cylinders(unit) : logical_cylinders(unit))) completion = 8;
		else m_cylinder[unit] = cylinder + (m_ce ? 0 : 1);
	}
	else if (command == 7) { }
	else if (command == 15) m_ce = true;
	else if (command == 20) m_ce = false;
	else if (command == 18)
	{
		// RBDT reloads the controller's geometry/defect map and leaves CE mode.
		// Only the no-defects map is supported by this sector-level adapter.
		u8 data[512];
		if (!m_drives[unit]->read(0, data)) { completion = 1; error = 0x21; }
		else if (get_u16be(data) != 0xffff) completion = 8;
		else m_ce = false;
	}
	else if (command == 5 || command == 19)
	{
		if (m_drives[unit]->is_readonly()) { completion = 1; error = 4; }
		else
		{
			u8 data[512] = {};
			auto const &info = m_drives[unit]->get_info();
			for (u32 lba = 0; lba < info.cylinders * info.heads * info.sectors; ++lba)
				if (!m_drives[unit]->write(lba, data)) { completion = 1; error = 0x80; break; }
			// No-defects metadata on physical cylinder zero, including EXTID.
			std::fill(std::begin(data), std::end(data), 0xff);
			std::fill(data + 2, data + 8, 0);
			put_u16be(data + 504, logical_cylinders(unit));
			data[506] = info.heads; data[507] = info.sectors;
			put_u16be(data + 508, physical_cylinders(unit));
			data[510] = info.heads * info.sectors; data[511] = 0;
			for (unsigned h = 0; h < info.heads && !completion; ++h)
				if (!m_drives[unit]->write(h * 24, data)) { completion = 1; error = 0x80; }
		}
	}
	else if (command == 1 || command == 2 || command == 11 || command == 23)
	{
		auto const &info = m_drives[unit]->get_info();
		unsigned const physical = cylinder + (m_ce ? 0 : 1);
		u32 const capacity = (m_ce ? info.cylinders : logical_cylinders(unit)) * info.heads * info.sectors;
		u32 const requested = (cylinder * info.heads + head) * info.sectors + sector;
		if (cylinder >= (m_ce ? info.cylinders : logical_cylinders(unit)) || head >= info.heads || sector >= info.sectors
			|| (command < 3 && requested + (unsigned(count) + 255) / 256 > capacity)
			|| (command == 23 && BIT(m_regs[5], 7))) completion = 8;
		else if ((command == 2 || command == 23) && m_drives[unit]->is_readonly()) { completion = 1; error = 4; }
		else
		{
			u32 lba = (physical * info.heads + head) * info.sectors + sector;
			unsigned remaining = command == 11 ? 522 : command == 23 ? 512 : unsigned(count) * 2;
			if (command == 23) remaining = 0; // canonical sector header already present; bad-sector injection is unsupported
			if (command == 11)
			{
				bytes = { 0x19, u8((sector << 3) | head), u8(physical >> 8), u8(physical), 0, 0, 0, 0 };
				u8 data[512];
				if (!m_drives[unit]->read(lba, data)) { completion = 1; error = 0x80; }
				else bytes.insert(bytes.end(), std::begin(data), std::end(data));
				bytes.resize(522, 0);
				dma_write(address, bytes);
			}
			else
			{
				u32 dma = address;
				while (remaining)
				{
					u8 data[512];
					unsigned const transfer = std::min(remaining, 512U);
					if (lba >= info.cylinders * info.heads * info.sectors || !m_drives[unit]->read(lba, data)) { completion = 1; error = 0x80; break; }
					if (command == 2)
					{
						for (unsigned i = 0; i < transfer; ++i) data[i] = dma_read(dma + i);
						if (!m_drives[unit]->write(lba, data)) { completion = 1; error = 0x80; break; }
					}
					else dma_write(dma, std::vector<u8>(data, data + transfer));
					dma += transfer; remaining -= transfer; ++lba;
				}
			}
			m_cylinder[unit] = physical;
		}
	}
	else completion = 8;
	if (unit < 4 && command != 3)
	{
		m_detail[unit][0] = m_drives[unit]->exists() ? 0 : 1;
		m_detail[unit][1] = 0;
		m_detail[unit][2] = m_cylinder[unit] >> 8;
		m_detail[unit][3] = m_cylinder[unit];
		m_detail[unit][4] = error;
		m_detail[unit][5] = 0;
	}
	m_completion = (unit < 4 ? unit << 4 : 0) | completion;
	m_status = 0x98 | (error ? 4 : 0) | (completion == 8 ? 2 : 1);
	m_busy = false;
	if (BIT(m_regs[0], 5))
	{
		m_irq_pending = true;
		m_bus->vi_w(ASSERT_LINE);
	}
}

void zbi_s8k_wdc_device::device_start()
{
	m_timer = timer_alloc(FUNC(zbi_s8k_wdc_device::execute), this);
	save_item(NAME(m_regs)); save_item(NAME(m_status)); save_item(NAME(m_vector)); save_item(NAME(m_completion));
	save_item(NAME(m_busy)); save_item(NAME(m_irq_pending)); save_item(NAME(m_irq_service)); save_item(NAME(m_ce));
	save_item(NAME(m_cylinder)); save_item(NAME(m_detail));
}
void zbi_s8k_wdc_device::device_reset()
{
	m_timer->adjust(attotime::never);
	std::fill(std::begin(m_regs), std::end(m_regs), 0);
	m_status = 0x80; m_vector = m_completion = 0;
	m_busy = m_irq_pending = m_irq_service = m_ce = false;
	std::fill(std::begin(m_cylinder), std::end(m_cylinder), 0);
	for (auto &detail : m_detail) std::fill(std::begin(detail), std::end(detail), 0);
	m_bus->iospace()->install_readwrite_handler(0x8000, 0x8010,
		read8sm_delegate(*this, FUNC(zbi_s8k_wdc_device::host_r)), write8sm_delegate(*this, FUNC(zbi_s8k_wdc_device::host_w)));
}
void zbi_s8k_wdc_device::device_add_mconfig(machine_config &config)
{
	for (auto &drive : m_drives) { HARDDISK(config, drive, 0); drive->set_device_load(FUNC(zbi_s8k_wdc_device::image_load)); }
}
ROM_START(s8k_wdc)
	ROM_REGION(0x2000, "firmware", 0)
	ROM_LOAD("wdc_34-0611-01.u27", 0x0000, 0x1000, CRC(2d1c88f3) SHA1(6d1882a8445b7aff0400ace0767051d164f0dfb6))
	ROM_LOAD("wdc_34-0612-01.u28", 0x1000, 0x1000, CRC(0575cfd8) SHA1(fa6c5d6c8126645de6da12590aa02ad11152151c))
ROM_END

tiny_rom_entry const *zbi_s8k_wdc_device::device_rom_region() const { return ROM_NAME(s8k_wdc); }
}
DEFINE_DEVICE_TYPE_PRIVATE(ZBI_S8K_WDC, device_zbi_card_interface, zbi_s8k_wdc_device, "s8k_wdc", "System 8000 8-inch Winchester Disk Controller (preliminary)")
