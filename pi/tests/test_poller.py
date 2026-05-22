"""Tests for poller.py — payload mapping under various claude.ai responses."""

import json
from pathlib import Path

import httpx
import pytest

from poller import (
    build_payload,
    load_session_config,
    parse_epoch,
    poll_usage,
)


# ── parse_epoch ────────────────────────────────────────────────────────

def test_parse_epoch_iso_with_z():
    # claude.ai returns ISO8601 with "Z" suffix
    # recomputed: int(datetime.fromisoformat("2026-05-22T17:42:00+00:00").timestamp())
    assert parse_epoch("2026-05-22T17:42:00Z") == 1779471720

def test_parse_epoch_iso_with_offset():
    assert parse_epoch("2026-05-22T17:42:00+00:00") == 1779471720

def test_parse_epoch_iso_with_fractional_seconds():
    assert parse_epoch("2026-05-22T17:42:00.123Z") == 1779471720

def test_parse_epoch_numeric_string():
    assert parse_epoch("1748022120") == 1748022120
    assert parse_epoch("1748022120.5") == 1748022120

def test_parse_epoch_none_and_empty():
    assert parse_epoch(None) is None
    assert parse_epoch("") is None
    assert parse_epoch("   ") is None

def test_parse_epoch_invalid():
    assert parse_epoch("not a date") is None


# ── build_payload ──────────────────────────────────────────────────────

def test_build_payload_full_usage():
    usage = {
        "five_hour": {"utilization": 42.3, "resets_at": "2026-05-22T17:42:00Z"},
        "seven_day": {"utilization": 67.0, "resets_at": "2026-05-26T09:00:00Z"},
    }
    p = build_payload(now=1748000000, usage=usage, status="ok")
    assert p["s"] == 42.3
    assert p["w"] == 67.0
    # recomputed: int(datetime.fromisoformat("2026-05-22T17:42:00+00:00").timestamp())
    assert p["sr"] == 1779471720
    # recomputed: int(datetime.fromisoformat("2026-05-26T09:00:00+00:00").timestamp())
    assert p["wr"] == 1779786000
    assert p["st"] == "ok"
    assert p["ts"] == 1748000000

def test_build_payload_missing_five_hour():
    usage = {"seven_day": {"utilization": 8.0, "resets_at": "2026-05-26T09:00:00Z"}}
    p = build_payload(now=1748000000, usage=usage, status="ok")
    assert p["s"] is None and p["sr"] is None
    assert p["w"] == 8.0

def test_build_payload_utilization_null():
    usage = {"five_hour": {"utilization": None, "resets_at": "2026-05-22T17:42:00Z"},
             "seven_day": {"utilization": 8.0,  "resets_at": "2026-05-26T09:00:00Z"}}
    p = build_payload(now=1748000000, usage=usage, status="ok")
    assert p["s"] is None
    assert p["w"] == 8.0

def test_build_payload_out_of_range_rejected():
    # firmware/src/payload.cpp:72 rejects 0..100 violations; we do the
    # same for consistency between the M5StickC and Inky paths.
    usage = {"five_hour": {"utilization": 150.0, "resets_at": "2026-05-22T17:42:00Z"},
             "seven_day": {"utilization": 67.0,  "resets_at": "2026-05-26T09:00:00Z"}}
    p = build_payload(now=1748000000, usage=usage, status="ok")
    assert p["s"] is None   # rejected
    assert p["w"] == 67.0

def test_build_payload_empty_usage_with_explicit_status():
    p = build_payload(now=1748000000, usage=None, status="net")
    assert p["s"] is None and p["w"] is None
    assert p["sr"] is None and p["wr"] is None
    assert p["st"] == "net"


# ── poll_usage ─────────────────────────────────────────────────────────
# Uses httpx.MockTransport so we never hit the real claude.ai.

async def _poll_with_mock(handler, *, expect_status="ok"):
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, timeout=5.0) as client:
        payload = await poll_usage(
            session_key="sk-ant-sid02-fake",
            org_uuid="00000000-0000-0000-0000-000000000000",
            client=client,
            now=1748000000,
        )
    return payload


async def test_poll_usage_200_full_response():
    def handler(req):
        assert req.headers.get("anthropic-client-platform") == "web_claude_ai"
        assert req.url.path.endswith("/usage")
        return httpx.Response(200, json={
            "five_hour": {"utilization": 42.3, "resets_at": "2026-05-22T17:42:00Z"},
            "seven_day": {"utilization": 67.0, "resets_at": "2026-05-26T09:00:00Z"},
        })
    p = await _poll_with_mock(handler)
    assert p["s"] == 42.3 and p["w"] == 67.0
    assert p["st"] == "ok"


async def test_poll_usage_401_sets_auth():
    def handler(req):
        return httpx.Response(401, text="Unauthorized")
    p = await _poll_with_mock(handler)
    assert p["st"] == "auth"
    assert p["s"] is None and p["w"] is None


async def test_poll_usage_403_sets_auth():
    def handler(req):
        return httpx.Response(403, text="Forbidden")
    p = await _poll_with_mock(handler)
    assert p["st"] == "auth"


async def test_poll_usage_429_sets_rate():
    def handler(req):
        return httpx.Response(429, text="Too many requests")
    p = await _poll_with_mock(handler)
    assert p["st"] == "rate"


async def test_poll_usage_500_sets_net():
    def handler(req):
        return httpx.Response(500, text="Server error")
    p = await _poll_with_mock(handler)
    assert p["st"] == "net"


async def test_poll_usage_cloudflare_html_sets_net():
    def handler(req):
        return httpx.Response(200, text="<html>cloudflare challenge</html>",
                              headers={"content-type": "text/html"})
    p = await _poll_with_mock(handler)
    assert p["st"] == "net"


async def test_poll_usage_connection_error_sets_net():
    def handler(req):
        raise httpx.ConnectError("DNS failure")
    p = await _poll_with_mock(handler)
    assert p["st"] == "net"


# ── load_session_config ────────────────────────────────────────────────

def test_load_session_config_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr("poller.SESSION_CFG", tmp_path / "nope.json")
    with pytest.raises(FileNotFoundError):
        load_session_config()

def test_load_session_config_invalid_json(tmp_path, monkeypatch):
    cfg = tmp_path / "session.json"
    cfg.write_text("{ not valid")
    monkeypatch.setattr("poller.SESSION_CFG", cfg)
    with pytest.raises(ValueError, match="not valid JSON"):
        load_session_config()

def test_load_session_config_missing_field(tmp_path, monkeypatch):
    cfg = tmp_path / "session.json"
    cfg.write_text(json.dumps({"session_key": "sk-foo"}))   # no org_uuid
    monkeypatch.setattr("poller.SESSION_CFG", cfg)
    with pytest.raises(ValueError, match="missing required"):
        load_session_config()

def test_load_session_config_happy_path(tmp_path, monkeypatch):
    cfg = tmp_path / "session.json"
    cfg.write_text(json.dumps({"session_key": "sk-ant-sid02-x", "org_uuid": "uuid-y"}))
    monkeypatch.setattr("poller.SESSION_CFG", cfg)
    key, org = load_session_config()
    assert key == "sk-ant-sid02-x" and org == "uuid-y"
