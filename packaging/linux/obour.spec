# Built by packaging/linux/build-rpm.sh; the version comes from obour/__init__.py.
Name:           obour
Version:        %{obour_version}
Release:        1%{?dist}
Summary:        Run apps from remote Linux machines as local windows
License:        MIT
URL:            https://github.com/Jazzmedo/Obour
BuildArch:      noarch

Requires:       python3 >= 3.11
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita >= 1.5
Requires:       openssh-clients
Recommends:     waypipe
Recommends:     xsetroot
Recommends:     libnotify
Recommends:     fuse-sshfs
Recommends:     dconf

%description
Obour opens individual apps from other Linux computers over SSH so they appear
as normal windows on your desktop, with sound. Wayland apps are forwarded with
waypipe and X11 apps with SSH X11 forwarding.

%description -l ar
يفتح عبور تطبيقات من حواسيب لينكس أخرى عبر SSH لتظهر كنوافذ عادية على سطح
مكتبك، مع الصوت.

%install
sh %{obour_src}/packaging/linux/stage.sh %{obour_src} %{buildroot}

%files
%{_bindir}/obour
%{_datadir}/obour/
%{_datadir}/applications/io.github.Jazzmedo.Obour.desktop
%{_datadir}/icons/hicolor/scalable/apps/io.github.Jazzmedo.Obour.svg
%{_datadir}/metainfo/io.github.Jazzmedo.Obour.metainfo.xml
%{_mandir}/man1/obour.1*
%doc %{_datadir}/doc/obour/
