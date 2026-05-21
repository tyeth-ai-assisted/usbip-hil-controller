# Vendored repositories

These are git submodules. They are the upstream sources of truth for
the three external pieces the controller integrates with. Source code
in `src/hil_controller/` may *parse* or *invoke* files here but never
edits them in place — fixes go upstream via a normal PR to the
relevant submodule, then this repo bumps the pinned commit.

| Path                       | Upstream                                                                                       | Tracked branch          | Role |
|----------------------------|------------------------------------------------------------------------------------------------|-------------------------|------|
| `vendor/protomq`           | [`tyeth-ai-assisted/protomq`](https://github.com/tyeth-ai-assisted/protomq) (dual-push to [`tyeth/protomq`](https://github.com/tyeth/protomq)) | `displays-v2-testing`   | ProtoMQ broker + JS frontend + per-board demo scripts in `scripts/*.json`. The JSONs are the current source of truth for board↔display wiring; the controller's topology importer reads them. |
| `vendor/usbip-autoattach`  | [`tyeth-ai-assisted/usbip-autoattach`](https://github.com/tyeth-ai-assisted/usbip-autoattach) | `main`                  | Server-side udev autobind rule + client-side reconciliation loop that survives device resets without manual reattach. Used by the controller's USB-IP adapter. |
| `vendor/hil-detection`     | [`tyeth-ai-assisted/hil-detection`](https://github.com/tyeth-ai-assisted/hil-detection)       | `main`                  | The existing HIL bench scripts and pytest suites (`tests/test_circuitpython.py`, `test_micropython.py`, `test_wippersnapper.py`), plus `references/hardware.md` (the hand-maintained topology map) and `usb_hub.py` / `pico_hil_flash.sh`. |

## First-time setup

After cloning this repo:

```bash
git submodule update --init --recursive
./scripts/setup-submodules.sh
```

The setup script configures dual-push on `vendor/protomq` so any
commit pushed from the submodule goes to **both** the `tyeth-ai-assisted`
fork (origin fetch) and the `tyeth/protomq` upstream — the user wants
the upstream personal repo to stay in sync. That config lives in the
submodule's local `.git/config` (not version-controlled), so each
clone needs to run the script once.

## Updating submodule pins

To pull the latest commit on each submodule's tracked branch:

```bash
git submodule update --remote --recursive
git add vendor/
git commit -m "vendor: bump submodule pins"
```

To work on a submodule and push the change:

```bash
cd vendor/protomq
git checkout displays-v2-testing       # submodules are checked out detached by default
# ... make edits, commit ...
git push                                # goes to BOTH remotes for vendor/protomq
cd ../..
git add vendor/protomq                  # bump the pin in this repo
git commit -m "vendor: bump protomq pin"
```
