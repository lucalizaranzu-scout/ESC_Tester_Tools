"""Minimal IQUART (Vertiq serial protocol) client.

Packet:  0x55 | len | type_idn | param_idn | (obj<<2 | access) | value... | crcL | crcH
  len  = number of bytes after type_idn (param + obj/access + value)
  crc  = CRC-16 (CCITT variant used by Vertiq) over len..value, little-endian

Mirrors the format used by Vertiq's own `iqmotion` Python library.
"""

import struct
import time

import serial

GET, SET, SAVE, REPLY = 0, 1, 2, 3
START = 0x55

FORMATS = {"B": "<B", "b": "<b", "H": "<H", "h": "<h", "I": "<I", "i": "<i", "f": "<f"}


class Param:
    def __init__(self, client, name, type_idn, param_idn, fmt, unit=""):
        self.client = client
        self.name = name
        self.type_idn = type_idn
        self.param_idn = param_idn
        self.fmt = fmt
        self.unit = unit

    @property
    def key(self):
        return f"{self.client}.{self.name}"

    def encode(self, value):
        if self.fmt in ("f",):
            value = float(value)
        else:
            value = int(value)
        return struct.pack(FORMATS[self.fmt], value)

    def decode(self, data):
        size = struct.calcsize(FORMATS[self.fmt])
        return struct.unpack(FORMATS[self.fmt], bytes(data[:size]))[0]


def _p(client, type_idn, rows):
    return [Param(client, name, type_idn, pid, fmt, unit) for name, pid, fmt, unit in rows]


# Parameters that make up a "PWM configuration". Taken from Vertiq's client
# definition files (iqmotion/clients/client_files/*.json). Read-only / action
# entries (e.g. motor_armed, reset_calibration) are intentionally left out.
MANAGED_PARAMS = (
    _p("esc_propeller_input_parser", 60, [
        ("mode", 0, "B", "enum"),
        ("sign", 3, "B", "enum"),
        ("volts_max", 4, "f", "V"),
        ("velocity_max", 5, "f", "rad/s"),
        ("safe_factor", 7, "f", "PU"),
        ("flip_negative", 8, "B", "bool"),
        ("zero_spin_throttle", 9, "f", "PU"),
        ("zero_spin_tolerance", 10, "f", "PU"),
        ("report_telemetry_as_speed", 11, "B", "bool"),
        ("torque_max", 12, "f", "Nm"),
    ])
    + _p("hobby_input", 76, [
        ("allowed_protocols", 0, "B", "enum"),
        ("protocol", 1, "B", "enum"),
        ("calibrated_protocol", 2, "B", "enum"),
        ("calibrated_high_ticks_us", 3, "I", "us"),
        ("calibrated_low_ticks_us", 4, "I", "us"),
        ("allow_dshot_disarming_message", 8, "B", "bool"),
    ])
    + _p("arming_handler", 86, [
        ("always_armed", 1, "B", "bool"),
        ("arm_on_throttle", 2, "B", "bool"),
        ("arm_throttle_upper_limit", 3, "f", "PU"),
        ("arm_throttle_lower_limit", 4, "f", "PU"),
        ("disarm_on_throttle", 5, "B", "bool"),
        ("disarm_throttle_upper_limit", 6, "f", "PU"),
        ("disarm_throttle_lower_limit", 7, "f", "PU"),
        ("consecutive_arming_throttles_to_arm", 8, "I", "throttles"),
        ("disarm_behavior", 9, "B", "enum"),
        ("disarm_song_option", 10, "B", "enum"),
        ("manual_arming_throttle_source", 11, "B", "enum"),
        ("consecutive_disarming_throttles_to_disarm", 13, "I", "throttles"),
        ("play_arming_song_on_arm", 14, "B", "bool"),
    ])
    + _p("throttle_source_manager", 104, [
        ("throttle_timeout", 0, "f", "s"),
        ("dronecan_priority", 1, "B", ""),
        ("hobby_priority", 2, "B", ""),
        ("iquart_priority", 3, "B", ""),
    ])
    # DroneCAN settings. uavcan_node_id / uavcan_esc_index / actuator_id are left
    # out on purpose: they are unique per motor and must never come from a profile.
    + _p("uavcan_node", 80, [
        ("zero_behavior", 2, "I", "enum"),
        ("telemetry_frequency", 9, "I", "Hz"),
        ("bit_rate", 10, "I", "bit/s"),
        ("bypass_arming", 11, "B", "bool"),
        ("arming_by_arming_status", 12, "B", "bool"),
        ("telemetry_style", 13, "B", "bool"),
    ])
)
PARAMS_BY_KEY = {p.key: p for p in MANAGED_PARAMS}

# system_control (type 5) identity entries
UID_PARAMS = [Param("system_control", f"uid{i}", 5, 3 + i, "I") for i in (1, 2, 3)]
FIRMWARE_PARAM = Param("system_control", "firmware_version", 5, 16, "I")
MODULE_ID_PARAM = Param("system_control", "module_id", 5, 14, "B")
REBOOT_PARAM = Param("system_control", "reboot_program", 5, 0, "")


def crc16(data):
    crc = 0xFFFF
    for byte in data:
        x = ((crc >> 8) ^ byte) & 0xFFFF
        x ^= x >> 4
        crc = ((crc << 8) ^ (x << 12) ^ (x << 5) ^ x) & 0xFFFF
    return crc


def make_packet(type_idn, param_idn, obj, access, payload=b""):
    body = bytes([type_idn, param_idn, ((obj & 0x3F) << 2) | access]) + payload
    framed = bytes([len(body) - 1]) + body
    return bytes([START]) + framed + struct.pack("<H", crc16(framed))


class PacketParser:
    """Incremental parser that pulls well-formed packets out of a byte stream."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        self.buf.extend(data)
        packets = []
        while True:
            start = self.buf.find(START)
            if start < 0:
                self.buf.clear()
                break
            del self.buf[:start]
            if len(self.buf) < 2:
                break
            total = 1 + 1 + 1 + self.buf[1] + 2  # start, len, type, body, crc
            if len(self.buf) < total:
                break
            framed = bytes(self.buf[1:total - 2])
            (crc,) = struct.unpack("<H", self.buf[total - 2:total])
            if crc == crc16(framed):
                packets.append(framed[1:])  # type_idn, param_idn, obj/access, value...
                del self.buf[:total]
            else:
                del self.buf[0]  # false start byte, resync
        return packets


class CommError(Exception):
    pass


class IQModule:
    def __init__(self, port="/dev/ttyUSB0", baud=115200, obj=0, timeout=0.25, retries=3):
        self.ser = serial.Serial(port, baud, timeout=0.02)
        self.obj = obj
        self.timeout = timeout
        self.retries = retries
        self.parser = PacketParser()

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def _send(self, param, access, payload=b""):
        self.ser.write(make_packet(param.type_idn, param.param_idn, self.obj, access, payload))
        self.ser.flush()

    def _wait_reply(self, param, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self.ser.read(256)
            if not data:
                continue
            for pkt in self.parser.feed(data):
                if (len(pkt) >= 3 and pkt[0] == param.type_idn and pkt[1] == param.param_idn
                        and pkt[2] & 3 == REPLY and pkt[2] >> 2 == self.obj):
                    return pkt[3:]
        return None

    def get(self, param, retries=None, timeout=None):
        """Returns the decoded value, or None if the module never answered."""
        for _ in range(self.retries if retries is None else retries):
            self.ser.reset_input_buffer()
            self.parser = PacketParser()
            self._send(param, GET)
            raw = self._wait_reply(param, self.timeout if timeout is None else timeout)
            if raw:
                return param.decode(raw)
        return None

    def set(self, param, value, save=True):
        self._send(param, SET, param.encode(value))
        if save:
            time.sleep(0.01)
            self._send(param, SAVE)
        time.sleep(0.02)

    def reboot(self, wait=8.0):
        """Restart the module's firmware, then wait until it answers again.
        Returns the UID it reports after the restart, or None if it never came back."""
        self._send(REBOOT_PARAM, SET)
        time.sleep(1.0)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            uid = self.read_uid()
            if uid is not None:
                return uid
        return None

    def read_uid(self):
        parts = [self.get(p) for p in UID_PARAMS]
        if any(v is None for v in parts):
            return None
        return "-".join(f"{v:08X}" for v in parts)


def values_equal(param, a, b):
    if a is None or b is None:
        return a is b
    if param.fmt == "f":
        return abs(float(a) - float(b)) <= max(1e-4, 1e-4 * abs(float(b)))
    return int(a) == int(b)


def find_module(port, baud):
    """Try object id 0 first, then scan the other ids. Returns a connected IQModule."""
    mod = IQModule(port, baud, obj=0)
    try:
        if mod.get(UID_PARAMS[0]) is not None:
            return mod
        for obj in range(1, 63):
            mod.obj = obj
            if mod.get(UID_PARAMS[0], retries=1, timeout=0.08) is not None:
                return mod
    except Exception:
        mod.close()
        raise
    mod.close()
    raise CommError(f"No Vertiq module answered on {port} @ {baud} baud.")
