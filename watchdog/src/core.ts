/** Only GitHub is contacted here. GLaDOS credentials remain in Actions. */
export type Stage = "recovery" | "final" | "audit";
export interface Config {
  repository: string;
  workflowId: string;
  ref: string;
  token: string;
}
export interface Services {
  request: typeof fetch;
  getState(key: string): Promise<string | null>;
  putState(key: string, value: string): Promise<void>;
  notify(subject: string, text: string): Promise<void>;
}
export interface Result {
  action: string;
  day: string;
  runId?: number;
  reason?: string;
}
const PAGE_SIZE = 100;
const MAX_PAGES = 10;
const DAY_MS = 86_400_000;
const BEIJING_MS = 8 * 3_600_000;
const CHECKIN_JOB = "GLaDOS checkin";
const CHECKIN_STEP = "Running checkin";
const NOTIFICATION_JOB = "Report actionable checkin failures";
const NOTIFICATION_STEP = "Report notification decision";

export function beijingDay(time: number): string {
  if (!Number.isFinite(time)) throw new Error("Invalid observation time");
  return new Date(time + BEIJING_MS).toISOString().slice(0, 10);
}
export function stageForCron(cron: string): Stage {
  if (cron === "11 8 * * *") return "recovery";
  if (cron === "11 12 * * *") return "final";
  if (cron === "56 12 * * *") return "audit";
  throw new Error("Unexpected watchdog cron");
}
function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function time(value: unknown): number {
  if (typeof value !== "string" || !/(Z|[+-]\d\d:\d\d)$/.test(value)) throw new Error("Missing or ambiguous history timestamp");
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) throw new Error("Invalid history timestamp");
  return parsed;
}
export async function boundedJson(response: Response, limit = 2_097_152): Promise<unknown> {
  if (!response.body) throw new Error("Missing response body");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      length += chunk.value.byteLength;
      if (length > limit) { await reader.cancel(); throw new Error("Response exceeds safe history limit"); }
      chunks.push(chunk.value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return JSON.parse(new TextDecoder().decode(bytes));
}
class GitHub {
  private readonly config: Config;
  private readonly request: typeof fetch;
  constructor(config: Config, request: typeof fetch) {
    this.config = config;
    this.request = request;
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(config.repository) || !/^\d+$/.test(config.workflowId)) throw new Error("Invalid repository or workflow configuration");
  }
  private headers(): Record<string, string> {
    return {
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2026-03-10",
      "User-Agent": "GLaDOS-independent-watchdog",
      ...(this.config.token ? { Authorization: `Bearer ${this.config.token}` } : {}),
    };
  }
  runsInRange(created: string) {
    return this.collection(`workflows/${this.config.workflowId}/runs`, "workflow_runs", { created });
  }
  async collection(path: string, key: string, query: Record<string, string> = {}): Promise<Record<string, unknown>[]> {
    const rows: Record<string, unknown>[] = [];
    for (let page = 1; page <= MAX_PAGES; page++) {
      const params = new URLSearchParams({ ...query, per_page: String(PAGE_SIZE), page: String(page) });
      // Workers rejects redirect:"error" before I/O. Manual mode exposes 3xx
      // to the non-2xx check without forwarding the Authorization header.
      const response = await this.request(`https://api.github.com/repos/${this.config.repository}/actions/${path}?${params}`, {
        headers: this.headers(), redirect: "manual", signal: AbortSignal.timeout(15_000),
      });
      if (!response.ok) throw new Error(`GitHub history unavailable (HTTP ${response.status})`);
      const data = await boundedJson(response);
      if (!record(data) || !Number.isInteger(data.total_count) || Number(data.total_count) < 0 || Number(data.total_count) > PAGE_SIZE * MAX_PAGES) throw new Error("Incomplete or excessive GitHub history");
      const entries = data[key];
      if (!Array.isArray(entries) || !entries.every(record)) throw new Error("Malformed GitHub history");
      const expected = Math.min(PAGE_SIZE, Math.max(0, Number(data.total_count) - (page - 1) * PAGE_SIZE));
      if (entries.length !== expected) throw new Error("Truncated GitHub history");
      rows.push(...entries);
      if (rows.length === data.total_count) return rows;
    }
    throw new Error("GitHub history exceeded pagination limit");
  }
  async dispatch(day: string, finalAttempt: boolean): Promise<number | undefined> {
    if (!this.config.token) throw new Error("GitHub Actions token is not configured");
    // Never retry a POST: a transport error may occur after GitHub accepted it.
    const response = await this.request(`https://api.github.com/repos/${this.config.repository}/actions/workflows/${this.config.workflowId}/dispatches`, {
      method: "POST", headers: { ...this.headers(), "Content-Type": "application/json" }, redirect: "manual", signal: AbortSignal.timeout(15_000),
      body: JSON.stringify({ ref: this.config.ref, inputs: { force_checkin: false, final_attempt: finalAttempt, checkin_day: day } }),
    });
    if (!response.ok) throw new Error(`Recovery dispatch failed (HTTP ${response.status})`);
    if (response.status === 204) return undefined;
    const data = await boundedJson(response);
    if (record(data) && Number.isSafeInteger(data.workflow_run_id) && Number(data.workflow_run_id) > 0) return Number(data.workflow_run_id);
    throw new Error("GitHub accepted a dispatch but returned unknown run details; do not retry");
  }
}
interface Observation { successRun?: number; activeRuns: number; finalAlertReported: boolean }
async function inspect(github: GitHub, day: string, now: number): Promise<Observation> {
  const start = Date.parse(`${day}T00:00:00+08:00`);
  const created = `${new Date(start - DAY_MS).toISOString()}..${new Date(now).toISOString()}`;
  const runs = await github.runsInRange(created);
  const observation: Observation = { activeRuns: 0, finalAlertReported: false };
  for (const run of runs) {
    if (!Number.isSafeInteger(run.id) || Number(run.id) <= 0 || typeof run.status !== "string") throw new Error("Malformed workflow run");
    if (run.status !== "completed") observation.activeRuns++;
    const jobs = await github.collection(`runs/${run.id}/jobs`, "jobs", { filter: "all" });
    for (const job of jobs) {
      if (job.name !== CHECKIN_JOB && job.name !== NOTIFICATION_JOB) continue;
      const steps = job.steps ?? (job.conclusion === "skipped" ? [] : undefined);
      if (!Array.isArray(steps) || !steps.every(record)) throw new Error("Missing job step history");
      if (job.name === NOTIFICATION_JOB && run.display_title === `GLaDOS ${day} final recovery` &&
          steps.some(step => step.name === NOTIFICATION_STEP && step.conclusion === "failure")) observation.finalAlertReported = true;
      if (job.name !== CHECKIN_JOB) continue;
      for (const step of steps) {
        if (step.name !== CHECKIN_STEP || step.conclusion !== "success") continue;
        const started = time(step.started_at), completed = time(step.completed_at);
        if (completed < started) throw new Error("Inconsistent successful checkin timestamps");
        if (completed >= start && completed < start + DAY_MS) {
          if (started < start) throw new Error("A successful checkin crossed midnight; automatic recovery is unsafe");
          return { ...observation, successRun: Number(run.id) };
        }
      }
    }
  }
  return observation;
}
export async function runWatchdog(config: Config, services: Services, stage: Stage, scheduledTime: number, now: number, dryRun = false): Promise<Result> {
  const day = beijingDay(scheduledTime);
  if (day !== beijingDay(now)) return { action: "expired_tick", day };
  const github = new GitHub(config, services.request);
  const alert = async (reason: string): Promise<Result> => {
    if (dryRun) return { action: "would_alert", day, reason };
    const key = `alert:${day}`;
    let reported: string | null = null;
    // Notification checkpoints are best-effort. Their own outage must not
    // suppress an otherwise deliverable final-failure email.
    try { reported = await services.getState(key); } catch { /* Still alert. */ }
    if (reported) return { action: "alert_already_reported", day };
    await services.notify(`GLaDOS final recovery unconfirmed (${day})`, `No successful GLaDOS checkin was confirmed for ${day} (Asia/Shanghai).\n${reason}\nInspect https://github.com/${config.repository}/actions\nThis checker never receives your GLaDOS Cookie.`);
    try { await services.putState(key, "reported"); } catch { /* Email was sent. */ }
    return { action: "alerted", day, reason };
  };
  let observation: Observation;
  try { observation = await inspect(github, day, now); }
  catch (error) {
    const known = error instanceof Error && /^(GitHub history unavailable \(HTTP \d+\)|Incomplete or excessive GitHub history|Malformed GitHub history|Truncated GitHub history|Missing job step history|Inconsistent successful checkin timestamps|A successful checkin crossed midnight; automatic recovery is unsafe|Missing or ambiguous history timestamp|Invalid history timestamp|Response exceeds safe history limit)$/.test(error.message);
    const reason = known && error instanceof Error ? error.message : `GitHub transport/history unavailable (${error instanceof TypeError ? "TypeError" : error instanceof SyntaxError ? "SyntaxError" : "transport error"})`;
    if (stage === "recovery") return { action: "recovery_deferred", day, reason };
    return alert(`Final recovery/history verification was unavailable: ${reason}. Inspect the workflow and the watcher's GitHub Actions token.`);
  }
  if (observation.successRun) return { action: "already_succeeded", day, runId: observation.successRun };
  if (stage === "audit") {
    if (observation.finalAlertReported) return { action: "github_already_reported_final_failure", day };
    return alert("The final recovery did not confirm success by the independent audit deadline, or its workflow could not start/finish.");
  }
  if (stage === "recovery" && observation.activeRuns) return { action: "checkin_in_flight", day };
  try {
    const key = `dispatch:${day}:${stage}`;
    if (await services.getState(key)) return { action: "dispatch_already_attempted", day };
    if (dryRun) return { action: "would_dispatch", day };
    // A KV receipt limits repeat cron delivery; Actions concurrency plus the
    // existing daily guard provide the authoritative request serialization.
    await services.putState(key, "dispatch_attempted");
    const runId = await github.dispatch(day, stage === "final");
    // The pre-POST receipt already prevents a blind retry if this update fails.
    try { await services.putState(key, JSON.stringify({ runId: runId ?? null, accepted: true })); } catch { /* Dispatch was accepted. */ }
    return { action: "dispatched", day, runId };
  } catch {
    // Do not include arbitrary API bodies, tokens or Cookie values in alerts.
    if (stage === "recovery") return { action: "recovery_deferred", day, reason: "GitHub history or dispatch unavailable; waiting for final recovery" };
    return alert("The final recovery could not be dispatched or its acceptance was uncertain. Inspect the workflow and the watcher's GitHub Actions token.");
  }
}

/** Setup-only write-permission probe, allowed only after real success today. */
export async function verifyDispatch(config: Config, services: Services, now: number): Promise<Result> {
  const day = beijingDay(now);
  const github = new GitHub(config, services.request);
  const observed = await inspect(github, day, now);
  if (!observed.successRun) throw new Error("Transport probe requires an already successful Beijing day");
  const key = `verification-dispatch:${day}`;
  const prior = await services.getState(key);
  if (prior === "accepted") return { action: "transport_probe_already_attempted", day };
  if (prior) throw new Error("Previous transport probe acceptance is uncertain; inspect before retrying");
  await services.putState(key, "attempted");
  const runId = await github.dispatch(day, false);
  await services.putState(key, "accepted");
  return { action: "transport_probe_dispatched", day, runId };
}
