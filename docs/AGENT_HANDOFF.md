# Agent handoff

Working notes for the next Claude Code agent picking up this project.
Read this **before** asking the user "what is this project?" — most of
the orientation is already in `docs/ARCHITECTURE.md` and the rest is
here.

**Session start:** read `.claude/memory/MEMORY.md` and the files it
indexes for current project state, conventions, and user preferences.
Memories live in the repo (`.claude/memory/`), not in `~/.claude/`.

## What this is

A controller that brokers GitHub-CI hardware-in-the-loop test requests
out to a fleet of Raspberry Pi HIL hosts. CI calls a long-poll HTTP
API; the controller routes work to whichever Pi owns the target
device, runs the test there over SSH, returns pass/fail + artifacts.

## Current state checklist

Done:

- [x] `docs/ARCHITECTURE.md` — full design v0.1+, milestones updated
      with [done]/[partial]/[open] status.
- [x] Four submodules under `vendor/` (see `vendor/README.md`).
- [x] `scripts/setup-submodules.sh` — idempotent post-clone setup.
- [x] `examples/` — caller-side templates. `hil-call.sh` is the
      reference long-poll script.
- [x] `.github/workflows/example-hil-call.yml` — `workflow_dispatch`
      demo covering both auth paths.
- [x] `.github/workflows/ws-python-ci.yml` — reusable workflow for
      Wippersnapper-Python (SBC, `wippersnapper-python` pool,
      `git-clone-and-run`, default `-m eink_large`).
- [x] `examples/ws-python-caller.yml` — caller template.
- [x] **M0** — `pyproject.toml`, FastAPI app, `/healthz`, `/readyz`,
      `hil-controller-ci.yml` CI (lint + pytest).
- [x] **M1** — `POST /v1/jobs`, `GET /v1/jobs/{id}`, long-poll
      `GET /v1/jobs/{id}/wait`, cancel; SQLite jobs + events schema;
      asyncio scheduler + EventBus; fake adapter worker. 23 tests.
- [x] **M1.5** — hosts/devices/auxes/connections/audit_log DB tables;
      topology seeder from `topology.yaml`; `GET /v1/hosts`, `/v1/devices`,
      `/v1/aux`, `/v1/topology`, `POST /v1/topology/resolve`.
- [x] **M2 partial** — `Principal` dataclass; `require_auth` returns
      `Principal`; pool/profile/capabilities gating on job submit (403);
      audit log on submit/cancel/auth-fail; `mint-token.py` extended.
      OIDC and policy file not yet implemented.
- [x] **M3** — `SSHTransport` (asyncssh, key auth).
      `RealHostRegistry` loads `topology.yaml` and returns SSH adapters.
- [x] **M4.5** — `GitDeployAdapter` (clone → setup → run → cleanup).
      Wired to SBC jobs via `git-clone-and-run` script name.
- [x] `deploy/topology.example.yaml`, systemd unit,
      `deploy/controller.env.example`.
- [x] `LocalTransport` (`hosts/local.py`) — asyncio subprocess transport
      for localhost SBC jobs; topology `kind: local` routes to it.
      `source.pat` injects a GH PAT into the HTTPS clone URL.
- [x] Log streaming — `GitDeployAdapter` stores `_run_stdout/_run_stderr/
      _deploy_stdout/_deploy_stderr`; `JobWorker` emits them as `log` kind
      events on the long-poll stream after deploy and run phases.
      `Scheduler` now wired to `RealHostRegistry` in `main.py` (was always
      `_FakeAdapter` before regardless of topology).
- [x] **M5 partial** — `ProtoMQObserver` (`adapters/protomq_observer.py`):
      HTTP script activation, MQTT `#` wildcard subscription, log event
      forwarding, completed-steps summary on teardown. `aiomqtt>=2.0`.
      Configured via `params.protomq.{broker_host,mqtt_port,api_port,script}`.
- [x] `examples/wippersnapper-python/job.json` — ready-to-use job body
      with `params.protomq` block.
- [x] `scripts/submit-wipper-test.sh` — GH PAT + ref substitution via jq,
      calls `hil-call.sh`.
- [x] **M2.5 partial** — `JobRequest.secrets` (flat `dict[str, str]`);
      `GitDeployAdapter` materialises as env vars / `secrets.json` / `.env`
      per `params.secrets_format`; `JobWorker` purges values to `"***"` in
      DB on `finally` (no plaintext at rest).
- [x] **M6** — USB identity: multi-VID/PID per device, hub-port path,
      `device_leases` with atomic acquire (exclusive_device vs
      exclusive_hub), passive USB-ID learn during exclusive jobs, and
      `UsbFingerprintAdapter` for active depower/repower capture. REST:
      `/v1/devices/{id}/usb-ids`, `/v1/devices/lookup-by-usb`,
      `/v1/devices/{id}/learn-usb`, `/v1/leases`. HTMX list editor +
      Learn-USB button on the devices form. See **"USB-identity wiring"**
      section below for production hookup. 56 new tests across PR1–PR5.
- [x] 275 tests pass.

Not done:

- [ ] **M2 remainder** — GitHub OIDC verifier, policy file.
- [ ] **M2.5 remainder** — named secret profiles YAML (`bench-protomq` /
      `live-io-test` / `live-io-prod`); `${env:...}` server-side resolver;
      nested secrets.json values. Core materialisation (flat secrets, purge,
      env/json/dotenv formats) is done — see Done list.
- [ ] **M3.5** — MCU adapter chain (serial capture, esptool, MCP23017).
- [ ] **M4** — USB-IP, solenoid-hub reset, uf2-msc / picotool flashers,
      hardcoded-password cleanup (OQ8).
- [ ] **M5 remainder** — camera capture; artifact storage; Prometheus
      metrics; `raw-firmware-smoke` built-in; `live-io-test`/`live-io-prod`
      profiles; protobuf decoding for MQTT messages;
      `GET /v1/jobs/{id}/logs` non-blocking endpoint.
- [ ] HTMX dashboard (queue + device view).
- [ ] `topology/importers/` (`protomq_scripts.py`, `hardware_md.py`).

## Working with the user

The user is `tyeth@adafruit.com`, working at Adafruit, building
this for the WS-Python / ProtoMQ / display-test bench.

Conventions established over the session:

- **Architecture-first then code.** When asked "we'll need X", the
  right first move is updating `docs/ARCHITECTURE.md`, not writing
  Python. Confirmed by user: their first scoping pick was
  "Architecture doc first".
- **Ask focused questions before committing to a design.** The
  `AskUserQuestion` tool is the right vehicle — small numbers of
  options with the recommended one first. Multiple sessions of
  user direction shape have come from this. Don't bury decisions
  in implementation; surface them.
- **Bias for terse, complete-sentence updates** rather than running
  commentary. Match commit messages to the `docs:` / `ci:` /
  `vendor:` style already in `git log`.
- **No emojis.** Anywhere — replies, files, commit messages.
- **End every commit message with the** `https://claude.ai/code/
  session_01KXJbynVGheaSkFiZGxzSrU` **trailer** that's been
  consistent throughout the repo. The harness expects this format
  for the commit footer.
- **Don't create planning / decision / progress docs unless the
  user asks.** They did ask for this handoff explicitly — that's
  the exception, not the rule.

## Branch & push protocol

- Designated working branch: **`claude/protomq-hil-api-frontend-CUUTm`**
  (per the harness's task spec).
- Every push: `git push -u origin claude/protomq-hil-api-frontend-CUUTm`.
- Network retries: up to 4× with exponential backoff (2s, 4s, 8s, 16s).
- **Merges to `main` happen only when the user explicitly says so.**
  Their phrasing has been "merge that to main" / "get it into main".
  Use `git merge --no-ff` from a clean local `main` reset to
  `origin/main` so the merge commit matches their earlier PR-merge
  style.
- A **parallel session** has already pushed to `main` once during
  this work (commits `c673e14`, `bb81464`, `f35329d` — submodule
  pin bumps). Expect this to keep happening. Always
  `git fetch origin main` before any merge plan; if local and
  remote diverge, resolve via merge commit, not by force-pushing.
- The parallel session branch name was
  `claude/circuit-python-solenoid-api-tNrMy`. Different scope from
  this branch; ignore unless work overlaps.

## Submodule setup (easy to miss)

`.gitmodules` doesn't carry per-submodule remote config that we
actually need. After any fresh clone:

```bash
git submodule update --init --recursive
./scripts/setup-submodules.sh
```

The script is idempotent. It does two things:

1. **`vendor/protomq`** — sets *two* push URLs on `origin`
   (`tyeth-ai-assisted/protomq` and `tyeth/protomq`), so any push
   from inside that submodule reaches both forks. User explicitly
   asked for this: "always ensure it's pushed to the tyeth fork".
2. **`vendor/wippersnapper-arduino`** — adds an `upstream` remote
   pointing at `adafruit/Adafruit_Wippersnapper_Arduino` (fetch
   only; upstreaming goes via PR, not direct push).

Parallel-session submodule bumps will keep landing on `main`. When
integrating, run `git submodule update --init --recursive` after
the merge so the working tree matches the new pins.

## Bench topology, in one sentence per machine

Pulled from `vendor/hil-detection/references/hardware.md` and user
clarification:

- **Controller host** — independent machine running this repo's
  service. Not on the bench. User runs it locally at
  `~/dev-projects/python/usbip-hil-controller` under WSL for now.
- **`rpi-displays`** (`192.168.1.234`, Pi Zero 2W) — owns *all*
  microcontroller DUTs via the Genesys USB hub (`05e3:0610`) with
  MCP23017 solenoid power/reset at I²C `0x20`. All eight solenoid
  channels are now considered operational (OQ9 directive: "assume
  all solenoid channels are working"). Can run many concurrent
  jobs (per-device locks); `exclusive.host: true` serialises
  everything on it.
- **`rpi-hil001` … `rpi-hil007`** — each owns SBC DUTs. **One test
  or suite at a time per host** (`max_concurrent_jobs: 1` in §5.1).
  Per-port power control planned, not yet wired.
- **`pi5-protomq`** (`192.168.1.210`) — ProtoMQ broker (MQTT `1884`,
  web UI `5173`). The controller observes it, does not host it.
- Every HIL host: `pi` user with controller's SSH key already
  authorised. The hardcoded password in `vendor/hil-detection/
  tests/conftest.py` (`RPI_PASSWORD = "sjahse98"`) is a residue
  flagged for cleanup PR (open question 8).

## Where to look first

- **`docs/ARCHITECTURE.md`** — full design. **§15 is now "Design
  decisions (formerly open questions)"** with all sixteen items
  resolved by stakeholder directive (verbatim quotes preserved).
  §16 has the milestone cut.
- **`vendor/hil-detection/references/hardware.md`** — the
  hand-maintained topology + solenoid map + USB mode tables. Direct
  input to the planned `hardware_md.py` importer.
- **`vendor/protomq/scripts/*.json`** — one demo per `(board,
  display)` pair. Source of truth for device↔display wiring.
  Direct input to the planned `protomq_scripts.py` importer.
- **`vendor/hil-detection/tests/`** — pytest fixtures already
  driving the bench over SSH. This is the *prototype* of the
  controller's adapter layer, not something to replace.
- **`vendor/wippersnapper-arduino/src/provisioning/ConfigJson.cpp`**
  — confirms `io_url` (string) / `io_port` (int) as the broker
  override fields. The example secrets file at
  `examples/wippersnapper-arduino/secrets.example.json` is wired
  to this contract.
- **`.github/workflows/ws-python-ci.yml`** — the recently-landed
  reusable workflow. Default tests filter is `-m eink_large`;
  default controller URL is `http://wan.gdenu.fi:8080`.

## Open asks from the user

`scripts/mint-token.py` is implemented (M2 partial). It accepts
`--db`, `--label`, `--pool`, `--repo`, writes an argon2id hash
row, and prints the plain `hil_<id>_<secret>` token once.

Default controller URL confirmed: `http://wan.gdenu.fi:8080`.

All sixteen original open questions are now resolved. See §15 of
`docs/ARCHITECTURE.md` for the verbatim stakeholder directives.
The resolutions added the following **new implementation tasks**
that the previous M0–M4.5 work did not yet cover:

- **OQ2 / OQ5 (camera pipeline).** Replace the per-host
  `copy_from` sketch with a central streaming pipeline. Aux
  records gain a `roi`; job event log records
  `(start_ts, end_ts, roi)`; pre-roll + trailing-buffer duty
  cycle.
- **OQ4 (force recover).** Add `POST /v1/devices/{id}/recover`
  and `POST /v1/hosts/{id}/recover`, admin-gated. Cancels
  in-flight, clears locks, clean detach + power cycle, re-probe.
- **OQ7 (drift detectors).** `protomq_scripts.py` and
  `hardware_md.py` importers under
  `src/hil_controller/topology/importers/` — flag-only, never
  overwrite `/etc/hil/topology.yaml`.
- **OQ11 (HTTP agent transport).** Add `src/hil_controller/
  hosts/agent.py` as the *preferred* transport — HTTPS, mTLS or
  controller-signed token. SSH stays as fallback. Per-host
  config picks. The Protocol in `hosts/base.py` already supports
  this; just add the implementation.
- **OQ12 (artifact transfer fallback).** Per-host
  `fetch_locally: bool` config. Default `false` keeps controller-
  pulls-then-pushes; opt-in lets specific hosts fetch directly.
- **OQ15 (forensic snapshots + retention daemon).** Snapshot
  every permissive-script payload to
  `/var/lib/hil/forensic/<job-id>/`. Background sweep deletes
  on the *earlier* of 30 days OR `/var/lib/hil` > 75% capacity.

These are not on the "do not re-litigate" list — they're now
concrete work items. Order of priority (stakeholder hasn't
sequenced these yet, so this is a suggestion): OQ11 first
(unblocks restricted-network HIL hosts), OQ4 next (operational
necessity once real DUTs land), then OQ2/OQ5 (M5 territory),
then OQ7 and OQ15.

## Decisions already made — don't re-litigate

These came up in conversation and were settled. Don't open them
again unless the user asks:

- **Stack**: FastAPI + HTMX/Jinja. Not Flask, not Node.
- **Queue**: in-process asyncio + SQLite. Not Redis/Celery.
- **Auth**: per-repo bearer tokens **and** GitHub Actions OIDC.
  Both, not either-or.
- **Controller location**: independent host, not on the bench.
- **Host transport**: dual SSH + HTTP-agent. SSH already
  shipped; the agent is now the *preferred* path per stakeholder
  directive on OQ11 — see "Open asks" above. `HostTransport`
  Protocol already abstracts both.
- **SBC concurrency**: 1 per host, period. MCU host: unbounded,
  per-device locks only.
- **SBC job shape**: `payload.kind = "git-source"` + `GitDeploy`
  adapter fills the flasher slot in the state machine. Not a
  separate deploy phase.
- **Secret profiles**: a named-bundle abstraction (§5.8) — three
  preset profiles (`bench-protomq`, `live-io-test`, `live-io-prod`)
  rendered into `secrets.json` / `.env` per job.
- **Trusted firmware**: gated by a `trusted-firmware` capability
  in the auth policy, plus two permissive built-in scripts
  (`raw-firmware-smoke`, `git-clone-and-run`).
- **Default WS-Python test filter**: `-m eink_large` only, for now.
  WS-Python repo will add the marker when ready.
- **Default controller URL**: `http://wan.gdenu.fi:8080`. Wired as
  the default in `ws-python-ci.yml`.

## Things NOT to do

- Don't write code yet unless the user says so. The pattern has
  been doc → user confirms → doc some more → user confirms → code.
- Don't add a Python Wippersnapper submodule. It's private /
  unreleased / the sandbox can't see it. Open question 10.
- Don't try to fix the hardcoded password in `vendor/hil-detection/
  tests/conftest.py` from inside this repo. It's a separate PR
  against `hil-detection` (open question 8); flag it, don't
  hot-fix.
- Don't force-push to the feature branch (or to main, obviously).
  The user reviews via the GitHub UI.
- Don't broaden the `-m eink_large` default until the user
  explicitly says the rest of the bench is wired up.

## USB-identity wiring (M6)

The full M6 design lives in `docs/ARCHITECTURE.md` section 16. This is
the operator's checklist for going from "all tests green" to "the
Learn-USB button actually depowers a hub port and captures a real
VID/PID."

**Topology YAML — add the hub-port fields to every MCU device:**

```yaml
- id: mcu-pyportal
  host_id: rpi-displays
  hub_host_id: rpi-displays          # defaults to host_id; usbip server
  hub_port_path: "1-1.1.3"            # sysfs bus-id — the real identity
  solenoid_channel: 3                 # MCP23017 channel (0..7)
  usb_serial: "F1DF00AE..."           # iSerial, for matching across resets
  usb_ids:
    - { vid: "239a", pid: "8053", role: runtime,    description: "WipperSnapper" }
    - { vid: "239a", pid: "8054", role: runtime,    description: "CircuitPython" }
    - { vid: "239a", pid: "0035", role: bootloader, description: "UF2" }
```

Roles are mechanism-level (`runtime | bootloader | dfu | msc | cdc |
unknown`); product info goes in `description`. The legacy single
`usb: {vid, pid}` block is still accepted and seeds one `unknown` row.

**Migration** is automatic. On first boot after upgrade:
- `ALTER TABLE` adds the four new device columns.
- Any pre-existing `usb_json` is backfilled into `device_usb_ids` with
  `source='migration'`, `role='unknown'`.
- `device_leases` table is created, then `startup_sweep` releases any
  active lease whose `job_id` is no longer in an active state (recovers
  from a crashed controller without manual cleanup).

**Wiring the active learn flow** (one-time, in your deployment entry
point — e.g. `main.py` or a startup hook):

```python
from hil_controller.adapters.usb_fingerprint import UsbFingerprintAdapter
from hil_controller.adapters.usb_scan import make_ssh_scan_fn

def usb_fingerprint_provider(*, db_path: str) -> UsbFingerprintAdapter:
    # 1. Build a transport for the hub host (your SSHTransport / similar).
    # 2. Wrap vendor/hil-detection/usb_hub.py's SolenoidHubController in an
    #    async facade (all_off / port_on / port_off).
    hub = AsyncSolenoidHub(transport=hub_transport)
    return UsbFingerprintAdapter(
        db_path=db_path,
        hub=hub,
        scan_fn=lambda: ssh_scan(hub_transport),  # parses `usbip list -l`
    )

app.state.usb_fingerprint_provider = usb_fingerprint_provider
```

Without the provider, `/v1/devices/{id}/learn-usb` and the UI button
still run end-to-end (lease acquired, DB upserted) but exercise no-op
placeholders for the hub and the scan — useful for testing the flow
but it captures nothing real.

**Passive learn** needs no wiring: as long as the adapter the host
registry returns for a job exposes a `transport` attribute with a
`run(cmd)` coroutine, `Scheduler._maybe_start_passive_learn` will
spawn the polling loop automatically. `SSHTransport` already qualifies.

**Knobs:**
- `UsbFingerprintAdapter(settle_s=2.0, reset_settle_s=1.5)` — adjust
  for slow-enumerating boards. SAMD51 double-tap timing parameters
  go on the `hub.port_off` call directly (see `vendor/hil-detection/
  usb_hub.py:68` for the defaults).
- `passive_learn_loop(interval_s=3.0)` — bump down if you want
  faster reaction to VID/PID flips during a job, up if SSH cost is
  noticeable.

**REST endpoints summary:**

```
GET    /v1/devices/{id}/usb-ids               # list
POST   /v1/devices/{id}/usb-ids               # manual add
DELETE /v1/devices/{id}/usb-ids/{row_id}      # remove
POST   /v1/devices/lookup-by-usb              # {vid,pid,iserial?} -> [devices]
POST   /v1/devices/{id}/learn-usb             # {include_reset_cycle?}
GET    /v1/leases?active_only=true            # observe exclusivity
POST   /v1/leases                             # manual claim (rarely needed)
DELETE /v1/leases/{id}                        # force-release
```

**Operator gotcha — exclusive_hub during learn is loud.** A learn-USB
pass briefly depowers *every* port on the target hub, so any other job
sharing that hub will see its DUT vanish. The lease primitive prevents
two such operations colliding, but it does not pause concurrent normal
jobs — schedule learn passes when the hub is idle, or accept the blip.

## Per-phase execution-location for arduino-ws jobs (M7)

WipperSnapper arduino-ws jobs used to run **every phase on the DUT's
host**. rpi-displays (the DUT host) has only 415 MB RAM and a 208 MB
tmpfs `/tmp`, so a PlatformIO build there OOM-thrashes / runs out of
disk. The controller (Tachyon, 192.168.1.169) builds easily. So each
phase's **execution host** is now selectable.

**Carrier:** `params.exec` (pass-through dict, mirrors `params.protomq`):

```
params.exec = {
  "build_host":   "controller" | "dut-host",          # where `pio run` compiles
  "flash_mode":   "usbip" | "ship-artifacts",          # how firmware reaches the DUT
  "test_host":    "controller" | "dut-host" | "none",
  "protomq_host": "controller" | "dut-host" | "off",
  "pio_env":      "<platformio env>",
}
```

Near-term defaults (set by the arduino-ws form builder): build +
protomq on the controller, `flash_mode=usbip`, pytest none. Under this
layout the DUT's `MQTT_HOST` = the controller LAN IP
(`config.controller_ip` / `HIL_CONTROLLER_IP`, default 192.168.1.169),
not 127.0.0.1.

**Code map:**
- `adapters/usbip_bridge.py` — `UsbipBridge` brokers a device from its
  USB-server host onto a client. `attached()` async CM does ensure-vhci →
  bind (server) → attach (client) → yield the new `/dev/tty*` → detach +
  unbind in a `finally`. Pure parsers `parse_usbip_port` /
  `diff_serial_ports` are unit-tested.
- `adapters/arduino_ws_exec.py` — `ArduinoWsExecAdapter` holds two
  transports (controller + DUT-host), delegates clone/build/run to an
  inner `GitDeployAdapter` on the build host, and adds **flash** as a
  distinct phase (usbip upload on the controller, or ship-artifacts +
  esptool on the DUT). usbip flash is wrapped in an `exclusive_device`
  lease released in a `finally`. Cross-host build+run → `NotImplementedError`.
- `hosts/registry.py` `make_adapter` (DB-free, unit-tested) routes jobs
  with `params.exec` to the new adapter, building the DUT transport from
  the device's `hub_host_id`.
- Topology: device `host_id` = execution host (the controller), separate
  from `hub_host_id` + `hub_port_path` (where USB physically lives). See
  `mcu-feather-esp32s3-revtft` in `deploy/topology.example.yaml`.
- `scripts/setup-hil-host.sh` provisions passwordless-sudo usbip +
  vhci-hcd/usbip-host modules + usbipd.

**⚠ usbipd MUST be running on the USB-server host (the one physically
holding the DUT, e.g. rpi-displays).** It is the daemon the controller's
`usbip attach` connects to on TCP **:3240**. If it is down, the flash phase
fails with `usbip attach failed (exit 1): usbip: error: tcp connect` — the
build can succeed and you still never reach the DUT. Diagnose + start:

```
# on the USB-server host (rpi-displays):
ss -ltn | grep 3240                 # is it listening?
pgrep -a usbipd                     # is the daemon up?
sudo usbipd -D                      # manual one-shot start (does NOT persist)
sudo usbip list -l | grep 1-1.1.1.4 # the revtft Feather busid 239a:8123
```

`sudo usbipd -D` is ephemeral — it dies on reboot. For persistence,
`setup-hil-host.sh` enables a packaged `usbipd.service` when present, else
installs a `hil-usbipd.service` unit (Debian/RPi ship the `usbipd` binary but
no unit). rpi-displays currently runs usbipd **manually started** (no unit yet
— rerun `setup-hil-host.sh` there to make it boot-persistent). The controller
(client) side needs only `vhci_hcd` loaded; the bridge `modprobe`s it at flash
time. busid for the revtft Feather = `1-1.1.1.4`.

**⚠ Model A re-enumeration risk — VALIDATE ON HARDWARE BEFORE TRUSTING.**
The ESP32-S3 re-enumerates (ROM↔app) during flash, which can drop the
one-shot usbip attachment mid-upload. `UsbipBridge.attached()` does **not**
yet run the `vendor/usbip-autoattach` reconciliation loop that handles
re-enum. So before relying on `flash_mode=usbip` for the revtft Feather,
run the **cheap validation** (no long build): on rpi-displays
`sudo usbip bind -b 1-1.1.1.4`; on the controller `sudo modprobe vhci-hcd`
+ `sudo usbip attach -r 192.168.1.234 -b 1-1.1.1.4`; then
`esptool chip-id` and a second reset-crossing call (`esptool read-mac`)
to exercise two re-enum cycles. If the attachment survives, usbip is
viable; **if it flakes, switch the job to `flash_mode=ship-artifacts`**
(already implemented) rather than grinding on usbip. These are privileged
commands on production hosts — run them deliberately, not from an agent
session.

## Session lineage

This handoff covers the work done in session
`session_01KXJbynVGheaSkFiZGxzSrU`. If you're picking up after a
context compaction within the same session, the conversation
summary should still cover the recent message; this doc is the
durable record. If you're a fresh agent on a new session, this is
the file to start from after `docs/ARCHITECTURE.md`.
