# Data-file install shared by all distro packages.  Python code is installed
# with the usual wheel tooling (see packaging/).
PREFIX ?= /usr
DESTDIR ?=
SHARE = $(DESTDIR)$(PREFIX)/share

.PHONY: install-data test run-daemon run-gui

install-data:
	install -Dm644 data/briod.service $(DESTDIR)$(PREFIX)/lib/systemd/user/briod.service
	install -Dm644 data/io.github.BrioControl.Daemon.service $(SHARE)/dbus-1/services/io.github.BrioControl.Daemon.service
	install -Dm644 data/io.github.BrioControl.desktop $(SHARE)/applications/io.github.BrioControl.desktop
	install -Dm644 data/icons/io.github.BrioControl.svg $(SHARE)/icons/hicolor/scalable/apps/io.github.BrioControl.svg
	install -Dm644 data/gnome-shell-extension/metadata.json $(SHARE)/gnome-shell/extensions/brio-control-focus@briocontrol.github.io/metadata.json
	install -Dm644 data/gnome-shell-extension/extension.js $(SHARE)/gnome-shell/extensions/brio-control-focus@briocontrol.github.io/extension.js
	install -Dm644 data/kwin-script/metadata.json $(SHARE)/kwin/scripts/brio-control-focus/metadata.json
	install -Dm644 data/kwin-script/contents/code/main.js $(SHARE)/kwin/scripts/brio-control-focus/contents/code/main.js
	install -Dm644 README.md $(SHARE)/doc/brio-control/README.md
	install -Dm644 docs/hardware-test-checklist.md $(SHARE)/doc/brio-control/hardware-test-checklist.md
	install -Dm644 docs/hardware-notes.md $(SHARE)/doc/brio-control/hardware-notes.md

test:
	python3 -m unittest discover -s . -p "test_*.py" -t . -v

run-daemon:
	python3 -m briod.daemon -v --replace

run-gui:
	python3 -m brio_control.main
