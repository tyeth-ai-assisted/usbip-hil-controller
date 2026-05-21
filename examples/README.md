# Examples — caller-side HIL integration

Templates a downstream repository copies into its own CI to ask the
HIL controller to run a hardware test. None of these files contain
real credentials; they are skeletons for `${HIL_*}` substitution.

## Layout

```
examples/
  wippersnapper-arduino/
    secrets.example.json     # WS Arduino firmware secrets pointed at the bench ProtoMQ
  wippersnapper-python/
    secrets.example.json     # WS Python SBC client secrets
    .env.example             # pytest env loader, same fields
hil-call.sh                   # reference CI step: submit job + long-poll
```

## Secrets substitution flow

1. Caller stores real values as GitHub Actions secrets in their repo
   (`HIL_IO_USERNAME`, `HIL_IO_KEY`, `HIL_WIFI_SSID`,
   `HIL_WIFI_PASSWORD`, plus the per-repo HIL bearer token in
   `HIL_API_TOKEN` or — preferred — GitHub Actions OIDC).
2. CI step renders the chosen `secrets.example.json` with `envsubst`
   into the artifact directory, uploads alongside the firmware.
3. CI step calls `hil-call.sh` (or the equivalent in their language)
   pointing at the controller and the artifact.
4. Controller fetches the artifact, validates `sha256`, flashes the
   device, runs the requested test script, returns pass/fail.

The two `HIL_PROTOMQ_*` values are **not caller secrets** — they're
bench identity and the controller fills them in. Callers should leave
those as placeholders.

## Caller-side CI step

`hil-call.sh` is the reference flow a downstream repo can copy
verbatim. It submits a job and long-polls until terminal, exiting
non-zero on `fail` / `error` / `timeout` so the GitHub Actions job
fails the right way. See the top of the script for the env contract.

## Why two Wippersnapper variants

- **Arduino** (`Adafruit_Wippersnapper_Arduino`, this repo's
  `vendor/wippersnapper-arduino`) — microcontrollers (RP2040,
  ESP32-S2/S3, SAMD51, …). Flashed as a UF2 or via esptool.
- **Python** (currently private, not vendored — see
  `docs/ARCHITECTURE.md` §11.1) — single-board computers (Raspberry
  Pi family). Deployed as a Python package + `secrets.json`.

Both speak ProtoMQ; both accept the same `${HIL_PROTOMQ_HOST}` /
`${HIL_PROTOMQ_PORT}` placeholders in the caller-side workflow, but
render them into **different field names** inside the per-variant
`secrets.json` because the two firmwares parse different keys:

- Arduino — top-level `io_url` (string) and `io_port` (int), per
  `vendor/wippersnapper-arduino/src/provisioning/ConfigJson.cpp`.
  Defaults are `io.adafruit.com` and `8883`.
- Python — different keys, TBC once the (private) Python repo is
  reachable. The placeholder template in
  `wippersnapper-python/secrets.example.json` flags this and uses
  `mqtt_host` / `mqtt_port` as a TODO, not a stable contract.

The caller-side workflow doesn't need to know either set — it just
exports the `HIL_PROTOMQ_*` env vars and points `envsubst` at the
matching `secrets.example.json` for the variant being flashed.
