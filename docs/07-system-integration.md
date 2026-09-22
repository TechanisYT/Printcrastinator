# 07 System integration (EndeavourOS / KDE)

## Printer device access

Printer USB ID `0456:0808`, kernel `usblp` creates `/dev/usb/lp0` (root:lp 0660).

Preferred: udev rule `packaging/60-printcrastinator-printer.rules`

```
SUBSYSTEM=="usbmisc", KERNEL=="lp*", ATTRS{idVendor}=="0456", ATTRS{idProduct}=="0808", TAG+="uaccess"
```
Install with `sudo cp packaging/60-printcrastinator-printer.rules /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger`.
`uaccess` grants the active seat's user an ACL on the node.

Fallback: `sudo usermod -aG lp $USER` (needs re-login).

## Daemon: systemd user unit (`packaging/printcrastinator.service`)

```
[Unit]
Description=Printcrastinator daemon and web UI
[Service]
WorkingDirectory=%h/Projects/Printcrastinator
ExecStart=/usr/bin/uv run --frozen printcrastinator serve
Restart=on-failure
RestartSec=5
[Install]
WantedBy=default.target
```
No `network-online.target` (does not exist in the user manager); the poller retries with backoff.
Enable: `systemctl --user enable --now printcrastinator.service`.

## Terminal window and notification

Both are triggered by the daemon itself whenever the daily check fires (daemon start, poll,
resume, unlock): it sends the notification and launches
`alacritty --title Printcrastinator -e python -m printcrastinator show`. The user service has
`DISPLAY`/`WAYLAND_DISPLAY` because KDE imports them into the systemd user environment.
Either can be disabled in Settings (`screen.terminal`, `screen.notify`).

`packaging/printcrastinator-show.desktop` is an optional autostart entry that opens the window
at every login regardless of whether today was already printed; not installed by default.

## Install checklist

1. `sudo pacman -S uv libnotify` (alacritty already present)
2. `uv sync` in the project
3. udev rule (above), replug the printer
4. `uv run printcrastinator test-print`, pick density and feed-after-mm in the web UI
5. `systemctl --user enable --now printcrastinator.service`, open http://127.0.0.1:8555,
   enter Nextcloud URL, user and app password, test connection
6. copy the autostart desktop file
