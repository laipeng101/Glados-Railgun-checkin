import assert from "node:assert/strict";
import { test } from "node:test";
import { beijingDay, boundedJson, runWatchdog, stageForSchedule, WATCHDOG_CRON, verifyDispatch, type Config, type Services } from "../src/core.ts";

const NOW = Date.parse("2026-11-02T08:11:00Z");
const CONFIG: Config = { repository: "laipeng101/Glados-Railgun-checkin", workflowId: "403045309", ref: "master", token: "test-token" };
type Row = Record<string, unknown>;
const successfulJob = (started = "2026-11-02T02:42:18Z", completed = "2026-11-02T02:42:20Z"): Row => ({
  name: "GLaDOS checkin", conclusion: "success",
  steps: [{ name: "Running checkin", conclusion: "success", started_at: started, completed_at: completed }],
});
function fixture(options: { runs?: Row[]; jobs?: Record<string, Row[]>; historyStatus?: number; dispatchStatus?: number; dispatchThrows?: boolean; truncated?: boolean; notifyThrows?: boolean } = {}) {
  const calls: { url: URL; method: string; body: string | undefined }[] = [];
  const notifications: string[] = [];
  const state = new Map<string, string>();
  const request: typeof fetch = async (input, init) => {
    const url = new URL(String(input));
    assert.equal(url.origin, "https://api.github.com");
    assert.equal(new Headers(init?.headers).get("Authorization"), "Bearer test-token");
    assert.equal(init?.redirect, "manual");
    const method = init?.method || "GET";
    calls.push({ url, method, body: typeof init?.body === "string" ? init.body : undefined });
    if (method === "POST") {
      if (options.dispatchThrows) throw new Error("connection lost after server accepted test-token");
      return new Response(null, { status: options.dispatchStatus || 204 });
    }
    if (options.historyStatus) return new Response(null, { status: options.historyStatus });
    const runs = options.runs || [];
    if (url.pathname.endsWith("/runs")) {
      const page = Number(url.searchParams.get("page"));
      return Response.json({ total_count: runs.length + (options.truncated ? 1 : 0), workflow_runs: runs.slice((page - 1) * 100, page * 100) });
    }
    const match = /\/runs\/(\d+)\/jobs$/.exec(url.pathname);
    assert.ok(match);
    assert.equal(url.searchParams.get("filter"), "all");
    const jobs = options.jobs?.[match[1]] || [];
    return Response.json({ total_count: jobs.length, jobs });
  };
  const services: Services = {
    request,
    getState: async key => state.get(key) ?? null,
    putState: async (key, value) => { state.set(key, value); },
    notify: async (subject, body) => {
      notifications.push(subject + "\n" + body);
      if (options.notifyThrows) throw new Error("email unavailable");
    },
  };
  return { services, calls, notifications, state, posts: () => calls.filter(call => call.method === "POST") };
}

test("one Cron preserves all three UTC windows and has one idle slot", () => {
  for (const day of ["2026-11-02", "2026-12-31", "2027-01-01"]) {
    for (const [slot, expected] of [["08:11", "recovery"], ["08:56", null], ["12:11", "final"], ["12:56", "audit"]] as const) {
      assert.equal(stageForSchedule(WATCHDOG_CRON, Date.parse(`${day}T${slot}:00Z`)), expected);
    }
  }
});
test("legacy schedules are accepted only at their own planned slot during propagation", () => {
  assert.equal(stageForSchedule("11 8 * * *", NOW), "recovery");
  assert.equal(stageForSchedule("11 12 * * *", Date.parse("2026-11-02T12:11:00Z")), "final");
  assert.equal(stageForSchedule("56 12 * * *", Date.parse("2026-11-02T12:56:00Z")), "audit");
  assert.throws(() => stageForSchedule("11 8 * * *", Date.parse("2026-11-02T12:56:00Z")));
});
test("unknown crons and unconfigured time slots cannot select a stage", () => {
  for (const cron of ["* * * * *", "1 5 2 11 *", "56 8 * * *"]) {
    assert.throws(() => stageForSchedule(cron, NOW));
  }
  for (const slot of ["00:11", "08:10", "08:12", "12:55", "13:11", "16:11"]) {
    assert.throws(() => stageForSchedule(WATCHDOG_CRON, Date.parse(`2026-11-02T${slot}:00Z`)));
  }
});
test("invalid planned timestamps cannot authorize work", () => {
  for (const timestamp of [NaN, Infinity, -Infinity, NOW + 0.5, Number.MAX_SAFE_INTEGER]) {
    assert.throws(() => stageForSchedule(WATCHDOG_CRON, timestamp));
  }
});
test("Beijing day remains explicit across UTC midnight", () => {
  assert.equal(beijingDay(Date.parse("2026-11-01T16:00:00Z")), "2026-11-02");
});
test("today's actual successful step suppresses dispatch and every notification", async () => {
  const f = fixture({ runs: [{ id: 1, status: "completed" }], jobs: { "1": [successfulJob()] } });
  for (const stage of ["recovery", "final", "audit"] as const) {
    assert.equal((await runWatchdog(CONFIG, f.services, stage, NOW, NOW)).action, "already_succeeded");
  }
  assert.equal(f.posts().length, 0);
  assert.equal(f.notifications.length, 0);
});
test("yesterday and skipped outer-success runs are not today's success", async () => {
  const f = fixture({ runs: [{ id: 1, status: "completed", conclusion: "success" }], jobs: { "1": [successfulJob("2026-11-01T02:42:18Z", "2026-11-01T02:42:20Z"), { name: "GLaDOS checkin", conclusion: "skipped", steps: [] }] } });
  assert.equal((await runWatchdog(CONFIG, f.services, "recovery", NOW, NOW)).action, "dispatched");
  const body = JSON.parse(f.posts()[0].body || "null");
  assert.deepEqual(body, { ref: "master", inputs: { force_checkin: false, final_attempt: false, checkin_day: "2026-11-02" } });
  assert.equal(f.notifications.length, 0);
});
test("final recovery is marked explicitly and can queue behind an active run", async () => {
  const f = fixture({ runs: [{ id: 1, status: "in_progress" }] });
  assert.equal((await runWatchdog(CONFIG, f.services, "final", NOW, NOW)).action, "dispatched");
  assert.equal(JSON.parse(f.posts()[0].body || "null").inputs.final_attempt, true);
});
test("first recovery waits for an existing run", async () => {
  const f = fixture({ runs: [{ id: 1, status: "queued" }] });
  assert.equal((await runWatchdog(CONFIG, f.services, "recovery", NOW, NOW)).action, "checkin_in_flight");
  assert.equal(f.posts().length, 0);
});
test("same recovery window never blindly dispatches twice", async () => {
  const f = fixture();
  await runWatchdog(CONFIG, f.services, "recovery", NOW, NOW);
  assert.equal((await runWatchdog(CONFIG, f.services, "recovery", NOW, NOW)).action, "dispatch_already_attempted");
  assert.equal(f.posts().length, 1);
});
test("ambiguous POST failure is not retried; separate final window still can recover", async () => {
  const f = fixture({ dispatchThrows: true });
  assert.equal((await runWatchdog(CONFIG, f.services, "recovery", NOW, NOW)).action, "recovery_deferred");
  await runWatchdog(CONFIG, f.services, "recovery", NOW, NOW);
  assert.equal(f.posts().length, 1);
  assert.equal(f.notifications.length, 0);
  assert.equal((await runWatchdog(CONFIG, f.services, "final", NOW, NOW)).action, "alerted");
  assert.equal(f.posts().length, 2);
  assert.equal(f.notifications.length, 1);
  assert.ok(!f.notifications[0].includes("test-token"));
});
test("first-window API failure stays quiet and cannot authorize a checkin", async () => {
  const f = fixture({ historyStatus: 403 });
  assert.equal((await runWatchdog(CONFIG, f.services, "recovery", NOW, NOW)).action, "recovery_deferred");
  assert.equal(f.posts().length, 0);
  assert.equal(f.notifications.length, 0);
});
test("final history failure produces an independent alert exactly once", async () => {
  const f = fixture({ historyStatus: 503 });
  await runWatchdog(CONFIG, f.services, "final", NOW, NOW);
  await runWatchdog(CONFIG, f.services, "audit", NOW, NOW);
  assert.equal(f.notifications.length, 1);
  assert.equal(f.posts().length, 0);
});
test("redirected history cannot authorize a recovery dispatch", async () => {
  const f = fixture({ historyStatus: 302 });
  assert.equal((await runWatchdog(CONFIG, f.services, "recovery", NOW, NOW)).action, "recovery_deferred");
  assert.equal(f.posts().length, 0);
});
test("redirected dispatch fails final recovery without a second POST", async () => {
  const f = fixture({ dispatchStatus: 307 });
  assert.equal((await runWatchdog(CONFIG, f.services, "final", NOW, NOW)).action, "alerted");
  await runWatchdog(CONFIG, f.services, "final", NOW, NOW);
  assert.equal(f.posts().length, 1);
  assert.equal(f.notifications.length, 1);
});
test("independent audit detects a final run that never started", async () => {
  const f = fixture();
  assert.equal((await runWatchdog(CONFIG, f.services, "audit", NOW, NOW)).action, "alerted");
  assert.equal(f.posts().length, 0);
});
test("GitHub's final failure notification suppresses a duplicate independent email", async () => {
  const f = fixture({ runs: [{ id: 1, status: "completed", display_title: "GLaDOS 2026-11-02 final recovery" }], jobs: { "1": [{ name: "Report actionable checkin failures", steps: [{ name: "Report notification decision", conclusion: "failure" }] }] } });
  assert.equal((await runWatchdog(CONFIG, f.services, "audit", NOW, NOW)).action, "github_already_reported_final_failure");
  assert.equal(f.notifications.length, 0);
});
test("email transport failure is surfaced without recursively sending again", async () => {
  const f = fixture({ notifyThrows: true });
  await assert.rejects(runWatchdog(CONFIG, f.services, "audit", NOW, NOW), /email unavailable/);
  assert.equal(f.notifications.length, 1);
});
test("cross-midnight success fails closed before any POST", async () => {
  const f = fixture({ runs: [{ id: 1, status: "completed" }], jobs: { "1": [successfulJob("2026-11-01T15:59:59Z", "2026-11-01T16:00:01Z")] } });
  assert.equal((await runWatchdog(CONFIG, f.services, "recovery", NOW, NOW)).action, "recovery_deferred");
  assert.equal(f.posts().length, 0);
});
test("expired cron deliveries never operate on the next Beijing day", async () => {
  const f = fixture();
  assert.equal((await runWatchdog(CONFIG, f.services, "final", NOW - 86_400_000, NOW)).action, "expired_tick");
  assert.equal(f.calls.length, 0);
});
test("truncated history cannot cause duplicate checkins", async () => {
  const f = fixture({ truncated: true });
  assert.equal((await runWatchdog(CONFIG, f.services, "recovery", NOW, NOW)).action, "recovery_deferred");
  assert.equal(f.posts().length, 0);
});
test("history is paginated and old attempts remain visible", async () => {
  const runs = Array.from({ length: 101 }, (_, index) => ({ id: index + 1, status: "completed" }));
  const f = fixture({ runs, jobs: { "101": [successfulJob()] } });
  assert.equal((await runWatchdog(CONFIG, f.services, "recovery", NOW, NOW)).runId, 101);
  assert.equal(f.posts().length, 0);
});
test("dry runs never dispatch, email or write receipts", async () => {
  for (const stage of ["recovery", "final", "audit"] as const) {
    const f = fixture();
    await runWatchdog(CONFIG, f.services, stage, NOW, NOW, true);
    assert.equal(f.posts().length, 0);
    assert.equal(f.notifications.length, 0);
    assert.equal(f.state.size, 0);
  }
});
test("response bodies are bounded before JSON parsing", async () => {
  await assert.rejects(boundedJson(new Response("x".repeat(100)), 10), /safe history limit/);
});
test("setup probe cannot dispatch unless today is already confirmed successful", async () => {
  const f = fixture();
  await assert.rejects(verifyDispatch(CONFIG, f.services, NOW), /already successful/);
  assert.equal(f.posts().length, 0);
});
test("setup probe tests write permission once with the normal daily guard enabled", async () => {
  const f = fixture({ runs: [{ id: 1, status: "completed" }], jobs: { "1": [successfulJob()] } });
  assert.equal((await verifyDispatch(CONFIG, f.services, NOW)).action, "transport_probe_dispatched");
  assert.equal((await verifyDispatch(CONFIG, f.services, NOW)).action, "transport_probe_already_attempted");
  assert.equal(f.posts().length, 1);
  assert.equal(JSON.parse(f.posts()[0].body || "null").inputs.force_checkin, false);
});
test("checkpoint read failure cannot suppress a final-failure email", async () => {
  const f = fixture({ historyStatus: 503 });
  f.services.getState = async () => { throw new Error("KV unavailable"); };
  assert.equal((await runWatchdog(CONFIG, f.services, "audit", NOW, NOW)).action, "alerted");
  assert.equal(f.notifications.length, 1);
});
test("checkpoint write failure after email success does not resend the email", async () => {
  const f = fixture();
  f.services.putState = async () => { throw new Error("KV unavailable"); };
  assert.equal((await runWatchdog(CONFIG, f.services, "audit", NOW, NOW)).action, "alerted");
  assert.equal(f.notifications.length, 1);
});
