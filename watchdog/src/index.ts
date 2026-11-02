import { runWatchdog, stageForSchedule, verifyDispatch, type Stage, type Config, type Services } from "./core.ts";
import { fetchFromWorker } from "./platform.ts";

function config(env: Env): Config {
  return { repository: env.REPOSITORY, workflowId: env.WORKFLOW_ID, ref: env.WORKFLOW_REF, token: env.GITHUB_ACTIONS_TOKEN || "" };
}
async function sendEmail(env: Env, subject: string, text: string) {
  return env.EMAIL.send({
    to: env.ALERT_TO,
    from: { email: env.ALERT_FROM, name: "GLaDOS Watchdog" },
    subject, text,
    html: `<pre>${text.replace(/[&<>]/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[character] || character)}</pre>`,
  });
}
function services(env: Env): Services {
  return {
    request: fetchFromWorker,
    getState: key => env.WATCHDOG_STATE.get(key),
    putState: (key, value) => env.WATCHDOG_STATE.put(key, value, { expirationTtl: 7 * 86_400 }),
    notify: async (subject, text) => { await sendEmail(env, subject, text); },
  };
}
async function execute(env: Env, stage: Stage, scheduledTime: number, dryRun: boolean) {
  const result = await runWatchdog(config(env), services(env), stage, scheduledTime, Date.now(), dryRun);
  console.log(JSON.stringify(result));
  await env.WATCHDOG_STATE.put("last_result", JSON.stringify({ ...result, stage, dryRun, checkedAt: new Date().toISOString() }));
  return result;
}
async function verifySetup(env: Env, scheduledTime: number) {
  let step = "history";
  try {
    const history = await execute(env, "recovery", scheduledTime, true);
    if (history.action !== "already_succeeded") throw new Error(`Setup history verification failed: ${history.reason || history.action}`);
    step = "dispatch";
    const probe = await verifyDispatch(config(env), services(env), Date.now());
    console.log(JSON.stringify(probe));
    step = "email";
    const key = `verification-email:${history.day}`;
    const mailState = await env.WATCHDOG_STATE.get(key);
    if (mailState !== "sent") {
      if (mailState) throw new Error("Previous email verification is uncertain; inspect before retrying");
      // Setup verification is operator-enabled, never part of normal checks.
      await env.WATCHDOG_STATE.put(key, "attempted", { expirationTtl: 7 * 86_400 });
      const receipt = await sendEmail(env, "GLaDOS watchdog email verification", "This is the one-time setup verification for your independent GLaDOS watchdog. Normal success and skipped checkins stay quiet. Operational alerts are sent only for expired GLaDOS credentials or unconfirmed final recovery.");
      await env.WATCHDOG_STATE.put("email_verification", JSON.stringify({ sentAt: new Date().toISOString(), receipt }));
      await env.WATCHDOG_STATE.put(key, "sent", { expirationTtl: 7 * 86_400 });
    }
    await env.WATCHDOG_STATE.put("setup_status", JSON.stringify({ status: "verified", checkedAt: new Date().toISOString(), history, probe }));
  } catch (error) {
    let message = error instanceof Error ? error.message : "Setup verification failed";
    if (env.GITHUB_ACTIONS_TOKEN) message = message.replaceAll(env.GITHUB_ACTIONS_TOKEN, "[redacted]");
    await env.WATCHDOG_STATE.put("setup_status", JSON.stringify({ status: "failed", step, message: message.slice(0, 1000), checkedAt: new Date().toISOString() }));
    throw new Error(`Watchdog setup failed at ${step}`);
  }
}
export default {
  async scheduled(controller, env, ctx) {
    const mode = String(env.ENABLED);
    if (mode === "verify") { ctx.waitUntil(verifySetup(env, controller.scheduledTime)); return; }
    if (mode !== "true") { console.log('{"action":"disabled"}'); return; }
    let stage: Stage | null;
    try { stage = stageForSchedule(controller.cron, controller.scheduledTime); }
    catch { console.log('{"action":"ignored_unexpected_schedule"}'); return; }
    // The merged Cron also fires at 16:56 Beijing. Exit before any binding I/O.
    if (stage === null) { console.log('{"action":"idle_tick"}'); return; }
    ctx.waitUntil(execute(env, stage, controller.scheduledTime, false));
  },
  async fetch() { return new Response("Not found", { status: 404 }); },
} satisfies ExportedHandler<Env>;
