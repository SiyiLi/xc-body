import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";

import {
  CompletionIntegration,
  extractCompletedResult,
} from "./core.ts";

const CHILD_MESSAGE_LIMIT = 8;

export function registerCompletionHooks(
  api: OpenClawPluginApi,
  integration: CompletionIntegration,
): void {
  api.on(
    "agent_end",
    async (event) => {
      if (!event.success || !event.runId) {
        return;
      }
      await integration.handle(
        "agent",
        event.runId,
        extractCompletedResult(event.messages),
      );
    },
    { timeoutMs: 300_000 },
  );

  api.on(
    "subagent_ended",
    async (event) => {
      if (
        event.targetKind !== "subagent" ||
        event.outcome !== "ok" ||
        !event.runId ||
        !event.targetSessionKey
      ) {
        return;
      }
      let result = "";
      try {
        const session = await api.runtime.subagent.getSessionMessages({
          sessionKey: event.targetSessionKey,
          limit: CHILD_MESSAGE_LIMIT,
        });
        result = extractCompletedResult(session.messages);
      } catch {
        result = "";
      }
      await integration.handle("subagent", event.runId, result);
    },
    { timeoutMs: 300_000 },
  );

  api.on(
    "cron_changed",
    async (event) => {
      if (
        event.action !== "finished" ||
        event.status !== "ok" ||
        typeof event.summary !== "string"
      ) {
        return;
      }
      const sourceId = event.runId || (
        event.jobId && Number.isSafeInteger(event.runAtMs)
          ? `${event.jobId}:${event.runAtMs}`
          : ""
      );
      if (!sourceId) {
        return;
      }
      await integration.handle("cron", sourceId, event.summary);
    },
    { timeoutMs: 300_000 },
  );
}
