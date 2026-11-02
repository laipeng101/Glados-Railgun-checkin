import assert from "node:assert/strict";
import { test } from "node:test";
import { fetchFromWorker } from "../src/platform.ts";

test("injected native fetch preserves the Worker global receiver", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = function (this: unknown, ..._args: Parameters<typeof fetch>) {
    assert.equal(this, globalThis, "Native Worker fetch rejects a client object as its receiver");
    return Promise.resolve(new Response("ok"));
  };
  try {
    const client = { request: fetchFromWorker };
    assert.equal(await (await client.request("https://api.github.com/" )).text(), "ok");
  } finally { globalThis.fetch = original; }
});
