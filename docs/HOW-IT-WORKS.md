# How Obour works

| What | How |
|---|---|
| Wayland mode | `waypipe --no-gpu ssh -Y host …` with `XDG_SESSION_TYPE=wayland`, `MOZ_ENABLE_WAYLAND` and `ELECTRON_OZONE_PLATFORM_HINT`. GPU buffer sharing is off unless you turn on *GPU Acceleration*, because it freezes waypipe on some hosts before any window appears. GTK and Qt 6 choose Wayland by themselves; Qt 5 apps stay on X11, because forcing them onto Wayland hangs some of them (VLC 3). |
| X11 mode | `ssh -Y host …` |
| Automatic mode | Wayland when this computer runs Wayland and both ends have waypipe; otherwise X11. The host's tools are checked once and cached in `~/.cache/obour/hosts.json`. |
| No-window fallback | After 15 seconds, a Wayland session that moved less than 400 KiB has shown no window (a window sends megabytes; a stuck launch about 150 KiB). With GPU sharing on, Obour turns it off for that host and retries Wayland; otherwise it retries with X11 (*Try X11 When Wayland Fails*). |
| Login | Key login (`BatchMode`) first. After you enter a password, it is either used once to add your public key to `~/.ssh/authorized_keys` on the host, or kept in memory and handed to `ssh` through `SSH_ASKPASS`. It is never written to disk. New host keys are accepted automatically (`StrictHostKeyChecking=accept-new`); changed host keys are still refused. |
| Stopping | The app runs in its own session on the host. A watcher ends that whole session when the SSH connection closes, because sshd doesn't signal commands that run without a terminal. |
| Self-restarting apps | Firefox and others sometimes exit and start a new copy of themselves. The connection stays open while a process of the app's session still has a window connection (Wayland or X11, checked with `ss`). Helpers without one, such as GTK's `glycin` image loaders that never exit, are stopped 4 s after the last window closes (but not in the first 30 s, so a launcher that hands over to the real app isn't cut short). |
| Sound | `ssh -R /tmp/obour-pulse-<random>.sock:$XDG_RUNTIME_DIR/pulse/native` plus `PULSE_SERVER` on the remote side. `PIPEWIRE_REMOTE` points nowhere, so apps that speak PipeWire directly (mpv) fall back to Pulse instead of playing on the host's speakers. Apps that use ALSA directly get a per-launch `ALSA_CONFIG_PATH` (the system's `alsa.conf` plus `pcm.!default { type pulse }`) when the host has the Pulse ALSA plugin. Works with PipeWire (pipewire-pulse) and PulseAudio, and only your user can use the socket (`StreamLocalBindMask=0177`). |
| Separate instances | Each app gets its own D-Bus session (`dbus-daemon --session`), so single-instance apps (Text Editor, Files, terminals) open a new window here instead of handing the request to a copy already running on the remote desktop. Apps that store passwords in the desktop keyring can't reach it for the same reason. |
| Your desktop's look | *Use My Desktop Theme* (on by default) finds the desktop from its running processes (DMS, Noctalia, Quickshell, plasmashell, gnome-shell, cinnamon, xfce4-session, lxqt-session), then `XDG_CURRENT_DESKTOP`. It reads the GTK theme, icons, fonts and color scheme from that desktop's own settings (GSettings, Cinnamon's GSettings, xfconf, `kdeglobals`, `lxqt.conf`, then `gtk-3.0/settings.ini`), plus `gtk.css` with the files it imports (Matugen colors, for example), qt5ct/qt6ct with their color schemes, `kdeglobals`, LXQt and Kvantum settings. KDE apps (Kate, Dolphin…) outside Plasma use light colors unless `[UiSettings] ColorScheme` is set, so the copy of `kdeglobals` gets one. These go to `~/.local/share/obour/look/<id>/` on the host whenever they change; the icon theme, a non-default GTK theme and the UI font's files go to `~/.local/share/{icons,themes,fonts}` once. The copy there carries a `fingerprint` file; if the host's copy doesn't match what this computer last sent (another computer or a test changed it), the wrapper prints `obour: look outdated` and the next launch sends it again. |
| Applying the look | Nothing in the host's own settings changes. The app gets `XDG_CONFIG_HOME` pointing at a temporary folder that links every entry of the host's `~/.config`, with the look's entries on top (folders like `gtk-3.0` are merged, so bookmarks stay). New files the app writes there are moved back to `~/.config` when it closes. GTK settings on Wayland come from GSettings, so `DCONF_PROFILE` adds a read-only database with locked values. Qt apps get `QT_QPA_PLATFORMTHEME` for their Qt version (checked with `ldd`): qt6ct/qt5ct, kde or lxqt, falling back to `gtk3` when the host lacks the plugin. |
| Qt apps | Qt 5 and Qt 6 need different theme plugins, so the wrapper checks the program with `ldd` (for VLC, whose Qt interface is a plugin, it checks `vlc/plugins/gui/libqt_plugin.so`). An app's *Qt Version* setting skips the check. |
| Settings layers | `~/.config/obour/settings.json` (Preferences), `hosts.json` → `settings` per host, and `overrides` per app in `launchers.json`. A missing key means "inherit"; `config.resolve` merges app → host → Preferences. |
| Light or dark | Separate from the desktop theme. Without the theme: `GTK_THEME=Adwaita[:dark]`, `ADW_DEBUG_COLOR_SCHEME` and `QT_QPA_PLATFORMTHEME=gtk3`; in dark mode, Qt 5 apps also get `QT_STYLE_OVERRIDE=Adwaita-Dark` (or `kvantum-dark`). |
| Copying files | While an app with file sharing runs, `obour share-daemon` keeps two read-only mounts at the same path on both computers. The host mounts this computer at `/tmp/obour-<user>-<id>` with `sshfs -o passive`, served over the app's SSH login by Obour's own SFTP server (`obour/sftp.py`), which shows only the files you copied. This computer mounts the host at `/tmp/obour-<user>-h<id>` with `sshfs` and the host's `sftp-server -R`. On the other side each path is a link to `/`, so a rewritten path works on both. A small Wayland clipboard client (`obour/clipboard.py`, data-control protocol) replaces copied file paths with those paths. For drags, waypipe connects to a socket of the helper instead of the compositor (`obour/wlproxy.py`); it passes every message through and rewrites only the file lists of `wl_data_offer.receive` (drops into a remote app) and `wl_data_source.send` (drags out of one). It follows messages with the signatures in `obour/wlproto.json`, made by `packaging/wayland/gen-protocols.py`. X11 apps get the same through `obour/x11proxy.py`: ssh's X11 forwarding connects to a display of the helper (`/tmp/.X11-unix/X200`…), which passes everything to the real one and rewrites only `GetProperty` replies and `ChangeProperty` requests whose type is `text/uri-list` or `x-special/gnome-copied-files` (drags use the `XdndSelection`, copy and paste `CLIPBOARD`; both work this way). It puts the real display's cookie into the handshake, since ssh has none for the helper's display. It also tells remote apps that the DRI3 extension is missing: its replies carry file descriptors, which can't cross the network, and apps like mpv would wait for them forever. The last app to close unmounts. Drags offer files also as `application/vnd.portal.filetransfer`, a key only this computer's portal can open, which GTK 4 apps prefer; the Wayland helper hides that type both ways so apps use `text/uri-list`. The helper's version must match the app's: after an update, an old `share-daemon` gives way to a new one as soon as no app uses it. |
| Cursor | `XCURSOR_THEME` and `XCURSOR_SIZE`, plus a one-time copy of the theme to the host's `~/.local/share/icons`. Qt 5 apps show the X root window's cursor, so Obour sets that with `xsetroot` on this computer. Wayland apps that support the cursor-shape protocol use your desktop's cursor directly. |
| Locale | Locale variables the host doesn't have (for example `LC_TIME=ar_EG.UTF-8`) are dropped, and `LANG` falls back to `C.UTF-8`, so apps don't fall back to ASCII. |
| Host setup | One SSH call reads `/etc/os-release` and checks for each tool; installing runs `sudo -S` with the password on stdin, never on the command line or in the log. |
| Tailscale | Only ssh's `HostName` changes (`-o HostName=<tailscale address>`), so the user, port and keys from `~/.ssh/config` still apply. The usual address is tested with a 2.5-second connection attempt, remembered for a minute. |
| App-menu entries | `Exec=obour launch <id>`: settings are read when the app starts, so entries always use the current options. The entry name follows the template in Preferences. |
| Translations | `obour/locale/<language>.po`, read directly at startup. To add a language, copy `ar.po`, translate the `msgstr` lines and add it to `LANGUAGES` in `obour/i18n.py`. |

## Files

- Saved apps: `~/.config/obour/launchers.json`
- Settings: `~/.config/obour/settings.json`; per-host options (Tailscale, GPU): `~/.config/obour/hosts.json`
- Menu entries: `~/.local/share/applications/obour-<id>.desktop`
- Icons: `~/.local/share/obour/icons/`
- Logs from app-menu launches: `~/.cache/obour/logs/`
- Backups: everything above except logs, plus `~/.ssh/known_hosts` (and the SSH key when chosen), in one `.zip` with a `manifest.json` (`obour/backup.py`). A restore rewrites the folder paths stored in the JSON files when the new computer's differ, adds missing `known_hosts` lines, saves an existing different key's replacement as `<key>.obour-backup` instead of overwriting it, and keeps the five latest pre-restore copies in `~/.local/share/obour/backups/`.
- File sharing log: `$XDG_RUNTIME_DIR/obour/share.log`
- On hosts: your look in `~/.local/share/obour/look/<id>/`; icons, themes and fonts in `~/.local/share/{icons,themes,fonts/obour}`

## More troubleshooting

- **Asked for the password every time**: leave **Don't ask again** on in the login dialog, or use the computer's ⋮ menu → *Set Up Key Login…*. If the key is copied but the host still asks, the host doesn't allow key login (`PubkeyAuthentication` in its `sshd_config`).
- **App-menu launch says the host needs a password**: menu entries can't show a login dialog. Open Obour and launch the app once from there.
- **"The host's key has changed"**: if you expected that (for example, the machine was reinstalled), run `ssh-keygen -R <host>`.
- **Qt app stays light in dark mode**: the computer's ⋮ menu → *Set Up Host…* installs the dark Qt style.
- **"Firefox is already running"**: the same profile is open on the host. Close it there, or turn off *Separate Instance* for that app.
- **Install fails with "not in the sudoers file"**: your user on the host can't use sudo; ask its administrator, or log in as root.
- **Choosing X11 by itself**: a Wayland launch that ends while starting (under 4 s) because it crashed (a crash line in its output, even with exit code 0), exited with an error, or showed no window in 15 s is opened again with X11 when *Try X11 When Wayland Fails* is on. If X11 gets past starting, `learned_protocol: "x11"` is saved in the app's entry in `launchers.json`; the same happens when the display check below sees the app use only X11. It's used unless the app sets its own Display. Login problems, a wrong command and "already running" never count. *Edit… → Try Wayland Again*, or changing the app's Display, host or command, clears it.
- **Which display an app uses**: in Wayland mode the wrapper checks (at 3, 8 and 20 s, with `ss`) whether the app's processes are connected to waypipe's socket or to the X11 display's TCP port, and prints `obour: display wayland|x11|both`; the app's row shows it.
- **mpv over Wayland**: without GPU sharing its Wayland video contexts fail, and mpv then probes the X11 ones and aborts (`Assertion '!vo->x11' failed`) while exiting with 0. Obour starts `mpv` with `--gpu-context=wayland,waylandvk` in Wayland mode (unless the command sets `--gpu-context`), which falls back to a working context.
- **GPU Acceleration was turned off for a host**: a Wayland app showed no window with it. Turning *GPU Acceleration* off and on again in Preferences gives every host another try.

## Building

```sh
packaging/appimage/build.sh    # dist/Obour-<version>-x86_64.AppImage (+ .zsync)
packaging/linux/build-deb.sh   # dist/obour_<version>_all.deb
packaging/linux/build-rpm.sh   # dist/obour-<version>-1.noarch.rpm
nix build                      # ./result (Nix)
assets/make-banner.py          # regenerates assets/banner.svg
assets/make-og.py              # regenerates docs/img/og.png, the link-preview picture
python3 -m unittest discover tests   # tests (the drag-and-drop proxy)
```

The AppImage is built in an Ubuntu 24.04 container, which sets the oldest supported glibc and GTK.
Its embedded update information (`gh-releases-zsync|Jazzmedo|Obour|latest|…`) lets Gear Lever and
AppImageUpdate fetch new releases from GitHub.
