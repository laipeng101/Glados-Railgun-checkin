"""Decide whether to check in without contacting GLaDOS or reading its Cookie."""

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


BEIJING = timezone(timedelta(hours=8))
WORKFLOW_FILE = "gladosCheck.yml"
# These names are shared with the workflow, including its initial deployment.
CHECKIN_JOB = "GLaDOS checkin"
CHECKIN_STEP = "Running checkin"
PAGE_SIZE = 100
MAX_PAGES = 10


class HistoryUnavailable(RuntimeError):
    """Do not send a checkin request when previous results cannot be verified."""


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise HistoryUnavailable("GitHub returned a timestamp without a timezone")
    return parsed


class GitHubHistory:
    def __init__(self, repository, token):
        self.root = f"https://api.github.com/repos/{quote(repository, safe='/')}/actions"
        self.token = token

    def get(self, path, **query):
        request = Request(
            f"{self.root}/{path}?{urlencode(query)}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2026-03-10",
                "User-Agent": "GLaDOS-daily-checkin-guard",
            },
        )
        with urlopen(request, timeout=20) as response:
            return json.load(response)

    def items(self, path, key, **query):
        for page in range(1, MAX_PAGES + 1):
            data = self.get(path, per_page=PAGE_SIZE, page=page, **query)
            rows = data[key]
            if not isinstance(rows, list) or data["total_count"] > PAGE_SIZE * MAX_PAGES:
                raise HistoryUnavailable("GitHub history is incomplete or exceeds the scan limit")
            yield from rows
            if len(rows) < PAGE_SIZE:
                return
        raise HistoryUnavailable("GitHub history exceeds the pagination limit")


def successful_run_today(history, current_run, current_attempt, now):
    """Return a run with an actual successful checkin step on this Beijing day.

    A successful outer workflow can contain a skipped checkin, or a failed
    keepalive can follow a successful checkin. Inspect the checkin step itself.
    Include old attempts so rerunning a workflow cannot erase success evidence.
    """
    start = now.astimezone(BEIJING).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    # Runs last at most 35 minutes. Include the previous day to detect a run
    # created before midnight that executed its checkin after midnight.
    since = (start - timedelta(days=1)).astimezone(timezone.utc)
    created = f"{since:%Y-%m-%dT%H:%M:%SZ}..{now.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ}"
    runs = history.items(f"workflows/{WORKFLOW_FILE}/runs", "workflow_runs", created=created)
    for run in runs:
        if run["id"] == current_run and current_attempt == 1:
            continue
        jobs = history.items(f"runs/{run['id']}/jobs", "jobs", filter="all")
        for job in jobs:
            if job["name"] != CHECKIN_JOB:
                continue
            for step in job.get("steps", []):
                if step["name"] != CHECKIN_STEP or step["conclusion"] != "success":
                    continue
                started = timestamp(step["started_at"])
                completed = timestamp(step["completed_at"])
                if completed < started:
                    raise HistoryUnavailable("GitHub returned inconsistent checkin timestamps")
                if start <= completed < end:
                    if started < start:
                        raise HistoryUnavailable("A successful checkin crossed midnight; inspect it before forcing another request")
                    return run["id"]
    return None


def main():
    try:
        now = datetime.now(timezone.utc)
        day = now.astimezone(BEIJING).date().isoformat()
        requested_day = os.environ.get("CHECKIN_DAY", "")
        if requested_day and date.fromisoformat(requested_day).isoformat() != requested_day:
            raise HistoryUnavailable("CHECKIN_DAY must be YYYY-MM-DD")
        if requested_day and requested_day > day:
            raise HistoryUnavailable("A recovery request cannot target a future Beijing day")
        expired_request = bool(requested_day and requested_day < day)
        forced = os.environ.get("FORCE_CHECKIN", "false") == "true"
        if forced and os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
            raise HistoryUnavailable("Only an explicit manual run can force checkin")
        if expired_request:
            matching_run = None
            reason = f"Recovery request for {requested_day} expired before execution on {day}. Skipping GLaDOS requests."
        elif forced:
            matching_run = None
            reason = "Explicit manual override: checkin requested even if already completed today."
        else:
            history = GitHubHistory(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
            matching_run = successful_run_today(
                history,
                int(os.environ["GITHUB_RUN_ID"]),
                int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")),
                now,
            )
            reason = (
                f"Already checked in on {day} (Asia/Shanghai), verified in run {matching_run}. Skipping GLaDOS requests."
                if matching_run is not None else
                f"No successful checkin step found on {day} (Asia/Shanghai). Checkin or fallback is allowed."
            )
        should_run = not expired_request and matching_run is None
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"should_run={str(should_run).lower()}\n")
        print(reason)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with Path(summary).open("a", encoding="utf-8") as output:
                output.write(f"### Daily checkin decision\n\n{reason}\n")
        return 0
    except Exception as error:
        # An API outage or malformed response is not evidence of a failed
        # checkin. Fail the decision job; its dependent checkin job stays skipped.
        print(f"Unable to verify daily history; no GLaDOS request will be sent: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
