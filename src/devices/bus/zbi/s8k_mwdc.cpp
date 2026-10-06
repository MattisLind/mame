// license:BSD-3-Clause
// copyright-holders:Mattis Lind
/*
 * Zilog System 8000 Mini-Winchester Disk Controller II.
 *
 * Preliminary firmware-driven emulation of board 09-0241 revision B.
 * U162/U163 contain Z8001 firmware, not an SMDC-compatible command processor.
 * The Z8036 register map and the host mailbox protocol are recovered from
 * that firmware and SADIE 3.5's MDCMON/MDCTEST.  As with SMDC, disks use
 * harddisk_image_device sector images.  A sector adapter supplies headers
 * and data at the firmware's word I/O interface; magnetic encoding and
 * the WD1100/PROM sequencer are abstracted rather than simulated.
 */

#include "emu.h"
#include "s8k_mwdc.h"

#include "cpu/z8000/z8000.h"
#include "imagedev/harddriv.h"
#include "machine/z8536.h"

#define LOG_HOST (1U << 1)
#define LOG_DMA  (1U << 2)
#define LOG_DISK (1U << 3)
#include "logmacro.h"

namespace {

class zbi_s8k_mwdcii_device : public device_t, public device_zbi_card_interface
{
public:
	zbi_s8k_mwdcii_device(machine_config const &mconfig, char const *tag, device_t *owner, u32 clock)
		: device_t(mconfig, ZBI_S8K_MWDCII, tag, owner, clock)
		, device_zbi_card_interface(mconfig, *this)
		, m_cpu(*this, "cpu")
		, m_cio(*this, "cio")
		, m_drives(*this, "mwd%u", 0U)
	{
	}

protected:
	virtual void device_start() override ATTR_COLD;
	virtual void device_reset() override ATTR_COLD;
	virtual void device_resolve_objects() override ATTR_COLD;
	virtual void device_add_mconfig(machine_config &config) override ATTR_COLD;
	virtual tiny_rom_entry const *device_rom_region() const override ATTR_COLD;
	virtual int z80daisy_irq_state() override;
	virtual int z80daisy_irq_ack() override;
	virtual void z80daisy_irq_reti() override;

private:
	void program_map(address_map &map) ATTR_COLD;
	void data_map(address_map &map) ATTR_COLD;
	void io_map(address_map &map) ATTR_COLD;
	u16 host_memory_r(offs_t offset, u16 mem_mask = ~0);
	void host_memory_w(offs_t offset, u16 data, u16 mem_mask = ~0);
	void host_command_w(u16 data);
	void host_irq_w(offs_t offset, u16 data);
	TIMER_CALLBACK_MEMBER(mailbox_w);
	u16 mailbox_r();
	void control_w(u16 data);
	void drive_control_w(u16 data);
	u16 disk_r();
	void disk_w(u16 data);
	u16 drive_types_r();
	std::error_condition image_load(device_image_interface &image);
	bool sector_address(u32 &lba) const;
	unsigned unit() const { return (m_drive_control >> 11) & 3; }
	// All supported drive types have at most eight heads.  During writes
	// bit 13 enables precompensation beyond the firmware's cylinder threshold.
	unsigned head() const { return (m_drive_control >> 8) & 7; }
	u8 drive_status_r();
	u8 board_status_r();

	required_device<z8001_device> m_cpu;
	required_device<z8036_device> m_cio;
	required_device_array<harddisk_image_device, 4> m_drives;
	u16 m_drive_control = 0;
	u16 m_cylinder[4] = {};
	u8 m_next_sector[4] = {};
	u8 m_sector = 0;
	u16 m_stream_pos = 0;
	u8 m_sector_buffer[512] = {};
	bool m_disk_error = false;
	bool m_write_fault = false;
	u16 m_mailbox = 0;
	u16 m_control = 0;
	u16 m_vector = 0;
	bool m_mailbox_full = false;
	bool m_irq_pending = false;
	bool m_irq_service = false;
};

void zbi_s8k_mwdcii_device::program_map(address_map &map)
{
	map(0x000000, 0x001fff).rom().region("firmware", 0);
	map(0x004000, 0x007fff).ram().share("ram");
}

void zbi_s8k_mwdcii_device::data_map(address_map &map)
{
	program_map(map);
	// Firmware forms external pointers as <40 + physical segment>:offset.
	map(0x400000, 0x7fffff).rw(FUNC(zbi_s8k_mwdcii_device::host_memory_r), FUNC(zbi_s8k_mwdcii_device::host_memory_w));
}

void zbi_s8k_mwdcii_device::io_map(address_map &map)
{
	map(0x0070, 0x0071).w(FUNC(zbi_s8k_mwdcii_device::drive_control_w));
	map(0x0072, 0x0073).w(FUNC(zbi_s8k_mwdcii_device::control_w));
	map(0x0074, 0x0075).lw16(NAME([this](u16 data) { m_vector = data; }));
	map(0x0076, 0x0077).r(FUNC(zbi_s8k_mwdcii_device::mailbox_r));
	map(0x0078, 0x0079).lr16(NAME([]() { return 0; })); // no correctable ECC syndrome
	map(0x007a, 0x007b).r(FUNC(zbi_s8k_mwdcii_device::disk_r));
	map(0x007c, 0x007d).r(FUNC(zbi_s8k_mwdcii_device::drive_types_r));
	map(0x007e, 0x007f).w(FUNC(zbi_s8k_mwdcii_device::disk_w));
	// Word I/O to an odd port swaps the lanes in the Z8000 core.  The
	// firmware's low register byte consequently appears on D15..D8 here.
	map(0x0080, 0x00df).rw(m_cio, FUNC(z8036_device::read), FUNC(z8036_device::write)).umask16(0xff00);
}

u16 zbi_s8k_mwdcii_device::host_memory_r(offs_t offset, u16 mem_mask)
{
	return m_bus->ram16_r(offset << 1, mem_mask);
}

void zbi_s8k_mwdcii_device::host_memory_w(offs_t offset, u16 data, u16 mem_mask)
{
	LOGMASKED(LOG_DMA, "MWDCII DMA %06x <- %04x (%04x)\n", offset << 1, data, mem_mask);
	m_bus->ram16_w(offset << 1, data, mem_mask);
}

void zbi_s8k_mwdcii_device::host_command_w(u16 data)
{
	// Synchronize the two CPUs before making the single-word latch visible.
	machine().scheduler().synchronize(timer_expired_delegate(FUNC(zbi_s8k_mwdcii_device::mailbox_w), this), data);
}

TIMER_CALLBACK_MEMBER(zbi_s8k_mwdcii_device::mailbox_w)
{
	LOGMASKED(LOG_HOST, "MWDCII mailbox <- %04x%s\n", param, m_mailbox_full ? " (overrun)" : "");
	m_mailbox = param;
	m_mailbox_full = true;
}

u16 zbi_s8k_mwdcii_device::mailbox_r()
{
	if (!machine().side_effects_disabled())
		m_mailbox_full = false;
	return m_mailbox;
}

void zbi_s8k_mwdcii_device::host_irq_w(offs_t offset, u16 data)
{
	// ZEUS 3.21 writes 80f2 at the end of every MD interrupt handler.
	// It acknowledges completion; it must not disable subsequent requests.
	// Firmware 2.03 selects interrupt delivery in the command packet itself.
	// The next packet may complete while the previous handler is still
	// in service. Acknowledge consumed only the previous request; its
	// later host reset must preserve this independently pending one.
	if (!m_irq_service) m_irq_pending = false;
	m_irq_service = false;
	m_bus->vi_w(CLEAR_LINE);
}

void zbi_s8k_mwdcii_device::control_w(u16 data)
{
	if (BIT(data, 1) && !BIT(m_control, 1))
	{
		m_irq_pending = true;
		m_bus->vi_w(ASSERT_LINE);
	}
	m_control = data;
}

std::error_condition zbi_s8k_mwdcii_device::image_load(device_image_interface &image)
{
	auto const &info = downcast<harddisk_image_device &>(image).get_info();
	if ((info.sectorbytes != 512) || (info.sectors != 17)
		|| !(((info.cylinders == 216) && (info.heads == 8))
			|| ((info.cylinders == 306) && (info.heads == 6))
			|| ((info.cylinders == 645) && (info.heads == 5))
			|| ((info.cylinders == 830) && (info.heads == 6))))
	{
		logerror("MWDCII requires a 512-byte, 17-sector image with a supported drive geometry (216/8, 306/6, 645/5 or 830/6)\n");
		return image_error::INVALIDIMAGE;
	}
	return std::error_condition();
}

u16 zbi_s8k_mwdcii_device::drive_types_r()
{
	u16 result = 0;
	for (unsigned drive = 0; drive != 4; ++drive)
	{
		if (!m_drives[drive]->exists())
			continue;
		auto const &info = m_drives[drive]->get_info();
		unsigned const type = info.cylinders == 306 ? 1 : info.cylinders == 645 ? 2 : info.cylinders == 830 ? 3 : 0;
		result |= type << (drive * 3);
	}
	return result;
}

bool zbi_s8k_mwdcii_device::sector_address(u32 &lba) const
{
	auto const &drive = *m_drives[unit()];
	if (!drive.exists())
		return false;
	auto const &info = drive.get_info();
	if ((m_cylinder[unit()] >= info.cylinders) || (head() >= info.heads) || (m_sector >= info.sectors))
		return false;
	lba = (m_cylinder[unit()] * info.heads + head()) * info.sectors + m_sector;
	return true;
}

void zbi_s8k_mwdcii_device::drive_control_w(u16 data)
{
	u16 const previous = m_drive_control;
	m_drive_control = data;
	if (BIT(data, 15) && !BIT(previous, 15) && m_drives[unit()]->exists())
	{
		if (BIT(data, 14))
			m_cylinder[unit()] = std::min<u16>(m_cylinder[unit()] + 1, m_drives[unit()]->get_info().cylinders - 1);
		else if (m_cylinder[unit()])
			--m_cylinder[unit()];
		m_next_sector[unit()] = 0;
	}
	if (BIT(data, 3) && !BIT(previous, 3))
	{
		m_stream_pos = 0;
		m_disk_error = false;
		m_write_fault = false;
	}
	if (BIT(data, 7) && !BIT(previous, 7))
	{
		m_stream_pos = 0;
		m_disk_error = false;
		if (!BIT(data, 6))
		{
			// A header search sees consecutive sector IDs.  The following data
			// gate operates on the last header, not on the next sector.
			m_sector = m_next_sector[unit()];
			m_next_sector[unit()] = (m_sector + 1) % 17;
		}
		else if (!BIT(data, 5))
		{
			u32 lba;
			m_disk_error = !sector_address(lba) || !m_drives[unit()]->read(lba, m_sector_buffer);
			LOGMASKED(LOG_DISK, "MWDCII read unit %u CHS %u/%u/%u%s\n", unit(), m_cylinder[unit()], head(), m_sector, m_disk_error ? " failed" : "");
		}
	}
}

u16 zbi_s8k_mwdcii_device::disk_r()
{
	u16 result = 0xffff;
	if (BIT(m_drive_control, 7))
	{
		if (!BIT(m_drive_control, 6))
			result = (m_stream_pos == 0) ? m_cylinder[unit()] : (head() << 8) | m_sector;
		else if (!m_disk_error && (m_stream_pos < 512))
			result = (u16(m_sector_buffer[m_stream_pos]) << 8) | m_sector_buffer[m_stream_pos + 1];
		if (!machine().side_effects_disabled())
			m_stream_pos += 2;
	}
	return result;
}

void zbi_s8k_mwdcii_device::disk_w(u16 data)
{
	if ((m_drive_control & 0x00e0) != 0x00e0)
		return;
	// Track-format writes supply ID/gap patterns (bit 4).  Sector images
	// already have canonical IDs, so these do not modify the sector payload.
	if (BIT(m_drive_control, 4))
		return;
	// Firmware writes 11 words of gap/sync/address mark, then 256 data words.
	// The two ID words were consumed separately by its preceding header read.
	if ((m_stream_pos >= 22) && (m_stream_pos < 534))
	{
		m_sector_buffer[m_stream_pos - 22] = data >> 8;
		m_sector_buffer[m_stream_pos - 21] = data;
	}
	if (m_stream_pos == 532)
	{
		u32 lba;
		m_write_fault = !sector_address(lba) || m_drives[unit()]->is_readonly() || !m_drives[unit()]->write(lba, m_sector_buffer);
		// CIO transition-pattern interrupts are not implemented yet.  Raise
		// the port-A pending flag via its command register so the firmware's
		// real write-fault ISR still supplies the host error status.
		if (m_write_fault)
			m_cio->write(8, 0x80); // port A: set IP, preserve IE
		LOGMASKED(LOG_DISK, "MWDCII write unit %u CHS %u/%u/%u%s\n", unit(), m_cylinder[unit()], head(), m_sector, m_write_fault ? " failed" : "");
	}
	m_stream_pos += 2;
}

u8 zbi_s8k_mwdcii_device::drive_status_r()
{
	// PA0/1: drive 0/1 selected; PA2: ready; PA3: seek complete;
	// PA5: track zero; PA7: host interrupt acknowledged.
	u8 result = m_irq_pending ? 0x00 : 0x80;
	if (m_write_fault)
		result |= 0x10;
	if (m_drives[unit()]->exists())
	{
		result |= 0x0c; // ready and seek complete (image-level seeks finish immediately)
		if (unit() < 2)
			result |= 1 << unit();
		if (!m_cylinder[unit()])
			result |= 0x20;
	}
	return result;
}

u8 zbi_s8k_mwdcii_device::board_status_r()
{
	// PB1: host mailbox full; PB0/3: drive 2/3 selected.
	u8 result = m_mailbox_full ? 0x02 : 0x00;
	if (m_drives[unit()]->exists() && (unit() >= 2))
		result |= unit() == 2 ? 0x01 : 0x08;
	if (m_disk_error)
		result |= 0x40;
	return result;
}

int zbi_s8k_mwdcii_device::z80daisy_irq_state()
{
	return m_irq_service ? Z80_DAISY_IEO : (m_irq_pending ? Z80_DAISY_INT : 0);
}

int zbi_s8k_mwdcii_device::z80daisy_irq_ack()
{
	m_irq_pending = false;
	m_irq_service = true;
	return m_vector;
}

void zbi_s8k_mwdcii_device::z80daisy_irq_reti()
{
	m_irq_service = false;
	m_bus->vi_w(CLEAR_LINE);
}

void zbi_s8k_mwdcii_device::device_resolve_objects()
{
	m_bus->add_to_daisy_chain(tag());
}

void zbi_s8k_mwdcii_device::device_start()
{
	save_item(NAME(m_mailbox));
	save_item(NAME(m_mailbox_full));
	save_item(NAME(m_control));
	save_item(NAME(m_vector));
	save_item(NAME(m_irq_pending));
	save_item(NAME(m_irq_service));
	save_item(NAME(m_drive_control));
	save_item(NAME(m_cylinder));
	save_item(NAME(m_next_sector));
	save_item(NAME(m_sector));
	save_item(NAME(m_stream_pos));
	save_item(NAME(m_sector_buffer));
	save_item(NAME(m_disk_error));
	save_item(NAME(m_write_fault));
}

void zbi_s8k_mwdcii_device::device_reset()
{
	m_mailbox = m_control = m_vector = 0;
	m_mailbox_full = m_irq_pending = m_irq_service = false;
	m_drive_control = m_sector = m_stream_pos = 0;
	std::fill(std::begin(m_cylinder), std::end(m_cylinder), 0);
	std::fill(std::begin(m_next_sector), std::end(m_next_sector), 0);
	m_disk_error = m_write_fault = false;
	m_bus->iospace()->install_write_handler(0x80f0, 0x80f1, write16smo_delegate(*this, FUNC(zbi_s8k_mwdcii_device::host_command_w)));
	m_bus->iospace()->install_write_handler(0x80f2, 0x80f5, write16sm_delegate(*this, FUNC(zbi_s8k_mwdcii_device::host_irq_w)));
}

void zbi_s8k_mwdcii_device::device_add_mconfig(machine_config &config)
{
	// Board oscillator confirmed as 20 MHz.  Divider ratios remain provisional.
	Z8001(config, m_cpu, 20_MHz_XTAL / 4);
	m_cpu->set_addrmap(AS_PROGRAM, &zbi_s8k_mwdcii_device::program_map);
	m_cpu->set_addrmap(AS_DATA, &zbi_s8k_mwdcii_device::data_map);
	// intack_r returns int, which devcb otherwise treats as a one-bit line.
	// Preserve the complete vector (2/4 select the firmware's port A/B ISRs).
	m_cpu->viack().set([this]() -> u16 { return m_cio->intack_r(); });
	m_cpu->set_addrmap(AS_IO, &zbi_s8k_mwdcii_device::io_map);

	Z8036(config, m_cio, 20_MHz_XTAL / 8);
	m_cio->irq_wr_cb().set_inputline(m_cpu, z8001_device::VI_LINE);
	m_cio->pa_rd_cb().set(FUNC(zbi_s8k_mwdcii_device::drive_status_r));
	m_cio->pb_rd_cb().set(FUNC(zbi_s8k_mwdcii_device::board_status_r));

	for (auto &drive : m_drives)
	{
		HARDDISK(config, drive, 0);
		drive->set_device_load(FUNC(zbi_s8k_mwdcii_device::image_load));
	}
}

ROM_START(s8k_mwdcii)
	ROM_REGION16_BE(0x2000, "firmware", 0)
	ROM_LOAD16_BYTE("mwdc_34-0727-00a.u163", 0x0000, 0x1000, CRC(ca6d28a9) SHA1(dcef61e67815c527cecabe7b06344137a80118e5))
	ROM_LOAD16_BYTE("mwdc_34-0726-00a.u162", 0x0001, 0x1000, CRC(ce73c522) SHA1(89cb7e78c600dc75609d8eaf1d794a076cdb375d))
	ROM_REGION(0x0800, "proms", 0)
	ROM_LOAD("mwdc_34-00112-00a.u157", 0x0000, 0x0400, CRC(5924674a) SHA1(075063d175bbee92e212e535a44999e542ba1d26))
	ROM_LOAD("mwdc_34-00113-00a.u158", 0x0400, 0x0400, CRC(74990714) SHA1(04f3509b17b93e923c1d2e88d383c43cc729c53b))
ROM_END

tiny_rom_entry const *zbi_s8k_mwdcii_device::device_rom_region() const
{
	return ROM_NAME(s8k_mwdcii);
}

} // anonymous namespace

DEFINE_DEVICE_TYPE_PRIVATE(ZBI_S8K_MWDCII, device_zbi_card_interface, zbi_s8k_mwdcii_device, "s8k_mwdcii", "System 8000 Mini-Winchester Disk Controller II (preliminary)")
