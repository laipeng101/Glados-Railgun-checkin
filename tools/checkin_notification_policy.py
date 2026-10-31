"""Control native GitHub failure emails without sending credentials elsewhere."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path


# Keep the two schedules in gladosCheck.yml separate so a delayed primary run
# can still be identified without relying on the runner's wall clock.
PRIMARY_SCHEDULE = "18 1 * * *"
AUTHENTICATION_EXIT_CODE = "2"  # checkin.py: EXIT_AUTHENTICATION_FAILURE


@dataclass(frozen=True)
class Decision:
    notify: bool
    message: str


def decide(daily_should_run, attempt_outcome, exit_code, event_name, schedule):
    if exit_code == AUTHENTICATION_EXIT_CODE:
        return Decision(True, "GLaDOS credentials are missing, expired, or access was denied. Update the repository secret GLADOS_COOKIES and retry manually.")
    if daily_should_run == "false":
        return Decision(False, "Today's checkin was already confirmed. Checkin skipped; no email notification.")
    if attempt_outcome == "success" and exit_code == "0":
        return Decision(False, "Checkin succeeded or was already completed. No email notification.")
    if event_name == "schedule" and schedule == PRIMARY_SCHEDULE:
        return Decision(False, "The primary attempt did not complete successfully. Waiting for the 12:18-12:33 Asia/Shanghai fallback; no email yet.")
    return Decision(True, "No successful checkin was confirmed after the fallback or manual attempt. Inspect the failed attempt or daily-history job. GitHub failure email is required.")


def main():
    decision = decide(
        os.environ.get("DAILY_SHOULD_RUN", ""),
        os.environ.get("CHECKIN_OUTCOME", ""),
        os.environ.get("CHECKIN_EXIT_CODE", ""),
        os.environ.get("GITHUB_EVENT_NAME", ""),
        os.environ.get("CHECKIN_SCHEDULE", ""),
    )
    print(decision.message)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as output:
            output.write(f"### Checkin notification decision\n\n{decision.message}\n")
    # Native Actions emails are configured for failed workflows only. The
    # attempt jobs retain their real failed steps; this job determines whether
    # the overall workflow failure is actionable now.
    return 1 if decision.notify else 0


if __name__ == "__main__":
    sys.exit(main())
