---
name: reference-bench-host
description: Bench repo layout + deploy workflow for the live HIL controller (host/SSH basics in project-deployment)
metadata:
  type: reference
---

Deploy/layout details for the live controller. For host + SSH-client basics see
[[project-deployment]] and [[ssh-openssh]] (Windows OpenSSH at
`/c/Windows/System32/OpenSSH/ssh.exe`).

- **Repo:** `/home/particle/dev-projects/python/usbip-hil-controller`
- **Service:** systemd `hil-controller.service`, `EnvironmentFile=run/controller.env`
  (sets `HIL_DB_PATH=run/jobs.db`, `HIL_TOPOLOGY_FILE=run/topology.yaml`,
  `HIL_STATIC_TOKEN`, host/port).
- **DB query:** `sqlite3` CLI is NOT installed — use `.venv/bin/python3` + `sqlite3` module.
- **Topology:** `run/topology.yaml` is **git-ignored** — edited directly on the bench, not
  via git. All MCU devices are in `pool: public` with capabilities `[arduino, wippersnapper, ...]`.

**Deploy is always via git** ([[feedback-commit-and-push]]): push to origin, then on the bench
`git pull && sudo systemctl restart hil-controller`. Never scp/rsync code. M6 migrations are
additive (new columns/tables) so pulling forward is safe.
