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
- **Pi side** — orchestrates flashing, reset, serial capture, optional
  USB-IP attach, and runs the canned test script (e.g. ProtoMQ
  validation against captured log output).

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
                +-----------------------------------+
   GitHub CI ── │   FastAPI app (single process)    │ ── HTMX dashboard
   (POST job,   │                                   │    (same origin)
    long-poll)  │  ┌──────────┐   ┌──────────────┐  │
                │  │  HTTP /  │   │ Background   │  │
                │  │  HTMX    │──▶│ worker pool  │──┼──▶ Hardware adapters
                │  │  router  │   │ (asyncio)    │  │     (USB-IP, flash,
                │  └────┬─────┘   └──────┬───────┘  │      serial, reset)
                │       │                │          │
                │       ▼                ▼          │
                │   ┌─────────────────────────┐    │
                │   │  SQLite (jobs, events,  │    │
                │   │  devices, tokens, audit)│    │
                │   └─────────────────────────┘    │
                +-----------------------------------+
                              │
                              ▼
                External repos shelled out to:
                - usbip-auto-attach   (USB-IP client management)
                - hil-testing repo    (named test scripts / fixtures)
                - flash tools         (esptool, picotool, dfu-util, …)
```

One process, one SQLite file, async workers in the same event loop. No
Redis, no Celery, no external broker in v1.

## 4. Repository layout (proposed)

```
docs/
  ARCHITECTURE.md          # this file
  API.md                   # to follow once endpoints firm up
  DEPLOY.md                # systemd unit, user creation, udev rules
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
    adapters/
      base.py              # DeviceAdapter protocol
      usbip.py             # shells out to usbip-auto-attach
      solenoid_hub.py      # USB hub + solenoid reset
      flashers/            # esptool, picotool, dfu, snapper-py, …
      serial_capture.py
      camera.py            # optional frame grabs alongside serial
    topology/
      manifest.py          # YAML loader for devices + aux + mux matrix
      resolver.py          # selector → (device, aux bindings, mux ops)
      importers/
        protomq_scripts.py # scrape display-v2 test scripts → manifest
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

### 5.1 Device

A physical thing wired to the Pi — the *unit under test*. Fixed in v1.

| Field             | Notes                                                       |
|-------------------|-------------------------------------------------------------|
| `id`              | Short stable slug, e.g. `snapper-py-01`.                    |
| `kind`            | `microcontroller` / `sbc` / `snapper-arduino` / …           |
| `model`           | More specific tag (`rp2040`, `esp32-s3`, `pi-zero-2w`, …).  |
| `capabilities`    | Tags the resolver matches on (`spi`, `i2c`, `cdc-acm`, …).  |
| `usb_path`        | Stable `by-path` or USB-IP busid.                           |
| `reset_channel`   | Solenoid-hub channel id, nullable if device self-resets.    |
| `flasher`         | Name of registered flasher adapter.                         |
| `serial_port`     | `/dev/serial/by-id/...`, baud, parity.                      |
| `camera_id`       | Optional v4l2 device watching this position.                |
| `pool`            | Logical grouping for authorization (`public`, `internal`, …).|
| `exposable_via`   | `local`, `usbip`, or both. Controls remote-attach jobs.     |
| `status`          | `available` / `in-use` / `quarantined` / `offline`.         |

A device on its own is not usually enough to run a meaningful test —
you also need the right peripherals wired to it. Those live in
§5.4 (auxiliary components) and §5.5 (connectivity matrix).

Devices are seeded from a YAML file checked into `deploy/`. The DB row
is hydrated on startup; humans can mark a device `quarantined` from the
dashboard without editing YAML.

### 5.2 Job

| Field             | Notes                                                       |
|-------------------|-------------------------------------------------------------|
| `id`              | UUIDv7, sortable.                                           |
| `submitted_by`    | Token id or OIDC subject (repo+workflow+ref).               |
| `repo`            | `owner/name` from auth context, not request body.           |
| `request`         | JSON: device selector, script name, params, artifact ref.   |
| `state`           | See state machine below.                                    |
| `assigned_device` | Filled in at `assigned` transition.                         |
| `created_at`, `started_at`, `finished_at`                                       |
| `result`          | `pass` / `fail` / `error` / `timeout` / `cancelled`.        |
| `summary`         | Short human string for dashboard rows.                      |
| `artifacts`       | List of saved files: serial log, camera frames, exit codes. |

### 5.3 Job event log

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

### 5.4 Auxiliary component

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

### 5.5 Connectivity matrix (multiplex)

Most aux-to-device wiring in v1 is **fixed**: aux `X` is bolted to
device `Y` and only `Y`. But some test rigs route auxes through a
mux — a TCA9548 I²C switch, an SPI mux, a relay-controlled GPIO
cross-bar, a USB hub with per-port power control. The manifest models
this uniformly so the scheduler can answer "can I give job J a device
of kind K with a 240x320 SPI display attached?" without the caller
knowing which physical seats satisfy that.

```yaml
# /etc/hil/topology.yaml  (excerpt, illustrative)
devices:
  - id: rp2040-01
    kind: microcontroller
    model: rp2040
    pool: public
  - id: esp32s3-01
    kind: microcontroller
    model: esp32-s3
    pool: public

auxes:
  - id: ili9341-2.4-01
    kind: display
    model: ili9341
    interface: spi
    capabilities: [display, "display:240x320", "display:spi"]
    observability: camera
  - id: bme280-01
    kind: sensor
    model: bme280
    interface: i2c
    capabilities: [sensor, "sensor:env", "i2c-target:0x76"]

connections:
  # Fixed wiring: aux belongs to exactly one device.
  - aux: bme280-01
    device: rp2040-01
    bus: i2c0

  # Multiplexed: aux can be routed to any of several devices via a mux.
  - aux: ili9341-2.4-01
    via:
      mux: spi-mux-01
      channels:
        rp2040-01: 0
        esp32s3-01: 1

muxes:
  - id: spi-mux-01
    kind: spi
    control: { adapter: gpio-relay, channel: 3 }
    exclusive: true       # only one downstream device active at a time
```

Three resolver rules fall out of this:

1. **Fixed-wired aux** is implicitly locked when its device is locked.
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
| GET    | `/v1/devices`                     | required | List devices visible to the caller's pool. Supports `?kind=&model=&capability=&aux=&pool=` filters; `?include=aux,connections` to expand. |
| GET    | `/v1/devices/{id}`                | required | Device detail + current job + attached/reachable aux. |
| GET    | `/v1/aux`                         | required | List auxiliary components. Same filter/include grammar. |
| GET    | `/v1/aux/{id}`                    | required | Aux detail + which devices it can be routed to (and via which mux). |
| GET    | `/v1/topology`                    | required | Full graph: devices, auxes, muxes, connections. Suitable for the dashboard's wiring view and for CI to introspect before submission. |
| POST   | `/v1/topology/resolve`            | required | Dry-run a job selector → returns matching `(device, aux bindings, mux ops)` candidates and any structured rejection reason. No job is created. |
| GET    | `/healthz`, `/readyz`             | none     | Liveness / readiness.                    |
| GET    | `/`, `/jobs`, `/jobs/{id}`, `/devices`, `/topology` | none (read-only) | HTMX dashboard pages.    |

### 7.1 `POST /v1/jobs` body

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
  "script": "protomq.validate-logs",            // allow-listed name
  "params": { "scenario": "boot-handshake" },   // passed to the script
  "artifact": {
    "kind": "github-release",                   // or "github-actions-artifact", "url+sha256"
    "repo": "owner/name",
    "tag": "v1.2.3",
    "asset": "firmware.bin",
    "sha256": "…"
  },
  "timeouts": { "total_s": 600, "flash_s": 120, "run_s": 300 },
  "metadata": { "pr": 42, "commit": "abc1234" }  // surfaced in dashboard
}
```

`target.device` can be a concrete `{ "id": "rp2040-01" }` or an
abstract selector (`kind` / `model` / `capabilities`); the topology
resolver picks the least-loaded matching seat. `target.requires` is a
list of auxiliary selectors that must be physically attached **or
reachable via a mux** from the chosen device — the resolver runs
*before* the job is accepted, so callers get a structured 409 with the
unsatisfiable selector rather than a mid-flash failure.

`artifact` is optional — some test scripts (USB-IP attach a device that
is already provisioned, just exercise it) don't flash anything.

CI that wants to see what's available before submitting can call
`POST /v1/topology/resolve` with the same `target` block and get back
the candidate seats without enqueueing anything.

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
- Policy file (YAML, hot-reloaded) maps claims to device pools, e.g.:

  ```yaml
  - match: { repository: "adafruit/protomq", ref: "refs/heads/main" }
    allow_pools: ["public", "protomq"]
  - match: { repository_owner: "adafruit" }
    allow_pools: ["public"]
  ```

- No long-lived secret on the CI side; revocation is by editing the
  policy file.

Both paths produce a `Principal { kind, subject, repo, allowed_pools }`
which the job submission handler uses to (a) reject jobs targeting
disallowed pools, (b) stamp `repo` and `submitted_by` on the row from
the auth context, never from the request body.

## 9. Scheduler & worker

- One `asyncio` scheduler task. Wakes on (a) new job enqueued,
  (b) device freed, (c) periodic tick.
- Per-device `asyncio.Lock`. A job in `assigned`+ holds its device's
  lock until terminal.
- Worker per active job (`asyncio.create_task`). Worker drives the
  state machine, emits events, calls into the device adapter.
- Concurrency cap = number of devices. No queueing across devices in
  v1; if you want parallelism, add devices.
- Graceful shutdown: scheduler stops accepting new starts, in-flight
  workers get a `cancel()` budget, then process exits. SQLite state is
  the source of truth; on restart, any non-terminal job is marked
  `error` with reason `restart` (we do not auto-resume hardware work).

## 10. Hardware adapter layer

The point of this layer is that the worker doesn't care whether a
device is local USB, USB-IP exported, or behind a solenoid hub —
adapters compose.

```python
class DeviceAdapter(Protocol):
    async def acquire(self) -> None: ...           # power on, usbip attach, etc
    async def reset(self) -> None: ...             # solenoid pulse or DTR toggle
    async def flash(self, artifact: Artifact) -> None: ...
    async def open_serial(self) -> AsyncIterator[bytes]: ...
    async def release(self) -> None: ...           # detach, power off
```

Concrete adapters compose smaller pieces:

- `UsbIpAttach` — shells `usbip` via the usbip-auto-attach repo,
  surfaces busid back as a local `/dev/...` node.
- `SolenoidHubReset` — talks to the USB hub controller that drives the
  reset solenoid; channel per device from YAML.
- `Flasher` — pluggable: esptool, picotool, dfu-util, a Snapper-Python
  uploader, a Snapper-Arduino sketch upload, or "no-op" for
  pre-provisioned devices.
- `SerialCapture` — `pyserial-asyncio`, line-buffered, tee'd to both
  the event log and an on-disk artifact file.
- `CameraCapture` — optional, captures N frames around interesting
  events for visual confirmation.

Test scripts (section 11) call into the same adapter the worker holds,
so a script can request an additional reset or a fresh serial window
mid-test.

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

### 11.1 Importing from the protomq display-v2 scripts

The current source of truth for which board is wired to which display
lives implicitly inside the test scripts in the protomq repo's
`display-v2` branch (`scripts/` folder). Until those scripts are
ported to declare a `REQUIRES` block, the topology importer
(`src/hil_controller/topology/importers/protomq_scripts.py`) will
parse them and emit a starting `topology.yaml` plus a list of scripts
whose wiring it could not infer. That bootstrap is one-shot; after the
manifest exists, the scripts get rewritten to consume `ctx.aux[...]`
instead of hard-coded pin assignments. See §15 open question 7.

## 12. Security posture

- Service runs as an unprivileged user (`hil`). udev rules grant that
  user access to the specific `/dev/serial/by-id/...`, USB-IP control
  node, and the solenoid-hub HID device. No `sudo`.
- No `shell=True` anywhere. All subprocess calls use argv lists with
  explicit binaries.
- Artifact fetches are restricted to an allow-list of hosts
  (`api.github.com`, `objects.githubusercontent.com`, …) and require a
  `sha256` from the caller; mismatch aborts before flashing.
- Token storage: argon2id hashes only. Plain token shown once.
- OIDC: verify `aud` against a server-configured value (default
  `hil-controller`), reject tokens older than 10 minutes.
- Per-pool rate limits on job submission to prevent a runaway CI
  matrix from monopolising hardware.
- Audit table records every authenticated request (principal, route,
  job id, decision) with a 30-day retention.

## 13. Deployment

- systemd unit running `uvicorn hil_controller.main:app` bound to
  `127.0.0.1`. Caddy or nginx in front terminates TLS and serves the
  dashboard over the LAN.
- SQLite file in `/var/lib/hil/` with WAL mode. Daily `sqlite3 .backup`
  to a sibling file; logs and artifacts under `/var/lib/hil/jobs/<id>/`.
- Devices, pools, and OIDC policy live in `/etc/hil/` as YAML, watched
  for changes and reloaded without a restart.
- Single-binary install isn't a goal; this is a Pi-side service
  installed via the package + a deploy script.

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
7. **Topology source of truth.** Today, the wiring between a board and
   its display/sensors is encoded inside the protomq display-v2 test
   scripts. Two choices: (a) write a one-shot importer that parses
   those scripts into the new `topology.yaml`, then have us own the
   manifest going forward; (b) keep the scripts authoritative and have
   the controller call into them to discover topology at boot. (a)
   gives us a queryable graph and a dashboard wiring view for free but
   creates a fork point with the protomq repo; (b) avoids the fork but
   means the resolver can't reason about availability without
   executing user code. Recommend (a). Either way, what mux hardware
   (if any) is currently in the rig — TCA9548, SPI mux, relay
   crossbar, USB-hub power control — needs confirming so the mux
   adapters can be specced.

## 16. Milestones

A suggested cut, each landable on its own:

- **M0** — repo skeleton, pyproject, FastAPI app, `/healthz`, CI for
  lint + tests, this doc merged.
- **M1** — domain model, SQLite schema, in-memory scheduler, fake
  adapter, end-to-end `POST /v1/jobs` → `wait` → `finished` against
  the fake. HTMX dashboard shows queue.
- **M1.5** — topology manifest loader + resolver + `/v1/devices`,
  `/v1/aux`, `/v1/topology`, `/v1/topology/resolve` endpoints. Fixed
  wiring only; mux modelled in the schema but not yet acted on. Run
  the protomq display-v2 importer to seed the first manifest.
- **M2** — auth: per-repo bearer tokens + GitHub OIDC verifier, policy
  file, audit log.
- **M3** — real adapters: serial capture, one flasher (esptool), one
  reset path. Drive a single fixed microcontroller end-to-end.
- **M4** — USB-IP integration via usbip-auto-attach, solenoid-hub
  reset, mux adapters, multi-device scheduling with resource locks
  that respect the connectivity matrix.
- **M5** — ProtoMQ test helpers, camera capture, artifact storage,
  Prometheus metrics.

Past M5 we revisit dynamic hardware switching and GitHub check-run
posting based on what we've learned.
