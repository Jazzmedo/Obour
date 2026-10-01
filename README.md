<p align="center">
  <img src="assets/banner.svg" alt="Obour: open apps from your other Linux machines as native windows, with drag and drop, copy and paste of files, your theme and sound" width="100%">
</p>

<p align="center">
  <a href="https://github.com/Jazzmedo/Obour/releases/latest"><img src="https://img.shields.io/github/v/release/Jazzmedo/Obour?style=flat-square&color=7c3aed&label=release" alt="Latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/Jazzmedo/Obour?style=flat-square&color=db2777" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/Linux-Wayland%20%7C%20X11-4f46e5?style=flat-square&logo=linux&logoColor=white" alt="Linux: Wayland and X11">
  <img src="https://img.shields.io/badge/GTK%204-libadwaita-0ea5e9?style=flat-square&logo=gnome&logoColor=white" alt="GTK 4 and libadwaita">
</p>

<p align="center">
  <b>English</b> · <a href="README.ar.md">العربية</a>
</p>

> [!WARNING]
> **Obour is in alpha.** So far it has only been tested with **Ubuntu Server 26.04** as the
> remote computer and **Hyprland** as the desktop. Expect bugs, especially on other
> distributions and desktops.
>
> **We need your testing and contributions to fix the app.** If something doesn't work,
> [open an issue](https://github.com/Jazzmedo/Obour/issues/new/choose) with your setup and
> the log, or send a pull request (see [CONTRIBUTING.md](CONTRIBUTING.md)).

**Obour** (عبور, "crossing") opens single apps from another Linux computer as normal windows
on your desktop. They move, resize and tile like local apps, and their sound plays on your
speakers. Pick an app, press play, done.

<!-- Screenshot: add assets/screenshot.png and uncomment
<p align="center"><img src="assets/screenshot.png" alt="Obour's main window" width="720"></p>
-->

## Features

- **Wayland and X11 apps**: Wayland apps go through `waypipe`, older X11-only apps through SSH X11 forwarding. Each app's row shows which one it's using, and when an app only works with X11, Obour notices and remembers it for that app.
- **Sound with zero setup**: your local PipeWire or PulseAudio is forwarded over the same SSH connection, for apps that use PulseAudio, PipeWire or ALSA.
- **App browser**: connect to a computer, see its installed apps with their real icons, add them in one click.
- **Looks like your desktop**: remote apps get your colors, icons, fonts and cursor, GTK, Qt and KDE apps (Kate, Dolphin…) alike. Obour detects your desktop (DankMaterialShell, Noctalia, Quickshell, KDE Plasma, GNOME, Cinnamon, Xfce, LXQt) and installs on the host only what that desktop needs.
- **Drag, copy and paste files**: drag a file from your file manager into a remote app, or copy it here and paste it there, and the other way round. Works with Wayland and X11 apps. You choose per app, per host, or ask every time.
- **Host setup**: checks each computer for missing tools and installs them with `apt`, `dnf` or `pacman` when you agree.
- **Smart fallbacks**: if a Wayland app crashes or shows no window, Obour retries without GPU sharing, then with X11, and tells you why.
- **Closes cleanly**: close an app's window and Obour also ends the helpers it left running on the host.
- **Easy logins**: tries your SSH key first; after one password it can set up key login for you.
- **Tailscale**: a second address per computer, used when the usual one is unreachable, or always.
- **App menu, your way**: add any app to your system menu from its ⋮ menu, with a name template you choose.
- **Backup and restore**: one `.zip` with all your apps, hosts, settings, icons and app-menu entries; restore it on a new computer and everything is back.
- **English and Arabic**, with a right-to-left layout in Arabic.

## Install

<details open>
<summary><b>AppImage</b> (any distribution; updates through Gear Lever)</summary>

Download `Obour-<version>-x86_64.AppImage` from the [latest release](https://github.com/Jazzmedo/Obour/releases/latest), then either:

- open it with [Gear Lever](https://flathub.org/apps/it.mijorus.gearlever), which adds it to your app menu and keeps it updated from this repository, or
- run it directly:

  ```sh
  chmod +x Obour-*-x86_64.AppImage
  ./Obour-*-x86_64.AppImage
  ```

It bundles Python, GTK 4 and libadwaita. You only need `openssh-client`, plus `waypipe` for Wayland apps.
Runs on Ubuntu 24.04+, Debian 13+, Fedora 40+, Arch and similar.
</details>

<details>
<summary><b>Debian / Ubuntu</b> (.deb)</summary>

```sh
sudo apt install ./obour_<version>_all.deb
```

Needs Debian 13+ or Ubuntu 24.04+ (libadwaita 1.5 or newer).
</details>

<details>
<summary><b>Fedora / openSUSE</b> (.rpm)</summary>

```sh
sudo dnf install ./obour-<version>-1.noarch.rpm
```
</details>

<details>
<summary><b>NixOS / Nix</b> (flake)</summary>

Try it without installing:

```sh
nix run github:Jazzmedo/Obour
```

Install it on NixOS by adding the flake to `/etc/nixos/flake.nix`:

```nix
{
  inputs.obour.url = "github:Jazzmedo/Obour";

  outputs = { nixpkgs, obour, ... }: {
    nixosConfigurations.my-pc = nixpkgs.lib.nixosSystem {
      modules = [ ./configuration.nix obour.nixosModules.default ];
    };
  };
}
```

Or with Home Manager: `home.packages = [ inputs.obour.packages.${pkgs.system}.default ];`.
Update with `nix flake update obour`.
</details>

<details>
<summary><b>From source</b></summary>

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 openssh-client waypipe
git clone https://github.com/Jazzmedo/Obour.git && cd Obour
./install.sh               # adds `obour` to ~/.local/bin and Obour to your app menu
./install.sh --uninstall   # removes it (your saved apps stay in ~/.config/obour)
```

Or run it in place with `bin/obour`.
</details>

## The remote computer

It only needs an SSH server (with `X11Forwarding yes` for X11 apps).
Obour checks for everything else and offers to install it:

| Needed for | Ubuntu / Debian | Fedora | Arch |
|---|---|---|---|
| Wayland apps | `waypipe` | `waypipe` | `waypipe` |
| X11 apps | `xauth` | `xorg-x11-xauth` | `xorg-xauth` |
| Sound | `libpulse0` | `pulseaudio-libs` | `libpulse` |
| Sound from apps that use ALSA directly | `libasound2-plugins` | `alsa-plugins-pulseaudio` | `alsa-plugins` |
| Separate app instances | `dbus` | `dbus-daemon` | `dbus` |
| The app browser | `python3` | `python3` | `python` |
| Dark mode for Qt 5 apps | `adwaita-qt` | `kvantum-qt5` | `kvantum-qt5` |
| Light/dark for Qt 6 apps | `qt6-gtk-platformtheme` | `qt6-qtbase-gui` | `qt6-base` |
| Copy and paste files | `sshfs` | `fuse-sshfs` | `sshfs` |

Your desktop's Qt settings need one more package, and only that one is offered:
`qt6ct`/`qt5ct` (DankMaterialShell, Noctalia, Hyprland, niri, sway…),
`plasma-integration` and Breeze (KDE Plasma), or `lxqt-qtplugin` (LXQt).
GNOME, Cinnamon and Xfce use the rows above.

## Copying files between computers

Copy a file in any app (Thunar, Dolphin, Files…) and paste it in an app on the other
computer, or drag it from one computer's window into the other's (for example from
Dolphin into a remote mpv). It works like a local copy, with a few differences:

- Files move at network speed, so large files take a while.
- **Cut** doesn't move files between computers; use **Copy**. (Cut and paste on the same computer is unchanged.)
- Copying and dragging work the same whether the app uses Wayland or X11: Obour sits between the app and your screen and changes only the file paths.
- Pasting remote files into apps on *this* computer works best on desktops that let apps manage the clipboard (KDE Plasma, Hyprland, sway, niri…). On GNOME, files copied in remote apps are still rewritten on their way, so it works there too for the usual copy and paste.
- Pasting a file into a text field gives a long path (`/tmp/obour-…/home/you/file.txt`).
- While sharing is on, the host (and its administrator) can read the files you copied, read-only. It sees nothing else on this computer.

*Copy, Paste and Drag Files* can be *Always Allow*, *Ask Every Time* or *Deny*, like every other
setting (see below). This computer needs `sshfs` to paste files copied on the host.

## Settings for each host and app

Every setting in Preferences can also be changed for all apps of one host (its ⋮ menu →
*Host Settings…*) and for a single app (*Edit…*). Each row there starts at **Inherit** and
says where the value comes from. The priority is:

**this app → its host → Preferences**

For example, set *Light or Dark Style* to *Dark* for a host, and one of its apps back to
*Match This Computer*. Environment variables add up: an app's `KEY=value` replaces the
same key from its host or Preferences and keeps the others.

*Light or Dark Style* and *Use My Desktop Theme* are separate: turn the theme off and remote
apps are plain Adwaita (GTK) or Adwaita-Dark (Qt 5) in your light or dark mode.

## Using it

1. Press **+** → *Add Apps from a Computer…* and enter `user@host` (or an alias from `~/.ssh/config`).
2. Add the apps you want. Obour checks the computer and offers to install anything missing.
3. Press ▶ next to an app. Press ■ to close it, both here and on the remote computer.

| Shortcut | Action |
|---|---|
| <kbd>Ctrl</kbd>+<kbd>N</kbd> | Add apps from a computer |
| <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>N</kbd> | Add an app by its command |
| <kbd>Ctrl</kbd>+<kbd>,</kbd> | Preferences (style, compression, language…) |
| <kbd>Ctrl</kbd>+<kbd>L</kbd> | Show or hide the log |

From a terminal: `obour launch <id>` starts a saved app, and `obour list` shows the ids.

### Backup and restore

Main menu → *Back Up…* saves everything Obour keeps to one `.zip`: your apps (and which ones are in your app menu), hosts, settings, icons, what Obour learned about each host, and your SSH `known_hosts`. You can include your SSH key too, so a new computer logs in to your hosts right away; keep that file private. Passwords are never saved.

Main menu → *Restore From a Backup…* (also on the start screen of a fresh install) puts it all back and makes the app-menu entries again. Your current setup is saved first in `~/.local/share/obour/backups/`. An SSH key never replaces one you already have.

From a terminal: `obour backup obour.zip [--with-ssh-key]` and `obour restore obour.zip`.

## Troubleshooting

- **Wayland or X11?** You don't have to pick or test each app. Leave its Display on *Inherit*: Obour tries Wayland, and if the app crashes, stops right away or shows no window, it opens it with X11 and remembers that for the app (the row then says *X11 (chosen automatically)*; *Edit… → Try Wayland Again* undoes it). Apps that use X11 anyway, like VLC 3, are remembered the same way. While an app runs, its row shows what it really uses.
- **No window appears**: open the log (<kbd>Ctrl</kbd>+<kbd>L</kbd>); it explains what went wrong. For X11-only apps, set the app's Display to *X11 only*.
- **Wayland apps never open and the remote computer has `waypipe` processes stuck in state `D`**: its graphics driver froze while sharing GPU memory. Keep *GPU Acceleration* off (the default) and reboot that computer.
- **Asked for a password although your key is there**: the login dialog says why the key was refused. Often the key belongs to another user (write `user@host`), or `~/.ssh` on the host is writable by others.
- **"The host refused X11 forwarding"**: set `X11Forwarding yes` in the host's `/etc/ssh/sshd_config` and install `xauth` there.
- **Firefox says "closed unexpectedly while starting"**: click **Open**. Earlier starts were cut off; it won't repeat.
- **Remote apps don't look like your desktop**: the host's ⋮ menu → *Set Up Host…* lists what your desktop needs there. *Use My Desktop Theme* must be on (Preferences, or the host's or app's settings).
- **Pasting a file says "No such file or directory"**: sharing wasn't active for that host. Obour shows why in its log; usually `sshfs` is missing on the host (⋮ → *Set Up Host…*).
- **A dropped file doesn't open ("No such file", or mpv closes)**: file sharing is off for that app, or `sshfs` is missing on the host. Allow *Copy, Paste and Drag Files* and check ⋮ → *Set Up Host…*.
- **The log says an app crashed although it "closed with exit code 0"**: some apps (mpv) exit with 0 after a crash; Obour reports the crash line from their output instead.
- **A Qt app stays light, or doesn't get your colors**: *Set Up Host…* offers what your desktop needs (`qt5ct` for VLC on most tiling desktops). If Obour guesses the Qt version wrong for an app, set *Qt Version* in its Edit dialog.

More in [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md).

## Contributing

Bug reports, translations and fixes are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © 7anafi
