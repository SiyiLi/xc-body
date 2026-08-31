import { readFile } from "node:fs/promises";

import type {
  LlmCompleter,
  LlmCompleteParams,
} from "./spoken-text.ts";

export const NVIDIA_PROJECTION_URL =
  "https://inference-api.nvidia.com/v1/chat/completions";
export const NVIDIA_PROJECTION_MODEL =
  "gcp/google/gemini-3.5-flash-lite";
export const NVIDIA_TRANSCRIPTION_PROMPT =
  "Transcribe this audio accurately. The speaker may use English, Chinese " +
  "(Mandarin), or French — sometimes mixed in the same message. Output the " +
  "transcribed text only, nothing else. Preserve the original language(s).";

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

export type NvidiaAudioTranscriber = (
  audioBase64: string,
  signal?: AbortSignal,
) => Promise<string>;

function requestSignal(
  signal: AbortSignal | undefined,
  timeoutMs: number,
): AbortSignal {
  const timeout = AbortSignal.timeout(timeoutMs);
  return signal ? AbortSignal.any([signal, timeout]) : timeout;
}

function projectionRequestBody(
  params: LlmCompleteParams,
): Record<string, unknown> {
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

function transcriptionRequestBody(audioBase64: string): Record<string, unknown> {
  return {
    model: NVIDIA_PROJECTION_MODEL,
    messages: [
      {
        role: "user",
        content: [
          {
            type: "input_audio",
            input_audio: { data: audioBase64, format: "ogg" },
          },
          { type: "text", text: NVIDIA_TRANSCRIPTION_PROMPT },
        ],
      },
    ],
    max_tokens: 2048,
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

async function requestNvidiaText(
  config: ProjectionClientConfig,
  fetchImpl: typeof fetch = fetch,
  body: Record<string, unknown>,
  signal: AbortSignal | undefined,
): Promise<string> {
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
    body: JSON.stringify(body),
    redirect: "error",
    signal: requestSignal(signal, config.timeoutMs),
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
  return responseText(value);
}

export function createNvidiaProjectionCompleter(
  config: ProjectionClientConfig,
  fetchImpl: typeof fetch = fetch,
): LlmCompleter {
  return async (params) => ({
    text: await requestNvidiaText(
      config,
      fetchImpl,
      projectionRequestBody(params),
      params.signal,
    ),
  });
}

export function createNvidiaAudioTranscriber(
  config: ProjectionClientConfig,
  fetchImpl: typeof fetch = fetch,
): NvidiaAudioTranscriber {
  return async (audioBase64, signal) =>
    requestNvidiaText(
      config,
      fetchImpl,
      transcriptionRequestBody(audioBase64),
      signal,
    );
}
