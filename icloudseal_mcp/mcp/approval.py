"""Two-phase prepare → native macOS approval store.

Mirrors the DraftStore / native-approval pattern from whatseal-mcp and
instaseal-mcp. Prepared actions are single-use, TTL-bound, and only execute
after Touch ID or macOS login-password authorization.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..paths import APP_DIR, ensure_app_dir
from ..skill_install import packaged_helper_source

APPROVALS_DIR = APP_DIR / "approvals"
OUTCOMES_DIR = APPROVALS_DIR / "outcomes"
DEFAULT_TTL_MS = 10 * 60 * 1000
DEFAULT_MAXIMUM = 100

DEFAULT_HELPER_SOURCE = packaged_helper_source()
DEFAULT_HELPER_BIN = Path(
    os.environ.get(
        "ICLOUDSEAL_APPROVAL_HELPER",
        str(APP_DIR / "bin" / "native-approval"),
    )
)


class ApprovalError(RuntimeError):
    """Raised for prepare/approval lifecycle failures."""


def _private_dir(path: Path) -> Path:
    """Create a state directory with owner-only access."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise ApprovalError(f"State path must be a real directory, not a symlink: {path}")
    if metadata.st_uid != os.geteuid():
        raise ApprovalError(f"State directory must be owned by the current user: {path}")
    path.chmod(0o700)
    return path


def _canonical_approval_id(approval_id: str) -> str:
    """Validate IDs before using them as filenames."""
    try:
        parsed = uuid.UUID(str(approval_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ApprovalError("approval_id must be a valid UUID.") from exc
    canonical = str(parsed)
    if str(approval_id).lower() != canonical:
        raise ApprovalError("approval_id must use canonical UUID form.")
    return canonical


def _outcome_path(approval_id: str) -> Path:
    canonical = _canonical_approval_id(approval_id)
    root = _private_dir(_private_dir(_private_dir(APP_DIR) / "approvals") / "outcomes")
    path = (root / f"{canonical}.json").resolve()
    if path.parent != root.resolve():
        raise ApprovalError("Invalid approval outcome path.")
    return path


@dataclass
class Draft:
    approval_id: str
    action: str
    target: str
    preview: str
    payload: dict[str, Any]
    state: str = "prepared"
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0

    def to_public(self) -> dict[str, Any]:
        return {
            "approvalId": self.approval_id,
            "action": self.action,
            "target": self.target,
            "preview": self.preview,
            "state": self.state,
            "expiresAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.expires_at)),
            "warning": (
                "Nothing has been executed. Show this exact target and preview to the user, "
                "wait for explicit OK in chat, then call icloud_request_local_approval "
                "(Touch ID / macOS password)."
            ),
        }


class DraftStore:
    def __init__(
        self,
        *,
        ttl_ms: int = DEFAULT_TTL_MS,
        maximum: int = DEFAULT_MAXIMUM,
    ) -> None:
        self.ttl_ms = ttl_ms
        self.maximum = maximum
        self._drafts: dict[str, Draft] = {}
        self._lock = threading.Lock()


    def prepare(
        self,
        *,
        action: str,
        target: str,
        preview: str,
        payload: dict[str, Any] | None = None,
    ) -> Draft:
        if not action or not target or not preview:
            raise ApprovalError("action, target, and preview are required.")
        if len(preview) > 10_000:
            raise ApprovalError("Preview exceeds the 10,000-character safety limit.")

        with self._lock:
            self._prune_locked()
            if len(self._drafts) >= self.maximum:
                raise ApprovalError(
                    f"Too many pending drafts ({self.maximum}). "
                    "Wait for expiry or approve/cancel one."
                )
            approval_id = str(uuid.uuid4())
            now = time.time()
            draft = Draft(
                approval_id=approval_id,
                action=action,
                target=target,
                preview=preview,
                payload=deepcopy(payload or {}),
                state="prepared",
                created_at=now,
                expires_at=now + (self.ttl_ms / 1000.0),
            )
            self._drafts[approval_id] = draft
            return deepcopy(draft)

    def begin_approval(self, approval_id: str) -> Draft:
        with self._lock:
            self._prune_locked()
            draft = self._drafts.get(approval_id)
            if not draft:
                raise ApprovalError("Approval is missing or expired. Prepare the action again.")
            if draft.state != "prepared":
                raise ApprovalError(f"Approval is already {draft.state}.")
            draft.state = "awaiting-local-approval"
            return deepcopy(draft)

    def consume_approved(self, approval_id: str) -> Draft:
        with self._lock:
            draft = self._drafts.get(approval_id)
            if not draft or draft.state != "awaiting-local-approval":
                raise ApprovalError("Draft is not awaiting native user approval.")
            del self._drafts[approval_id]
            return draft

    def cancel(self, approval_id: str) -> None:
        with self._lock:
            self._drafts.pop(approval_id, None)

    def restore_prepared(self, draft: Draft) -> bool:
        """Put a draft back to prepared after cancelled/failed native dialog."""
        with self._lock:
            if draft.expires_at <= time.time():
                self._drafts.pop(draft.approval_id, None)
                return False
            draft.state = "prepared"
            self._drafts[draft.approval_id] = draft
            return True

    def get(self, approval_id: str) -> Draft | None:
        with self._lock:
            self._prune_locked()
            draft = self._drafts.get(approval_id)
            return deepcopy(draft) if draft else None

    def _prune_locked(self) -> None:
        now = time.time()
        expired = [k for k, d in self._drafts.items() if d.expires_at <= now]
        for k in expired:
            del self._drafts[k]


_OUTCOME_LOCK = threading.Lock()


def record_outcome(approval_id: str, outcome: dict[str, Any]) -> dict[str, Any]:
    ensure_app_dir()
    canonical = _canonical_approval_id(approval_id)
    path = _outcome_path(canonical)
    record = {
        "approvalId": canonical,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **outcome,
    }
    with _OUTCOME_LOCK:
        existing: dict[str, Any] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}
        existing.update(record)
        temp = path.with_name(f".{path.name}.{uuid.uuid4()}.tmp")
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(existing, handle, indent=2, default=str)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
            path.chmod(0o600)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temp.unlink(missing_ok=True)
        return existing


def get_outcome(approval_id: str) -> dict[str, Any] | None:
    path = _outcome_path(approval_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def ensure_helper_binary(
    *,
    source: Path = DEFAULT_HELPER_SOURCE,
    binary: Path = DEFAULT_HELPER_BIN,
) -> Path:
    """Compile native-approval.swift if the binary is missing or older than source."""
    if not source.exists():
        raise ApprovalError(f"Native approval source missing: {source}")

    _private_dir(APP_DIR)
    _private_dir(binary.parent)
    if binary.exists() or binary.is_symlink():
        existing = binary.lstat()
        if not stat.S_ISREG(existing.st_mode):
            raise ApprovalError("Native approval helper is not a regular file.")
        if existing.st_uid != os.geteuid():
            raise ApprovalError("Native approval helper must be owned by the current user.")
    needs_build = (not binary.exists()) or (binary.stat().st_mtime < source.stat().st_mtime)
    if needs_build:
        temp_binary = binary.with_name(f".{binary.name}.{uuid.uuid4()}.tmp")
        try:
            result = subprocess.run(
                [
                    "swiftc",
                    "-O",
                    "-o",
                    str(temp_binary),
                    str(source),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise ApprovalError(
                    "Failed to compile native approval helper: "
                    + (result.stderr.strip() or result.stdout.strip() or "unknown error")
                )
            temp_binary.chmod(0o500)
            os.replace(temp_binary, binary)
        finally:
            temp_binary.unlink(missing_ok=True)

    st = binary.lstat()
    if not stat.S_ISREG(st.st_mode):
        raise ApprovalError("Native approval helper is not a regular file.")
    if st.st_uid != os.geteuid():
        raise ApprovalError("Native approval helper must be owned by the current user.")
    binary.chmod(0o500)
    return binary


def request_native_approval(draft: Draft, *, helper: Path | None = None) -> bool:
    """Show immutable preview + require Touch ID / macOS password.

    Returns True if authorized, False if user cancelled.
    Raises ApprovalError on helper/system failure.
    """
    helper_path = ensure_helper_binary(binary=helper or DEFAULT_HELPER_BIN)
    payload = {
        "target": draft.target,
        "text": draft.preview,
        "action": draft.action,
    }
    try:
        child = subprocess.run(
            [str(helper_path)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        raise ApprovalError("Native approval timed out.") from exc
    except OSError as exc:
        raise ApprovalError(f"Native approval helper failed to start: {exc}") from exc

    if child.returncode == 0:
        return True
    if child.returncode == 2:
        return False
    detail = (child.stderr or child.stdout or "").strip()
    raise ApprovalError(f"Native approval failed (exit {child.returncode}): {detail[:300]}")


# Global process-local store (one MCP server process).
STORE = DraftStore()
_EXECUTORS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
_APPROVAL_GATE = threading.Lock()


def register_executor(action: str, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    _EXECUTORS[action] = fn


def prepare_action(
    *,
    action: str,
    target: str,
    preview: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if action not in _EXECUTORS:
        raise ApprovalError(f"No executor registered for action {action!r}.")
    draft = STORE.prepare(action=action, target=target, preview=preview, payload=payload)
    record_outcome(
        draft.approval_id,
        {"state": "prepared", "action": draft.action, "target": draft.target},
    )
    return draft.to_public()


def request_local_approval(approval_id: str) -> dict[str, Any]:
    if not _APPROVAL_GATE.acquire(blocking=False):
        raise ApprovalError("Another native approval is already in progress.")
    try:
        draft = STORE.begin_approval(approval_id)
        record_outcome(
            approval_id,
            {"state": "awaiting-local-approval", "action": draft.action, "target": draft.target},
        )
        try:
            approved = request_native_approval(draft)
        except ApprovalError:
            STORE.cancel(approval_id)
            record_outcome(approval_id, {"state": "helper-failed", "action": draft.action})
            raise

        if not approved:
            # Allow retry of the same prepared draft.
            restored = STORE.restore_prepared(draft)
            state = "prepared" if restored else "expired"
            record_outcome(
                approval_id,
                {"state": state, "lastAttempt": "cancelled", "action": draft.action},
            )
            return {
                "success": False,
                "approvalId": approval_id,
                "state": state,
                "message": (
                    "User cancelled Touch ID / password authorization. "
                    + ("Draft remains prepared until expiry." if restored else "Draft expired.")
                ),
            }

        draft = STORE.consume_approved(approval_id)
        executor = _EXECUTORS.get(draft.action)
        if not executor:
            record_outcome(approval_id, {"state": "failed", "error": "missing executor"})
            raise ApprovalError(f"No executor for action {draft.action!r}.")

        record_outcome(approval_id, {"state": "executing", "action": draft.action})
        try:
            result = executor(draft.payload)
        except Exception as exc:  # noqa: BLE001 - surface to agent + outcome log
            record_outcome(
                approval_id,
                {"state": "failed", "action": draft.action, "error": str(exc)},
            )
            raise ApprovalError(f"Approved action failed during execution: {exc}") from exc

        outcome = {
            "state": "succeeded",
            "action": draft.action,
            "target": draft.target,
            "result": result,
        }
        record_outcome(approval_id, outcome)
        return {
            "success": True,
            "approvalId": approval_id,
            "state": "succeeded",
            "action": draft.action,
            "target": draft.target,
            "result": result,
        }
    finally:
        _APPROVAL_GATE.release()
