"""Asserts the inference pipeline never reaches the public internet.

Crude but effective: monkey-patch socket.getaddrinfo to refuse anything
that isn't localhost. If the LLM/STT/RAG modules try to phone home,
the test fails.
"""

import socket

import pytest


@pytest.fixture
def block_external_dns(monkeypatch):
    real_getaddrinfo = socket.getaddrinfo

    def guarded(host, *args, **kw):
        if host in ("localhost", "127.0.0.1", "::1"):
            return real_getaddrinfo(host, *args, **kw)
        raise OSError(f"Blocked external DNS lookup to {host} — Adjutant must be offline")

    monkeypatch.setattr(socket, "getaddrinfo", guarded)


def test_form_registry_offline(block_external_dns):
    """Loading the form registry is pure Python — must work offline."""
    from adjutant.forms import REGISTRY  # noqa: F401
    assert REGISTRY


def test_per_diem_offline(block_external_dns):
    """Per-diem lookup reads a local JSON cache — must work offline."""
    from adjutant.per_diem import lookup
    rate = lookup("nowhere", "ZZ")
    assert rate["lodging"] > 0
    assert rate["mie"] > 0
