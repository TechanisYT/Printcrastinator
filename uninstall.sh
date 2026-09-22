#!/usr/bin/env bash
set -euo pipefail
systemctl --user disable --now printcrastinator.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/printcrastinator.service" "$HOME/.config/autostart/printcrastinator-show.desktop"
systemctl --user daemon-reload
sudo rm -f /etc/udev/rules.d/99-printcrastinator-printer.rules
rm -rf "$HOME/.local/share/printcrastinator"
echo "Removed. Config (~/.config/printcrastinator) and state (~/.local/state/printcrastinator) were kept."
