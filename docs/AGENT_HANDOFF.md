# Agent handoff

Working notes for the next Claude Code agent picking up this project.
Read this **before** asking the user "what is this project?" — most of
the orientation is already in `docs/ARCHITECTURE.md` and the rest is
here.

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
- [x] **M2 partial** — bearer auth: `HIL_STATIC_TOKEN` env + argon2id
      DB tokens. `scripts/mint-token.py` CLI.
- [x] **M3** — `SSHTransport` (asyncssh, key auth).
      `RealHostRegistry` loads `topology.yaml` and returns SSH adapters.
- [x] **M4.5** — `GitDeployAdapter` (clone → setup → run → cleanup).
      Wired to SBC jobs via `git-clone-and-run` script name.
- [x] `deploy/topology.example.yaml`, systemd unit,
      `deploy/controller.env.example`.

Not done:

- [ ] **M1.5** — `/v1/hosts`, `/v1/devices`, `/v1/aux`, `/v1/topology`
      endpoints; `protomq_scripts.py` and `hardware_md.py` importers.
- [ ] **M2 remainder** — GitHub OIDC verifier, policy file,
      `allow_profiles` / `capabilities` gating, audit log.
- [ ] **M2.5** — secret profiles YAML; per-job secrets materialisation
      onto HIL host; artifact sanitisation.
- [ ] **M3.5** — MCU adapter chain (serial capture, esptool, MCP23017).
- [ ] **M4** — USB-IP, solenoid-hub reset, uf2-msc / picotool flashers,
      hardcoded-password cleanup (OQ8).
- [ ] **M5** — ProtoMQ helpers, camera capture, artifact storage,
      Prometheus metrics, `raw-firmware-smoke` built-in.
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
  MCP23017 solenoid power/reset at I²C `0x20`. Can run many
  concurrent jobs (per-device locks); `exclusive.host: true`
  serialises everything on it.
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

- **`docs/ARCHITECTURE.md`** — full design. §15 lists 16 open
  questions; §16 has the milestone cut.
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

## Open ask from the user

`scripts/mint-token.py` is now implemented (M2 partial). It accepts
`--db`, `--label`, `--pool`, `--repo`, writes an argon2id hash row,
and prints the plain `hil_<id>_<secret>` token once.

Default controller URL confirmed: `http://wan.gdenu.fi:8080`.

## Decisions already made — don't re-litigate

These came up in conversation and were settled. Don't open them
again unless the user asks:

- **Stack**: FastAPI + HTMX/Jinja. Not Flask, not Node.
- **Queue**: in-process asyncio + SQLite. Not Redis/Celery.
- **Auth**: per-repo bearer tokens **and** GitHub Actions OIDC.
  Both, not either-or.
- **Controller location**: independent host, not on the bench.
- **Host transport**: SSH (asyncssh) for v1. Agent transport is
  open question 11, deliberately deferred.
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

## Session lineage

This handoff covers the work done in session
`session_01KXJbynVGheaSkFiZGxzSrU`. If you're picking up after a
context compaction within the same session, the conversation
summary should still cover the recent message; this doc is the
durable record. If you're a fresh agent on a new session, this is
the file to start from after `docs/ARCHITECTURE.md`.
