# Independent checkin watchdog

This Cloudflare Worker uses one Cron Trigger, independently of GitHub's
scheduled-event delivery. It never receives the GLaDOS Cookie or contacts GLaDOS.

| Asia/Shanghai | Action when no actual successful checkin step exists today |
| --- | --- |
| 16:11 | Dispatch ordinary recovery, unless another run is already active |
| 16:56 | Idle callback: return without GitHub, KV or email operations |
| 20:11 | Dispatch the final recovery, queuing behind an active run if necessary |
| 20:56 | Confirm success or send an independent final-failure alert |

The single UTC expression is `11,56 8,12 * * *`: four callbacks per day, with
three business checks. Stages use `controller.scheduledTime`, not the current
clock, so late delivery cannot turn an ordinary recovery into a final attempt
or audit. Previous-day deliveries still expire under the existing daily guard.
The three former expressions are accepted only at their matching planned slots
while schedule changes propagate; they are not retained as configured triggers.

The two existing GitHub schedules remain. All attempts use the same workflow
concurrency group and daily success guard. The Worker passes `force_checkin=false`
and a dated `checkin_day`. A recovery queued beyond its Beijing day expires.

Only an actual successful **Running checkin** step is success evidence. Outer
workflow success and skipped jobs are not. History is paginated, old attempts
remain visible, and incomplete/ambiguous history cannot authorize another checkin.
POST dispatches are never automatically retried after an ambiguous transport
failure. KV receipts limit repeated deliveries within the same recovery window;
GitHub concurrency and its second daily guard serialize the actual GLaDOS calls.

## Notifications

- Successful or already-completed/skipped days stay quiet.
- Explicit GLaDOS authentication failures keep immediate native GitHub failure
  email notifications.
- Other ordinary/native-window failures wait for independent recovery.
- A failed explicitly marked final recovery fails the notification job and uses
  the existing GitHub failure-email setting.
- Cloudflare covers the case where final dispatch/history is unavailable or the
  final workflow never starts/finishes by the audit deadline. Its email binding
  is restricted to the configured sender and recipient. The audit suppresses a
  second email when the final GitHub notification step already reported failure.

This covers a missed GitHub cron event without moving the GLaDOS Cookie to
another provider. It still needs GitHub's workflow-dispatch and runner services;
an outage there is reported by the independent email path.

## Provisioning

1. Copy `wrangler.example.jsonc` to the ignored `wrangler.jsonc`. Set the account,
   KV namespace and email addresses; keep `ENABLED=false` until checks pass.
   For local type generation, copy `.dev.vars.example` to the ignored `.dev.vars`.
   The empty entry declares the secret name; production uses the encrypted binding.
2. Use a dedicated fine-grained GitHub personal access token restricted to this
   repository, with **Actions: Read and write** and automatic Metadata read only.
   Store it as Worker secret `GITHUB_ACTIONS_TOKEN`. Do not use a broad personal
   CLI token, give Contents write access, or transfer `GLADOS_COOKIES`.
3. Keep `workers_dev=false` and preview URLs disabled. The checker is invoked
   only by Cron Triggers; no public management endpoint is required.
4. Onboard a dedicated Email Service sending subdomain and verify the fixed
   recipient. Keep any existing apex-domain MX records. Validate email delivery
   before relying on the independent alert path.
5. Deploy the updated GitHub workflow and policy. For initial verification only,
   set `ENABLED=verify` and a temporary cron. This inspects an already-successful
   day, probes dispatch once with the normal daily guard still enabled, and
   sends one setup email to the verified recipient. Inspect KV `setup_status`,
   `last_result` and `email_verification`. An uncertain transport or email result
   fails verification and is not blindly retried.
6. Remove the temporary cron, set `ENABLED=true` and enable only
   `11,56 8,12 * * *`. Delayed verification ticks are ignored in production.
   When migrating from three triggers, deploy the compatible handler first,
   then replace the entire Cron list with the single expression. Cloudflare
   schedule changes can take up to 15 minutes to propagate.

Local checks:

```sh
npm ci
npx wrangler types
npm test
npm run test:runtime
npm run typecheck
npm run build
```

Use `wrangler secret put GITHUB_ACTIONS_TOKEN` or Cloudflare's encrypted secret UI.
Never put credentials in command-line arguments, source, the config, or chat.
Keep `worker-configuration.d.ts` generated locally from the deployed bindings.
