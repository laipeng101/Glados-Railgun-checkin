import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import daily_checkin_guard as guard


NOW = guard.timestamp("2026-11-01T04:30:00Z")  # Beijing 12:30.
TODAY_START = "2026-10-31T20:50:15Z"  # Beijing Nov 1, despite the UTC date.
TODAY_END = "2026-10-31T20:50:16Z"


def checkin_job(conclusion="success", started=TODAY_START, completed=TODAY_END):
    return {
        "name": guard.CHECKIN_JOB,
        "conclusion": conclusion,
        "steps": [{"name": guard.CHECKIN_STEP, "conclusion": conclusion,
                   "started_at": started, "completed_at": completed}],
    }


def history_with(jobs, run_ids=None):
    if run_ids is None:
        run_ids = list(jobs)
    def items(path, key, **query):
        if key == "workflow_runs":
            return iter({"id": run_id} for run_id in run_ids)
        return iter(jobs[int(path.split("/")[1])])
    return Mock(items=Mock(side_effect=items))


class DailyHistoryTests(unittest.TestCase):
    def result(self, history, now=NOW, current_run=99, attempt=1):
        return guard.successful_run_today(history, current_run, attempt, now)

    def test_today_success_skips_even_when_utc_date_is_yesterday(self):
        self.assertEqual(self.result(history_with({1: [checkin_job()]})), 1)

    def test_failed_checkin_allows_fallback(self):
        self.assertIsNone(self.result(history_with({1: [checkin_job("failure")]})))

    def test_no_earlier_attempt_allows_checkin(self):
        self.assertIsNone(self.result(history_with({})))

    def test_skipped_checkin_is_not_a_new_success_record(self):
        self.assertIsNone(self.result(history_with({2: [checkin_job("skipped")]})))

    def test_other_successful_jobs_are_not_checkin_evidence(self):
        job = checkin_job()
        job["name"] = "Keep scheduled workflow active"
        self.assertIsNone(self.result(history_with({2: [job]})))

    def test_successful_checkin_survives_later_job_or_workflow_failure(self):
        job = checkin_job()
        job["conclusion"] = "failure"  # A post step/other job can fail afterwards.
        self.assertEqual(self.result(history_with({1: [job]})), 1)

    def test_next_beijing_day_allows_a_new_checkin(self):
        tomorrow = guard.timestamp("2026-11-01T16:01:00Z")
        self.assertIsNone(self.result(history_with({1: [checkin_job()]}), now=tomorrow))

    def test_skipped_runs_cannot_extend_yesterdays_success_into_today(self):
        yesterday = checkin_job(started="2026-10-30T20:50:15Z", completed="2026-10-30T20:50:16Z")
        history = history_with({2: [checkin_job("skipped")], 1: [yesterday]})
        self.assertIsNone(self.result(history))

    def test_current_first_attempt_is_not_its_own_success_evidence(self):
        self.assertIsNone(self.result(history_with({99: [checkin_job()]})))

    def test_rerun_uses_success_from_an_older_attempt(self):
        history = history_with({99: [checkin_job("failure"), checkin_job()]})
        self.assertEqual(self.result(history, attempt=2), 99)
        self.assertEqual(history.items.call_args.kwargs["filter"], "all")

    def test_query_includes_previous_day_for_delayed_runs(self):
        history = history_with({})
        self.result(history)
        self.assertEqual(history.items.call_args.kwargs["created"],
                         "2026-10-30T16:00:00Z..2026-11-01T04:30:00Z")

    def test_midnight_ambiguity_stops_automatic_requests(self):
        job = checkin_job(started="2026-10-31T15:59:59Z", completed="2026-10-31T16:00:01Z")
        with self.assertRaises(guard.HistoryUnavailable):
            self.result(history_with({1: [job]}))

    def test_missing_success_timestamp_is_not_assumed_to_be_a_failure(self):
        job = checkin_job()
        del job["steps"][0]["completed_at"]
        with self.assertRaises(KeyError):
            self.result(history_with({1: [job]}))


class PaginationTests(unittest.TestCase):
    def test_runs_and_jobs_are_paginated(self):
        history = guard.GitHubHistory("owner/repo", "test-token")
        def get(path, **query):
            if path.startswith("workflows/"):
                rows = [{"id": 2}, {"id": 3}] if query["page"] == 1 else [{"id": 1}]
                return {"total_count": 3, "workflow_runs": rows}
            if path == "runs/1/jobs":
                rows = [{"name": "irrelevant"}] * 2 if query["page"] == 1 else [checkin_job()]
                return {"total_count": 3, "jobs": rows}
            return {"total_count": 0, "jobs": []}
        with patch.object(guard, "PAGE_SIZE", 2), patch.object(history, "get", side_effect=get):
            self.assertEqual(guard.successful_run_today(history, 99, 1, NOW), 1)

    def test_truncated_history_stops_instead_of_allowing_checkin(self):
        history = guard.GitHubHistory("owner/repo", "test-token")
        with patch.object(history, "get", return_value={"total_count": 1001, "workflow_runs": []}):
            with self.assertRaises(guard.HistoryUnavailable):
                list(history.items("workflows/gladosCheck.yml/runs", "workflow_runs"))


class EntryPointTests(unittest.TestCase):
    def invoke(self, history=None, force="false", event="workflow_dispatch", failure=None):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            summary = Path(temporary) / "summary"
            environ = {
                "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary),
                "GITHUB_REPOSITORY": "owner/repo", "GH_TOKEN": "test-token",
                "GITHUB_RUN_ID": "99", "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_EVENT_NAME": event, "FORCE_CHECKIN": force,
            }
            clock = Mock(wraps=guard.datetime)
            clock.now.return_value = NOW
            with patch.dict(os.environ, environ, clear=True), \
                    patch.object(guard, "datetime", clock), \
                    patch.object(guard, "GitHubHistory", return_value=history) as factory, \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                if failure:
                    factory.side_effect = failure
                code = guard.main()
            return code, output.read_text() if output.exists() else "", factory.call_count

    def test_automatic_and_default_manual_runs_skip_after_success(self):
        for event in ("schedule", "workflow_dispatch"):
            with self.subTest(event=event):
                code, output, _ = self.invoke(history_with({1: [checkin_job()]}), event=event)
                self.assertEqual((code, output), (0, "should_run=false\n"))

    def test_failed_first_attempt_emits_permission_to_run_backup(self):
        code, output, _ = self.invoke(history_with({1: [checkin_job("failure")]}), event="schedule")
        self.assertEqual((code, output), (0, "should_run=true\n"))

    def test_api_failure_never_emits_permission_to_contact_glados(self):
        code, output, _ = self.invoke(failure=TimeoutError("GitHub unavailable"))
        self.assertEqual((code, output), (1, ""))

    def test_explicit_manual_override_avoids_history_and_allows_debugging(self):
        self.assertEqual(self.invoke(force="true"), (0, "should_run=true\n", 0))

    def test_schedule_cannot_force_checkin(self):
        self.assertEqual(self.invoke(force="true", event="schedule"), (1, "", 0))


if __name__ == "__main__":
    unittest.main()
