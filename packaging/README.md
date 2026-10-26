# Packaging

All packages build the Python wheel from `pyproject.toml` and then run
`make install-data` for the systemd user unit, D-Bus activation file, desktop
entry, icon and the optional GNOME Shell / KWin focus helpers.

- **Arch / AUR:** `packaging/arch/PKGBUILD` (expects a `brio-control-0.1.0.tar.gz` source tarball:
  `git archive --prefix=brio-control-0.1.0/ -o brio-control-0.1.0.tar.gz HEAD`).
- **Fedora / COPR:** `packaging/fedora/brio-control.spec` (same tarball).
- **Debian / Ubuntu:** copy `packaging/debian` to `debian/` at the source root and run
  `dpkg-buildpackage -us -uc -b`.

After installing, enable the daemon for your user:

    systemctl --user enable --now briod.service

The GUI also starts it on demand through D-Bus activation.

`url` fields are placeholders until the project has a public home.
