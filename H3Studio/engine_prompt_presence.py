"""Conservative, bounded detection of an accepted prompt lost by its engine."""
from __future__ import annotations

from datetime import datetime, timezone
import time


MISSING_GRACE_SECONDS = 90.0
MIN_MISSING_CHECKS = 3


class ComfyJobMissingError(RuntimeError):
    error_code = "engine_job_missing"

    def __init__(self, checks: int, seconds: float):
        super().__init__(
            "引擎已找不到這筆生成工作，尚未取得結果。原稿、素材與設定已保留；"
            "請重新確認設定後再生成，系統未自動重送。")
        self.checks = checks
        self.seconds = seconds


class EnginePromptPresenceGuard:
    """Only consecutive, reliable absence can terminate a lost prompt watch.

    A present queue item has no wall-clock timeout. Failed/ambiguous queries
    clear absence evidence rather than turning a network failure into a loss.
    """

    def __init__(self, prompt_id, *, grace_seconds=None, min_checks=None, clock=None):
        self.prompt_id = str(prompt_id)
        self.grace_seconds = MISSING_GRACE_SECONDS if grace_seconds is None else grace_seconds
        self.min_checks = MIN_MISSING_CHECKS if min_checks is None else min_checks
        self.clock = clock or time.monotonic
        self.missing_since = None
        self.missing_since_utc = None
        self.missing_checks = 0
        self.missing_seconds = 0.0
        self.state = "unknown"

    def _reset(self, state):
        self.missing_since = None
        self.missing_since_utc = None
        self.missing_checks = 0
        self.missing_seconds = 0.0
        self.state = state
        return state

    def mark_present(self):
        return self._reset("present")

    def observe(self, history_snapshot, queue_snapshot=None):
        if (not isinstance(history_snapshot, dict) or history_snapshot.get("available") is not True
                or not isinstance(history_snapshot.get("history"), dict)):
            return self._reset("unknown")
        if history_snapshot["history"]:
            return self.mark_present()
        if not isinstance(queue_snapshot, dict) or queue_snapshot.get("available") is not True:
            return self._reset("unknown")
        entries = []
        for name in ("running", "pending"):
            rows = queue_snapshot.get(name)
            count = queue_snapshot.get(name + "_count")
            if not isinstance(rows, list) or type(count) is not int or count != len(rows):
                return self._reset("unknown")
            for row in rows:
                if (not isinstance(row, dict) or "prompt_id" not in row or
                        row["prompt_id"] is not None and
                        (not isinstance(row["prompt_id"], str) or not row["prompt_id"].strip())):
                    return self._reset("unknown")
                entries.append(row["prompt_id"])
        if self.prompt_id in entries:
            return self.mark_present()
        # A gateway may redact another owner's prompt ID. It cannot provide
        # proof of absence while unidentified entries remain in the queue.
        if None in entries:
            return self._reset("unknown")
        now = self.clock()
        if self.missing_since is None:
            self.missing_since = now
            self.missing_since_utc = datetime.now(timezone.utc).isoformat()
        self.missing_checks += 1
        self.missing_seconds = max(0.0, now - self.missing_since)
        self.state = ("engine_missing" if self.missing_checks >= self.min_checks and
                      self.missing_seconds >= self.grace_seconds else "monitoring")
        return self.state

    def fields(self):
        now = datetime.now(timezone.utc).isoformat()
        fields = {"recovery_state": self.state, "recovery_last_checked_at": now,
                  "recovery_missing_since": self.missing_since_utc,
                  "recovery_missing_checks": self.missing_checks,
                  "recovery_missing_seconds": round(self.missing_seconds, 3)}
        if self.state == "present":
            fields["engine_last_seen_at"] = now
        return fields

    def raise_if_missing(self):
        if self.state == "engine_missing":
            raise ComfyJobMissingError(self.missing_checks, self.missing_seconds)
