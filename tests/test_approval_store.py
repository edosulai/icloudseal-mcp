"""Unit tests for two-phase approval store (no network / no Touch ID UI)."""

from __future__ import annotations

import time

import pytest

from icloudseal_mcp.mcp.approval import ApprovalError, DraftStore


def test_prepare_and_begin_consume() -> None:
    store = DraftStore(ttl_ms=60_000, maximum=10)
    draft = store.prepare(
        action="notes.create",
        target="note:x",
        preview="Create note\nTitle: x",
        payload={"title": "x"},
    )
    assert draft.state == "prepared"
    assert draft.approval_id

    begun = store.begin_approval(draft.approval_id)
    assert begun.state == "awaiting-local-approval"

    consumed = store.consume_approved(draft.approval_id)
    assert consumed.payload["title"] == "x"
    assert store.get(draft.approval_id) is None


def test_missing_or_double_begin() -> None:
    store = DraftStore()
    with pytest.raises(ApprovalError):
        store.begin_approval("nope")

    draft = store.prepare(
        action="notes.create", target="note:y", preview="y", payload={}
    )
    store.begin_approval(draft.approval_id)
    with pytest.raises(ApprovalError):
        store.begin_approval(draft.approval_id)


def test_ttl_prune() -> None:
    store = DraftStore(ttl_ms=1, maximum=10)
    draft = store.prepare(
        action="notes.create", target="note:z", preview="z", payload={}
    )
    time.sleep(0.01)
    assert store.get(draft.approval_id) is None


def test_preview_limit() -> None:
    store = DraftStore()
    with pytest.raises(ApprovalError):
        store.prepare(
            action="x",
            target="t",
            preview="x" * 10_001,
            payload={},
        )
