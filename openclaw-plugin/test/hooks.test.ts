import assert from "node:assert/strict";
import test from "node:test";

import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";

import { CompletionIntegration } from "../core.ts";
import { registerCompletionHooks } from "../hooks.ts";

type HookHandler = (
  event: Record<string, unknown>,
  context?: Record<string, unknown>,
) => Promise<void>;

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
    "agent_end",
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

test("successful agent turn uses its final assistant result", async () => {
  const completedResults: string[] = [];
  const submitted: unknown[] = [];
  const integration = new CompletionIntegration({
    async complete(params) {
      completedResults.push(params.messages[0]?.content ?? "");
      return {
        text: '{"decision":"offer","summary":"当前工作已经完成。"}',
      };
    },
    async submit(payload) {
      submitted.push(payload);
      return true;
    },
  });
  const fixture = fakeApi([]);
  registerCompletionHooks(fixture.api, integration);

  await fixture.hooks.get("agent_end")?.({
    success: true,
    runId: "agent-run",
    messages: [
      { role: "user", content: "ignore user text" },
      { role: "assistant", content: "meaningful completed result" },
    ],
  });

  assert.deepEqual(completedResults, ["meaningful completed result"]);
  assert.equal(submitted.length, 1);
});

test("same run is deduplicated across completion hooks", async () => {
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
  const fixture = fakeApi([
    { role: "assistant", content: "completed result" },
  ]);
  registerCompletionHooks(fixture.api, integration);

  await fixture.hooks.get("agent_end")?.({
    success: true,
    runId: "shared-run",
    messages: [{ role: "assistant", content: "completed result" }],
  });
  await fixture.hooks.get("subagent_ended")?.({
    targetKind: "subagent",
    outcome: "ok",
    runId: "shared-run",
    targetSessionKey: "agent:main:subagent:shared",
  });
  await fixture.hooks.get("cron_changed")?.({
    action: "finished",
    status: "ok",
    runId: "shared-run",
    summary: "completed result",
  });

  assert.equal(completions, 1);
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
  await fixture.hooks.get("agent_end")?.({
    success: false,
    runId: "failed-agent",
    messages: [{ role: "assistant", content: "failed" }],
  });
  await fixture.hooks.get("agent_end")?.({
    success: true,
    messages: [{ role: "assistant", content: "missing run id" }],
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
