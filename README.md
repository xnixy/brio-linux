# BRIO Control for Linux

A native GTK 4 / libadwaita app and a small user daemon that give the
Logitech BRIO (`046d:085e`) the same controls as Logitech G HUB on Windows:
**HDR, 90°/78°/65° field of view, zoom, pan/tilt, exposure, ISO, RightLight,
white balance, focus, anti-flicker**, built-in and user presets, per-app
profiles, a live preview up to 4K, and snapshots.

It talks to the inbox `uvcvideo` driver. It needs no kernel module and no root,
because logind already gives the logged-in user access to `/dev/video*`.

## Install

From a package (see [`packaging/`](packaging/README.md)), then:

    systemctl --user enable --now briod.service
    brio-control

### From a source checkout

    sudo pacman -S python-gobject gtk4 libadwaita libgudev gst-plugins-good gst-plugin-gtk4   # Arch
    sudo dnf install python3-gobject gtk4 libadwaita libgudev gstreamer1-plugins-good gstreamer1-plugin-gtk4   # Fedora
    make run-daemon        # terminal 1
    make run-gui           # terminal 2
    make test

## Command line

    brio-ctl set fov=78 hdr=off zoom=150          # edits the active profile via briod
    brio-ctl set wb=auto focus=40 exposure=200 iso=400 lowlight=off antiflicker=50 mic=off
    brio-ctl apply "Video call"                    # select and apply a profile
    brio-ctl get                                   # current device values
    brio-ctl --direct set brightness=140           # bypass the daemon
    brio-probe -o dump.json                        # every control + XU selector (read-only)
    brio-import-ghub settings.db                   # cards/profiles from a G HUB install

Exposure takes the shutter denominator (`exposure=200` means 1/200 s). ISO runs from 100 to 1600.
`fov` accepts 90, 78 or 65.

## How it works

| Piece | What it does |
|---|---|
| `briod/uvc.py` | Raw `VIDIOC_*` and `UVCIOC_CTRL_QUERY` ioctls via ctypes. Finds the Logitech extension units by GUID in the USB descriptors |
| `briod/models/brio.py` | The control map, exposure/gain tables and the built-in cards, all taken from G HUB's BRIO capability data |
| `briod/engine.py` | Applies settings in G HUB's order (crop → image → exposure → focus), writing each auto control before its manual value. It retries once on EBUSY/EIO, and one failing control never blocks the rest |
| `briod/daemon.py` | Applies settings 3 s after hotplug (G HUB's delay) and re-applies when another app opens the camera. Detects drift through V4L2 control events, switches profiles per app, and serves the session D-Bus API `io.github.BrioControl.Daemon` |
| `briod/tray.py` | The tray icon: StatusNotifierItem plus a `com.canonical.dbusmenu` menu, in plain Gio |
| `briod/store.py` | `~/.config/brio-control/profiles.json`, following G HUB's model: cards, profiles, the manual card and the persistent lock |
| `brio_control/` | The GUI: a preview pane plus Crop / Exposure / Image / Focus / Device tabs |

**Per-app profiles.** Wayland has no global focused-window API, so a profile is
picked by the app that has the camera open. That app is found by scanning
`/proc/*/fd` and matching on the process name, executable, Flatpak ID or systemd
app scope. Profile switching by focused window is optional. It works natively on
Sway and Hyprland. On GNOME and KDE, enable the bundled helper:

    gnome-extensions enable brio-control-focus@briocontrol.github.io
    kwriteconfig6 --file kwinrc --group Plugins --key brio-control-focusEnabled true && qdbus6 org.kde.KWin /KWin reconfigure

**System tray.** The daemon shows a tray icon with quick toggles: HDR, field of view, autofocus,
"Disable device in app", the profile, and "Open BRIO Control". The icon changes while another app
is streaming or the camera is disabled. It works with KDE, waybar/Hyprland/Sway bars, and GNOME
with the AppIndicator extension. Turn it off in Device → Behaviour or with `briod --no-tray`.

**When another app changes a setting** (for example `v4l2-ctl` or OBS), the default
is to keep the change. It is recorded as a manual adjustment, as G HUB does. You
can switch this to "Restore the profile" in Device → Behaviour.

## Notes and limits

- The preview pins the frame rate, because **the BRIO ignores zoom in its 720p90 mode**.
- Other apps choose their own resolution. 4K is just one of the formats the camera offers.
- uvcvideo lets only one process stream at a time. The preview pauses when the window
  loses focus (configurable), and the controls keep working while another app streams.
- The G HUB import is best effort. G HUB's settings layout changes between versions,
  so the importer looks for card-shaped objects. Use `brio-import-ghub --dump` to inspect a database.
- The firmware version is shown as major.minor.build from DeviceInfo selector 1.
  This is inferred from one camera.
- Video stabilisation, RightSight, show mode, tint/vibrance and factory reset are out of scope.
  G HUB doesn't offer them for the original BRIO either.

See [`docs/hardware-notes.md`](docs/hardware-notes.md) for the control map and firmware
findings, and [`docs/hardware-test-checklist.md`](docs/hardware-test-checklist.md) for what has
been verified on hardware.
