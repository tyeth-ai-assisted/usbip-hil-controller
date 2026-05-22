# USB-IP HIL Controller — Architecture

Status: **Draft v0.1** — design only, no code yet. This document is the
contract the first implementation will be built against. Comments and
revisions welcome before we start cutting code.

## 1. Goal

Provide a small HTTP service, hosted on a Raspberry Pi, that lets a
GitHub Actions job (or any CI runner) request a hardware-in-the-loop
test against a real device and receive structured pass/fail + logs back,
**without** the calling repository having to run a self-hosted GitHub
runner.

The service is the seam between:

- **Caller side** — a CI step that submits a job and waits for a result
  (long-poll, up to ~5 min per call, re-issued on timeout).
- **Controller host** — runs *this* repository. An independent host
  (any Linux box with network reachability to the bench), **not** one
  of the HIL hosts itself. Owns the API, the dashboard, the SQLite
  job store, and the scheduler. Does not have any DUTs attached to
  it.
- **HIL host fleet** — a set of bench Pis the controller fans work
  out to over SSH. Each HIL host owns a slice of the DUTs:
  - `rpi-displays` (Pi Zero 2W, `192.168.1.234`) — **all microcontroller
    DUTs**, attached via the Genesys USB hub with MCP23017 solenoid
    power/reset control at I²C `0x20`.
  - `rpi-hil001` … `rpi-hil007` (one per host) — **single-board-computer
    DUTs**, currently on a separate USB hub without per-port power
    control. Independent port-power control is planned for parity with
    rpi-displays.
  - SSH access on every HIL host is via key-based auth to the `pi`
    user; the controller holds the private key.
- **ProtoMQ broker host** — `pi5-protomq` (`192.168.1.210`, MQTT
  `1884`). DUTs talk to it as MQTT clients during protomq-flavoured
  tests; the controller observes the broker but doesn't host it.

A thin web UI sits in front of the same API for human inspection of
queue state, device availability, the wiring graph (devices + aux +
mux), and per-job logs / artifacts. The same topology endpoints are
available to CI so a job can ask "is there a seat for me?" before
submitting, instead of finding out by failing.

## 2. Non-goals (v1)

- Multi-Pi clustering. Single host, single in-process worker pool.
- Dynamic hardware switching (programmable matrix / crossbar). Devices
  are fixed to ports; cameras observe known positions. Planned later.
- Running arbitrary user-supplied shell scripts. v1 only runs a
  pre-registered allow-list of test scripts, parameterised by job input.
- Hosting GitHub-managed self-hosted runners. We are deliberately
  avoiding that cost model.
- Authenticated browser sessions for the public dashboard. Read-only
  pages are public on the LAN; write operations require an API token.

## 3. Top-level shape

```
                +--------------------------------------+
   GitHub CI ── │   Controller host (this repo)        │ ── HTMX dashboard
   (POST job,   │   FastAPI app (single process)       │    (same origin)
    long-poll)  │                                      │
                │  ┌──────────┐   ┌──────────────┐    │
                │  │  HTTP /  │   │ Scheduler +  │    │
                │  │  HTMX    │──▶│ worker pool  │────┼────────────────────┐
                │  │  router  │   │ (asyncio)    │    │  per-host SSH      │
                │  └────┬─────┘   └──────┬───────┘    │  transport         │
                │       │                │            │                    │
                │       ▼                ▼            │                    │
                │   ┌─────────────────────────┐      │                    │
                │   │  SQLite (jobs, events,  │      │                    │
                │   │  devices, hosts, tokens,│      │                    │
                │   │  audit)                 │      │                    │
                │   └─────────────────────────┘      │                    │
                +--------------------------------------+                    │
                                                                            │
                                  ┌─────────────────────────────────────────┘
                                  │
                                  ▼
                       HIL host fleet (each owns its DUTs)
                       ┌──────────────────────────────────────────────┐
                       │ rpi-displays — microcontroller DUTs          │
                       │   Genesys USB hub + MCP23017 solenoid (0x20) │
                       │   /dev/serial/by-id/..., esptool, picotool   │
                       │   vendor/usbip-autoattach (server side)      │
                       ├──────────────────────────────────────────────┤
                       │ rpi-hil001 … rpi-hil007 — SBC DUTs           │
                       │   (per-port power control planned)           │
                       └──────────────────────────────────────────────┘

                       ProtoMQ broker host (read-only observer)
                       ┌──────────────────────────────────────────────┐
                       │ pi5-protomq — MQTT 1884, web UI 5173         │
                       └──────────────────────────────────────────────┘
```

One controller process, one SQLite file, async workers in the same
event loop. No Redis, no Celery, no external broker in v1. Each
worker holds an SSH session to the HIL host that owns its assigned
device; the work happens *there*, not on the controller.

## 4. Repository layout (proposed)

```
docs/
  ARCHITECTURE.md          # this file
  API.md                   # to follow once endpoints firm up
  DEPLOY.md                # systemd unit, user creation, udev rules
vendor/                    # git submodules — see vendor/README.md
  protomq/                 # tyeth-ai-assisted/protomq @ displays-v2-testing
                           #   (dual-push to tyeth/protomq)
  usbip-autoattach/        # tyeth-ai-assisted/usbip-autoattach @ main
  hil-detection/           # tyeth-ai-assisted/hil-detection @ main
  wippersnapper-arduino/   # tyeth-ai-assisted/adafruit-Adafruit_Wippersnapper_Arduino @ migrate-api-v2
                           #   (fetch-only upstream remote to adafruit/...)
                           # Python WS variant is private/unreleased — not vendored;
                           # wiring info for SBC targets comes from vendor/protomq/scripts.
scripts/
  setup-submodules.sh      # dual-push for vendor/protomq, upstream remote for WS Arduino
examples/                  # caller-side templates a downstream repo copies into its CI
  wippersnapper-arduino/secrets.example.json
  wippersnapper-python/{secrets.example.json,.env.example}
  hil-call.sh              # submit + long-poll reference flow
  README.md
.github/workflows/
  example-hil-call.yml     # workflow_dispatch example of the caller flow
src/
  hil_controller/
    __init__.py
    main.py                # FastAPI app factory + uvicorn entry
    config.py              # pydantic-settings, env-driven
    db/
      models.py            # SQLAlchemy models
      migrations/          # alembic
    api/
      jobs.py              # POST /v1/jobs, GET /v1/jobs/{id}, /wait, /logs
      devices.py           # GET /v1/devices…
      health.py
    web/
      templates/           # Jinja2 + HTMX partials
      static/
      routes.py            # dashboard pages
    auth/
      tokens.py            # per-repo bearer tokens
      oidc.py              # GitHub Actions OIDC verifier
      policy.py            # repo → allowed device-pool mapping
    queue/
      scheduler.py         # in-process scheduler, per-device locks
      worker.py            # job runner state machine
      events.py            # append-only log + long-poll wakeups
    hosts/
      base.py              # HostTransport protocol (exec, copy_to, copy_from, stream)
      ssh.py               # default transport: asyncssh-based, key auth
      agent.py             # future: HTTP agent transport (see §15 OQ11)
      registry.py          # host pool from YAML, health checks, key paths
    adapters/
      base.py              # DeviceAdapter protocol — calls into the HostTransport
      usbip.py             # invokes vendor/usbip-autoattach on the remote host
      solenoid_hub.py      # invokes vendor/hil-detection/usb_hub.py on the remote host
      flashers/            # esptool, picotool, dfu, uf2-msc, snapper-py, …
      serial_capture.py    # streams the remote /dev/serial/by-id/... over SSH
      camera.py            # optional frame grabs alongside serial
    topology/
      manifest.py          # YAML loader for devices + aux + mux matrix
      resolver.py          # selector → (device, aux bindings, mux ops)
      importers/
        protomq_scripts.py # parse vendor/protomq/scripts/*.json → manifest
        hardware_md.py     # parse vendor/hil-detection/references/hardware.md
    tests/
      runner.py            # invokes named scripts from the HIL repo
      protomq.py           # ProtoMQ-specific log-assertion helpers
tests/                     # pytest, unit + integration with fakes
deploy/
  systemd/
  udev/
  README.md
pyproject.toml
```

This layout exists to be argued with — we should land it before writing
code so import paths don't churn.

## 5. Domain model

### 5.1 Host

A HIL host the controller can fan work out to. Modelled as a
first-class entity because devices, auxes, muxes, and per-job
adapter calls are all scoped to one. The controller itself does *not*
have a Host row — it's the orchestrator, not a worker.

| Field             | Notes                                                       |
|-------------------|-------------------------------------------------------------|
| `id`                    | Stable slug, e.g. `rpi-displays`, `rpi-hil003`.       |
| `role`                  | `microcontroller-fleet` / `sbc-fleet` / `protomq-broker`. |
| `addr`                  | Hostname or IP.                                       |
| `transport`             | `ssh` (v1 default) or `agent` (see §15 OQ11).         |
| `ssh_user`              | Default `pi`.                                         |
| `ssh_key_path`          | Path on the controller filesystem to the per-host key. |
| `capabilities`          | Tags the resolver can require, e.g. `power-control`,  |
|                         | `usbip-server`, `mcp23017`, `cameras`.                |
| `max_concurrent_jobs`   | Hard cap on simultaneous jobs on this host. Default: |
|                         | `1` for `sbc-fleet` (one SBC HIL host runs one test  |
|                         | or suite at a time — the bench can't usefully share   |
|                         | a Pi between two CI jobs), unbounded (`null`) for     |
|                         | `microcontroller-fleet` (per-device locks are the     |
|                         | real constraint there). See §9 for how exclusive-host |
|                         | jobs interact with this. |
| `status`                | `available` / `quarantined` / `offline`.              |
| `last_seen_at`          | Updated by periodic health probe.                     |

Host rows are seeded from `/etc/hil/hosts.yaml`. Periodic health
probes (SSH `true` over the configured key) update `status` and
`last_seen_at` so the dashboard can show offline hosts before a job
is wasted.

### 5.2 Device

A physical thing wired to a HIL host — the *unit under test*.

| Field             | Notes                                                       |
|-------------------|-------------------------------------------------------------|
| `id`              | Short stable slug, e.g. `qtpy-s3-01`, `rpi-hil003-pi5b`.    |
| `host_id`         | FK to the Host that owns this device.                       |
| `kind`            | `microcontroller` / `sbc` / `snapper-arduino` / …           |
| `model`           | More specific tag (`rp2040`, `esp32-s3`, `pi-zero-2w`, …).  |
| `capabilities`    | Tags the resolver matches on (`spi`, `i2c`, `cdc-acm`, …).  |
| `usb_path`        | Stable `by-path` or USB-IP busid (on the owning host).      |
| `reset`           | `{ mux, channel, profile }` — nullable if device self-resets. |
| `flasher`         | Name of registered flasher adapter.                         |
| `serial_port`     | `/dev/serial/by-id/...` *on the host*, baud, parity.        |
| `camera_id`       | Optional v4l2 device on the host watching this position.    |
| `pool`            | Logical grouping for authorization (`public`, `internal`, …).|
| `exposable_via`   | `local`, `usbip`, or both. Controls remote-attach jobs.     |
| `status`          | `available` / `in-use` / `quarantined` / `offline`.         |

A device on its own is not usually enough to run a meaningful test —
you also need the right peripherals wired to it. Those live in
§5.5 (auxiliary components) and §5.6 (connectivity matrix).

Devices are seeded from a YAML file checked into `deploy/`. The DB row
is hydrated on startup; humans can mark a device `quarantined` from the
dashboard without editing YAML.

### 5.3 Job

| Field             | Notes                                                       |
|-------------------|-------------------------------------------------------------|
| `id`              | UUIDv7, sortable.                                           |
| `submitted_by`    | Token id or OIDC subject (repo+workflow+ref).               |
| `repo`            | `owner/name` from auth context, not request body.           |
| `request`         | JSON: target selector, script name, params, payload, secrets_profile, flags. |
| `secrets_profile` | Resolved profile id (see §5.8), pinned at submission time so a hot-reload of the policy file doesn't change what a queued job sees. |
| `exclusive_host`  | Bool. If true, the job takes an exclusive lock on its assigned host: no other job may run there, and the worker can attribute every `dmesg` / `usbmon` / serial line unambiguously. Trade-off: blocks other jobs on the same host for the duration, so reserve it for hard-to-trace problems. |
| `state`           | See state machine below.                                    |
| `assigned_host`   | Filled in at `assigned` transition.                         |
| `assigned_device` | Filled in at `assigned` transition.                         |
| `created_at`, `started_at`, `finished_at`                                       |
| `result`          | `pass` / `fail` / `error` / `timeout` / `cancelled`.        |
| `summary`         | Short human string for dashboard rows.                      |
| `artifacts`       | List of saved files: serial log, camera frames, exit codes, dmesg/usbmon (when exclusive_host=true). |

### 5.4 Job event log

Append-only. One row per state transition or worker-emitted line.
Powers (a) the long-poll wakeup, (b) the live HTMX log view, (c) audit.

| Field        | Notes                                                       |
|--------------|-------------------------------------------------------------|
| `job_id`     | FK.                                                         |
| `seq`        | Monotonic per job, assigned at insert.                      |
| `at`         | Timestamp.                                                  |
| `kind`       | `state` / `log` / `metric` / `artifact`.                    |
| `payload`    | JSON.                                                       |

Long-poll clients pass a `since=<seq>` cursor and block on a
per-job `asyncio.Condition` until new rows land or the timeout fires.

### 5.5 Auxiliary component

An auxiliary is anything that hangs off a device and that a test might
need to assert against — a display, a sensor, an LED ring, a logic
analyser channel, a power monitor, a second microcontroller acting as a
companion. Modelled separately from devices because (a) the same
physical aux can serve more than one device via a multiplexer, and (b)
test scripts care about *capabilities*, not which port a thing happens
to be on this week.

| Field             | Notes                                                       |
|-------------------|-------------------------------------------------------------|
| `id`              | Stable slug, e.g. `ili9341-2.4-01`.                         |
| `kind`            | `display` / `sensor` / `actuator` / `companion-mcu` / …     |
| `model`           | Specific part, e.g. `ili9341`, `ssd1306`, `bme280`.         |
| `capabilities`    | Tags the resolver matches on (`display:240x320`, `i2c-target:0x76`). |
| `interface`       | `spi` / `i2c` / `uart` / `gpio` / `usb-cdc`.                |
| `signals`         | Logical names → bus pin/line (`mosi`, `cs`, `sda`, `int`).  |
| `observability`   | How the controller can read it back: `camera`, `serial-tap`, `i2c-sniffer`, or `none`. |
| `pool`            | Same authorization model as devices.                        |
| `status`          | `available` / `in-use` / `faulty` / `offline`.              |

Aux records are seeded from the same YAML manifest as devices. The
manifest is also the place where physical wiring gets recorded —
today that knowledge lives implicitly inside the protomq display-v2
test scripts (see §15 open question 7).

### 5.6 Connectivity matrix (multiplex)

Most aux-to-device wiring in v1 is **fixed**: aux `X` is bolted to
device `Y` and only `Y`. But the bench *does* have a real shared
resource: the Genesys Logic USB hub (`05e3:0610`, dual-cascaded on
`rpi-displays`) with an **MCP23017 solenoid driver at I²C `0x20`**
controlling power/reset per channel. That hub is effectively a
per-port power mux; the manifest models it uniformly so the scheduler
can answer "can I give job J a device of kind K with a 240x320 SPI
display attached?" without the caller knowing which physical seats
satisfy that. Future genuine signal muxes (TCA9548 I²C switch, SPI
mux, relay GPIO crossbar) drop into the same model.

```yaml
# /etc/hil/topology.yaml  (excerpt — grounded in vendor/hil-detection/references/hardware.md)
hosts:
  - id: rpi-displays
    role: microcontroller-fleet
    addr: 192.168.1.234
    transport: ssh
    ssh_user: pi
    ssh_key_path: /etc/hil/keys/rpi-displays
    capabilities: [power-control, mcp23017, usbip-server, cameras]
  - id: rpi-hil001
    role: sbc-fleet
    addr: rpi-hil001.local
    transport: ssh
    ssh_user: pi
    ssh_key_path: /etc/hil/keys/rpi-hil-fleet      # shared key, per-host known_hosts pinning
    capabilities: []                                # power-control coming later
  # rpi-hil002 .. rpi-hil007 follow the same shape.
  - id: pi5-protomq
    role: protomq-broker
    addr: 192.168.1.210
    transport: none                                 # read-only observer; no SSH
    mqtt_port: 1884

muxes:
  - id: rpi-displays.usb-hub-01
    host: rpi-displays
    kind: usb-power
    model: genesys-logic-05e3-0610
    control:
      adapter: mcp23017-solenoid
      i2c_bus: 1
      i2c_addr: 0x20
      script: vendor/hil-detection/usb_hub.py       # path on the HIL host after deploy
    exclusive: false                                # per-channel independent
    timing_profiles:
      standard:   { on: "200ms-H,LOW", off: "200ms-H,500ms-L,1000ms-H,LOW" }
      samd51_uf2: { on: "200ms-H,LOW", off: "200ms-H,100ms-L,300ms-H,LOW" }
  # Future: rpi-hil001.usb-hub-01 etc. once per-port power control lands.

devices:
  - id: qtpy-s3-01
    host_id: rpi-displays
    kind: microcontroller
    model: esp32-s3
    capabilities: [native-cdc, spi, i2c]
    usb: { vid: "239a", pid: "8143" }
    serial_port: /dev/serial/by-id/...
    reset: { mux: rpi-displays.usb-hub-01, channel: 0, profile: standard }
    pool: public
  - id: pyportal-titano-01
    host_id: rpi-displays
    kind: microcontroller
    model: samd51
    capabilities: [uf2, spi, i2c]
    usb: { vid: "239a", pid: "8053", uf2_vid: "239a", uf2_pid: "0035" }
    flasher: uf2-msc
    reset: { mux: rpi-displays.usb-hub-01, channel: 4, profile: samd51_uf2 }
    pool: public
  - id: pico-w-01
    host_id: rpi-displays
    kind: microcontroller
    model: rp2040
    capabilities: [bootsel-msc, spi, i2c]
    flasher: picotool
    reset: { mux: rpi-displays.usb-hub-01, channel: 6, profile: standard }
    pool: public
  - id: rpi-hil003-pi5-a
    host_id: rpi-hil003
    kind: sbc
    model: pi5
    capabilities: [linux, python-snapper]
    serial_port: /dev/serial/by-id/...              # UART via host
    reset: null                                     # no power control yet — manual or via systemd reboot over SSH
    pool: internal

auxes:
  - id: oled128x32-metro-s2
    kind: display
    model: ssd1306
    interface: i2c
    capabilities: [display, "display:128x32", "display:i2c"]
    signals: { sda: D47, scl: D48, addr: 0x3c }
    observability: camera
  - id: qualia-round-480-01
    kind: display
    model: st7701
    interface: ttl-rgb666
    capabilities: [display, "display:480x480", "display:ttl-rgb666"]
    signals: { r: [D1,D2,D42], g: [D21,D47,D48], b: [D10,D11,D12] }
    observability: camera

connections:
  # Fixed wiring: imported from vendor/protomq/scripts/*.json.
  - aux: oled128x32-metro-s2
    device: metro-s2-01
    bus: i2c0
  - aux: qualia-round-480-01
    device: qualia-s3-01
    bus: ttl-rgb666
```

Three resolver rules fall out of this:

1. **Fixed-wired aux** is implicitly locked when its device is locked.
   This is the v1 common case.
2. **Multiplexed aux** is a separate lockable resource; the scheduler
   acquires `(device, aux, mux)` together, then issues a mux switch
   adapter call before the worker enters `preparing`.
3. **Exclusive muxes** serialise across all downstream devices for the
   duration of any job that uses *any* aux on that mux. The resolver
   reports this so the dashboard can show why a device is blocked even
   though it looks idle.

If no mux exists, jobs that need an aux the assigned device can't
reach are rejected at submission time with a structured error rather
than failing mid-flash.

### 5.7 Where the manifest comes from

Two existing artefacts seed the first `topology.yaml` — neither is
authoritative on its own, but together they cover the bench:

- **`vendor/hil-detection/references/hardware.md`** — hosts, USB hub
  VID:PID, MCP23017 control address, the 8-channel solenoid map
  (channel → board, USB VID:PID, USB serial, expected `/dev/ttyACM*`),
  and the timing profiles. Source of truth for *power/reset wiring*.
- **`vendor/protomq/scripts/*.json`** — one demo per `(board, display)`
  pair. Each file has `display.add` with one of `i2c { busSda, busScl,
  deviceAddress }`, `spi { … }`, or `ttlRgb666 { pinR0…pinB2 }`, the
  driver name (`SSD1306`, `SH1107`, `ST7701`, …), and the panel ID.
  Source of truth for *device↔display wiring + display interface*.

The `protomq_scripts.py` importer:

1. Walks `vendor/protomq/scripts/*.json`.
2. Parses the filename (`feather-s2-sh1107-demo.json` →
   `device=feather-s2`, `aux=sh1107`) and the `display.add` step.
3. Emits an `auxes:` entry per unique `(driver, panel, interface)`
   tuple and a `connections:` entry binding it to the device.
4. Cross-references the device against `hardware.md` to fill in
   `usb.vid/pid`, `reset.channel`, and `reset.profile`.
5. Writes a draft `topology.yaml` and a `topology.unresolved.md`
   listing any (a) scripts whose device can't be found in
   `hardware.md`, (b) `hardware.md` channels with no matching script,
   (c) pin assignments that vary between scripts targeting the same
   board.

Re-running the importer is non-destructive — humans own the final
`topology.yaml`; the importer regenerates a side-by-side `*.imported`
file for diffing.

### 5.8 Secret profile

A *secret profile* is the named bundle of credentials the controller
materialises onto a HIL host at deploy/flash time. Profiles let us
target the bench ProtoMQ broker, an Adafruit IO test account, or the
live Adafruit IO production endpoint with the same job-submission
body — the caller picks a profile name (or the policy picks one for
them) and the controller substitutes the right values into the
rendered `secrets.json` / `.env`.

```yaml
# /etc/hil/secrets-profiles.yaml  (illustrative)
profiles:
  - id: bench-protomq           # default; bench ProtoMQ broker, throw-away test AIO account
    io_url:  pi5-protomq.local
    io_port: 1884
    io_username: ${env:HIL_BENCH_AIO_USERNAME}
    io_key:      ${env:HIL_BENCH_AIO_KEY}
    wifi_ssid:     ${env:HIL_BENCH_WIFI_SSID}
    wifi_password: ${env:HIL_BENCH_WIFI_PASSWORD}

  - id: live-io-test            # live io.adafruit.com, a dedicated test account
    io_url:  io.adafruit.com
    io_port: 8883
    io_username: ${env:HIL_LIVE_AIO_TEST_USERNAME}
    io_key:      ${env:HIL_LIVE_AIO_TEST_KEY}
    wifi_ssid:     ${env:HIL_BENCH_WIFI_SSID}
    wifi_password: ${env:HIL_BENCH_WIFI_PASSWORD}

  - id: live-io-prod            # live io.adafruit.com, production account — guarded
    io_url:  io.adafruit.com
    io_port: 8883
    io_username: ${env:HIL_LIVE_AIO_PROD_USERNAME}
    io_key:      ${env:HIL_LIVE_AIO_PROD_KEY}
    wifi_ssid:     ${env:HIL_BENCH_WIFI_SSID}
    wifi_password: ${env:HIL_BENCH_WIFI_PASSWORD}
    requires_trusted: true       # only callers with the `trusted-firmware` policy bit
```

| Field              | Notes                                                       |
|--------------------|-------------------------------------------------------------|
| `id`               | Stable slug. Pinned on the Job row at submission.           |
| `io_url`, `io_port`| MQTT broker the firmware will talk to.                      |
| `io_username`, `io_key` | AIO credentials. Stored only as `${env:...}` references — never plaintext in the YAML. The controller reads the actual values from systemd `EnvironmentFile=` or a sealed `/etc/hil/secrets.env` at startup. |
| `wifi_ssid`, `wifi_password` | Network creds for the DUT to reach the broker.    |
| `extra`            | Optional dict of additional key/values for arbitrary firmware-specific secrets the test happens to need. |
| `requires_trusted` | Bool. If true, the auth policy must grant the caller the `trusted-firmware` capability to use this profile. |

Policy (§8.1, §8.2) maps a principal to a list of allowed profile
ids and, optionally, a default. Jobs submitted without an explicit
`secrets_profile` get the principal's default; jobs requesting a
profile outside the allow-list are rejected at the API boundary.

Materialisation flow (controller-side, per job):

1. Load the resolved profile values into memory.
2. Render the relevant `examples/.../secrets.example.json` (or `.env`)
   with `envsubst` into the job's per-host scratch directory.
3. `copy_to` the rendered file to `/tmp/hil/<job-id>/secrets.json`
   on the HIL host, mode `0400`, owner `pi`.
4. Worker invokes the flash/deploy adapter, which references the
   secrets path.
5. At job terminal (success, failure, or cancel), the worker
   `rm -f`s the secrets file before releasing the device. SQLite
   records that the file was wiped but **never the contents**.

The controller never echoes a profile's secret values back through
the API — `/v1/jobs/{id}` shows only the profile id used.

## 6. Job state machine

```
  queued ──▶ assigned ──▶ preparing ──▶ flashing ──▶ running ──▶ finished
     │           │             │            │            │
     │           ▼             ▼            ▼            ▼
     └───── cancelled      error         error        timeout
```

- `queued`     — accepted by API, awaiting a free device that matches.
- `assigned`   — scheduler picked a device, took its lock.
- `preparing`  — USB-IP attach / power-on / pre-flight checks.
- `flashing`   — writing the requested artifact, if any.
- `running`    — test script executing; serial capture + assertions live.
- `finished`   — terminal, with `result` populated.
- `error`      — infrastructure failure (flash failed, USB-IP gone, etc).
                 Distinct from `finished` + `fail`, which is "device ran
                 the test and the test said no".
- `timeout`    — wall-clock or per-phase budget exceeded.
- `cancelled`  — operator action via dashboard or API.

Transitions write a `state` event; the worker also writes `log` events
inline so callers see progress, not just terminal status.

## 7. HTTP surface (v1)

All API routes under `/v1`. JSON in/out. Bearer auth required for
writes. Dashboard at `/`.

| Method | Path                              | Auth     | Purpose                                  |
|--------|-----------------------------------|----------|------------------------------------------|
| POST   | `/v1/jobs`                        | required | Submit job. Returns `{id, wait_url}`.    |
| GET    | `/v1/jobs/{id}`                   | required | Current snapshot (state, result, etc).   |
| GET    | `/v1/jobs/{id}/wait?since=&timeout=` | required | Long-poll. Blocks up to `timeout` (cap 600s) or until new events. Returns events + current state. CI re-issues on timeout. |
| GET    | `/v1/jobs/{id}/logs?since=`       | required | Same as wait but non-blocking; for tools that prefer polling. |
| GET    | `/v1/jobs/{id}/artifacts/{name}`  | required | Stream a captured artifact (serial log, frame, etc). |
| POST   | `/v1/jobs/{id}/cancel`            | required | Best-effort cancel.                      |
| GET    | `/v1/hosts`                       | required | List HIL hosts, with `status`, `last_seen_at`, and the device count owned by each. |
| GET    | `/v1/hosts/{id}`                  | required | Host detail + devices on it + recent jobs run there. |
| GET    | `/v1/devices`                     | required | List devices visible to the caller's pool. Supports `?host=&kind=&model=&capability=&aux=&pool=` filters; `?include=aux,connections,host` to expand. |
| GET    | `/v1/devices/{id}`                | required | Device detail + current job + attached/reachable aux + owning host. |
| GET    | `/v1/aux`                         | required | List auxiliary components. Same filter/include grammar. |
| GET    | `/v1/aux/{id}`                    | required | Aux detail + which devices it can be routed to (and via which mux). |
| GET    | `/v1/topology`                    | required | Full graph: hosts, devices, auxes, muxes, connections. Suitable for the dashboard's wiring view and for CI to introspect before submission. |
| POST   | `/v1/topology/resolve`            | required | Dry-run a job selector → returns matching `(host, device, aux bindings, mux ops)` candidates and any structured rejection reason. No job is created. |
| GET    | `/healthz`, `/readyz`             | none     | Liveness / readiness.                    |
| GET    | `/`, `/jobs`, `/jobs/{id}`, `/hosts`, `/devices`, `/topology` | none (read-only) | HTMX dashboard pages. |

### 7.1 `POST /v1/jobs` body

Microcontroller job — firmware binary + named test script:

```json
{
  "target": {
    "device": { "kind": "microcontroller", "model": "rp2040" },
    "requires": [
      { "kind": "display", "capabilities": ["display:240x320", "display:spi"] },
      { "kind": "sensor",  "model": "bme280" }
    ],
    "pool": "public"
  },
  "script": "protomq.validate-logs",
  "params": { "scenario": "boot-handshake" },
  "payload": {
    "kind": "firmware-binary",
    "source": {
      "kind": "github-release",                  // or "github-actions-artifact", "url+sha256"
      "repo": "owner/name",
      "tag": "v1.2.3",
      "asset": "firmware.bin",
      "sha256": "…"
    }
  },
  "secrets_profile": "bench-protomq",
  "exclusive": { "host": false },
  "timeouts": { "total_s": 600, "flash_s": 120, "run_s": 300 },
  "metadata": { "pr": 42, "commit": "abc1234" }
}
```

SBC job — git clone + ref + entry-point script:

```json
{
  "target": {
    "device": { "kind": "sbc", "model": "pi5" },
    "pool": "internal"
  },
  "script": "git-clone-and-run",                 // allow-listed entry-point runner
  "params": { "entry": "tests/run_hil.py", "args": ["--smoke"] },
  "payload": {
    "kind": "git-source",
    "source": {
      "repo":   "https://github.com/owner/name.git",   // https or ssh
      "ref":    "abc1234",                              // sha, tag, or branch
      "submodules": true,
      "shallow": true,
      "setup":  ["pip", "install", "-e", "."]           // optional, run after checkout
    }
  },
  "secrets_profile": "live-io-test",
  "exclusive": { "host": true },                  // SBC hosts already max-1, but flag carries through
  "timeouts": { "total_s": 1800, "deploy_s": 300, "run_s": 1200 }
}
```

Key fields:

- **`target.device`** — concrete `{ "id": "rp2040-01" }` or abstract
  selector (`kind` / `model` / `capabilities`); the topology
  resolver picks the least-loaded matching seat.
- **`target.requires`** — auxiliary selectors that must be physically
  attached **or reachable via a mux**. Resolver runs *before* the
  job is accepted, so callers get a structured 409 with the
  unsatisfiable selector rather than a mid-flash failure.
- **`payload.kind`** — `firmware-binary` (MCU path, downloaded by
  hash) or `git-source` (SBC path, cloned + checked out on the HIL
  host). Optional only for scripts that exercise pre-provisioned
  hardware (USB-IP attach to an already-flashed device, etc).
- **`secrets_profile`** — opt-in identifier into §5.8. Omit to use
  the principal's default profile. Mismatch with the auth policy →
  403 at submit time.
- **`exclusive.host`** — when true, the assigned host runs *only*
  this job for the duration, dmesg/usbmon capture starts, and the
  job artifacts grow a `host-dmesg.log` and `host-usbmon.pcap` (see
  §9 for the scheduler interaction and §10 for what gets captured).
  SBC hosts are already max-1, so the flag is a no-op there; on
  rpi-displays it actually changes behaviour.

CI that wants to see what's available before submitting can call
`POST /v1/topology/resolve` with the same `target` block and get
back the candidate seats without enqueueing anything.

### 7.2 Long-poll semantics

CI flow:

```
POST /v1/jobs                       -> 202 {id, wait_url, since: 0}
GET  /v1/jobs/{id}/wait?since=0     -> 200 {events:[…], next_since: 17, state: "running"}
GET  /v1/jobs/{id}/wait?since=17    -> 200 {events:[],   next_since: 17, state: "running"}  (timeout, re-poll)
GET  /v1/jobs/{id}/wait?since=17    -> 200 {events:[…], next_since: 31, state: "finished", result: "pass"}
```

Server-side cap on `timeout` is 300s by default, 600s hard ceiling, to
keep within reverse-proxy and GitHub-side network budgets. The client
just re-issues — there is no special "still alive" response.

## 8. Auth

Two paths, both producing the same internal `Principal` object that
downstream policy checks against.

### 8.1 Per-repo / per-client bearer tokens

- Generated from the dashboard by an admin (admin auth out of scope for
  v1 — assume LAN-trusted operator). Stored as `argon2` hash; the plain
  token shown once at creation.
- Token row carries: `id`, `label`, `repo` (optional pin), `pool`
  (which device pool it can target), `created_at`, `revoked_at`,
  `last_used_at`.
- Presented as `Authorization: Bearer hil_<id>_<secret>`. The `id`
  prefix lets us look up the row without a full-table scan.
- Suitable for non-GitHub callers (a developer's laptop, a Jenkins job,
  a cron).

### 8.2 GitHub Actions OIDC

- CI step requests an OIDC token from GitHub
  (`id-token: write` permission) and presents it as a bearer.
- Server verifies: signature against
  `https://token.actions.githubusercontent.com/.well-known/jwks` (JWKS
  cached with TTL), `iss`, `aud` (we set our own audience and document
  it), `exp`/`iat`.
- Claim → `Principal` mapping uses `repository`, `repository_owner`,
  `ref`, `workflow`, `job_workflow_ref`, `environment`.
- Policy file (YAML, hot-reloaded) maps claims to device pools and
  secret profiles (§5.8), and optionally grants capabilities. E.g.:

  ```yaml
  - match: { repository: "adafruit/protomq", ref: "refs/heads/main" }
    allow_pools:    ["public", "protomq"]
    allow_profiles: ["bench-protomq", "live-io-test"]
    default_profile: bench-protomq
    capabilities:   []
  - match: { repository: "adafruit/wippersnapper-arduino-firmware" }
    allow_pools:    ["public", "protomq", "wippersnapper"]
    allow_profiles: ["bench-protomq", "live-io-test", "live-io-prod"]
    default_profile: live-io-test
    capabilities:   ["trusted-firmware"]   # may run raw-firmware-smoke
  - match: { repository_owner: "adafruit" }
    allow_pools:    ["public"]
    allow_profiles: ["bench-protomq"]
    default_profile: bench-protomq
  ```

- No long-lived secret on the CI side; revocation is by editing the
  policy file.

Both paths produce a `Principal { kind, subject, repo, allowed_pools,
allowed_profiles, default_profile, capabilities }` which the job
submission handler uses to (a) reject jobs targeting disallowed
pools or profiles, (b) gate access to permissive scripts behind the
`trusted-firmware` capability, (c) stamp `repo` and `submitted_by`
on the row from the auth context, never from the request body.

## 9. Scheduler & worker

- One `asyncio` scheduler task. Wakes on (a) new job enqueued,
  (b) device freed, (c) host status change, (d) periodic tick.
- Per-device `asyncio.Lock`. A job in `assigned`+ holds its device's
  lock until terminal.
- **Per-host concurrency**: each Host row carries
  `max_concurrent_jobs` (§5.1). The scheduler models it as a
  per-host `asyncio.Semaphore`. SBC hosts (`rpi-hil00N`) default to
  1 — a SBC HIL host runs one test or suite at a time, period.
  Microcontroller hosts (`rpi-displays`) default to unbounded; per-
  device locks are the real constraint there.
- **Per-host exclusive lock** (for `exclusive_host: true` jobs):
  separate from the semaphore, a per-host `asyncio.Lock` that is
  acquired *write-style*. Semantics:
  - A job requesting `exclusive.host = true` must wait until every
    other in-flight job on that host has reached terminal.
  - Once an exclusive job is `queued` for a host, the scheduler
    refuses to *start* further non-exclusive jobs on that host
    (existing in-flight ones drain). This prevents an exclusive job
    from being starved by a steady drip of regular jobs.
  - While an exclusive job is running, the worker turns on dmesg
    capture (`journalctl -k -f`) and usbmon (`cat /sys/kernel/debug/
    usb/usbmon/0u` or `tcpdump -i usbmonX -w …`) on the HIL host;
    artifacts grow `host-dmesg.log` and `host-usbmon.pcap`. These
    captures are unambiguous because no other job is touching the
    USB bus on that host for the duration.
- **Routing at assignment**: the resolver picks a `(host, device,
  aux bindings, mux ops)` tuple — taking host semaphore availability
  and the exclusive-pending state into account. The worker opens a
  transport session against `device.host_id` and runs every adapter
  call through it; nothing about the work itself happens on the
  controller. Hosts in `offline` or `quarantined` status are
  filtered out of resolution.
- Worker per active job (`asyncio.create_task`). Worker drives the
  state machine, emits events, calls into the device adapter (which
  in turn goes through the host transport).
- Graceful shutdown: scheduler stops accepting new starts, in-flight
  workers get a `cancel()` budget which propagates as SSH session
  close on the HIL host, then process exits. SQLite state is the
  source of truth; on restart, any non-terminal job is marked
  `error` with reason `restart` (we do not auto-resume hardware work
  on a possibly-orphaned remote process).

## 10. Hardware adapter layer

Two cooperating abstractions: a **host transport** that gives the
worker a way to run commands and stream data on a remote HIL host,
and **device adapters** that compose host-transport calls into
device-shaped operations (acquire / reset / flash / serial /
release). The worker only sees the adapter; the adapter is the only
thing that knows the transport exists.

### 10.1 Host transport

```python
class HostTransport(Protocol):
    async def exec(self, argv: list[str], *, env: dict[str, str] | None = None,
                   stdin: bytes | None = None) -> ExecResult: ...
    async def stream(self, argv: list[str]) -> AsyncIterator[bytes]: ...   # stdout
    async def copy_to(self, local: Path, remote: PurePosixPath) -> None: ...
    async def copy_from(self, remote: PurePosixPath, local: Path) -> None: ...
    async def open_serial(self, device_node: str, baud: int) -> AsyncIterator[bytes]: ...
    async def healthcheck(self) -> bool: ...
```

V1 default implementation is **SSH** (`asyncssh`), key-based auth,
known_hosts pinned per HIL host. The transport pools one persistent
connection per host and multiplexes channels over it so individual
adapter calls don't pay connection setup latency. Serial streaming
goes through an SSH channel running `socat OPEN:/dev/serial/by-id/...
- ` (or equivalent) so the bytestream is line-buffered to the
controller without temp files.

A future `agent` transport — small Python service running as a
systemd unit on each HIL host, exposing an HTTPS API the controller
calls — is sketched as a drop-in alternative once SSH's failure modes
become painful (process supervision, partial stdout on connection
drop, sandboxing). See §15 OQ11.

### 10.2 Device adapters

```python
class DeviceAdapter(Protocol):
    async def acquire(self) -> None: ...           # power on, usbip attach, etc
    async def reset(self) -> None: ...             # solenoid pulse or DTR toggle
    async def flash(self, artifact: Artifact) -> None: ...
    async def open_serial(self) -> AsyncIterator[bytes]: ...
    async def release(self) -> None: ...           # detach, power off
```

Each adapter holds a `HostTransport` for the device's owning host.
Concrete adapters compose smaller pieces, *all* of which run on the
HIL host:

- `UsbIpAttach` — uses `vendor/usbip-autoattach`. That repo is split:
  the **server side** (udev rule + `usbip-autobind` helper) is
  installed on the HIL host that physically owns the USB device, so
  the device stays bound to `usbip-host` across resets; the **client
  side** (stdlib-only Python reconciliation loop) runs on whichever
  HIL host is consuming the USB-IP-exported device — the controller
  supervises that loop over SSH.
- `Mcp23017Solenoid` — invokes `vendor/hil-detection/usb_hub.py` on
  `rpi-displays` (the only host with a solenoid-controlled hub
  today). Channel per-device from the manifest; **timing profile**
  (`standard`, `samd51_uf2`, …) also from the manifest, because
  SAMD51 boards need a specific short/long pulse sequence to enter
  the UF2 bootloader vs the standard off sequence. When `rpi-hil00N`
  gain per-port power control, each grows its own mux record and the
  same adapter is parameterised by host.
- `Flasher` — pluggable, all invoked through the host transport so
  the firmware artifact is copied to the HIL host's `/tmp` first and
  the flash tool runs *there*: `esptool` (ESP), `picotool` +
  1200-baud CDC sentinel (RP2040 BOOTSEL chain, see
  `vendor/hil-detection/scripts/pico_hil_flash.sh` for the three-
  stage strategy already in use), `uf2-msc` (mount the BOOTSEL drive
  and copy `.uf2`), an Arduino-sketch upload, or "no-op" for
  pre-provisioned devices. Selected via `payload.kind ==
  "firmware-binary"` plus the device's `flasher` field.
- `GitDeploy` — the SBC-side counterpart of the flashers. Triggered
  by `payload.kind == "git-source"`. Steps, all over the host
  transport, all on the SBC HIL host's filesystem:
  1. `git clone --depth=1 --branch=<ref>` (or full clone + checkout
     if `shallow=false`); `--recurse-submodules` if requested.
  2. Render the chosen secrets profile (§5.8) into the workspace —
     `secrets.json` for the long-running Python-Wippersnapper client,
     `.env` for pytest runs.
  3. Optional `payload.source.setup` argv list (typically
     `pip install -e .` or `npm ci`).
  4. Hand off to the test runner (§11) with the workspace path and
     the chosen `params.entry` script.
  5. On terminal, wipe the secrets file (§5.8 step 5) and remove the
     workspace.
  Note: a Python-Wippersnapper *installer* (deploy the long-lived
  client onto an SBC and start it as a service) is a small wrapper
  around `GitDeploy` that runs a systemd-unit install step after
  setup; it isn't a separate Flasher.
- `SerialCapture` — opens a streaming SSH channel against the host's
  `/dev/serial/by-id/...` (stable ID, because `ttyACM*` numbering is
  unstable across re-enumeration). Line-buffered, tee'd to both the
  event log and an on-disk artifact file on the controller.
- `CameraCapture` — captures frames on the HIL host (where the
  camera is attached), pulls them back over the transport's
  `copy_from` for storage in the per-job artifact directory.

Test scripts (section 11) call into the same adapter the worker
holds, so a script can request an additional reset or a fresh serial
window mid-test without knowing the transport details.

## 11. Test scripts (the "HIL repo")

Scripts are not arbitrary code submitted at request time. They are
named, versioned entry points provided either by:

- The `hil-testing` repo, vendored or installed as a Python package.
- This repo's `src/hil_controller/tests/` for built-ins (ProtoMQ
  helpers, generic "boot and look for string", etc).

A script signature:

```python
REQUIRES = TopologySpec(
    device={"kind": "microcontroller", "capabilities": ["spi"]},
    aux=[
        {"kind": "display", "capabilities": ["display:240x320", "display:spi"]},
    ],
)

async def run(ctx: TestContext, params: dict) -> TestResult: ...
```

Each script declares a `REQUIRES` topology spec. The dashboard's
script catalogue and `GET /v1/topology/resolve` both use it so a
caller can ask "which seats can run `protomq.display-smoke` right
now?" without reading the script source.

`TestContext` exposes the adapter handle, the bound aux handles
(`ctx.aux["display"]`), a `log()` helper that goes to the event
stream, and helpers like `expect_serial("READY", timeout=10)` and
`assert_log_contains(pattern)` for the ProtoMQ case.

Allow-list of script names lives in config; submissions referencing an
unknown name — or a known name whose `REQUIRES` cannot be satisfied by
any seat in the caller's pool — are rejected at the API boundary.

Two **permissive built-ins** exist for the "trusted user wants to
test something unknown" case, both gated by the `trusted-firmware`
policy capability (§8 / §5.8 `requires_trusted`):

- `raw-firmware-smoke` — for the MCU path. Flashes the
  `payload.firmware-binary`, resets the device, opens serial, and
  streams whatever comes out for `params.observe_s` seconds. The
  script doesn't assert anything itself; the job result is `pass`
  unless the flasher itself failed or the device never enumerated
  back. Useful for "does this firmware boot at all" smoke tests
  against unfamiliar code.
- `git-clone-and-run` — for the SBC path. After `GitDeploy` lands
  the workspace and the secrets file, this script just invokes
  `params.entry` with `params.args` and propagates the exit code
  (0 → pass, non-zero → fail). The caller's repo decides what
  counts as a passing test.

Both built-ins still capture serial, dmesg (when exclusive), camera
frames, and the standard artifact set, so failure diagnosis isn't
worse than for a curated script — just the *assertion* is the
caller's responsibility. The `trusted-firmware` gate exists because
these scripts are the only path through which caller-controlled
binaries / code execute on bench hardware with bench secrets in
scope; the gate is what makes "trusted users testing unknown
firmware in unknown ways" a tractable trust boundary (§15 OQ15).

### 11.1 Wippersnapper firmware variants

Two variants exist, both speaking ProtoMQ; the bench can target
either with the same job-submission shape.

- **Arduino** — `vendor/wippersnapper-arduino` (the `migrate-api-v2`
  branch of the ai-assisted fork; `upstream` remote points at
  `adafruit/Adafruit_Wippersnapper_Arduino` for upstreaming via PR).
  Targets microcontrollers (ESP32-S2/S3, SAMD51, RP2040). Flashed via
  esptool / uf2-msc / picotool. Consumes `secrets.json` (`io_username`,
  `io_key`, `network_type_wifi.{network_ssid,network_password}`, plus
  the bench's `io_url` / `io_port` overrides — those are the
  top-level broker-host fields the firmware actually parses, per
  `vendor/wippersnapper-arduino/src/provisioning/ConfigJson.cpp`,
  defaulting to `io.adafruit.com` : `8883`). The canonical example
  is `vendor/wippersnapper-arduino/examples/secrets-examples/
  secrets-wifi.json`; this repo's `examples/wippersnapper-arduino/
  secrets.example.json` extends it with the `io_url`/`io_port`
  overrides the controller fills in at flash time.

- **Python** — single-board-computer client, **currently
  private/unreleased**. Not vendored. The Python client uses
  **different** broker-override field names from the Arduino firmware
  (not `io_url`/`io_port`); the exact names will be confirmed once
  the repo is reachable. When it opens up, expect the `displays-v2`
  branch with sub-submodules for `protomq` and
  `wippersnapper-protobufs`, and a similar config flow (`secrets.json`
  for the long-running client, `.env` for pytest runs). The bench
  already covers the Pi-class targets via the Pi 5 broker host (§13).
  Wiring info for SBC targets is reachable today from
  `vendor/protomq/scripts/*.json` — some of those demos are
  Pi-targeted even though the rest are Arduino-targeted.

The secrets-substitution flow (caller renders a `secrets.example.json`
with `envsubst`, uploads alongside firmware, controller validates +
flashes + runs) is documented in `examples/README.md` with a
`workflow_dispatch` reference workflow in
`.github/workflows/example-hil-call.yml`.

### 11.2 Two existing test bodies we integrate with

**`vendor/protomq/scripts/*.json`** — JSON, *not* Python. Each file is
a sequence of `steps` keyed off ProtoMQ topics (`checkin.request`,
`display.addedOrReplaced`, …) with `send` payloads and `waitFor`
gates. They are run by the ProtoMQ broker (Pi5 at `192.168.1.210`,
MQTT port `1884`); the device under test connects, the broker replies
according to the script, and the script asserts log/state at each
step. In v1 the controller doesn't re-implement this — a
`protomq.<script-name>` controller-side test just (a) brings the
device up via the device adapter, (b) points it at the ProtoMQ
broker, (c) tails serial + observes the broker's view of the
exchange, (d) calls pass/fail based on whether the script reached its
terminal step. The JSONs feed the topology importer for wiring info
(§5.7), not the runtime.

**`vendor/hil-detection/tests/`** — pytest suites
(`test_circuitpython.py`, `test_micropython.py`,
`test_wippersnapper.py`) with a `conftest.py` that already drives the
bench (SSH to `rpi-displays`, toggle the USB hub, flash via
`pico_hil_flash.sh`, mount CIRCUITPY, etc). **This SSH-from-an-
orchestrator-into-a-HIL-host pattern is exactly the architecture
we're formalising** — the controller is the orchestrator, the HIL
hosts are the targets, and the fixtures are the prototype of what
the device adapters will do over the host transport. Two concrete
clean-ups port hil-detection's conftest onto the new layer:

1. Drop the hardcoded `RPI_PASSWORD` / `sshpass` path entirely;
   switch to the key-based auth the HIL hosts already have set up
   for the `pi` user.
2. Pull `RPI_HOST` (and the equivalent for SBC tests) from the
   controller-injected host context instead of a module constant,
   so the same fixture can run against any host in the fleet.

The controller's `tests/runner.py` shells into pytest with markers
(`pytest -m circuitpython tests/test_circuitpython.py`) on the
target HIL host, captures the report as the job result, and streams
the live log back through the host transport.

See §15 open question 7 for the manifest ownership choice.

## 12. Security posture

**Controller host**

- Service runs as an unprivileged user (`hil`). Holds the
  per-HIL-host SSH private keys in `/etc/hil/keys/` with mode `0400`,
  owned by the `hil` user.
- No `shell=True` anywhere. All subprocess calls (locally and over
  the host transport) use argv lists with explicit binaries.
- Artifact fetches are restricted to an allow-list of hosts
  (`api.github.com`, `objects.githubusercontent.com`, …) and require a
  `sha256` from the caller; mismatch aborts before any artifact is
  pushed to a HIL host.
- Token storage: argon2id hashes only. Plain token shown once.
- OIDC: verify `aud` against a server-configured value (default
  `hil-controller`), reject tokens older than 10 minutes.
- Per-pool rate limits on job submission to prevent a runaway CI
  matrix from monopolising hardware.
- Audit table records every authenticated request (principal, route,
  job id, **target host**, **secrets profile id**, decision) with a
  30-day retention.

**Secret profiles**

- Profile values are referenced from `secrets-profiles.yaml` as
  `${env:NAME}` only; the YAML file itself contains no plaintext
  credentials. Actual values are read from a systemd
  `EnvironmentFile=/etc/hil/secrets.env` (mode `0400`, owner `hil`)
  loaded once at controller start.
- The controller never echoes a profile's secret values back through
  the API or to the dashboard. `/v1/jobs/{id}` shows only the
  profile *id* used. The audit log records the id, not the values.
- Rendered `secrets.json` / `.env` files are written to the HIL
  host's `/tmp/hil/<job-id>/`, mode `0400`, owner `pi`. On job
  terminal the worker `rm -f`s them before releasing the device.
  The event log records that the wipe happened, never the file's
  contents.
- Profiles with `requires_trusted: true` (e.g. `live-io-prod`) are
  only available to principals carrying the `trusted-firmware`
  capability — a deliberate brake on production-account exposure.
- Exclusive-host jobs that capture `host-dmesg.log` and
  `host-usbmon.pcap` get a sanitisation pass on the way back: any
  byte sequence matching a known-secret pattern (a profile value
  loaded for *this* job) is replaced with `***` before the artifact
  is persisted. Imperfect (binary in pcap may evade naive matching),
  but a basic filter against accidental log-disclosure.

**HIL hosts (rpi-displays, rpi-hil00N)**

- `pi` user authenticates with the controller's per-host public key
  only; password auth disabled, the hardcoded `pi/sjahse98`
  bench-default password in `vendor/hil-detection/tests/conftest.py`
  is rotated out as part of the cutover (see §15 OQ8).
- known_hosts pinning on the controller side so a man-in-the-middle
  swap can't hijack a HIL session.
- udev rules grant `pi` access to the specific `/dev/serial/by-id/...`,
  USB-IP control node, MCP23017 I²C bus, and the
  solenoid-hub HID device on rpi-displays. No `sudo`.
- Each HIL host runs a long-lived `usbip-autobind` udev rule + a
  short-lived per-job pytest/flasher invocation. There is no
  persistent agent in v1.

## 13. Deployment

Three tiers:

1. **Controller host** — an independent Linux box (Pi, NUC, VM,
   whatever). Requirements: network reachability to every HIL host
   and to `pi5-protomq`, Python 3.11+, disk for SQLite + job
   artifacts, ability to bind an HTTPS port. Does **not** have any
   DUTs attached.
2. **HIL host fleet** — `rpi-displays` for microcontroller DUTs,
   `rpi-hil001` … `rpi-hil007` for SBC DUTs. Each runs a `pi` user
   the controller SSHes into.
3. **ProtoMQ broker** — `pi5-protomq` (`192.168.1.210`, MQTT `1884`,
   web UI `5173`). DUTs talk to it as MQTT clients during
   protomq-flavoured tests. The controller observes the broker but
   does not host it. Not strictly required for non-protomq tests.

**Controller host install** (concretely):

- systemd unit running `uvicorn hil_controller.main:app` bound to
  `127.0.0.1`. Caddy or nginx in front terminates TLS and serves the
  dashboard over the LAN.
- SQLite file in `/var/lib/hil/` with WAL mode. Daily
  `sqlite3 .backup` to a sibling file; logs and artifacts under
  `/var/lib/hil/jobs/<id>/`.
- Hosts, devices, pools, and OIDC policy live in `/etc/hil/` as
  YAML, watched for changes and reloaded without a restart.
- Per-HIL-host SSH private keys in `/etc/hil/keys/`, mode `0400`,
  one file per host (or one shared key for the SBC fleet if that's
  how the bench is set up).

**HIL host prep** (per host, one-off; will be a `deploy/setup-hil-host.sh`
that the controller pushes via SSH):

- Authorise the controller's public key for the `pi` user.
- Install `vendor/usbip-autoattach/server/` (udev rule +
  `usbip-autobind` helper, `usbipd -D` running, `usbip_host` module
  pinned).
- On `rpi-displays`: install `vendor/hil-detection/usb_hub.py` and
  `solenoid_hub_control.py` under `/opt/hil/`, plus the udev rules
  for the MCP23017 and the stable `/dev/serial/by-id/...` symlinks.
- On `rpi-hil00N`: same `pi`-user SSH setup; per-port power-control
  bits land when that hardware does.
- Install Python (system or virtualenv) sufficient for the pytest
  suites in `vendor/hil-detection/tests/` to run.

Single-binary install isn't a goal; the controller is installed via
the Python package + the deploy scripts.

## 14. Observability

- Structured logs (JSON) to stdout, captured by journald.
- `/metrics` (Prometheus) — queue depth, jobs by state, per-device
  utilisation, flash duration histograms, OIDC verification failures.
- Dashboard has a "live tail" panel per job using HTMX SSE on top of
  the same event-log endpoint.

## 15. Open questions

Things worth resolving before implementation, in rough priority order:

1. **Artifact provenance.** Do we want to require a signed
   GitHub Actions attestation for any binary we flash, or is
   "caller-supplied sha256 + host allow-list" enough for v1?
2. **Concurrent jobs on the same device family.** Is "one lock per
   physical device" sufficient, or do some test scripts need exclusive
   access to a shared resource (e.g. a single camera covering two
   positions)? If so, generalise to named resource locks.
3. **Result reporting back to GitHub.** v1 returns results only to the
   long-poll caller. Do we also want to post a check-run via a GitHub
   App, so the dashboard can deep-link from the PR?
4. **USB-IP topology.** When the Pi is *exporting* a device to a remote
   developer's machine, do we still want the API to be able to grab it
   back for a CI job (with a "kick" notification), or is the device
   strictly one mode at a time?
5. **Camera artifacts.** Always-on capture vs. capture-on-trigger.
   Storage cost vs. debuggability.
6. **Snapper-Python / Snapper-Arduino flashing.** Confirm the exact
   tooling so the flasher adapters can be specced precisely.
7. **Topology source of truth.** Wiring info today is split across
   `vendor/hil-detection/references/hardware.md` (power/reset
   channels) and `vendor/protomq/scripts/*.json` (device↔display
   binding + display interface). Recommendation: write the
   `protomq_scripts.py` and `hardware_md.py` importers, produce a
   single `topology.yaml` we own from here, and let the importers run
   periodically as a *drift detector* — they flag when an upstream
   file disagrees with the manifest, rather than overwriting. Open:
   does the protomq team want to keep adding new boards via more
   `scripts/*.json` (and we follow), or should new boards land
   directly in `topology.yaml`?
8. **Hardcoded password in hil-detection conftest.** The
   `RPI_PASSWORD = "sjahse98"` constant in
   `vendor/hil-detection/tests/conftest.py` was originally a
   workaround; key-based auth for the `pi` user already exists on
   every HIL host. The cutover is just (a) PR `hil-detection` to
   read the controller-supplied host context instead of the
   hardcoded constants, (b) delete the password from the file. This
   is no longer architectural — it's a small cleanup PR.
9. **Channel 3 of the solenoid map** is recorded as `UNCONFIRMED` in
   `hardware.md` — "does not produce unique device on toggle test".
   Worth a bench session to either populate or formally retire that
   channel; the resolver will currently see it as a "missing seat".
10. **Python Wippersnapper repo access.** The Python WS variant is
    private/unreleased and not vendored. When it opens up we need
    (a) the canonical repo URL + branch, (b) confirmation of its
    sub-submodule layout (the user described `displays-v2` →
    `protomq` + `wippersnapper-protobufs`), (c) whether dual-push to
    an Adafruit upstream is desired the way protomq dual-pushes to
    `tyeth/protomq`.
11. **Host transport: SSH vs. agent.** v1 ships SSH (asyncssh,
    persistent connection per host, multiplexed channels) because
    it's zero-install on the HIL hosts beyond the SSH key that's
    already there. Failure modes that may push us to an HTTP agent
    later: (a) hung remote processes after a TCP drop (SSH alone
    can't clean these up without `setsid` + an unhappy reaper),
    (b) stdout truncation on disconnect mid-flash, (c) wanting to
    sandbox the per-job pytest invocation under a systemd-run
    scope. Decision deferred until we hit one of those; the
    transport interface is shaped so swapping is a single-class
    change.
12. **Artifact transfer to HIL hosts.** Two options. (a) Controller
    fetches the artifact (with sha256 verification), then pushes
    once to the chosen host's `/tmp/hil/<job-id>/`. (b) Controller
    hands the URL + sha256 to the host, which fetches it directly.
    (a) is simpler, keeps egress on one box, and means the host
    only needs the controller in its allow-list; (b) is faster
    when the artifact is large and the controller-to-HIL link is
    slow. Recommend (a) for v1.
13. **Per-host concurrency caps.** ~~A noisy flash can saturate
    rpi-displays' USB bus.~~ **Resolved**: Host rows carry
    `max_concurrent_jobs` (§5.1). SBC hosts default to `1`,
    microcontroller hosts unbounded. The `exclusive.host` job flag
    (§7.1) is the explicit opt-in when a job needs the host to
    itself for hard-to-trace problems — see §9 for scheduling
    semantics.
14. **SBC test execution shape.** ~~Flasher vs distinct phase~~
    **Resolved**: a SBC payload is `payload.kind == "git-source"`
    and the controller's `GitDeploy` adapter (§10.2) fills the
    flasher slot in the state machine — clone, render secrets,
    optional setup command, hand off to the test runner, wipe
    secrets at terminal. The microcontroller-style state machine
    (§6) stays unchanged; "flashing" is the right verb for both
    paths since both turn a payload into a runnable device state.
15. **Trust model for arbitrary firmware from trusted users.**
    Trusted users want to flash binaries we have not vetted and
    run SBC code we have not reviewed. The current draft cordons
    this with: (a) `trusted-firmware` capability in the auth
    policy, (b) `requires_trusted` on sensitive secret profiles
    (§5.8), (c) permissive built-in scripts (`raw-firmware-smoke`,
    `git-clone-and-run`) that *only* trusted callers can target.
    Open: do we additionally want to *snapshot* every binary /
    cloned tree the trusted path runs (sha256 stored, artifact
    retained for N days) so a post-hoc forensic investigation is
    possible? And: is `exclusive.host: true` *required* for the
    permissive scripts (forcing dmesg/usbmon capture so any weird
    behaviour leaves a trail), or just recommended?
16. **Live IO production access from the bench.** Using
    `secrets_profile: live-io-prod` means a DUT on the bench talks
    to `io.adafruit.com` with a real production account. Open
    questions: (a) which AIO sub-account exactly — a dedicated
    bench account, or a real customer account in a sandboxed
    feed namespace? (b) is there a rate-limit budget we should
    enforce on the controller side so a runaway bench doesn't
    hammer the production broker? (c) do we want a "destructive
    action" deny-list (no `feed-delete`, no `device-delete`) the
    controller can enforce by inspecting the protomq exchange,
    or do we trust the test scripts to be well-behaved?

## 16. Milestones

Status key: **[done]** shipped, **[partial]** partially implemented, **[open]** not started.

- **M0** [done] — pyproject, FastAPI app factory, `/healthz` `/readyz`, SQLite init,
  `hil-controller-ci.yml` CI. Branch: `claude/m0-m1-hil-controller-impl`.

- **M1** [done] — jobs, events SQLite schema; `POST /v1/jobs`, `GET /v1/jobs/{id}`,
  `GET /v1/jobs/{id}/wait` long-poll, `POST /v1/jobs/{id}/cancel`; in-process asyncio
  scheduler + EventBus; fake adapter worker driving the full state machine.
  23 unit tests pass (TDD). HTMX dashboard not yet built.

- **M1.5** [open] — topology manifest resolver; `/v1/hosts`, `/v1/devices`,
  `/v1/aux`, `/v1/topology`, `/v1/topology/resolve` endpoints. `protomq_scripts.py`
  and `hardware_md.py` importers.

- **M2** [partial] — bearer-token auth implemented: static bootstrap token
  (`HIL_STATIC_TOKEN` env) and argon2id DB tokens via `scripts/mint-token.py`.
  GitHub OIDC verifier, policy file, and audit log not yet implemented.

- **M2.5** [open] — secret profiles YAML; `${env:...}` resolver;
  per-job secrets materialisation onto the HIL host; sanitisation pass.

- **M3** [done] — `SSHTransport` (`asyncssh`, key auth, per-call connections).
  `RealHostRegistry` loads `topology.yaml` and returns SSH-backed adapters.
  Known-hosts pinning and connection pooling deferred (open question 11).

- **M3.5** [open] — first real MCU adapter chain against `rpi-displays`:
  serial capture, esptool flasher, MCP23017 reset. Exercise `exclusive.host`.

- **M4** [open] — USB-IP via `usbip-autoattach`, solenoid-hub reset,
  `uf2-msc` + `picotool` flashers, hardcoded-password cleanup (OQ8).

- **M4.5** [done] — `GitDeployAdapter` (clone → setup → run → cleanup over SSH),
  `RealHostRegistry.get_adapter` wires it to SBC jobs via `git-clone-and-run`.
  `deploy/topology.example.yaml` seeds the first SBC host config.

- **M5** [open] — ProtoMQ helpers; camera capture; artifact storage; Prometheus
  `/metrics`; `raw-firmware-smoke` permissive built-in; `live-io-test` /
  `live-io-prod` profiles; Python Wippersnapper submodule (OQ10).

Past M5 we revisit dynamic hardware switching, GitHub check-run posting,
and the SSH → agent transport upgrade (open question 11) based on what
we've learned.
