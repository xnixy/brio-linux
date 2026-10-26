Name:           brio-control
Version:        0.1.0
Release:        1%{?dist}
Summary:        Logitech BRIO controls for Linux (HDR, FOV, zoom, profiles)
License:        GPL-3.0-or-later
URL:            https://example.invalid/brio-control
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
BuildRequires:  make
BuildRequires:  systemd-rpm-macros
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       libgudev
Requires:       gstreamer1-plugins-base
Requires:       gstreamer1-plugins-good
Requires:       gstreamer1-plugin-gtk4

%description
A native GTK4/libadwaita app and a user daemon (briod) that expose every
setting Logitech G HUB offers for the BRIO webcam, including HDR, the
90/78/65 degree field of view, 4K preview, presets and per-app profiles.

%prep
%autosetup

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files briod brio_control
make install-data DESTDIR=%{buildroot} PREFIX=%{_prefix}

%check
%{python3} -m unittest discover -s . -p "test_*.py" -t .

%post
%systemd_user_post briod.service

%preun
%systemd_user_preun briod.service

%files -f %{pyproject_files}
%{_bindir}/briod
%{_bindir}/brio-ctl
%{_bindir}/brio-probe
%{_bindir}/brio-import-ghub
%{_bindir}/brio-control
%{_userunitdir}/briod.service
%{_datadir}/dbus-1/services/io.github.BrioControl.Daemon.service
%{_datadir}/applications/io.github.BrioControl.desktop
%{_datadir}/icons/hicolor/scalable/apps/io.github.BrioControl.svg
%{_datadir}/gnome-shell/extensions/brio-control-focus@briocontrol.github.io/
%{_datadir}/kwin/scripts/brio-control-focus/
%doc %{_datadir}/doc/brio-control/

%changelog
* Mon Oct 26 2026 BRIO Control contributors - 0.1.0-1
- Initial package
