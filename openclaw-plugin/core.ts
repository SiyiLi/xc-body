import { createHash } from "node:crypto";

import {
  needsSpeechProjection,
  projectSpokenText,
  type LlmCompleteParams,
} from "./spoken-text.ts";

export type CompletionSource = "agent" | "subagent" | "cron";
export type CompletionOutcome =
  | "duplicate"
  | "rejected"
  | "skipped"
  | "submitted";

export type SummaryPayload = {
  version: "v1";
  thought_id: string;
  summary: string;
};

export type IntegrationDependencies = {
  complete: (params: LlmCompleteParams) => Promise<{ text: string }>;
  submit: (payload: SummaryPayload) => Promise<boolean>;
};

export type NativeIntegrationConfig = {
  summaryUrl: string;
  voiceUrl?: string;
  token: string;
  sessionKey?: string;
  telegramTarget?: string;
  projectionApiKeyFile: string;
  agentId: string;
  pollMs: number;
  timeoutMs: number;
};

const MAX_OUTCOMES = 1_024;
const MAX_SOURCE_ID_CHARS = 512;
const SUBMISSION_RETRY_DELAYS_MS = [0, 1_000, 3_000];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function messageText(message: unknown): string {
  if (!isRecord(message) || message.role !== "assistant") {
    return "";
  }
  if (typeof message.content === "string") {
    return message.content.trim();
  }
  if (!Array.isArray(message.content)) {
    return "";
  }
  return message.content
    .map((block) => {
      if (
        isRecord(block) &&
        block.type === "text" &&
        typeof block.text === "string"
      ) {
        return block.text.trim();
      }
      return "";
    })
    .filter(Boolean)
    .join("\n");
}

export function extractCompletedResult(messages: unknown[]): string {
  const finalMessages: string[] = [];
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (isRecord(message) && message.role === "user") {
      break;
    }
    const text = messageText(message);
    if (text) {
      finalMessages.unshift(text);
    }
    if (finalMessages.length === 4) {
      break;
    }
  }
  return finalMessages.join("\n\n").trim();
}

export function createThoughtId(
  source: CompletionSource,
  sourceId: string,
): string {
  const digest = createHash("sha256").update(sourceId).digest("hex");
  return `${source}:${digest.slice(0, 32)}`;
}

export function parsePluginConfig(
  value: Record<string, unknown>,
): NativeIntegrationConfig {
  const summaryUrl = value.summaryUrl;
  const voiceUrl = value.voiceUrl;
  const token = value.token;
  const sessionKey = value.sessionKey;
  const telegramTarget = value.telegramTarget;
  const projectionApiKeyFile = value.projectionApiKeyFile;
  const agentId = value.agentId ?? "main";
  const pollMs = value.pollMs ?? 1_000;
  const timeoutMs = value.timeoutMs ?? 180_000;
  if (
    typeof summaryUrl !== "string" ||
    typeof token !== "string" ||
    !token.trim() ||
    (voiceUrl !== undefined && typeof voiceUrl !== "string") ||
    (sessionKey !== undefined && typeof sessionKey !== "string") ||
    (telegramTarget !== undefined && typeof telegramTarget !== "string") ||
    typeof projectionApiKeyFile !== "string" ||
    !projectionApiKeyFile.trim() ||
    typeof agentId !== "string" ||
    !agentId.trim() ||
    !Number.isInteger(pollMs) ||
    (pollMs as number) < 250 ||
    (pollMs as number) > 30_000 ||
    !Number.isInteger(timeoutMs) ||
    (timeoutMs as number) < 1_000 ||
    (timeoutMs as number) > 180_000
  ) {
    throw new Error("XC Body native integration configuration is invalid");
  }
  let endpoint: URL;
  try {
    endpoint = new URL(summaryUrl);
  } catch {
    throw new Error("XC Body summary URL is invalid");
  }
  if (
    endpoint.protocol !== "https:" ||
    endpoint.username ||
    endpoint.password
  ) {
    throw new Error("XC Body summary URL must use authenticated TLS");
  }
  let voiceEndpoint: URL | undefined;
  if (voiceUrl !== undefined) {
    try {
      voiceEndpoint = new URL(voiceUrl);
    } catch {
      throw new Error("XC Body voice URL is invalid");
    }
    if (
      voiceEndpoint.protocol !== "https:" ||
      voiceEndpoint.username ||
      voiceEndpoint.password ||
      !sessionKey?.trim() ||
      !telegramTarget?.trim()
    ) {
      throw new Error("XC Body voice configuration is invalid");
    }
  }
  return {
    summaryUrl: endpoint.toString(),
    voiceUrl: voiceEndpoint?.toString(),
    token: token.trim(),
    sessionKey: sessionKey?.trim(),
    telegramTarget: telegramTarget?.trim(),
    projectionApiKeyFile: projectionApiKeyFile.trim(),
    agentId: agentId.trim(),
    pollMs: pollMs as number,
    timeoutMs: timeoutMs as number,
  };
}

export async function submitSummary(
  config: NativeIntegrationConfig,
  payload: SummaryPayload,
  fetchImpl: typeof fetch = fetch,
): Promise<boolean> {
  const deadline = Date.now() + config.timeoutMs;
  for (const delayMs of SUBMISSION_RETRY_DELAYS_MS) {
    if (delayMs > 0) {
      if (Date.now() + delayMs >= deadline) {
        return false;
      }
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
    const remainingMs = deadline - Date.now();
    if (remainingMs <= 0) {
      return false;
    }
    let response: Response;
    try {
      response = await fetchImpl(config.summaryUrl, {
        method: "POST",
        headers: {
          authorization: `Bearer ${config.token}`,
          "content-type": "application/json",
        },
        body: JSON.stringify(payload),
        redirect: "error",
        signal: AbortSignal.timeout(remainingMs),
      });
    } catch {
      continue;
    }
    if (!response.ok) {
      try {
        await response.body?.cancel();
      } catch {
        // Cleanup failure must not change the HTTP retry decision.
      }
      if (response.status !== 429 && response.status < 500) {
        return false;
      }
      continue;
    }
    try {
      const body = await response.text();
      if (body.length > 8_192) {
        return false;
      }
      const result: unknown = JSON.parse(body);
      return isRecord(result) && result.ok === true;
    } catch {
      return false;
    }
  }
  return false;
}

export class CompletionIntegration {
  private readonly dependencies: IntegrationDependencies;
  private readonly inFlight = new Set<string>();
  private readonly outcomes = new Map<string, Exclude<CompletionOutcome, "duplicate">>();
  private readonly maxOutcomes: number;

  constructor(
    dependencies: IntegrationDependencies,
    maxOutcomes = MAX_OUTCOMES,
  ) {
    this.dependencies = dependencies;
    this.maxOutcomes = maxOutcomes;
  }

  outcomeFor(
    _source: CompletionSource,
    sourceId: string,
  ): Exclude<CompletionOutcome, "duplicate"> | undefined {
    return this.outcomes.get(sourceId);
  }

  async handle(
    source: CompletionSource,
    sourceId: string,
    completedResult: string,
  ): Promise<CompletionOutcome> {
    if (
      !sourceId ||
      [...sourceId].length > MAX_SOURCE_ID_CHARS
    ) {
      return "skipped";
    }
    // A cron or subagent run can also emit agent_end. The run ID is the
    // completion identity, so deduplicate it across all source hooks.
    const key = sourceId;
    if (this.inFlight.has(key) || this.outcomes.has(key)) {
      return "duplicate";
    }
    this.inFlight.add(key);
    const result = completedResult.trim();
    if (!result) {
      this.record(key, "skipped");
      this.inFlight.delete(key);
      return "skipped";
    }

    const projection = await projectSpokenText(
      this.dependencies.complete,
      result,
    );
    if (projection === null || projection.decision === "skip") {
      this.record(key, "skipped");
      this.inFlight.delete(key);
      return "skipped";
    }

    let submitted = false;
    try {
      submitted = await this.dependencies.submit({
        version: "v1",
        thought_id: createThoughtId(source, sourceId),
        summary: needsSpeechProjection(result) ? projection.speech : result,
      });
    } catch {
      submitted = false;
    }
    const outcome = submitted ? "submitted" : "rejected";
    this.record(key, outcome);
    this.inFlight.delete(key);
    return outcome;
  }

  private record(
    key: string,
    outcome: Exclude<CompletionOutcome, "duplicate">,
  ): void {
    this.outcomes.set(key, outcome);
    while (this.outcomes.size > this.maxOutcomes) {
      const oldest = this.outcomes.keys().next().value;
      if (typeof oldest !== "string") {
        break;
      }
      this.outcomes.delete(oldest);
    }
  }
}
