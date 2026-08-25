import assert from "node:assert/strict";
import test from "node:test";

import {
  CompletionIntegration,
  createThoughtId,
  extractCompletedResult,
  submitSummary,
} from "../core.ts";

test("extracts only assistant messages from the current turn", () => {
  const result = extractCompletedResult([
    { role: "user", content: "older question" },
    { role: "assistant", content: "older result" },
    { role: "user", content: "current question" },
    { role: "assistant", content: "current progress" },
    {
      role: "assistant",
      content: [{ type: "text", text: "current final result" }],
    },
  ]);

  assert.equal(result, "current progress\n\ncurrent final result");
});

test("submits one private Chinese offer and deduplicates its run", async () => {
  const submitted: unknown[] = [];
  let completions = 0;
  const integration = new CompletionIntegration({
    model: "fast/summarizer",
    async complete(params) {
      completions += 1;
      assert.equal(params.model, "fast/summarizer");
      return { text: "你的私人医疗报告已经整理完成。" };
    },
    async submit(payload) {
      submitted.push(payload);
      return true;
    },
  });

  assert.equal(
    await integration.handle(
      "subagent",
      "run-42",
      "| 项目 | 状态 |\n| --- | --- |\n| 私人医疗报告 | 完成 |",
    ),
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

test("speech-friendly offer preserves its original language", async () => {
  let submittedSummary = "";
  const integration = new CompletionIntegration({
    async complete() {
      return {
        text: "构建已经完成。",
      };
    },
    async submit(payload) {
      submittedSummary = payload.summary;
      return true;
    },
  });

  assert.equal(
    await integration.handle("agent", "run-english", "Build completed."),
    "submitted",
  );
  assert.equal(submittedSummary, "Build completed.");
});

test("model failure retries once and fails closed", async () => {
  let submitted = 0;
  let throwingCalls = 0;
  const throwing = new CompletionIntegration({
    async complete() {
      throwingCalls += 1;
      throw new Error("model unavailable");
    },
    async submit() {
      submitted += 1;
      return true;
    },
  });

  assert.equal(await throwing.handle("cron", "run-1", "done"), "skipped");
  assert.equal(throwingCalls, 2);
  assert.equal(submitted, 0);
});

test("remote rejection stores only an operational outcome", async () => {
  const privateSummary = "私人财务汇总已经完成。";
  const integration = new CompletionIntegration({
    async complete() {
      return {
        text: privateSummary,
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
