# 07 System integration (EndeavourOS / KDE)

## Printer device access

Printer USB ID `0456:0808`, kernel `usblp` creates `/dev/usb/lp0` (root:lp 0660).

Preferred: udev rule `packaging/99-printcrastinator-printer.rules`

```
SUBSYSTEM=="usbmisc", KERNEL=="lp*", ATTRS{idVendor}=="0456", ATTRS{idProduct}=="0808", TAG+="uaccess"
```
Install with `sudo cp packaging/99-printcrastinator-printer.rules /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger`.
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

## Terminal window at login (`packaging/printcrastinator-show.desktop`)

Copied to `~/.config/autostart/`. Runs `alacritty --title Printcrastinator -e uv run --project %h/Projects/Printcrastinator printcrastinator show`.
The desktop notification is sent by the daemon itself when the daily check fires. Either can be
disabled in Settings (`screen.terminal`, `screen.notify`).

## Install checklist

1. `sudo pacman -S uv libnotify` (alacritty already present)
2. `uv sync` in the project
3. udev rule (above), replug the printer
4. `uv run printcrastinator test-print`, pick density and feed-after-mm in the web UI
5. `systemctl --user enable --now printcrastinator.service`, open http://127.0.0.1:8555,
   enter Nextcloud URL, user and app password, test connection
6. copy the autostart desktop file
