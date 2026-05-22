---
name: project-state
description: Current implementation state of the HIL controller
metadata:
  type: project
---

As of 2026-05-22, main is at commit 8ad86c3.

**What is built:**

M0–M4.5: pyproject.toml, FastAPI app, `/healthz` `/readyz`, SQLite init, `POST /v1/jobs`,
long-poll wait, cancel, asyncio scheduler+EventBus, SSHTransport, GitDeployAdapter,
RealHostRegistry, static + argon2id bearer auth, mint-token.py, topology.example.yaml,
systemd unit, hil-controller-ci.yml.

M1.5: hosts/devices/auxes/connections/audit_log DB tables; topology seeder; full topology
REST endpoints.

M2 (no OIDC): Principal auth, pool/profile/capability gating, audit log, mint-token.py
extended.

LocalTransport + PAT injection: `hosts/local.py` subprocess transport; `source.pat` injects
into HTTPS clone URL; `RealHostRegistry` routes `kind: local` hosts to LocalTransport;
`topology.example.yaml` has a localhost host + wippersnapper-python device;
`examples/wippersnapper-python/job.json` + `scripts/submit-wipper-test.sh` for demo runs.

M5 (partial — ProtoMQ observer): `adapters/protomq_observer.py` — activate script via HTTP,
subscribe MQTT `#`, forward messages as job log events (protobuf topics shown as size,
others decoded), emit completed steps on teardown. `aiomqtt>=2.0` added.

Scheduler now wired to RealHostRegistry: `main.py` creates `RealHostRegistry` when
`HIL_TOPOLOGY_FILE` is set; was always using `_FakeAdapter` before.

Log streaming: `GitDeployAdapter` stores `_run_stdout/_run_stderr/_deploy_stdout/_deploy_stderr`;
`JobWorker` emits them as `log` kind events after run/deploy phases.

ProtoMQ observer wired into worker: started as concurrent asyncio Task during run phase,
cancelled after test, completed steps emitted. Configured via `params.protomq.{broker_host,
mqtt_port, api_port, script}`.

**87 tests pass, 0 failures.**

**Not yet done:**
- M2 remainder: GitHub OIDC verifier, policy file
- M2.5: secret profiles YAML, per-job secrets materialisation, artifact sanitisation
- M3.5: MCU adapter chain (serial capture, esptool, MCP23017)
- M4: USB-IP, solenoid-hub reset, uf2-msc / picotool flashers
- M5 remainder: camera capture, artifact storage, Prometheus metrics
- HTMX dashboard
- topology/importers/ (protomq_scripts.py, hardware_md.py)
- ProtoMQ protobuf decoding (Python proto definitions not yet compiled)
- GET /v1/jobs/{id}/logs non-blocking endpoint
