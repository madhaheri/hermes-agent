"""Contract checks for the inert scheduled Telegram report artifact."""

import json
from pathlib import Path


ARTIFACT = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "examples"
    / "scheduled-telegram-reports.dry-run.json"
)


def test_report_artifact_is_inert_bounded_and_failure_aware():
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))

    assert artifact["dry_run"] is True
    assert artifact["delivery"]["active"] == "local"
    assert artifact["delivery"]["network_writes"] is False
    assert artifact["redaction"]["on_redaction_error"] == "fail_closed"
    assert artifact["redaction"]["maximum_output_characters"] > 0

    reports = {report["id"]: report for report in artifact["reports"]}
    assert reports["daily-infrastructure-health"]["schedule"] == "0 8 * * *"
    assert reports["weekly-model-performance"]["schedule"] == "0 9 * * 1"
    assert reports["alert-report"]["schedule"] == "*/5 * * * *"

    for report in reports.values():
        assert report["timezone"] == "scheduler-local"
        assert report["input_schema"]["required"]
        assert report["output_schema"]["required"]
        assert set(report["failure"]) >= {"collection", "render_or_redaction", "delivery"}
