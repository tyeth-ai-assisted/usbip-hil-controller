#!/usr/bin/env bash
# Provision a HIL host: hardware group membership + SSH authorized key.
#
# Usage (run as root on the target host):
#   bash setup-hil-host.sh <user> <pubkey-file>
#
# Example:
#   bash setup-hil-host.sh particle ~/.ssh/id_ed25519.pub
#
# What it does:
#   1. Adds <user> to gpio, i2c, plugdev (SPI), dialout (UART), video groups
#   2. Creates any groups that don't exist yet
#   3. Installs the public key into ~<user>/.ssh/authorized_keys
#
# Group → device mapping (Linux):
#   gpio    → /dev/gpiochip*   (GPIO, bit-bang 1-wire)
#   i2c     → /dev/i2c-*
#   plugdev → /dev/spidev*     (on Particle Tachyon / many Linux SBCs)
#   dialout → /dev/ttyS*, /dev/ttyUSB*, /dev/ttyACM*  (UART, USB-serial)
#   video   → /dev/video*      (camera capture)
#
# Run `newgrp <group>` or log out/in after this script for changes to take effect.

set -euo pipefail

HIL_USER="${1:-}"
PUBKEY_FILE="${2:-}"

if [[ -z "$HIL_USER" || -z "$PUBKEY_FILE" ]]; then
    echo "Usage: $0 <user> <pubkey-file>" >&2
    exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Must be run as root." >&2
    exit 1
fi

if ! id "$HIL_USER" &>/dev/null; then
    echo "User '$HIL_USER' does not exist." >&2
    exit 1
fi

if [[ ! -f "$PUBKEY_FILE" ]]; then
    echo "Public key file '$PUBKEY_FILE' not found." >&2
    exit 1
fi

GROUPS_NEEDED=(gpio i2c plugdev dialout video)

for grp in "${GROUPS_NEEDED[@]}"; do
    if ! getent group "$grp" &>/dev/null; then
        echo "Creating group: $grp"
        groupadd "$grp"
    fi
    if id -nG "$HIL_USER" | grep -qw "$grp"; then
        echo "  $HIL_USER already in $grp"
    else
        echo "  Adding $HIL_USER to $grp"
        usermod -aG "$grp" "$HIL_USER"
    fi
done

# udev rules — ensure non-root access to hardware devices
# SPI: on Tachyon (and many Linux SBCs) spidev is root-only by default.
# Adafruit_Wippersnapper_Python README mandates this rule for Tachyon users.
UDEV_SPI=/etc/udev/rules.d/99-spi.rules
UDEV_SPI_RULE='SUBSYSTEM=="spidev", GROUP="plugdev", MODE="0660"'
if [[ -f "$UDEV_SPI" ]] && grep -qF "$UDEV_SPI_RULE" "$UDEV_SPI"; then
    echo "  udev SPI rule already present"
else
    echo "$UDEV_SPI_RULE" > "$UDEV_SPI"
    echo "  udev SPI rule written to $UDEV_SPI"
fi
udevadm trigger
echo "  udevadm trigger done"

# Install SSH authorized key
HOME_DIR="$(getent passwd "$HIL_USER" | cut -d: -f6)"
SSH_DIR="$HOME_DIR/.ssh"
AUTH_KEYS="$SSH_DIR/authorized_keys"

mkdir -p "$SSH_DIR"
chmod 700 "$SSH_DIR"
chown "$HIL_USER:$HIL_USER" "$SSH_DIR"

PUBKEY="$(cat "$PUBKEY_FILE")"
if grep -qF "$PUBKEY" "$AUTH_KEYS" 2>/dev/null; then
    echo "  Key already present in $AUTH_KEYS"
else
    echo "$PUBKEY" >> "$AUTH_KEYS"
    echo "  Key installed in $AUTH_KEYS"
fi
chmod 600 "$AUTH_KEYS"
chown "$HIL_USER:$HIL_USER" "$AUTH_KEYS"

# usbip — passwordless sudo + kernel modules for per-phase flashing.
# arduino-ws jobs with flash_mode=usbip have the controller (client) attach a
# DUT's USB port that physically lives on a server host, then flash it. Both
# sides call usbip via sudo without a TTY, so a no-prompt sudoers drop-in is
# required. We provision both roles (harmless if a host only acts as one):
#   server (USB-host, e.g. rpi-displays): usbipd + `usbip bind/unbind`
#   client (controller, e.g. tachyon):    vhci-hcd + `usbip attach/detach/port`
USBIP_BIN="$(command -v usbip || echo /usr/sbin/usbip)"
SUDOERS_USBIP=/etc/sudoers.d/hil-usbip
MODPROBE_BIN="$(command -v modprobe || echo /usr/sbin/modprobe)"
cat > "$SUDOERS_USBIP" <<EOF
# Managed by setup-hil-host.sh — passwordless usbip for HIL per-phase flashing.
$HIL_USER ALL=(root) NOPASSWD: $USBIP_BIN, $MODPROBE_BIN vhci-hcd, $MODPROBE_BIN usbip-host
EOF
chmod 440 "$SUDOERS_USBIP"
if visudo -cf "$SUDOERS_USBIP" >/dev/null 2>&1; then
    echo "  usbip sudoers drop-in written to $SUDOERS_USBIP"
else
    echo "  WARNING: $SUDOERS_USBIP failed visudo check — removing" >&2
    rm -f "$SUDOERS_USBIP"
fi

# Load the usbip kernel modules now and persist them across reboots.
modprobe vhci-hcd 2>/dev/null && echo "  vhci-hcd loaded (usbip client)" || true
modprobe usbip-host 2>/dev/null && echo "  usbip-host loaded (usbip server)" || true
echo -e "vhci-hcd\nusbip-host" > /etc/modules-load.d/hil-usbip.conf
echo "  persisted usbip modules in /etc/modules-load.d/hil-usbip.conf"

# Start usbipd on USB-server hosts if the unit is available.
if systemctl list-unit-files 2>/dev/null | grep -q '^usbipd\.service'; then
    systemctl enable --now usbipd 2>/dev/null \
        && echo "  usbipd.service enabled+started" \
        || echo "  WARNING: could not enable usbipd.service" >&2
else
    echo "  usbipd.service not found (install 'usbip'/'linux-tools' on USB-server hosts)"
fi

# NOTE: keep the blanket vendor/usbip-autoattach autobind rule OFF — per-phase
# flashing binds only the single leased busid, on demand.

echo ""
echo "Done. Current groups for $HIL_USER:"
id "$HIL_USER"
echo ""
echo "Log out and back in (or run 'newgrp <group>') for group changes to take effect."
