"""Tests for device matching and no-match handling in the host registry."""

import pytest

from hil_controller.hosts.registry import HostRegistry, _UnmatchedAdapter


def _registry(devices, hosts=None):
    reg = HostRegistry(topology_file="")
    reg._hosts = hosts or [{"id": "h1"}]
    reg._devices = devices
    return reg


_MCU = {
    "id": "mcu-revtft",
    "host_id": "h1",
    "kind": "microcontroller",
    "model": "Feather ESP32-S3 Reverse TFT",
    "pool": "public",
    "capabilities": ["arduino", "wippersnapper", "tft-display"],
    "status": "available",
}


def test_explicit_id_skips_pool_gate():
    reg = _registry([_MCU])
    # Job pins a pool the device is NOT in, but selects it explicitly by id.
    req = {"target": {"pool": "wippersnapper-arduino", "device": {"id": "mcu-revtft"}}}
    result = reg.find_device_for_job(req)
    assert result is not None
    _host, device = result
    assert device["id"] == "mcu-revtft"


def test_explicit_id_unavailable_no_match():
    busy = {**_MCU, "status": "busy"}
    reg = _registry([busy])
    req = {"target": {"device": {"id": "mcu-revtft"}}}
    assert reg.find_device_for_job(req) is None


def test_pool_mismatch_no_match_without_id():
    reg = _registry([_MCU])
    req = {"target": {"pool": "wippersnapper-arduino", "device": {"kind": "microcontroller"}}}
    assert reg.find_device_for_job(req) is None


def test_capability_subset_match():
    reg = _registry([_MCU])
    req = {"target": {"pool": "public", "device": {"capabilities": ["wippersnapper"]}}}
    assert reg.find_device_for_job(req) is not None


def test_capability_not_subset_no_match():
    reg = _registry([_MCU])
    req = {"target": {"pool": "public", "device": {"capabilities": ["bluetooth"]}}}
    assert reg.find_device_for_job(req) is None


@pytest.mark.asyncio
async def test_unmatched_adapter_acquire_raises():
    adapter = _UnmatchedAdapter("no device matched (pool='x')")
    with pytest.raises(RuntimeError, match="no device matched"):
        await adapter.acquire()
