"""Control native GitHub failure emails without sending credentials elsewhere."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path


AUTHENTICATION_EXIT_CODE = "2"  # checkin.py: EXIT_AUTHENTICATION_FAILURE


@dataclass(frozen=True)
class Decision:
    notify: bool
    message: str


def decide(daily_should_run, attempt_outcome, exit_code, event_name, schedule, final_attempt="false"):
    if exit_code == AUTHENTICATION_EXIT_CODE:
        return Decision(True, "GLaDOS credentials are missing, expired, or access was denied. Update the repository secret GLADOS_COOKIES and retry manually.")
    if daily_should_run == "false":
        return Decision(False, "Checkin was already confirmed or this dated recovery request expired. Checkin skipped; no email notification.")
    if attempt_outcome == "success" and exit_code == "0":
        return Decision(False, "Checkin succeeded or was already completed. No email notification.")
    if event_name == "workflow_dispatch" and final_attempt == "true":
        return Decision(True, "The final recovery attempt could not confirm a successful checkin. Inspect the checkin or daily-history job. GitHub failure email is required.")
    return Decision(False, "This attempt did not complete successfully. Waiting for independent recovery; no email yet.")


def main():
    decision = decide(
        os.environ.get("DAILY_SHOULD_RUN", ""),
        os.environ.get("CHECKIN_OUTCOME", ""),
        os.environ.get("CHECKIN_EXIT_CODE", ""),
        os.environ.get("GITHUB_EVENT_NAME", ""),
        os.environ.get("CHECKIN_SCHEDULE", ""),
        os.environ.get("CHECKIN_FINAL_ATTEMPT", "false"),
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
