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
- Retain upstream device-platform matching. HTTP retries are limited to GET
  queries; checkin POSTs and rate-limit responses are not automatically retried.
  The upstream single retry for an explicit device-platform mismatch remains.
- Missing or expired credentials and failed checkins fail the Actions job.
  An explicit already-checked-in response counts as success.
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
