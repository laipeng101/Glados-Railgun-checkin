# Personal GLaDOS deployment

This fork uses the upstream Cookie-based GitHub Actions deployment described in
[README.md](README.md), with the following settings:

- One account, using only its original login domain, `glados.cloud`.
- Store only the current session Cookie pair in the repository secret
  `GLADOS_COOKIES`. Do not commit credentials to files or paste them into logs.
- Run at 09:18 and 12:18 Asia/Shanghai, followed by a fresh random delay of
  0–900 seconds for each scheduled run. The intended windows are therefore
  09:18–09:33 and 12:18–12:33; GitHub queueing may delay them further.
- Optional repository variable `GLADOS_JITTER_MAX_SECONDS` accepts an integer
  from 0 to 900. Leaving it unset uses 900. Manual runs skip the random delay.
- Before waiting or contacting GLaDOS, inspect this workflow's GitHub job
  history for an actual successful `Running checkin` step on the current
  Asia/Shanghai date. Success and an explicit already-checked-in response both
  count. Once confirmed, later runs skip the entire checkin job, including its
  random delay, dependency installation, status queries and checkin request.
- If no successful checkin is recorded today, the next candidate may perform
  the first attempt or make up for a failed/missed earlier attempt. Skipped
  jobs never count as new success records; yesterday's success does not block
  a new day. Workflow concurrency serializes overlapping triggers.
- The history reader has only repository Contents/Actions read permission and
  receives no GLaDOS credentials. History/API errors stop that run rather than
  risk a duplicate request. Keep today's run history; deleting it removes the
  evidence used to avoid repeats. A successful step spanning midnight requires
  manual inspection because its exact checkin date is ambiguous.
- Manual runs follow the same daily check by default. The optional
  `force_checkin` checkbox bypasses it only when explicitly selected for
  debugging; it can send an additional request even after today's success.
- Retain upstream device-platform matching. HTTP retries are limited to GET
  queries; checkin POSTs and rate-limit responses are not automatically retried.
  The upstream single retry for an explicit device-platform mismatch remains.
- Missing credentials, explicit authentication failures (`code=-2` or HTTP
  401/403) use checkin exit code 2. Other failures use exit code 1. An explicit
  already-checked-in response counts as success. If authentication has already
  failed during the initial status query, no checkin POST or points query follows.
- Native GitHub Actions email notifications are used. In personal Notification
  settings, enable Actions Email with **Only notify for failed workflows**.
  Cookie/authentication failures produce an actionable workflow failure at once.
  Other first-window failures wait quietly for the afternoon fallback; a failed
  fallback (including an unreadable daily history or setup failure) fails the
  workflow and triggers the native email. Manual failures are reported at once.
- Successful and already-completed/skipped checkins keep the workflow successful
  and send no email. A keepalive-only failure does not send a checkin alert.
  Attempt jobs use job-level `continue-on-error`, preserving their actual step
  results for the daily guard. The final notification job controls the workflow's
  actionable failure status; the first failed attempt remains visible in logs.
- The two cron entries are separate so the primary/fallback identity survives
  GitHub queue delays. Their times and random delay windows are unchanged.
- Automatic points exchange and third-party notifications remain optional and
  inactive unless their corresponding secrets are configured.
- Action versions are pinned. The keepalive job has separate, repository-scoped
  Actions write permission and does not receive GLaDOS credentials. It keeps
  scheduled runs enabled without deleting run history.

Random timing spreads requests across a small window; it does not make a script
indistinguishable from a browser or guarantee account safety. GitHub-hosted runner
IP addresses can change. GLaDOS may change its interfaces or account policies.
Review the current [GLaDOS terms](https://glados.cloud/terms) before use.

If a run reports an expired session, log in at `glados.cloud` again and update the
existing `GLADOS_COOKIES` secret. If it reports rate limiting or an account
restriction, stop manual retries and inspect the response before continuing.
