#!/usr/bin/env python3
"""
ADV7280-M setup for DVX-Nebula / sdtv2csi hardware.

The Linux adv7180 kernel driver (active at boot) already handles:
  - Chip reset and core init (adv7182_init)
  - CVBS input selection and lbias bias resistors (adv7182_select_input)
  - MIPI CSI-2 output and sub-map slave address config
  - Signal autodetect (PAL/NTSC)
  - Clamp reset

Kernel does NOT handle:
  - I2P deinterlacing: kernel defaults to V4L2_FIELD_ALTERNATE (interlaced).
    We mirror what adv7180_set_field_mode(FIELD_NONE) would do.
  - AGC: not exposed as a V4L2 control in the adv7180 driver.

I2C addresses (7-bit):
  0x21 - main register map        (/dev/i2c-10)
  0x42 - VPP sub-map (I2P deinterlacer)
  0x44 - CSI sub-map (MIPI transmitter)
"""

import ctypes
import fcntl
import time

# ---------------------------------------------------------------------------
# Camera-specific configuration — edit for your source signal
# ---------------------------------------------------------------------------
ENABLE_I2P = True   # deinterlace to progressive output (recommended for capture)
ENABLE_AGC = True   # automatic gain control (helps with indoor/low-light sources)
# ---------------------------------------------------------------------------

# ADV7280-M I2C addresses (7-bit)
MAIN = 0x21
VPP  = 0x42   # I2P deinterlacer sub-map
CSI  = 0x44   # MIPI CSI-2 transmitter sub-map

I2C_BUS = 10  # /dev/i2c-10


# ---- I2C via I2C_RDWR ioctl -----------------------------------------------
# Standard smbus uses I2C_SLAVE ioctl, which fails (EBUSY) when a kernel driver
# owns the device. I2C_RDWR works regardless and supports the repeated-start
# read sequence the ADV7280-M requires (write reg addr, read byte, no stop).

I2C_RDWR = 0x0707
I2C_M_RD = 0x0001

class _Msg(ctypes.Structure):
    _fields_ = [
        ("addr",  ctypes.c_uint16),
        ("flags", ctypes.c_uint16),
        ("len",   ctypes.c_uint16),
        ("buf",   ctypes.c_char_p),
    ]

class _Rdwr(ctypes.Structure):
    _fields_ = [
        ("msgs",  ctypes.POINTER(_Msg)),
        ("nmsgs", ctypes.c_uint32),
    ]

class I2C:
    def __init__(self, bus):
        self._fd = open(f"/dev/i2c-{bus}", "rb+", buffering=0)

    def write(self, addr, reg, val):
        buf  = ctypes.create_string_buffer(bytes([reg, val]))
        msgs = (_Msg * 1)()
        msgs[0].addr = addr; msgs[0].flags = 0; msgs[0].len = 2
        msgs[0].buf  = ctypes.cast(buf, ctypes.c_char_p)
        fcntl.ioctl(self._fd, I2C_RDWR, _Rdwr(msgs=msgs, nmsgs=1))

    def read(self, addr, reg):
        wb = ctypes.create_string_buffer(bytes([reg]))
        rb = ctypes.create_string_buffer(1)
        msgs = (_Msg * 2)()
        msgs[0].addr = addr; msgs[0].flags = 0;       msgs[0].len = 1; msgs[0].buf = ctypes.cast(wb, ctypes.c_char_p)
        msgs[1].addr = addr; msgs[1].flags = I2C_M_RD; msgs[1].len = 1; msgs[1].buf = ctypes.cast(rb, ctypes.c_char_p)
        fcntl.ioctl(self._fd, I2C_RDWR, _Rdwr(msgs=msgs, nmsgs=2))
        return rb.raw[0]

    def close(self):
        self._fd.close()


# ---- Status decoding -------------------------------------------------------

_AUTOD = {
    0x00: "NTSC M/J",
    0x10: "NTSC 4.43",
    0x20: "PAL M",
    0x30: "PAL 60",
    0x40: "PAL B/G/D/I/H",
    0x50: "SECAM",
    0x60: "PAL Comb N",
    0x70: "SECAM 525",
}

_INPUT = {
    0x00: "CVBS AIN1",
    0x01: "CVBS AIN2",
    0x02: "CVBS AIN3",
    0x03: "CVBS AIN4",
    0x08: "S-Video AIN1/2",
    0x09: "S-Video AIN3/4",
}

def read_state(i2c):
    # Status registers (read-only, reflect detected signal)
    s1 = i2c.read(MAIN, 0x10)   # STATUS 1: lock + autodetect result
    s3 = i2c.read(MAIN, 0x13)   # STATUS 3: interlaced flag, field length, freq

    # Config registers
    inp    = i2c.read(MAIN, 0x00)   # INPUT_CONTROL: active input
    vidsel = i2c.read(MAIN, 0x02)   # INPUT_VIDSEL: video standard (bits[7:4]) + reserved bit[2]
    pwr    = i2c.read(MAIN, 0x0F)   # PWR_MAN
    ident  = i2c.read(MAIN, 0x11)   # chip identity (0x1C for ADV7280)
    agc    = i2c.read(MAIN, 0x2C)   # manual gain control 2

    # I2P / deinterlace state (VPP sub-map)
    vpp_55 = i2c.read(VPP, 0x55)   # I2P core enable  (0x80 = on)
    vpp_5b = i2c.read(VPP, 0x5B)   # I2P bypass       (0x80 = bypassed/off)
    vpp_a3 = i2c.read(VPP, 0xA3)   # I2P aux control  (0x00 = normal)

    # CSI timing register [1] distinguishes progressive vs interlaced config
    csi_01 = i2c.read(CSI, 0x01)   # 0x20 = progressive (I2P on), 0x18 = interlaced

    locked   = bool(s1 & 0x01)
    autod    = s1 & 0x70
    hz50     = bool(s3 & 0x04)     # 1 = 50 Hz (PAL), 0 = 60 Hz (NTSC)
    # Note: s3 bit[6] (INTERLACED) reflects the input signal, not the output.
    # It will read 1 even when I2P is converting to progressive output.
    i2p_on   = bool(vpp_55 & 0x80) and not bool(vpp_5b & 0x80)

    reserved_bit_ok = bool(vidsel & 0x04)

    print(f"  Ident:      0x{ident:02X}")
    print(f"  Signal:     {'LOCKED' if locked else 'UNLOCKED'} | "
          f"{_AUTOD.get(autod, f'unknown 0x{autod:02X}')} | "
          f"{'50 Hz (PAL)' if hz50 else '60 Hz (NTSC)'}")
    print(f"  Input:      0x{inp:02X}  ({_INPUT.get(inp, 'unknown')})")
    std_bits = vidsel >> 4
    std_note = "autodetect" if std_bits == 0 else f"forced mode {std_bits}"
    reserved_note = "" if reserved_bit_ok else "  *** reserved bit 0x04 missing — increased noise risk; reboot to let kernel re-init"
    print(f"  Std reg:    0x{vidsel:02X}  ({std_note}){reserved_note}")
    print(f"  Power:      0x{pwr:02X}")
    print(f"  AGC:        0x{agc:02X}  ({'enabled' if agc == 0xAE else 'disabled' if agc == 0x8C else 'custom'})")
    print(f"  I2P:        {'ON' if i2p_on else 'OFF'}   "
          f"(VPP 0x55=0x{vpp_55:02X} 0x5B=0x{vpp_5b:02X} 0xA3=0x{vpp_a3:02X})")
    print(f"  CSI[0x01]:  0x{csi_01:02X}  ({'progressive' if csi_01 == 0x20 else 'interlaced' if csi_01 == 0x18 else 'unknown'})")


# ---- Settings --------------------------------------------------------------

def enable_i2p(i2c):
    """
    Enable I2P deinterlacing for progressive output.
    Mirrors what the kernel's adv7180_set_field_mode(V4L2_FIELD_NONE) does.
    """
    # CSI MIPI timing for progressive (from adv7180_set_field_mode, FIELD_NONE path)
    i2c.write(CSI, 0x01, 0x20)
    i2c.write(CSI, 0x02, 0x28)
    i2c.write(CSI, 0x03, 0x38)
    i2c.write(CSI, 0x04, 0x30)
    i2c.write(CSI, 0x05, 0x30)
    i2c.write(CSI, 0x06, 0x80)
    i2c.write(CSI, 0x07, 0x70)
    i2c.write(CSI, 0x08, 0x50)
    # VPP I2P enable (from same kernel path)
    i2c.write(VPP, 0xA3, 0x00)
    i2c.write(VPP, 0x5B, 0x00)
    i2c.write(VPP, 0x55, 0x80)


def disable_i2p(i2c):
    """Revert to interlaced output (kernel default)."""
    i2c.write(CSI, 0x01, 0x18)
    i2c.write(CSI, 0x02, 0x18)
    i2c.write(CSI, 0x03, 0x30)
    i2c.write(CSI, 0x04, 0x20)
    i2c.write(CSI, 0x05, 0x28)
    i2c.write(CSI, 0x06, 0x40)
    i2c.write(CSI, 0x07, 0x58)
    i2c.write(CSI, 0x08, 0x30)
    i2c.write(VPP, 0xA3, 0x70)
    i2c.write(VPP, 0x5B, 0x80)
    i2c.write(VPP, 0x55, 0x00)


def set_agc(i2c, enabled):
    """
    Manual gain control (reg 0x2C). Not a standard V4L2 control.
    Useful for adjusting exposure level without post-processing.
    """
    i2c.write(MAIN, 0x2C, 0xAE if enabled else 0x8C)


# ---- Main ------------------------------------------------------------------

if __name__ == "__main__":
    i2c = I2C(I2C_BUS)

    print("=== ADV7280-M state (before) ===")
    read_state(i2c)

    print("\n=== Applying settings ===")

    if ENABLE_I2P:
        enable_i2p(i2c)
        print("  I2P deinterlacing: enabled (progressive output)")
    else:
        disable_i2p(i2c)
        print("  I2P deinterlacing: disabled (interlaced output)")

    set_agc(i2c, ENABLE_AGC)
    print(f"  AGC: {'enabled' if ENABLE_AGC else 'disabled'}")

    print("\n=== ADV7280-M state (after) ===")
    read_state(i2c)

    i2c.close()
