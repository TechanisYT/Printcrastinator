#!/usr/bin/env bash
# Installs Printcrastinator for the current user on an Arch-based system.
# - copies the source to ~/.local/share/printcrastinator/src (or symlinks with --dev)
# - installs uv and libnotify via pacman if missing
# - installs the udev rule (asks for sudo), the systemd user unit and the autostart entry
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$HOME/.local/share/printcrastinator/src"
DEV=0
[[ "${1:-}" == "--dev" ]] && DEV=1

need_pkgs=()
command -v uv >/dev/null || need_pkgs+=(uv)
command -v notify-send >/dev/null || need_pkgs+=(libnotify)
if ((${#need_pkgs[@]})); then
  echo "Installing: ${need_pkgs[*]}"
  sudo pacman -S --needed --noconfirm "${need_pkgs[@]}"
fi

mkdir -p "$(dirname "$TARGET")"
if ((DEV)); then
  [[ -e "$TARGET" && ! -L "$TARGET" ]] && rm -rf "$TARGET"
  ln -sfn "$HERE" "$TARGET"
  echo "Linked $TARGET -> $HERE (dev mode)"
else
  rm -rf "$TARGET"
  mkdir -p "$TARGET"
  git -C "$HERE" ls-files -z | xargs -0 -I{} cp --parents {} "$TARGET"
  cp "$HERE/uv.lock" "$TARGET/" 2>/dev/null || true
  echo "Copied sources to $TARGET"
fi
(cd "$TARGET" && uv sync --frozen)

echo "Installing udev rule (sudo)"
sudo install -m 644 "$HERE/packaging/60-printcrastinator-printer.rules" /etc/udev/rules.d/
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=usbmisc

install -Dm644 "$HERE/packaging/printcrastinator.service" "$HOME/.config/systemd/user/printcrastinator.service"
install -Dm644 "$HERE/packaging/printcrastinator-show.desktop" "$HOME/.config/autostart/printcrastinator-show.desktop"
systemctl --user daemon-reload
systemctl --user enable --now printcrastinator.service

cat <<MSG

Done.
  Web UI:   http://127.0.0.1:8555   (enter Nextcloud URL, user and an app password there)
  Logs:     journalctl --user -u printcrastinator -f
  Preview:  uv run --project "$TARGET" printcrastinator preview /tmp/receipt.png
If the printer was plugged in before the udev rule, unplug and replug it once.
MSG
