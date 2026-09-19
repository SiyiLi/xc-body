import { readFile } from "node:fs/promises";

import type {
  LlmCompleter,
  LlmCompleteParams,
} from "./spoken-text.ts";
import {
  EXPRESSION_GUIDANCE,
  isExpressionName,
  type ExpressionName,
} from "./expression.ts";

export const MODEL_API_URL =
  "https://inference-api.nvidia.com/v1/chat/completions";
export const MODEL_NAME =
  "gcp/google/gemini-3.5-flash-lite";
export const TRANSCRIPTION_PROMPT = `Transcribe and route one spoken \
XC Body turn. Return exactly one JSON object with exactly these keys: \
{"transcript":string,"route":"expression_only"|"conversation",\
"expression":string|null}. Do not return Markdown or commentary.

Transcribe faithfully in the speaker's original language. The only supported \
languages are English and Chinese, including mixtures of the two.

The expression_only contract has exactly these three request templates, where \
<expression> identifies one supported expression:
- Show me your <expression> expression.
- 给我看看你<expression>的表情。
- 给我做一个<expression>的表情。
Use expression_only only for a direct request closely matching one of these \
templates. They are contracts, not examples of a broader rule. Every other \
utterance is conversation. Do not infer expression_only from the speaker's \
mood or message. Any additional intent or request for a spoken answer is \
conversation. Only after the request qualifies, select the requested expression \
using the guidance below. Otherwise set expression to null.

${EXPRESSION_GUIDANCE}`;

const TRANSCRIPTION_CORRECTION_PROMPT = `Your previous response was not valid \
JSON matching the required schema. Correct it now. Return only one JSON object \
with exactly transcript, route, and expression; no Markdown or commentary.`;

type ProjectionClientConfig = {
  apiKeyFile: string;
  timeoutMs: number;
};

type ModelResponse = {
  choices?: Array<{
    message?: {
      content?: unknown;
    };
  }>;
};

export type AudioTranscriber = (
  audioBase64: string,
  signal?: AbortSignal,
) => Promise<DirectTranscription>;

export type DirectTranscription =
  | {
      transcript: string;
      route: "expression_only";
      expression: ExpressionName;
    }
  | {
      transcript: string;
      route: "conversation";
      expression: null;
    };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseDirectTranscription(text: string): DirectTranscription {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error("Model transcription is invalid JSON");
  }
  if (
    !isRecord(value) ||
    Object.keys(value).some(
      (key) => !["transcript", "route", "expression"].includes(key),
    ) ||
    typeof value.transcript !== "string" ||
    !value.transcript.trim() ||
    (value.route !== "expression_only" && value.route !== "conversation")
  ) {
    throw new Error("Model transcription has an invalid structure");
  }
  const transcript = value.transcript.trim();
  if (value.route === "expression_only" && isExpressionName(value.expression)) {
    return { transcript, route: value.route, expression: value.expression };
  }
  if (value.route === "expression_only") {
    return { transcript, route: "conversation", expression: null };
  }
  if (value.route === "conversation" && value.expression === null) {
    return { transcript, route: value.route, expression: null };
  }
  throw new Error("Model transcription has an invalid structure");
}

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
    model: MODEL_NAME,
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

function transcriptionRequestBody(
  audioBase64: string,
  previousResponse?: string,
): Record<string, unknown> {
  return {
    model: MODEL_NAME,
    messages: [
      { role: "system", content: TRANSCRIPTION_PROMPT },
      {
        role: "user",
        content: [
          {
            type: "input_audio",
            input_audio: { data: audioBase64, format: "ogg" },
          },
        ],
      },
      ...(previousResponse === undefined
        ? []
        : [
            { role: "assistant", content: previousResponse },
            { role: "user", content: TRANSCRIPTION_CORRECTION_PROMPT },
          ]),
    ],
    max_tokens: 256,
    temperature: 0,
    reasoning_effort: "none",
    thinking_level: "off",
  };
}

function responseText(value: ModelResponse): string {
  const content = value.choices?.[0]?.message?.content;
  if (typeof content !== "string" || !content.trim()) {
    throw new Error("Model API response has no text");
  }
  return content;
}

async function requestModelText(
  config: ProjectionClientConfig,
  fetchImpl: typeof fetch = fetch,
  body: Record<string, unknown>,
  signal: AbortSignal | undefined,
): Promise<string> {
  const apiKey = (await readFile(config.apiKeyFile, "utf8")).trim();
  if (!apiKey) {
    throw new Error("Model API key is empty");
  }
  const response = await fetchImpl(MODEL_API_URL, {
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
  let value: ModelResponse;
  try {
    value = await response.json() as ModelResponse;
  } catch {
    throw new Error("Model API response is invalid JSON");
  }
  return responseText(value);
}

export function createProjectionCompleter(
  config: ProjectionClientConfig,
  fetchImpl: typeof fetch = fetch,
): LlmCompleter {
  return async (params) => ({
    text: await requestModelText(
      config,
      fetchImpl,
      projectionRequestBody(params),
      params.signal,
    ),
  });
}

export function createAudioTranscriber(
  config: ProjectionClientConfig,
  fetchImpl: typeof fetch = fetch,
): AudioTranscriber {
  return async (audioBase64, signal) => {
    const operationSignal = requestSignal(signal, config.timeoutMs);
    const firstResponse = await requestModelText(
      config,
      fetchImpl,
      transcriptionRequestBody(audioBase64),
      operationSignal,
    );
    try {
      return parseDirectTranscription(firstResponse);
    } catch {
      return parseDirectTranscription(
        await requestModelText(
          config,
          fetchImpl,
          transcriptionRequestBody(audioBase64, firstResponse),
          operationSignal,
        ),
      );
    }
  };
}
