#!/usr/bin/env python3
"""
ADV7280-M setup for ADV7280 uHat Shield / sdtv2csi hardware.

The Linux adv7180 kernel driver (active at boot) already handles:
  - Chip reset and ADI-recommended quality writes (adv7182_init)
  - CVBS input selection, lbias bias resistors, SH1 filter (adv7182_select_input)
  - MIPI CSI-2 output config and sub-map slave address assignment
  - Clamp reset

What the kernel gets wrong or leaves unconfigured, and what this script fixes:
  1. Video standard: kernel hardcodes curr_norm = V4L2_STD_NTSC at probe,
     writing 0x54 (forced NTSC-M) to reg 0x02. A PAL signal won't lock.
     Fix: set standard via VIDIOC_S_STD ioctl (updates kernel height too).
  2. I2P deinterlacing: kernel defaults to V4L2_FIELD_ALTERNATE (interlaced).
     Fix: write VPP/CSI sub-maps directly, mirroring adv7180_set_field_mode.
  3. AGC: not a V4L2 control in the adv7180 driver.
     Fix: write reg 0x2C directly.

Line count / framing note (https://github.com/rniwase/sdtv2csi/issues/1):
  The ADV7280-M with I2P enabled outputs additional blanking lines beyond the
  active picture. The patched adv7180 kernel module (install_patch.sh) tells
  the driver the correct total line counts: 507 for NTSC, 576 for PAL.
  GST_CAPS in recorder.py must match: height=576 for PAL, height=507 for NTSC.

  Setting STANDARD correctly here ensures the kernel reports the right height
  to capture tools (FFmpeg etc.). Without this, the kernel defaults to NTSC
  dimensions regardless of the actual signal.

Frame rate note:
  I2P outputs one frame per field (not per field-pair), doubling the capture rate
  vs. the source: PAL 50fps, NTSC ~60fps (59.94). The V4L2 driver still reports the
  field rate (25fps PAL, ~30fps NTSC) via timeperframe -- this is what GStreamer
  negotiates with the device. recorder.py uses capssetter to set the correct doubled
  rate downstream for the AVI header. GST_FRAMERATE controls this: 50/1 for PAL,
  60000/1001 for NTSC. Update it (and GST_CAPS height) when switching standards.

I2C addresses (7-bit):
  0x21 - main register map       (/dev/i2c-10)
  0x42 - VPP sub-map             (I2P deinterlacer)
  0x44 - CSI sub-map             (MIPI transmitter)
"""

import ctypes
import fcntl
import os
import struct
import time

# ---------------------------------------------------------------------------
# Configuration -- adjust for your source signal / preferences
# ---------------------------------------------------------------------------

# Input source.
# "CVBS_AIN1"    = composite on AIN1 (default; hardware wires camera to AIN1)
# "CVBS_AIN2"    = composite on AIN2
# "SVIDEO_AIN12" = S-Video (luma AIN1, chroma AIN2)
# "SVIDEO_AIN34" = S-Video (luma AIN3, chroma AIN4)
INPUT = "CVBS_AIN1"

# Signal standard. Sets decoder mode and kernel output height (PAL=576, NTSC=507).
# If changed, also update height and framerate in recorder.py (GST_CAPS).
# "PAL"  = PAL B/G (European cameras, most consumer camcorders outside USA/Japan)
# "NTSC" = NTSC-M  (USA/Japan cameras)
# "AUTO" = autodetect (chip decides; kernel height stays wrong at NTSC default)
STANDARD = "PAL"

# I2P deinterlacing: converts interlaced fields to progressive frames, doubling
# output rate (PAL: 25fps -> 50fps, NTSC: ~30fps -> ~60fps). Strongly recommended.
# If disabled, update framerate in recorder.py (GST_CAPS) to match.
# Interlaced output (V4L2_FIELD_ALTERNATE) will need deinterlacing in post.
ENABLE_I2P = True

# AGC (Automatic Gain Control): normalises gain across scenes.
# Too dark without it on composite sources. Disable if overexposed.
ENABLE_AGC = True

# Luma (contrast) gain, reg 0x08. Range 0x00-0xFF; 0x80 = 1.0x, 0xFF = 2.0x.
# If the image is too bright or too dark, adjust here.
CONTRAST_GAIN = 0x80

# Adaptive Contrast Enhancement. Tested: looks too bright on composite sources.
ENABLE_ACE = False

# --- Additional settings (uncomment here AND the matching call in main) -----

# NOISE_REDUCTION = 0x40        # reg 0x4D; tested: no noticeable difference
# ANTIALIAS       = 0x80        # reg 0xF3 (user sub-map); CAUTION: causes errors on this hardware
# ENABLE_FREE_RUN = True        # regs 0x0C, 0x14; diagnostic test pattern only
# ENABLE_BT656_4  = True        # reg 0x04; NTSC-only: 507 -> 487 lines when I2P disabled; no effect with I2P
# CHROMA_GAIN     = (0x00, 0x00)  # regs 0x2D, 0x2E; effect unclear from testing

# ---------------------------------------------------------------------------

MAIN    = 0x21
VPP     = 0x42   # I2P deinterlacer sub-map
CSI     = 0x44   # MIPI CSI-2 transmitter sub-map
I2C_BUS = 10
VIDEO_DEV = "/dev/video0"

# V4L2 standard IDs (from linux/videodev2.h)
_V4L2_STD_PAL_BG = 0x00000007   # PAL B/G (PAL-B | PAL-B1 | PAL-G)
_V4L2_STD_NTSC_M = 0x00001000   # NTSC-M

# VIDIOC_S_STD ioctl: _IOW('V', 24, v4l2_std_id)
# _IOW = direction(write=1)<<30 | size<<16 | type<<8 | nr
_VIDIOC_S_STD = (1 << 30) | (8 << 16) | (ord('V') << 8) | 24   # = 0x40085618


# ---- I2C via I2C_RDWR ioctl -----------------------------------------------
# smbus uses I2C_SLAVE ioctl, which fails (EBUSY) when a kernel driver owns the
# device. I2C_RDWR works regardless. ADV7280-M also requires a repeated-start
# for register reads (write reg addr, read byte, no STOP in between) -- this is
# only possible via I2C_RDWR, not via separate write+read transactions.

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
        msgs[0].addr = addr; msgs[0].flags = 0;        msgs[0].len = 1; msgs[0].buf = ctypes.cast(wb, ctypes.c_char_p)
        msgs[1].addr = addr; msgs[1].flags = I2C_M_RD; msgs[1].len = 1; msgs[1].buf = ctypes.cast(rb, ctypes.c_char_p)
        fcntl.ioctl(self._fd, I2C_RDWR, _Rdwr(msgs=msgs, nmsgs=2))
        return rb.raw[0]

    def close(self):
        self._fd.close()


# ---- Decode tables ---------------------------------------------------------

# STATUS1 bits[6:4]: autodetect result
_AUTOD = {
    0x00: "NTSC M/J      (60 Hz)",
    0x10: "NTSC 4.43     (60 Hz)",
    0x20: "PAL M         (60 Hz)",
    0x30: "PAL 60        (60 Hz)",
    0x40: "PAL B/G/D/I/H (50 Hz)",
    0x50: "SECAM         (50 Hz)",
    0x60: "PAL Comb N    (50 Hz)",
    0x70: "SECAM 525     (60 Hz)",
}

# reg 0x02 bits[7:4]: configured decoder standard
_VIDSEL = {
    0:  "autodetect (AD/PAL-B+NTSC-J+SECAM)",
    4:  "NTSC J   (forced)",
    5:  "NTSC M   (forced)",
    6:  "PAL 60   (forced)",
    7:  "NTSC 4.43 (forced)",
    8:  "PAL B/G  (forced)",
    9:  "PAL N    (forced)",
    10: "PAL M    (forced)",
}

_INPUT = {
    0x00: "CVBS AIN1",
    0x01: "CVBS AIN2",
    0x08: "S-Video AIN1/2",
    0x09: "S-Video AIN3/4",
}

_INPUT_REGS = {
    "CVBS_AIN1":    0x00,
    "CVBS_AIN2":    0x01,
    "SVIDEO_AIN12": 0x08,
    "SVIDEO_AIN34": 0x09,
}


# ---- Status display --------------------------------------------------------

def read_state(i2c):
    s1     = i2c.read(MAIN, 0x10)   # STATUS1: lock bits + autodetect result
    s3     = i2c.read(MAIN, 0x13)   # STATUS3: 50Hz flag
    inp    = i2c.read(MAIN, 0x00)   # INPUT_CONTROL: active input
    vidsel = i2c.read(MAIN, 0x02)   # INPUT_VIDSEL: decoder standard + reserved bit
    autoen = i2c.read(MAIN, 0x07)   # AUTODETECT_ENABLE: which standards to detect
    ident  = i2c.read(MAIN, 0x11)   # chip identity
    agc      = i2c.read(MAIN, 0x2C)   # manual gain control
    contrast = i2c.read(MAIN, 0x08)   # luma contrast gain
    vpp_55 = i2c.read(VPP,  0x55)   # I2P core enable  (0x80 = on)
    vpp_5b = i2c.read(VPP,  0x5B)   # I2P bypass       (0x80 = bypassed/off)
    vpp_a3 = i2c.read(VPP,  0xA3)   # I2P aux control  (0x00 = normal)
    csi_01 = i2c.read(CSI,  0x01)   # CSI timing[1]: 0x20 = progressive, 0x18 = interlaced

    # On ADV7280, STATUS1 has two lock bits:
    #   bit 1 = IN_LOCK  (sync lock -- primary signal lock indicator)
    #   bit 0 = FSC_LOCK (color subcarrier lock -- secondary, may lag by seconds)
    # The kernel uses ADV7180_STATUS1_IN_LOCK = 0x01 (bit 0 = FSC_LOCK on ADV7280),
    # which causes V4L2 to report "no signal" even when sync is locked.
    in_lock  = bool(s1 & 0x02)   # bit 1: sync lock
    fsc_lock = bool(s1 & 0x01)   # bit 0: color subcarrier lock
    col_kill = bool(s1 & 0x08)   # bit 3: color killer active (no color burst detected)

    autod   = s1 & 0x70           # bits[6:4]: autodetected standard
    hz50    = bool(s3 & 0x04)     # STATUS3 bit 2: 50Hz (PAL) vs 60Hz (NTSC)

    std_mode = vidsel >> 4
    res_bit  = bool(vidsel & 0x04)
    i2p_on   = bool(vpp_55 & 0x80) and not bool(vpp_5b & 0x80)

    lock_str = "LOCKED" if in_lock else "UNLOCKED"
    fsc_str  = ", color locked" if fsc_lock else ", color not yet locked"
    ck_str   = "  *** COL_KILL active (no color burst -- camera paused/monochrome?)" if col_kill else ""
    res_warn = "" if res_bit else "  *** reserved bit missing -- reboot to re-init"

    print(f"  Ident:        0x{ident:02X}")
    print(f"  Signal:       {lock_str}{fsc_str} | {'50Hz' if hz50 else '60Hz'} | {_AUTOD.get(autod, f'unknown 0x{autod:02X}')}{ck_str}")
    print(f"  Input:        0x{inp:02X}  ({_INPUT.get(inp, 'unknown')})")
    print(f"  Std (0x02):   0x{vidsel:02X}  {_VIDSEL.get(std_mode, f'mode {std_mode}')}{res_warn}")
    print(f"  Autoen (0x07):0x{autoen:02X}  (0x7F = all standards enabled)")
    print(f"  AGC (0x2C):   0x{agc:02X}  ({'enabled' if agc == 0xAE else 'disabled' if agc == 0x8C else 'custom'})")
    print(f"  Contrast(08): 0x{contrast:02X}  ({contrast/128:.2f}x)")
    print(f"  I2P:          {'ON' if i2p_on else 'OFF'}   "
          f"(VPP 0x55=0x{vpp_55:02X} 0x5B=0x{vpp_5b:02X} 0xA3=0x{vpp_a3:02X})")
    print(f"  CSI[0x01]:    0x{csi_01:02X}  "
          f"({'progressive' if csi_01 == 0x20 else 'interlaced' if csi_01 == 0x18 else 'unknown'})")


# ---- Settings --------------------------------------------------------------

def configure_standard(i2c, standard):
    """
    Set the video standard in both the chip and the kernel.

    The kernel hardcodes curr_norm = V4L2_STD_NTSC at probe, causing two problems:
      1. adv7182_set_std writes 0x54 (forced NTSC-M) to reg 0x02 -> PAL won't lock
      2. adv7180_mbus_fmt reports height 480/507 (NTSC) regardless of actual signal

    We fix this with VIDIOC_S_STD, which calls adv7180_s_std -> adv7180_program_std
    -> adv7182_set_std. This writes the correct decoder mode to reg 0x02 AND updates
    state->curr_norm so the kernel reports the correct frame height.

    For AUTO mode, we write reg 0x02 directly to enable autodetect. The kernel's
    curr_norm stays NTSC -- capture tools will need explicit dimensions in this case.

    reg 0x07 = 0x7F: enable all autodetect standards (chip power-on default;
    explicit here to guard against a previous session disabling it).
    """
    if standard == "AUTO":
        # Autodetect: chip determines standard from signal.
        # Kernel height stays wrong (NTSC default) -- specify dimensions explicitly
        # in capture tools: 720x507 for NTSC, 720x576 for PAL.
        i2c.write(MAIN, 0x02, 0x04)   # autodetect + reserved bit
        i2c.write(MAIN, 0x07, 0x7F)
        return

    v4l2_std = _V4L2_STD_PAL_BG if standard == "PAL" else _V4L2_STD_NTSC_M
    std_bytes = struct.pack("Q", v4l2_std)  # v4l2_std_id is u64, little-endian
    try:
        fd = os.open(VIDEO_DEV, os.O_RDWR | os.O_NONBLOCK)
        try:
            fcntl.ioctl(fd, _VIDIOC_S_STD, std_bytes)
        finally:
            os.close(fd)
    except OSError as e:
        print(f"  Warning: VIDIOC_S_STD failed ({e}) -- falling back to chip autodetect")
        i2c.write(MAIN, 0x02, 0x04)
    # Ensure all autodetect standard types are enabled (for STATUS1 display)
    i2c.write(MAIN, 0x07, 0x7F)


def enable_i2p(i2c):
    """
    Enable I2P deinterlacing for progressive output.

    Mirrors kernel adv7180_set_field_mode(V4L2_FIELD_NONE). That function is called
    at probe time with V4L2_FIELD_ALTERNATE (interlaced) as the default, so we
    override it here. The kernel won't re-apply its defaults unless the chip is reset.

    VPP sub-map writes:
      0x41 = 0x01: reset I2P core before reconfiguring
      0xA3 = 0x00: aux control (normal mode)
      0x5B = 0x00: no bypass -- deinterlacer is active in the signal path
      0x55 = 0x80: enable I2P core

    CSI sub-map writes: progressive MIPI timing values from kernel source
    (adv7180_set_field_mode, FIELD_NONE + MIPI_CSI2 path).
    """
    i2c.write(VPP, 0x41, 0x01)   # reset I2P core before reconfiguring
    time.sleep(0.05)
    i2c.write(CSI, 0x01, 0x20)
    i2c.write(CSI, 0x02, 0x28)
    i2c.write(CSI, 0x03, 0x38)
    i2c.write(CSI, 0x04, 0x30)
    i2c.write(CSI, 0x05, 0x30)
    i2c.write(CSI, 0x06, 0x80)
    i2c.write(CSI, 0x07, 0x70)
    i2c.write(CSI, 0x08, 0x50)
    i2c.write(VPP, 0xA3, 0x00)
    i2c.write(VPP, 0x5B, 0x00)
    i2c.write(VPP, 0x55, 0x80)


def disable_i2p(i2c):
    """
    Revert to interlaced output (kernel default).
    Mirrors adv7180_set_field_mode(V4L2_FIELD_ALTERNATE) + MIPI_CSI2 path.
    """
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


def set_contrast(i2c, gain):
    "Luma contrast gain (reg 0x08). 0x80 = 1.0x (chip default), 0xFF = 2.0x."
    i2c.write(MAIN, 0x08, gain)


def set_agc(i2c, enabled):
    """
    Automatic gain control (reg 0x2C). Not a V4L2 control in the adv7180 driver.
    0xAE = on, 0x8C = off (values from sdtv2csi Lua reference).
    """
    i2c.write(MAIN, 0x2C, 0xAE if enabled else 0x8C)


def set_input(i2c, inp):
    """
    Input source (reg 0x00). Kernel sets this at probe from device tree config;
    this overrides it. See _INPUT_REGS for valid values.
    """
    i2c.write(MAIN, 0x00, _INPUT_REGS[inp])


def set_ace(i2c, enabled):
    """
    Adaptive Contrast Enhancement (ACE sub-map reg 0x80).
    Accessed via user sub-map (reg 0x0E = 0x40); main map restored after.
    """
    i2c.write(MAIN, 0x0E, 0x40)
    i2c.write(MAIN, 0x80, 0x80 if enabled else 0x00)
    i2c.write(MAIN, 0x0E, 0x00)


def set_noise_reduction(i2c, val):
    "Noise reduction (reg 0x4D). 0x40 = on, 0x00 = off."
    i2c.write(MAIN, 0x4D, val)


def set_antialias(i2c, val):
    "Antialias filter (reg 0xF3, user sub-map). 0x80 = on, 0x00 = off."
    i2c.write(MAIN, 0x0E, 0x40)
    i2c.write(MAIN, 0xF3, val)
    i2c.write(MAIN, 0x0E, 0x00)


def set_free_run(i2c, enabled):
    "Free-run / color bar test pattern (regs 0x0C, 0x14)."
    i2c.write(MAIN, 0x0C, 0x80 if enabled else 0x00)
    i2c.write(MAIN, 0x14, 0x00)


def set_bt656_4(i2c, enabled):
    "BT.656-4 timing flag (reg 0x04). Enabled: 0xD7. Default: 0x57."
    i2c.write(MAIN, 0x04, 0xD7 if enabled else 0x57)


def set_chroma_gain(i2c, gain1, gain2):
    "Chroma gain (regs 0x2D, 0x2E)."
    i2c.write(MAIN, 0x2D, gain1)
    i2c.write(MAIN, 0x2E, gain2)


# ---- Main ------------------------------------------------------------------

if __name__ == "__main__":
    i2c = I2C(I2C_BUS)
    try:
        print("=== ADV7280-M state (before) ===")
        read_state(i2c)

        print("\n=== Applying settings ===")

        set_input(i2c, INPUT)
        print(f"  Input: {INPUT} (0x{_INPUT_REGS[INPUT]:02X})")

        configure_standard(i2c, STANDARD)
        print(f"  Standard: {STANDARD} (reg 0x02 via VIDIOC_S_STD, 0x07=0x7F)")

        if ENABLE_I2P:
            enable_i2p(i2c)
            print("  I2P: enabled (progressive output)")
        else:
            disable_i2p(i2c)
            print("  I2P: disabled (interlaced output)")

        set_contrast(i2c, CONTRAST_GAIN)
        print(f"  Contrast gain: 0x{CONTRAST_GAIN:02X} ({CONTRAST_GAIN/128:.2f}x)")

        set_agc(i2c, ENABLE_AGC)
        print(f"  AGC: {'enabled' if ENABLE_AGC else 'disabled'}")

        set_ace(i2c, ENABLE_ACE)
        print(f"  ACE: {'enabled' if ENABLE_ACE else 'disabled'}")

        # set_noise_reduction(i2c, NOISE_REDUCTION)
        # set_antialias(i2c, ANTIALIAS)
        # set_free_run(i2c, ENABLE_FREE_RUN)
        # set_bt656_4(i2c, ENABLE_BT656_4)
        # set_chroma_gain(i2c, *CHROMA_GAIN)

        # Allow chip time to lock after standard change
        time.sleep(0.5)

        print("\n=== ADV7280-M state (after) ===")
        read_state(i2c)
    finally:
        i2c.close()
