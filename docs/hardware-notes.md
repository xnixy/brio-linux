# BRIO hardware notes (Linux)

Measured on 2026-10-26 with kernel 7.3.0 and BRIO firmware 2.0.68.

| Item | Result |
|---|---|
| Pan/tilt range | **±36000 step 3600**, not G HUB's ±72000. The code uses whatever `VIDIOC_QUERYCTRL` reports |
| `auto_exposure` menu | Offers only 1 (Manual) and 3 (Aperture Priority) ✅ |
| XU unit IDs | Videopipe 10, Peripheral 11, DeviceInfo 8 ✅ (resolved from GUIDs in sysfs `descriptors`) |
| DeviceInfo 0x0B (color code) | **Not present.** It is missing from bmControls, and GET_LEN returns ENOENT. Dropped |
| Firmware (DeviceInfo 0x01) | `00 02 44 00`, shown as 2.0.68 |
| XU 12 / XU 14 | Readable on Linux (Windows couldn't expose them). Use `brio-probe` to dump them |
| Reset on stream start | **None observed.** Zoom, pan, brightness, saturation and FOV all survive STREAMON at 720p30, 1080p30 and 4K. Re-apply-on-open stays in as a cheap safety net |
| Zoom vs. stream mode | **720p90 ignores zoom entirely.** 720p30/60 and 1080p apply it. At 4K, zoom 400 is visibly capped (≈2×). The preview pins the frame rate. The per-resolution zoom caps are not reflected in the slider yet |
| uvcvideo caching | V4L2 `G_CTRL` returns uvcvideo's cached value, not a device read. Only XU reads hit the device |
| 2nd capture node | `/dev/video2` is the IR sensor (GREY 340×340). The primary node is the first one that offers MJPEG |

## Control map

| Setting | V4L2 control / extension unit | Value |
|---|---|---|
| HDR | `backlight_compensation` | 1 / 0 |
| Field of view | Videopipe XU `49E40215-F434-47FE-B158-0E885023E51B`, selector 0x05, 1 byte | 90° → 0, 78° → 1, 65° → 2 |
| Microphone | Peripheral XU `FFE52D21-8030-4E2C-82D9-F587D00540BD`, selector 0x10 | 1 on, 0 off |
| Firmware | DeviceInfo XU `69678EE4-410F-40DB-A850-7420D7D8240E`, selector 0x01, 4 bytes | read-only |
| Low light compensation (RightLight) | `exposure_dynamic_framerate` | on → 1 (exposure priority) |
| Exposure | `auto_exposure` 3 (auto) / 1 (manual) + `exposure_time_absolute` | 1/2000 s → 5 … 1/17 s → 2047 (100 µs units) |
| ISO | `gain` | ISO 100…1600 → (iso − 100) / 100 × 16 |
| Anti-flicker | `power_line_frequency` | 50 Hz → 1, 60 Hz → 2 |

Auto controls must be written before the manual value they gate; uvcvideo
rejects the value with EACCES while the auto control is on.

Other extension-unit selectors (units 6 and 9) are firmware/debug controls and
are never written.

## MX Brio (046d:0944)

Observed on 2026-11-13 with Ubuntu, kernel 7.0.0-42-generic and GStreamer
1.28.2. This is a different model from the original BRIO (046d:085e).
Linux enumerated /dev/video0 and /dev/video1; /dev/video0 was used for capture.

The camera exposes standard UVC controls for brightness, contrast, saturation,
sharpness, automatic/manual white balance, gain, anti-flicker, backlight
compensation, automatic/manual exposure, dynamic frame rate, pan, tilt and
automatic/manual focus and zoom. Notable reported ranges:

| Control | Range |
|---|---|
| Pan / tilt | ±72000, step 3600 |
| Zoom | 100–400, step 1 |
| Manual exposure | 3–2047, step 1 |
| White balance temperature | 2800–7500 K |
| Gain / manual focus | 0–255, step 1 |

Discovery now accepts 0944. Standard controls use the existing UVC path, but
individual control effects have not all been verified on hardware. Exposure
presets and ISO labels retain the original BRIO conversions; they are not
calibrated MX Brio measurements. Original-BRIO proprietary FOV, microphone and
firmware selectors are disabled for this model. The original BRIO mapping of
backlight compensation to HDR is also disabled pending verification.

### Preview findings

At 1280×720, 30 fps, direct V4L2 MJPEG capture using v4l2-ctl successfully
captured 60 frames. The equivalent GStreamer mmap pipeline failed: the driver
provided 1,092,266-byte buffers while GStreamer expected 1,638,400 bytes.
The resulting “Failed to allocate required memory” message was a buffer-size
mismatch, not evidence of exhausted system RAM.

GStreamer USERPTR failed because the JPEG pipeline supplied no downstream
buffer pool to import. Read/write capture was unsupported by the driver.
GStreamer mmap capture using YUYV at 1280×720, 30 fps successfully captured
60 frames, and the user subsequently confirmed the GUI preview worked.

MX Brio preview therefore prefers YUYV where the selected mode offers it.
MJPEG-only modes remain unverified and may encounter the same failure.
Preview errors no longer trigger continuous retries; toggling preview or
changing the device/mode permits another attempt.
