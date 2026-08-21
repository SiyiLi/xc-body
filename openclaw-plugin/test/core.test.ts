import assert from "node:assert/strict";
import test from "node:test";

import {
  CompletionIntegration,
  createThoughtId,
  extractCompletedResult,
  parseModelDecision,
  submitSummary,
} from "../core.ts";

test("extracts only bounded final assistant messages", () => {
  const result = extractCompletedResult([
    { role: "user", content: "ignore this" },
    { role: "assistant", content: "older result" },
    {
      role: "assistant",
      content: [{ type: "text", text: "final result" }],
    },
  ]);

  assert.equal(result, "older result\n\nfinal result");
});

test("strictly validates offer and skip model JSON", () => {
  assert.deepEqual(
    parseModelDecision('{"decision":"offer","summary":"任务已经完成。"}'),
    { decision: "offer", summary: "任务已经完成。" },
  );
  assert.deepEqual(
    parseModelDecision('{"decision":"skip","summary":""}'),
    { decision: "skip", summary: "" },
  );
  for (const invalid of [
    "```json\n{}\n```",
    '{"decision":"remember","summary":"记住"}',
    '{"decision":"offer","summary":"English only"}',
    '{"decision":"offer","summary":"完成","extra":true}',
    JSON.stringify({ decision: "offer", summary: "完".repeat(151) }),
  ]) {
    assert.equal(parseModelDecision(invalid), null);
  }
});

test("submits one private Chinese offer and deduplicates its run", async () => {
  const submitted: unknown[] = [];
  let completions = 0;
  const integration = new CompletionIntegration({
    async complete(params) {
      completions += 1;
      assert.equal(params.temperature, undefined);
      assert.equal(params.maxTokens, 320);
      return {
        text: JSON.stringify({
          decision: "offer",
          summary: "你的私人医疗报告已经整理完成。",
        }),
      };
    },
    async submit(payload) {
      submitted.push(payload);
      return true;
    },
  });

  assert.equal(
    await integration.handle("subagent", "run-42", "completed"),
    "submitted",
  );
  assert.equal(
    await integration.handle("subagent", "run-42", "completed"),
    "duplicate",
  );
  assert.equal(completions, 1);
  assert.deepEqual(submitted, [
    {
      version: "v1",
      thought_id: createThoughtId("subagent", "run-42"),
      summary: "你的私人医疗报告已经整理完成。",
    },
  ]);
});

test("deduplicates one run observed by different completion hooks", async () => {
  let completions = 0;
  const integration = new CompletionIntegration({
    async complete() {
      completions += 1;
      return { text: '{"decision":"skip","summary":""}' };
    },
    async submit() {
      return true;
    },
  });

  assert.equal(await integration.handle("agent", "run-7", "done"), "skipped");
  assert.equal(
    await integration.handle("subagent", "run-7", "done"),
    "duplicate",
  );
  assert.equal(await integration.handle("cron", "run-7", "done"), "duplicate");
  assert.equal(completions, 1);
});

test("model failures and malformed output fail closed without submission", async () => {
  let submitted = 0;
  const throwing = new CompletionIntegration({
    async complete() {
      throw new Error("model unavailable");
    },
    async submit() {
      submitted += 1;
      return true;
    },
  });
  const malformed = new CompletionIntegration({
    async complete() {
      return { text: '{"decision":"offer","summary":"English"}' };
    },
    async submit() {
      submitted += 1;
      return true;
    },
  });

  assert.equal(await throwing.handle("cron", "run-1", "done"), "skipped");
  assert.equal(await malformed.handle("cron", "run-2", "done"), "skipped");
  assert.equal(submitted, 0);
});

test("remote rejection stores only an operational outcome", async () => {
  const privateSummary = "私人财务汇总已经完成。";
  const integration = new CompletionIntegration({
    async complete() {
      return {
        text: JSON.stringify({ decision: "offer", summary: privateSummary }),
      };
    },
    async submit() {
      return false;
    },
  });

  assert.equal(
    await integration.handle("cron", "run-private", "done"),
    "rejected",
  );
  assert.equal(integration.outcomeFor("cron", "run-private"), "rejected");
  assert.equal(JSON.stringify(integration).includes(privateSummary), false);
});

test("authenticated submission validates responses and retries briefly", async () => {
  const calls: Array<{ input: unknown; init: RequestInit | undefined }> = [];
  const acceptedFetch = async (input: unknown, init?: RequestInit) => {
    calls.push({ input, init });
    return new Response('{"ok":true}', { status: 200 });
  };
  const config = {
    summaryUrl: "https://body.invalid/xc-body/summary/v1",
    token: "secret-token",
    timeoutMs: 5_000,
  };
  const payload = {
    version: "v1" as const,
    thought_id: "cron:abc",
    summary: "任务完成。",
  };

  assert.equal(
    await submitSummary(config, payload, acceptedFetch as typeof fetch),
    true,
  );
  assert.equal(calls[0]?.input, config.summaryUrl);
  assert.equal(calls[0]?.init?.redirect, "error");
  assert.equal(
    (calls[0]?.init?.headers as Record<string, string>).authorization,
    "Bearer secret-token",
  );

  const rejectedFetch = async () => new Response("not-json", { status: 200 });
  assert.equal(
    await submitSummary(config, payload, rejectedFetch as typeof fetch),
    false,
  );

  let transientCalls = 0;
  const transientFetch = async () => {
    transientCalls += 1;
    if (transientCalls === 1) {
      return {
        ok: false,
        status: 503,
        body: {
          async cancel() {
            throw new Error("response cleanup failed");
          },
        },
      } as unknown as Response;
    }
    return new Response('{"ok":true}', { status: 200 });
  };

  assert.equal(
    await submitSummary(config, payload, transientFetch as typeof fetch),
    true,
  );
  assert.equal(transientCalls, 2);

  let permanentCalls = 0;
  const permanentFetch = async () => {
    permanentCalls += 1;
    return new Response("Unauthorized", { status: 401 });
  };
  assert.equal(
    await submitSummary(config, payload, permanentFetch as typeof fetch),
    false,
  );
  assert.equal(permanentCalls, 1);
});
