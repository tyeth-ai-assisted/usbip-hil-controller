# Vendored repositories

These are git submodules. They are the upstream sources of truth for
the three external pieces the controller integrates with. Source code
in `src/hil_controller/` may *parse* or *invoke* files here but never
edits them in place — fixes go upstream via a normal PR to the
relevant submodule, then this repo bumps the pinned commit.

| Path                            | Origin                                                                                                                                   | Upstream / mirror                                                                                          | Tracked branch        | Role |
|---------------------------------|------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------|-----------------------|------|
| `vendor/protomq`                | [`tyeth-ai-assisted/protomq`](https://github.com/tyeth-ai-assisted/protomq)                                                              | dual-push to [`tyeth/protomq`](https://github.com/tyeth/protomq)                                            | `displays-v2-testing` | ProtoMQ broker + JS frontend + per-board demo scripts in `scripts/*.json`. JSONs are the current source of truth for board↔display wiring; the controller's topology importer reads them. |
| `vendor/usbip-autoattach`       | [`tyeth-ai-assisted/usbip-autoattach`](https://github.com/tyeth-ai-assisted/usbip-autoattach)                                            | —                                                                                                          | `main`                | Server-side udev autobind rule + client-side reconciliation loop that survives device resets without manual reattach. Used by the controller's USB-IP adapter. |
| `vendor/hil-detection`          | [`tyeth-ai-assisted/hil-detection`](https://github.com/tyeth-ai-assisted/hil-detection)                                                  | —                                                                                                          | `main`                | The existing HIL bench scripts and pytest suites (`tests/test_circuitpython.py`, `test_micropython.py`, `test_wippersnapper.py`), plus `references/hardware.md` (the hand-maintained topology map) and `usb_hub.py` / `pico_hil_flash.sh`. |
| `vendor/wippersnapper-arduino`  | [`tyeth-ai-assisted/adafruit-Adafruit_Wippersnapper_Arduino`](https://github.com/tyeth-ai-assisted/adafruit-Adafruit_Wippersnapper_Arduino) | `upstream` remote → [`adafruit/Adafruit_Wippersnapper_Arduino`](https://github.com/adafruit/Adafruit_Wippersnapper_Arduino) (fetch only) | `migrate-api-v2`      | Microcontroller-side WS firmware (ESP32/SAMD51/RP2040). The `migrate-api-v2` branch is the active V2-line; `examples/secrets-examples/secrets-wifi.json` was the source for the bench secret template in this repo's `examples/`. |

A fifth submodule for the **Python** Wippersnapper variant (SBC
client, currently private/unreleased) is intentionally absent. The
existing `vendor/protomq/scripts/*.json` carry enough wiring info for
both variants because the per-board demos cover both microcontroller
and Pi targets. When the Python repo opens up, add it here on the
`displays-v2` branch and re-run `git submodule update --init
--recursive` to pull in its protomq + wippersnapper-protobufs
sub-submodules.

## First-time setup

After cloning this repo:

```bash
git submodule update --init --recursive
./scripts/setup-submodules.sh
```

The setup script wires per-submodule remote config that `.gitmodules`
can't express:

- **`vendor/protomq`** — dual-push: a `git push` from inside the
  submodule writes to **both** `tyeth-ai-assisted/protomq` and
  `tyeth/protomq`. The user wants the upstream personal repo to stay
  in lockstep with the ai-assisted fork.
- **`vendor/wippersnapper-arduino`** — fetch-only `upstream` remote
  pointing at `adafruit/Adafruit_Wippersnapper_Arduino`. Upstreaming
  goes via a normal PR, not a direct push.

Both configs live in the submodule's local `.git/config` (not
version-controlled), so each fresh clone needs to run the script
once. It's idempotent — safe to re-run.

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
