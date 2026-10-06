// license:BSD-3-Clause
// copyright-holders:smf,Carl
#ifndef MAME_BUS_RS232_NULL_MODEM_H
#define MAME_BUS_RS232_NULL_MODEM_H

#pragma once

#include "rs232.h"
#include "imagedev/bitbngr.h"
#include "diserial.h"

class generic_terminal_device;

class null_modem_device : public device_t,
	public device_serial_interface,
	public device_rs232_port_interface
{
public:
	null_modem_device(const machine_config &mconfig, const char *tag, device_t *owner, uint32_t clock);

	virtual void input_txd(int state) override { device_serial_interface::rx_w(state); }
	virtual void input_rts(int state) override { m_rts = state; }
	virtual void input_dtr(int state) override { m_dtr = state; }

	void update_serial(int state);

protected:
	null_modem_device(const machine_config &mconfig, device_type type, const char *tag, device_t *owner, uint32_t clock);

	virtual ioport_constructor device_input_ports() const override ATTR_COLD;
	virtual void device_start() override ATTR_COLD;
	virtual void device_reset() override ATTR_COLD;
	virtual void device_add_mconfig(machine_config &config) override ATTR_COLD;

	virtual void tra_callback() override;
	virtual void tra_complete() override;
	virtual void rcv_complete() override;
	virtual void received_byte(uint8_t data);

private:
	TIMER_CALLBACK_MEMBER(update_queue);
	void update_input_buffer();

	required_device<bitbanger_device> m_stream;

	required_ioport m_rs232_txbaud;
	required_ioport m_rs232_rxbaud;
	required_ioport m_rs232_databits;
	required_ioport m_rs232_parity;
	required_ioport m_rs232_stopbits;
	required_ioport m_flow;

	uint8_t m_input_buffer[1000];
	uint32_t m_input_count;
	uint32_t m_input_index;
	emu_timer *m_timer_poll;
	int m_rts;
	int m_dtr;
	int m_xoff;
	bool m_cr;
};

DECLARE_DEVICE_TYPE(NULL_MODEM, null_modem_device)

class null_modem_terminal_device : public null_modem_device
{
public:
	null_modem_terminal_device(const machine_config &mconfig, const char *tag, device_t *owner, uint32_t clock);

protected:
	virtual void device_add_mconfig(machine_config &config) override ATTR_COLD;
	virtual void received_byte(uint8_t data) override;

private:
	required_device<generic_terminal_device> m_terminal;
};

DECLARE_DEVICE_TYPE(NULL_MODEM_TERMINAL, null_modem_terminal_device)

#endif // MAME_BUS_RS232_NULL_MODEM_H
