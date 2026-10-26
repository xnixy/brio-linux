# Hardware test checklist (Logitech BRIO 046d:085e)

Run against a real camera before a release. Results from 2026-10-26 (kernel 7.3,
firmware 2.0.68) are marked ✅; items that need a human or root are marked ☐.

## Probe / CLI
- ✅ `brio-probe` lists units 6, 8, 9, 10 (Videopipe), 11 (Peripheral), 12, 14, 21; the GUID resolver gives 10 and 11
- ✅ `brio-ctl set fov=78 hdr=off zoom=150 wb=4500 exposure=200 gain=400`: the read-back matches (XU10/05 = 01, backlight 0, zoom 150, WB manual 4500, AE manual 39, gain 48)
- ✅ `brio-ctl --direct get` works without the daemon

## Daemon
- ✅ Startup applies the active profile
- ✅ An external `v4l2-ctl -c brightness=50` is recorded as a manual adjustment (device wins)
- ☐ The "profile wins" policy reverts an external change (switch in Device → Behaviour)
- ✅ A camera user (`gst-launch-1.0`) is detected and named; a profile listing it is applied while it streams, and the default comes back after it closes
- ☐ Unplug and replug: settings come back about 3 s after the add event (needs physical access)
- ☐ Suspend and resume: check FOV and zoom after resume
- ☐ The GNOME Shell extension and the KWin script report focus (enable "Switch profiles by focused window")
- ☐ Sway and Hyprland focus backends

## Tray
- ✅ The icon registers with the StatusNotifierWatcher (GNOME + AppIndicator extension)
- ✅ The menu lists the camera, HDR, FOV 90/78/65, autofocus, disable, profiles and Open; clicking HDR and FOV changes the device
- ✅ The icon switches to camera-disabled while the device is disabled; "Open BRIO Control" launches the window
- ☐ The menu looks right in KDE Plasma and waybar

## GUI
- ✅ All five tabs render, and controls appear only when the camera reports them
- ✅ FOV buttons, zoom slider, HDR switch, and manual WB + kelvin slider reach the device
- ✅ Editing a built-in preset creates "Manual"; Reset to Default restores the defaults and disables pan/tilt at minimum zoom
- ✅ The preview runs at 720p30; a snapshot is saved as a 1280×720 PNG in `~/Pictures/Webcam Snapshots/`
- ☐ The 4K preview runs smoothly on your GPU
- ☐ The preview stops when the window loses focus, and another app can then open the camera
- ☐ The in-use banner shows while Zoom, OBS or a browser has the camera

## Firmware behaviour (milestone 1 findings)
- ✅ No control resets on stream start: zoom, pan, brightness, saturation and FOV set before `STREAMON` all survive at 720p30, 1080p and 4K
- ✅ **720p90 ignores zoom completely.** The preview pins the frame rate (720p30/60 zoom fine)
- ✅ Zoom 400 at 4K is visibly capped (≈2×); 1080p reaches the full range
