import assert from "node:assert/strict";
import { homedir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  createNvidiaProjectionCompleter,
  NVIDIA_PROJECTION_MODEL,
  NVIDIA_PROJECTION_URL,
} from "../projection-client.ts";

const EXPECTED_TEXT = "XC_BODY_GEMINI_35_FLASH_LITE_OK";

test("Gemini 3.5 Flash Lite completes a live projection request", {
  timeout: 30_000,
}, async () => {
  const apiKeyFile = process.env.XC_BODY_PROJECTION_API_KEY_FILE ??
    join(homedir(), ".openclaw/secrets/nvidia-inference-api-key");
  assert.equal(
    NVIDIA_PROJECTION_URL,
    "https://inference-api.nvidia.com/v1/chat/completions",
  );
  assert.equal(
    NVIDIA_PROJECTION_MODEL,
    "gcp/google/gemini-3.5-flash-lite",
  );

  let request: RequestInit | undefined;
  const complete = createNvidiaProjectionCompleter(
    {
      apiKeyFile,
      timeoutMs: 25_000,
    },
    async (input, init) => {
      request = init;
      return fetch(input, init);
    },
  );
  const result = await complete({
    systemPrompt: [
      "This is a live integration test.",
      `Reply with exactly ${EXPECTED_TEXT} and nothing else.`,
    ].join(" "),
    messages: [{ role: "user", content: "Complete the test." }],
    purpose: "xc-body-native.live-projection-test",
    maxTokens: 32,
    temperature: 0,
  });

  assert.equal(result.text.trim(), EXPECTED_TEXT);
  const body = JSON.parse(String(request?.body)) as Record<string, unknown>;
  assert.equal(body.reasoning_effort, "none");
  assert.equal(body.thinking_level, "off");
});
