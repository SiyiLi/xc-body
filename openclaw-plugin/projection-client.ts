import { readFile } from "node:fs/promises";

import type {
  LlmCompleter,
  LlmCompleteParams,
} from "./spoken-text.ts";

export const NVIDIA_PROJECTION_URL =
  "https://inference-api.nvidia.com/v1/chat/completions";
export const NVIDIA_PROJECTION_MODEL =
  "gcp/google/gemini-3.5-flash-lite";

type ProjectionClientConfig = {
  apiKeyFile: string;
  timeoutMs: number;
};

type NvidiaResponse = {
  choices?: Array<{
    message?: {
      content?: unknown;
    };
  }>;
};

function requestSignal(
  signal: AbortSignal | undefined,
  timeoutMs: number,
): AbortSignal {
  const timeout = AbortSignal.timeout(timeoutMs);
  return signal ? AbortSignal.any([signal, timeout]) : timeout;
}

function requestBody(params: LlmCompleteParams): Record<string, unknown> {
  return {
    model: NVIDIA_PROJECTION_MODEL,
    messages: [
      { role: "system", content: params.systemPrompt },
      ...params.messages,
    ],
    max_tokens: params.maxTokens,
    ...(params.temperature === undefined
      ? {}
      : { temperature: params.temperature }),
    reasoning_effort: "none",
    thinking_level: "off",
  };
}

function responseText(value: NvidiaResponse): string {
  const content = value.choices?.[0]?.message?.content;
  if (typeof content !== "string" || !content.trim()) {
    throw new Error("Model API response has no text");
  }
  return content;
}

export function createNvidiaProjectionCompleter(
  config: ProjectionClientConfig,
  fetchImpl: typeof fetch = fetch,
): LlmCompleter {
  return async (params) => {
    const apiKey = (await readFile(config.apiKeyFile, "utf8")).trim();
    if (!apiKey) {
      throw new Error("Model API key is empty");
    }
    const response = await fetchImpl(NVIDIA_PROJECTION_URL, {
      method: "POST",
      headers: {
        authorization: `Bearer ${apiKey}`,
        "content-type": "application/json",
      },
      body: JSON.stringify(requestBody(params)),
      redirect: "error",
      signal: requestSignal(params.signal, config.timeoutMs),
    });
    if (!response.ok) {
      try {
        await response.body?.cancel();
      } catch {
        // Cleanup failure must not hide the endpoint failure.
      }
      throw new Error(
        `Model API returned ${response.status}`,
      );
    }
    let value: NvidiaResponse;
    try {
      value = await response.json() as NvidiaResponse;
    } catch {
      throw new Error("Model API response is invalid JSON");
    }
    return { text: responseText(value) };
  };
}
