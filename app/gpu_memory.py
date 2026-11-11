"""A card's memory as its kernel driver counts it — (total, used), in bytes.

ONE VERB, ONE READER PER KERNEL DRIVER. Whoever asks names a PCI device and
gets two numbers or none; which driver answered is this module's business and
nobody else's. The GPU probe asks through here and never learns a
manufacturer's name (operator, 2026-09-20, model-pool.md@c572c3b §18: no
manufacturer in the platform — a second manufacturer is a second reader behind the same verb, as with the
clock, `app/gpu_clock.py`). A driver with no reader here gives (None, None,
why), and the asker goes on with what it declares, as it did before this
existed.

WHY IT EXISTS (node, 2026-09-20). The model pool admitted loads against what ITS OWN
models declare. A container outside the model pool took 21.4 GiB of the 32 GiB card;
the driver moved every resident model into host RAM without an error to
anyone; the host ran out of memory and the kernel killed three models.
Admission cannot see a stranger on the card unless somebody reads the card.

`xe` reports what is USED only to CAP_PERFMON (or CAP_SYS_ADMIN); without it
the total is read and `used` is None. `amdgpu` publishes both in sysfs.

Read by the GPU probe (`app/gpu_probe.py`) and by tools/profile_models.py;
like `app/gpu_clock.py`, it imports nothing of the platform.
"""
from __future__ import annotations

import ctypes
import fcntl
import os
import struct
from pathlib import Path
from typing import Callable

# include/uapi/drm/xe_drm.h. DRM_IOWR(DRM_COMMAND_BASE + DRM_XE_DEVICE_QUERY,
# struct drm_xe_device_query): dir 3, size 40, type 'd', nr 0x40.
DRM_IOCTL_XE_DEVICE_QUERY = 0xC0286440
XE_QUERY_MEM_REGIONS = 1
XE_MEM_REGION_CLASS_VRAM = 1
_XE_QUERY = struct.Struct("<QIIQQQ")          # extensions, query, size, data, reserved[2]
_XE_REGIONS = struct.Struct("<II")            # num_mem_regions, pad
_XE_REGION = struct.Struct("<HHIQQQQ48x")     # class, instance, min_page_size, total_size,
                                              # used, cpu_visible_size, cpu_visible_used, reserved[6]
CAP_SYS_ADMIN, CAP_PERFMON = 21, 38
# include/uapi/drm/amdgpu_drm.h. DRM_IOW(DRM_COMMAND_BASE + DRM_AMDGPU_INFO,
# struct drm_amdgpu_info): dir 1, size 32, type 'd', nr 0x45.
DRM_IOCTL_AMDGPU_INFO = 0x40206445
AMDGPU_INFO_DEV_INFO = 0x16
AMDGPU_IDS_FLAGS_FUSION = 0x1
_AMDGPU_INFO = struct.Struct("<QII16x")       # return_pointer, return_size, query, the union
# struct drm_amdgpu_info_device: eight u32, two u64, cu_active_number,
# cu_ao_mask, cu_bitmap[4][4], enabled_rb_pipes_mask, num_rb_pipes,
# num_hw_gfx_contexts, pcie_gen — then ids_flags (u64) at 136
_DEV_INFO_IDS_FLAGS = 136
_DEV_INFO_SIZE = _DEV_INFO_IDS_FLAGS + 8

Ioctl = Callable[..., object]


def perfmon_capable(status: str | None = None, *,
                    status_path: Path = Path("/proc/self/status")) -> bool:
    """Whether the driver will tell this process what is used: CAP_PERFMON or
    CAP_SYS_ADMIN in its effective set."""
    if status is None:
        try:
            status = status_path.read_text()
        except OSError:
            return False
    for line in status.splitlines():
        if line.startswith("CapEff:"):
            caps = int(line.split()[1], 16)
            return bool(caps & (1 << CAP_PERFMON) or caps & (1 << CAP_SYS_ADMIN))
    return False


def xe_query(fd: int, query: int, *, ioctl: Ioctl = fcntl.ioctl) -> bytes:
    """The two-call protocol: the kernel says the size, then fills it."""
    head = bytearray(_XE_QUERY.pack(0, query, 0, 0, 0, 0))
    ioctl(fd, DRM_IOCTL_XE_DEVICE_QUERY, head, True)
    size = _XE_QUERY.unpack(head)[2]
    data = ctypes.create_string_buffer(size)
    head = bytearray(_XE_QUERY.pack(0, query, size, ctypes.addressof(data), 0, 0))
    ioctl(fd, DRM_IOCTL_XE_DEVICE_QUERY, head, True)
    return data.raw


def amdgpu_integrated(render: Path, *, ioctl: Ioctl = fcntl.ioctl) -> bool:
    """Is this amdgpu card part of the processor (an APU)? The driver's own
    word: AMDGPU_INFO_DEV_INFO's `ids_flags` carries AMDGPU_IDS_FLAGS_FUSION
    — read the way nvtop reads it (extract_gpuinfo_amdgpu.c,
    `integrated_graphics`). An integrated card's memory IS the system's RAM:
    its buffers in GTT are where they belong, never "pushed off" (operator,
    2026-11-11, on the Radeon 610M). Raises OSError where it cannot be asked."""
    fd = os.open(render, os.O_RDWR | os.O_CLOEXEC)
    try:
        out = ctypes.create_string_buffer(_DEV_INFO_SIZE)
        head = bytearray(_AMDGPU_INFO.pack(ctypes.addressof(out), _DEV_INFO_SIZE, AMDGPU_INFO_DEV_INFO))
        ioctl(fd, DRM_IOCTL_AMDGPU_INFO, head, True)
        flags = struct.unpack_from("<Q", out.raw, _DEV_INFO_IDS_FLAGS)[0]
    finally:
        os.close(fd)
    return bool(flags & AMDGPU_IDS_FLAGS_FUSION)


def xe_vram_regions(raw: bytes) -> tuple[int, int]:
    """(total, used) over the card's VRAM regions — one per tile."""
    count, _ = _XE_REGIONS.unpack_from(raw)
    total = used = 0
    for i in range(count):
        mem_class, _inst, _page, size, in_use, _vis, _vis_used = _XE_REGION.unpack_from(
            raw, _XE_REGIONS.size + i * _XE_REGION.size)
        if mem_class == XE_MEM_REGION_CLASS_VRAM:
            total += size
            used += in_use
    return total, used


def xe_vram(render: Path, *, ioctl: Ioctl = fcntl.ioctl) -> tuple[int, int]:
    fd = os.open(render, os.O_RDWR | os.O_CLOEXEC)
    try:
        return xe_vram_regions(xe_query(fd, XE_QUERY_MEM_REGIONS, ioctl=ioctl))
    finally:
        os.close(fd)


def _int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _xe(device: Path, dev_dri: Path, ioctl: Ioctl,
        capable: Callable[[], bool]) -> tuple[int | None, int | None, str | None]:
    renders = sorted(p.name for p in (device / "drm").glob("renderD*"))
    if not renders:
        return None, None, "the card has no render node"
    try:
        total, used = xe_vram(dev_dri / renders[0], ioctl=ioctl)
    except OSError as e:
        return None, None, f"the driver's memory query failed ({e})"
    if not capable():
        return total, None, ("the driver tells what is used only to CAP_PERFMON: add it to "
                             "this container (cap_add: PERFMON)")
    return total, used, None


def _amdgpu(device: Path, _dev_dri: Path, _ioctl: Ioctl,
            _capable: Callable[[], bool]) -> tuple[int | None, int | None, str | None]:
    total, used = _int(device / "mem_info_vram_total"), _int(device / "mem_info_vram_used")
    return total, used, None if total is not None else "the driver publishes no VRAM total"


#: kernel driver -> its reader. The ONLY place a driver is named.
READERS = {"xe": _xe, "amdgpu": _amdgpu}


def driver_of(device: Path) -> str | None:
    """The kernel driver the PCI device is bound to, as the bus says it."""
    try:
        return os.path.basename(os.readlink(device / "driver"))
    except OSError:
        return None


def read(pci: str, *, sys_root: Path = Path("/sys"), dev_dri: Path = Path("/dev/dri"),
         ioctl: Ioctl = fcntl.ioctl, capable: Callable[[], bool] = perfmon_capable,
         ) -> tuple[int | None, int | None, str | None]:
    """(total, used, why not) for the card at this PCI address, in bytes.
    Either number may be None, and then the third says why."""
    device = Path(sys_root) / "bus" / "pci" / "devices" / pci
    driver = driver_of(device)
    if driver is None:
        return None, None, f"no device at {pci}, or it is bound to no driver"
    reader = READERS.get(driver)
    if reader is None:
        return None, None, (f"no memory reader for the {driver} driver: the card's memory is "
                            f"not read, and what the models declare decides alone")
    return reader(device, Path(dev_dri), ioctl, capable)
