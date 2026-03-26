"""
Tests for webhook verification and HMAC signature validation.
No external services required.
"""
import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.whatsapp import verify_signature

client = TestClient(app)


# ── GET /webhook ───────────────────────────────────────────────────────────────

def test_webhook_verification_success(monkeypatch):
    monkeypatch.setattr("app.api.webhook.settings.WA_VERIFY_TOKEN", "my_test_token")
    resp = client.get("/webhook", params={
        "hub.mode": "subscribe",
        "hub.verify_token": "my_test_token",
        "hub.challenge": "abc123",
    })
    assert resp.status_code == 200
    assert resp.text == "abc123"


def test_webhook_verification_wrong_token(monkeypatch):
    monkeypatch.setattr("app.api.webhook.settings.WA_VERIFY_TOKEN", "my_test_token")
    resp = client.get("/webhook", params={
        "hub.mode": "subscribe",
        "hub.verify_token": "wrong_token",
        "hub.challenge": "abc123",
    })
    assert resp.status_code == 403


# ── HMAC signature ─────────────────────────────────────────────────────────────

def test_verify_signature_valid(monkeypatch):
    monkeypatch.setattr("app.services.whatsapp.settings.WA_APP_SECRET", "test_secret")
    payload = b'{"entry":[]}'
    sig = hmac.new(b"test_secret", payload, hashlib.sha256).hexdigest()
    assert verify_signature(payload, f"sha256={sig}") is True


def test_verify_signature_invalid(monkeypatch):
    monkeypatch.setattr("app.services.whatsapp.settings.WA_APP_SECRET", "test_secret")
    payload = b'{"entry":[]}'
    assert verify_signature(payload, "sha256=deadbeef") is False


# ── Acknowledgement message ────────────────────────────────────────────────────

def test_ack_message_contains_ref_id():
    from app.services.whatsapp import build_ack_message
    msg = build_ack_message("GR-DMO-2026-0001", "critical", "infrastructure")
    assert "GR-DMO-2026-0001" in msg
    assert "CRITICAL" in msg


def test_ack_message_low_urgency():
    from app.services.whatsapp import build_ack_message
    msg = build_ack_message("GR-DMO-2026-0002", "low", "education")
    assert "GR-DMO-2026-0002" in msg
    assert "registered for review" in msg
