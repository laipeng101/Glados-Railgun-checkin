import assert from "node:assert/strict";
import { before, after, test } from "node:test";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { Miniflare, convertV4MiniflareOptions } from "miniflare";

// Exercise the real scheduled handler in workerd, with no live credentials or I/O.
const CRON = "11,56 8,12 * * *";
const at = slot => Date.parse(`2026-11-02T${slot}:00Z`);
let runtime, scenario, calls;
before(async () => {
  const bundled = await build({
    stdin: {
      resolveDir: fileURLToPath(new URL("../", import.meta.url)),
      contents: `import handler from "./src/index.ts";
        export default { async fetch(request) {
          const input = await request.json();
          const state = new Map(), notifications = [], results = [];
          let reads = 0, writes = 0;
          const env = {
            REPOSITORY: "laipeng101/Glados-Railgun-checkin", WORKFLOW_ID: "403045309",
            WORKFLOW_REF: "master", GITHUB_ACTIONS_TOKEN: "runtime-test-token",
            ENABLED: input.enabled ?? "true", ALERT_FROM: "from@example.com", ALERT_TO: "to@example.com",
            WATCHDOG_STATE: {
              get: async key => { reads++; return state.get(key) ?? null; },
              put: async (key, value) => { writes++; state.set(key, value); },
            },
            EMAIL: { send: async email => { notifications.push(email); } },
          };
          const realNow = Date.now;
          try {
            for (const tick of input.ticks) {
              Date.now = () => tick.now ?? tick.scheduledTime + 5000;
              const pending = [];
              await handler.scheduled(tick, env, {waitUntil(promise) {pending.push(promise);}});
              await Promise.all(pending);
              results.push(state.has("last_result") ? JSON.parse(state.get("last_result")) : null);
            }
          } finally { Date.now = realNow; }
          return Response.json({results, reads, writes, notifications});
        }};`,
    },
    bundle: true, write: false, format: "esm", platform: "browser", target: "es2022",
  });
  runtime = new Miniflare(convertV4MiniflareOptions({
    modules: true, script: bundled.outputFiles[0].text, compatibilityDate: "2026-11-02",
    outboundService: async request => {
      const url = new URL(request.url);
      assert.equal(url.origin, "https://api.github.com");
      assert.equal(request.headers.get("Authorization"), "Bearer runtime-test-token");
      const call = {method: request.method, path: url.pathname};
      calls.push(call);
      if (request.method === "POST") {
        call.body = await request.json();
        return new Response(null, {status: scenario === "post-failure" ? 503 : 204});
      }
      if (scenario === "history-failure") return new Response(null, {status: 503});
      if (url.pathname.endsWith("/runs")) {
        return Response.json({total_count: 1, workflow_runs: [{id: 1, status: "completed",
          ...(scenario === "github-reported" ? {display_title: "GLaDOS 2026-11-02 final recovery"} : {})}]});
      }
      const jobs = scenario === "success" ? [{name: "GLaDOS checkin", steps: [{
        name: "Running checkin", conclusion: "success",
        started_at: "2026-11-02T02:42:18Z", completed_at: "2026-11-02T02:42:20Z",
      }]}] : scenario === "github-reported" ? [{name: "Report actionable checkin failures",
        steps: [{name: "Report notification decision", conclusion: "failure"}]}] : [];
      assert.match(url.pathname, /\/runs\/1\/jobs$/);
      return Response.json({total_count: jobs.length, jobs});
    },
  }));
});
after(async () => { await runtime?.dispose(); });

async function invoke(mode, ticks, enabled) {
  scenario = mode;
  calls = [];
  const response = await runtime.dispatchFetch("http://local.test/", {
    method: "POST", body: JSON.stringify({ticks, enabled}),
  });
  assert.equal(response.status, 200);
  const result = await response.json();
  return {...result, calls, posts: calls.filter(call => call.method === "POST")};
}
const tick = (slot, extra = {}) => ({cron: CRON, scheduledTime: at(slot), ...extra});

test("16:56 idle callback performs zero GitHub, KV and email operations", async () => {
  const result = await invoke("missing", [tick("08:56")]);
  assert.deepEqual(result.results, [null]);
  assert.equal(result.calls.length + result.reads + result.writes + result.notifications.length, 0);
});
for (const [slot, stage] of [["08:11", "recovery"], ["12:11", "final"], ["12:56", "audit"]]) {
  test(`${slot} UTC keeps successful-day skipping and silence in the ${stage} stage`, async () => {
    const result = await invoke("success", [tick(slot)]);
    assert.equal(result.results[0].stage, stage);
    assert.equal(result.results[0].action, "already_succeeded");
    assert.equal(result.posts.length + result.notifications.length, 0);
  });
}
test("16:11 missing-day recovery dispatches once with the original daily guard", async () => {
  const result = await invoke("missing", [tick("08:11"), tick("08:11")]);
  assert.deepEqual(result.results.map(row => row.action), ["dispatched", "dispatch_already_attempted"]);
  assert.equal(result.posts.length, 1);
  assert.deepEqual(result.posts[0].body.inputs, {force_checkin: false, final_attempt: false, checkin_day: "2026-11-02"});
  assert.equal(result.notifications.length, 0);
});
test("20:11 still marks the final attempt and 20:56 only audits", async () => {
  const result = await invoke("missing", [tick("12:11"), tick("12:56")]);
  assert.equal(result.posts.length, 1);
  assert.equal(result.posts[0].body.inputs.final_attempt, true);
  assert.equal(result.posts[0].body.inputs.force_checkin, false);
  assert.deepEqual(result.results.map(row => row.stage), ["final", "audit"]);
  assert.equal(result.results[1].action, "alerted");
  assert.equal(result.notifications.length, 1);
});
test("a delayed recovery cannot become a final attempt or audit", async () => {
  const result = await invoke("missing", [tick("08:11", {now: at("12:56")})]);
  assert.equal(result.results[0].stage, "recovery");
  assert.equal(result.posts[0].body.inputs.final_attempt, false);
  assert.equal(result.notifications.length, 0);
});
test("a delayed final callback still dispatches instead of becoming an audit", async () => {
  const result = await invoke("missing", [tick("12:11", {now: at("12:56")})]);
  assert.equal(result.results[0].stage, "final");
  assert.equal(result.posts.length, 1);
  assert.equal(result.posts[0].body.inputs.final_attempt, true);
});
test("previous Beijing-day deliveries still expire before GitHub or email I/O", async () => {
  const result = await invoke("missing", [tick("12:11", {now: Date.parse("2026-11-02T16:00:00Z")})]);
  assert.equal(result.results[0].action, "expired_tick");
  assert.equal(result.calls.length + result.notifications.length, 0);
});
test("legacy and merged delivery overlap cannot repeat a recovery POST", async () => {
  const result = await invoke("missing", [tick("08:11", {cron: "11 8 * * *"}), tick("08:11")]);
  assert.equal(result.posts.length, 1);
  assert.equal(result.results[1].action, "dispatch_already_attempted");
});
test("all three legacy schedules remain correctly routed during propagation", async () => {
  const result = await invoke("success", [tick("08:11", {cron: "11 8 * * *"}),
    tick("12:11", {cron: "11 12 * * *"}), tick("12:56", {cron: "56 12 * * *"})]);
  assert.deepEqual(result.results.map(row => row.stage), ["recovery", "final", "audit"]);
  assert.equal(result.posts.length + result.notifications.length, 0);
});
test("ordinary history failure is quiet; final failure alerts once across the audit", async () => {
  const result = await invoke("history-failure", [tick("08:11"), tick("12:11"), tick("12:56")]);
  assert.deepEqual(result.results.map(row => row.action), ["recovery_deferred", "alerted", "alert_already_reported"]);
  assert.equal(result.posts.length, 0);
  assert.equal(result.notifications.length, 1);
});
test("a failed final dispatch retains its single independent alert", async () => {
  const result = await invoke("post-failure", [tick("12:11"), tick("12:56")]);
  assert.equal(result.posts.length, 1);
  assert.equal(result.notifications.length, 1);
});
test("a final failure already reported by GitHub does not send a second email", async () => {
  const result = await invoke("github-reported", [tick("12:56")]);
  assert.equal(result.results[0].action, "github_already_reported_final_failure");
  assert.equal(result.posts.length + result.notifications.length, 0);
});
test("unexpected or retired schedules exit before binding I/O", async () => {
  const result = await invoke("missing", [tick("08:11", {cron: "1 5 2 11 *"}), tick("08:10")]);
  assert.equal(result.calls.length + result.reads + result.writes + result.notifications.length, 0);
});
test("disabled mode remains silent and performs no binding I/O", async () => {
  const result = await invoke("missing", [tick("08:11")], "false");
  assert.equal(result.calls.length + result.reads + result.writes + result.notifications.length, 0);
});
