import assert from "node:assert/strict";
import test from "node:test";

import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";

import { CompletionIntegration } from "../core.ts";
import { registerCompletionHooks } from "../hooks.ts";

type HookHandler = (event: Record<string, unknown>) => Promise<void>;

function fakeApi(messages: unknown[]) {
  const hooks = new Map<string, HookHandler>();
  const sessionCalls: unknown[] = [];
  const api = {
    on(name: string, handler: HookHandler) {
      hooks.set(name, handler);
    },
    runtime: {
      subagent: {
        async getSessionMessages(params: unknown) {
          sessionCalls.push(params);
          return { messages };
        },
      },
    },
  };
  return {
    api: api as unknown as OpenClawPluginApi,
    hooks,
    sessionCalls,
  };
}

test("registers only completion hooks and retrieves bounded child messages", async () => {
  const submitted: unknown[] = [];
  const integration = new CompletionIntegration({
    async complete() {
      return {
        text: '{"decision":"offer","summary":"子任务已经完成。"}',
      };
    },
    async submit(payload) {
      submitted.push(payload);
      return true;
    },
  });
  const fixture = fakeApi([
    { role: "assistant", content: "completed child result" },
  ]);
  registerCompletionHooks(fixture.api, integration);

  assert.deepEqual([...fixture.hooks.keys()].sort(), [
    "cron_changed",
    "subagent_ended",
  ]);
  await fixture.hooks.get("subagent_ended")?.({
    targetKind: "subagent",
    outcome: "ok",
    runId: "child-run",
    targetSessionKey: "agent:main:subagent:child",
  });

  assert.deepEqual(fixture.sessionCalls, [
    { sessionKey: "agent:main:subagent:child", limit: 8 },
  ]);
  assert.equal(submitted.length, 1);
});

test("successful cron completion uses its typed summary and run ID", async () => {
  const submitted: unknown[] = [];
  const integration = new CompletionIntegration({
    async complete(params) {
      assert.equal(params.messages[0]?.content, "cron completed result");
      return {
        text: '{"decision":"offer","summary":"定时任务已经完成。"}',
      };
    },
    async submit(payload) {
      submitted.push(payload);
      return true;
    },
  });
  const fixture = fakeApi([]);
  registerCompletionHooks(fixture.api, integration);

  await fixture.hooks.get("cron_changed")?.({
    action: "finished",
    status: "ok",
    runId: "cron-run",
    summary: "cron completed result",
  });

  assert.equal(submitted.length, 1);
});

test("scheduled cron completion uses its job and start time", async () => {
  const submitted: unknown[] = [];
  const integration = new CompletionIntegration({
    async complete() {
      return {
        text: '{"decision":"offer","summary":"定时任务已经完成。"}',
      };
    },
    async submit(payload) {
      submitted.push(payload);
      return true;
    },
  });
  const fixture = fakeApi([]);
  registerCompletionHooks(fixture.api, integration);
  const event = {
    action: "finished",
    status: "ok",
    jobId: "daily-report",
    runAtMs: 1_787_284_800_000,
    summary: "cron completed result",
  };

  await fixture.hooks.get("cron_changed")?.(event);
  await fixture.hooks.get("cron_changed")?.(event);

  assert.equal(submitted.length, 1);
});

test("unsuccessful and incomplete events fail closed", async () => {
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
  const fixture = fakeApi([]);
  registerCompletionHooks(fixture.api, integration);

  await fixture.hooks.get("subagent_ended")?.({
    targetKind: "subagent",
    outcome: "error",
    runId: "failed-child",
    targetSessionKey: "agent:main:subagent:failed",
  });
  await fixture.hooks.get("cron_changed")?.({
    action: "finished",
    status: "error",
    runId: "failed-cron",
    summary: "failed",
  });
  await fixture.hooks.get("cron_changed")?.({
    action: "finished",
    status: "ok",
    summary: "missing run id",
  });

  assert.equal(completions, 0);
});
