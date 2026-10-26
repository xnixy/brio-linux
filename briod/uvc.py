"""Low-level V4L2 + UVC extension-unit access via ioctl (no external deps).

Standard controls go through VIDIOC_{G,S}_CTRL / QUERYCTRL; Logitech extension
units go through UVCIOC_CTRL_QUERY addressed by UVC unit ID, which is resolved
from the extension-unit GUID in the USB configuration descriptors (sysfs).
"""
from __future__ import annotations

import ctypes
import errno
import fcntl
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- ioctl helpers
_IOC_WRITE, _IOC_READ = 1, 2


def _ioc(direction: int, typ: str, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(typ) << 8) | nr


def _iowr(typ, nr, struct):
    return _ioc(_IOC_READ | _IOC_WRITE, typ, nr, ctypes.sizeof(struct))


def _ior(typ, nr, struct):
    return _ioc(_IOC_READ, typ, nr, ctypes.sizeof(struct))


def _iow(typ, nr, struct):
    return _ioc(_IOC_WRITE, typ, nr, ctypes.sizeof(struct))


class v4l2_capability(ctypes.Structure):
    _fields_ = [("driver", ctypes.c_char * 16), ("card", ctypes.c_char * 32),
                ("bus_info", ctypes.c_char * 32), ("version", ctypes.c_uint32),
                ("capabilities", ctypes.c_uint32), ("device_caps", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32 * 3)]


class v4l2_queryctrl(ctypes.Structure):
    _fields_ = [("id", ctypes.c_uint32), ("type", ctypes.c_uint32),
                ("name", ctypes.c_char * 32), ("minimum", ctypes.c_int32),
                ("maximum", ctypes.c_int32), ("step", ctypes.c_int32),
                ("default_value", ctypes.c_int32), ("flags", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32 * 2)]


class _menu_union(ctypes.Union):
    _fields_ = [("name", ctypes.c_char * 32), ("value", ctypes.c_int64)]


class v4l2_querymenu(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("id", ctypes.c_uint32), ("index", ctypes.c_uint32),
                ("u", _menu_union), ("reserved", ctypes.c_uint32)]


class v4l2_control(ctypes.Structure):
    _fields_ = [("id", ctypes.c_uint32), ("value", ctypes.c_int32)]


class v4l2_fmtdesc(ctypes.Structure):
    _fields_ = [("index", ctypes.c_uint32), ("type", ctypes.c_uint32),
                ("flags", ctypes.c_uint32), ("description", ctypes.c_char * 32),
                ("pixelformat", ctypes.c_uint32), ("mbus_code", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32 * 3)]


class v4l2_frmsizeenum(ctypes.Structure):
    _fields_ = [("index", ctypes.c_uint32), ("pixel_format", ctypes.c_uint32),
                ("type", ctypes.c_uint32), ("u", ctypes.c_uint32 * 6),
                ("reserved", ctypes.c_uint32 * 2)]


class v4l2_frmivalenum(ctypes.Structure):
    _fields_ = [("index", ctypes.c_uint32), ("pixel_format", ctypes.c_uint32),
                ("width", ctypes.c_uint32), ("height", ctypes.c_uint32),
                ("type", ctypes.c_uint32), ("u", ctypes.c_uint32 * 6),
                ("reserved", ctypes.c_uint32 * 2)]


class v4l2_event_subscription(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("id", ctypes.c_uint32),
                ("flags", ctypes.c_uint32), ("reserved", ctypes.c_uint32 * 5)]


class v4l2_event_ctrl(ctypes.Structure):
    _fields_ = [("changes", ctypes.c_uint32), ("type", ctypes.c_uint32),
                ("value", ctypes.c_int64), ("flags", ctypes.c_uint32),
                ("minimum", ctypes.c_int32), ("maximum", ctypes.c_int32),
                ("step", ctypes.c_int32), ("default_value", ctypes.c_int32)]


class _timespec(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_nsec", ctypes.c_long)]


class v4l2_event(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("u", ctypes.c_uint8 * 64),
                ("pending", ctypes.c_uint32), ("sequence", ctypes.c_uint32),
                ("timestamp", _timespec), ("id", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32 * 8)]


class uvc_xu_control_query(ctypes.Structure):
    _fields_ = [("unit", ctypes.c_uint8), ("selector", ctypes.c_uint8),
                ("query", ctypes.c_uint8), ("size", ctypes.c_uint16),
                ("data", ctypes.POINTER(ctypes.c_uint8))]


VIDIOC_QUERYCAP = _ior("V", 0, v4l2_capability)
VIDIOC_ENUM_FMT = _iowr("V", 2, v4l2_fmtdesc)
VIDIOC_G_CTRL = _iowr("V", 27, v4l2_control)
VIDIOC_S_CTRL = _iowr("V", 28, v4l2_control)
VIDIOC_QUERYCTRL = _iowr("V", 36, v4l2_queryctrl)
VIDIOC_QUERYMENU = _iowr("V", 37, v4l2_querymenu)
VIDIOC_ENUM_FRAMESIZES = _iowr("V", 74, v4l2_frmsizeenum)
VIDIOC_ENUM_FRAMEINTERVALS = _iowr("V", 75, v4l2_frmivalenum)
VIDIOC_DQEVENT = _ior("V", 89, v4l2_event)
VIDIOC_SUBSCRIBE_EVENT = _iow("V", 90, v4l2_event_subscription)
UVCIOC_CTRL_QUERY = _iowr("u", 0x21, uvc_xu_control_query)

V4L2_CTRL_FLAG_DISABLED = 0x0001
V4L2_CTRL_FLAG_INACTIVE = 0x0010
V4L2_CTRL_FLAG_NEXT_CTRL = 0x80000000
V4L2_CTRL_TYPE_MENU = 3
V4L2_CTRL_TYPE_INTEGER_MENU = 9
V4L2_CTRL_TYPE_CTRL_CLASS = 6
V4L2_BUF_TYPE_VIDEO_CAPTURE = 1
V4L2_CAP_VIDEO_CAPTURE = 0x00000001
V4L2_FRMSIZE_TYPE_DISCRETE = 1
V4L2_EVENT_CTRL = 3
V4L2_EVENT_CTRL_CH_VALUE = 1

# UVC request codes
SET_CUR, GET_CUR, GET_MIN, GET_MAX = 0x01, 0x81, 0x82, 0x83
GET_RES, GET_LEN, GET_INFO, GET_DEF = 0x84, 0x85, 0x86, 0x87

# Standard control IDs used by this project (linux/v4l2-controls.h)
CID = {
    "brightness": 0x00980900, "contrast": 0x00980901, "saturation": 0x00980902,
    "white_balance_automatic": 0x0098090C, "gain": 0x00980913,
    "power_line_frequency": 0x00980918, "white_balance_temperature": 0x0098091A,
    "sharpness": 0x0098091B, "backlight_compensation": 0x0098091C,
    "auto_exposure": 0x009A0901, "exposure_time_absolute": 0x009A0902,
    "exposure_dynamic_framerate": 0x009A0903, "pan_absolute": 0x009A0908,
    "tilt_absolute": 0x009A0909, "focus_absolute": 0x009A090A,
    "focus_automatic_continuous": 0x009A090C, "zoom_absolute": 0x009A090D,
}
CID_NAME = {v: k for k, v in CID.items()}


def fourcc(code: int) -> str:
    return "".join(chr((code >> (8 * i)) & 0xFF) for i in range(4))


# --------------------------------------------------------- descriptor parsing
@dataclass
class ExtensionUnit:
    unit_id: int
    guid: uuid.UUID
    num_controls: int
    controls_bitmap: bytes

    def selectors(self) -> list[int]:
        """Selectors advertised in bmControls (bit n → selector n+1)."""
        out = []
        for i, b in enumerate(self.controls_bitmap):
            for bit in range(8):
                if b & (1 << bit):
                    out.append(i * 8 + bit + 1)
        return out


def parse_extension_units(raw: bytes) -> list[ExtensionUnit]:
    """Find VC_EXTENSION_UNIT descriptors inside VideoControl interfaces."""
    units: list[ExtensionUnit] = []
    in_vc = False
    i = 0
    while i + 2 <= len(raw):
        blen, btype = raw[i], raw[i + 1]
        if blen < 2 or i + blen > len(raw):
            break
        d = raw[i:i + blen]
        if btype == 0x04 and blen >= 9:  # INTERFACE
            in_vc = d[5] == 0x0E and d[6] == 0x01  # CC_VIDEO / SC_VIDEOCONTROL
        elif btype == 0x24 and in_vc and blen >= 24 and d[2] == 0x06:
            unit_id = d[3]
            guid = uuid.UUID(bytes_le=bytes(d[4:20]))
            num_controls = d[20]
            p = d[21]
            off = 22 + p
            n = d[off] if off < blen else 0
            bitmap = bytes(d[off + 1:off + 1 + n])
            units.append(ExtensionUnit(unit_id, guid, num_controls, bitmap))
        i += blen
    return units


def usb_device_dir(devnode: str) -> Path | None:
    """sysfs USB *device* directory for /dev/videoN (parent of the interface)."""
    name = os.path.basename(devnode)
    p = Path(f"/sys/class/video4linux/{name}/device")
    try:
        iface = p.resolve(strict=True)
    except OSError:
        return None
    dev = iface.parent
    return dev if (dev / "descriptors").exists() else None


def read_sysfs(d: Path | None, name: str) -> str:
    if d is None:
        return ""
    try:
        return (d / name).read_text().strip()
    except OSError:
        return ""


# ------------------------------------------------------------------- device
class UVCError(OSError):
    pass


@dataclass
class CtrlInfo:
    id: int
    name: str
    type: int
    minimum: int
    maximum: int
    step: int
    default: int
    flags: int
    menu: dict[int, str] = field(default_factory=dict)

    @property
    def inactive(self) -> bool:
        return bool(self.flags & V4L2_CTRL_FLAG_INACTIVE)


class Device:
    """An open V4L2 node.  Not thread-safe; one per daemon per camera."""

    def __init__(self, devnode: str):
        self.devnode = devnode
        self.fd = os.open(devnode, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        self._xu_units: dict[str, int] | None = None

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def _ioctl(self, req, arg):
        try:
            fcntl.ioctl(self.fd, req, arg)
        except OSError as e:
            raise UVCError(e.errno, f"{os.strerror(e.errno)} (ioctl 0x{req:08x} on {self.devnode})") from None

    # --- identity
    def querycap(self) -> v4l2_capability:
        cap = v4l2_capability()
        self._ioctl(VIDIOC_QUERYCAP, cap)
        return cap

    def is_capture(self) -> bool:
        return bool(self.querycap().device_caps & V4L2_CAP_VIDEO_CAPTURE)

    # --- standard controls
    def query_controls(self) -> dict[str, CtrlInfo]:
        out: dict[str, CtrlInfo] = {}
        qc = v4l2_queryctrl(id=V4L2_CTRL_FLAG_NEXT_CTRL)
        while True:
            try:
                fcntl.ioctl(self.fd, VIDIOC_QUERYCTRL, qc)
            except OSError:
                break
            if qc.type != V4L2_CTRL_TYPE_CTRL_CLASS and not qc.flags & V4L2_CTRL_FLAG_DISABLED:
                name = CID_NAME.get(qc.id, qc.name.decode(errors="replace"))
                info = CtrlInfo(qc.id, name, qc.type, qc.minimum, qc.maximum,
                                qc.step, qc.default_value, qc.flags)
                if qc.type in (V4L2_CTRL_TYPE_MENU, V4L2_CTRL_TYPE_INTEGER_MENU):
                    for idx in range(qc.minimum, qc.maximum + 1):
                        qm = v4l2_querymenu(id=qc.id, index=idx)
                        try:
                            fcntl.ioctl(self.fd, VIDIOC_QUERYMENU, qm)
                        except OSError:
                            continue
                        info.menu[idx] = (qm.u.name.decode(errors="replace")
                                          if qc.type == V4L2_CTRL_TYPE_MENU else str(qm.u.value))
                out[name] = info
            qc = v4l2_queryctrl(id=qc.id | V4L2_CTRL_FLAG_NEXT_CTRL)
        return out

    def get(self, name: str) -> int:
        c = v4l2_control(id=CID[name])
        self._ioctl(VIDIOC_G_CTRL, c)
        return c.value

    def set(self, name: str, value: int):
        self._ioctl(VIDIOC_S_CTRL, v4l2_control(id=CID[name], value=int(value)))

    # --- control-change events (changes made through *other* file handles)
    def subscribe_ctrl_events(self, names):
        for n in names:
            sub = v4l2_event_subscription(type=V4L2_EVENT_CTRL, id=CID[n], flags=0)
            try:
                self._ioctl(VIDIOC_SUBSCRIBE_EVENT, sub)
            except UVCError:
                pass

    def dequeue_events(self) -> list[tuple[str, int]]:
        out = []
        while True:
            ev = v4l2_event()
            try:
                fcntl.ioctl(self.fd, VIDIOC_DQEVENT, ev)
            except OSError:
                break
            if ev.type == V4L2_EVENT_CTRL:
                ec = v4l2_event_ctrl.from_buffer_copy(bytes(ev.u)[:ctypes.sizeof(v4l2_event_ctrl)])
                if ec.changes & V4L2_EVENT_CTRL_CH_VALUE and ev.id in CID_NAME:
                    out.append((CID_NAME[ev.id], int(ec.value)))
        return out

    # --- extension units
    def xu_query(self, unit: int, selector: int, query: int, size: int,
                 data: bytes | None = None) -> bytes:
        buf = (ctypes.c_uint8 * max(size, 1))()
        if data:
            ctypes.memmove(buf, data, min(len(data), size))
        q = uvc_xu_control_query(unit=unit, selector=selector, query=query,
                                 size=size, data=ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint8)))
        self._ioctl(UVCIOC_CTRL_QUERY, q)
        return bytes(buf[:size])

    def xu_len(self, unit: int, selector: int) -> int:
        return int.from_bytes(self.xu_query(unit, selector, GET_LEN, 2), "little")

    def xu_get(self, unit: int, selector: int, size: int | None = None) -> bytes:
        return self.xu_query(unit, selector, GET_CUR, size or self.xu_len(unit, selector))

    def xu_set(self, unit: int, selector: int, data: bytes):
        self.xu_query(unit, selector, SET_CUR, len(data), data)

    def extension_units(self) -> list[ExtensionUnit]:
        d = usb_device_dir(self.devnode)
        if d is None:
            return []
        return parse_extension_units((d / "descriptors").read_bytes())

    def xu_unit_for(self, guid: str) -> int | None:
        if self._xu_units is None:
            self._xu_units = {str(u.guid).upper(): u.unit_id for u in self.extension_units()}
        return self._xu_units.get(guid.upper())

    # --- formats
    def enum_formats(self) -> list[dict]:
        """[{fourcc, description, sizes: [{width, height, fps: [..]}]}]"""
        fmts = []
        i = 0
        while True:
            fd = v4l2_fmtdesc(index=i, type=V4L2_BUF_TYPE_VIDEO_CAPTURE)
            try:
                fcntl.ioctl(self.fd, VIDIOC_ENUM_FMT, fd)
            except OSError:
                break
            entry = {"fourcc": fourcc(fd.pixelformat),
                     "description": fd.description.decode(errors="replace"), "sizes": []}
            j = 0
            while True:
                fs = v4l2_frmsizeenum(index=j, pixel_format=fd.pixelformat)
                try:
                    fcntl.ioctl(self.fd, VIDIOC_ENUM_FRAMESIZES, fs)
                except OSError:
                    break
                if fs.type != V4L2_FRMSIZE_TYPE_DISCRETE:
                    break
                w, h = fs.u[0], fs.u[1]
                fps = []
                k = 0
                while True:
                    fi = v4l2_frmivalenum(index=k, pixel_format=fd.pixelformat, width=w, height=h)
                    try:
                        fcntl.ioctl(self.fd, VIDIOC_ENUM_FRAMEINTERVALS, fi)
                    except OSError:
                        break
                    if fi.type == V4L2_FRMSIZE_TYPE_DISCRETE and fi.u[0]:
                        fps.append(round(fi.u[1] / fi.u[0], 3))
                    k += 1
                entry["sizes"].append({"width": w, "height": h, "fps": sorted(set(fps), reverse=True)})
                j += 1
            fmts.append(entry)
            i += 1
        return fmts


def retry(fn, *args, attempts=2, delay=0.05):
    """Run fn, retrying once on EBUSY/EIO."""
    for n in range(attempts):
        try:
            return fn(*args)
        except UVCError as e:
            if e.errno not in (errno.EBUSY, errno.EIO) or n == attempts - 1:
                raise
            time.sleep(delay)


# ---------------------------------------------------------------- discovery
@dataclass
class CameraInfo:
    devnode: str          # primary capture node
    nodes: list[str]      # every /dev/videoN belonging to the USB device
    vid: str
    pid: str
    serial: str
    name: str
    usb_path: str         # sysfs path of the USB device

    @property
    def slot(self) -> str:
        return f"brio.{self.serial or self.usb_path.rsplit('/', 1)[-1]}"


def find_cameras(vid="046d", pids=("085e",)) -> list[CameraInfo]:
    """All matching cameras; the primary node is the lowest-numbered capture node
    that offers MJPEG (the BRIO's second capture node is the IR sensor)."""
    groups: dict[str, list[str]] = {}
    for p in sorted(Path("/sys/class/video4linux").glob("video*"),
                    key=lambda p: int(p.name[5:]) if p.name[5:].isdigit() else 1 << 30):
        node = f"/dev/{p.name}"
        d = usb_device_dir(node)
        if d is None or read_sysfs(d, "idVendor") != vid or read_sysfs(d, "idProduct") not in pids:
            continue
        groups.setdefault(str(d), []).append(node)
    cams = []
    for usb, nodes in groups.items():
        primary = None
        for n in nodes:
            try:
                with Device(n) as dev:
                    if dev.is_capture() and any(f["fourcc"] == "MJPG" for f in dev.enum_formats()):
                        primary = n
                        break
            except OSError:
                continue
        if primary is None:
            continue
        d = Path(usb)
        cams.append(CameraInfo(primary, nodes, vid, read_sysfs(d, "idProduct"),
                               read_sysfs(d, "serial"),
                               read_sysfs(Path(f"/sys/class/video4linux/{os.path.basename(primary)}"), "name"),
                               usb))
    return cams
