import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  createNvidiaAudioTranscriber,
  createNvidiaProjectionCompleter,
  NVIDIA_PROJECTION_MODEL,
  NVIDIA_PROJECTION_URL,
  NVIDIA_TRANSCRIPTION_PROMPT,
} from "../projection-client.ts";

const EXPECTED_TEXT = "XC_BODY_GEMINI_35_FLASH_LITE_OK";

test("sends the captured Ogg directly to fixed-model transcription", async () => {
  const directory = await mkdtemp(join(tmpdir(), "xc-body-projection-test-"));
  const apiKeyFile = join(directory, "model-api-key");
  await writeFile(apiKeyFile, "test-key");
  let request: RequestInit | undefined;
  const transcribe = createNvidiaAudioTranscriber(
    { apiKeyFile, timeoutMs: 1_000 },
    async (_input, init) => {
      request = init;
      return new Response(
        JSON.stringify({ choices: [{ message: { content: "你好" } }] }),
        { status: 200 },
      );
    },
  );

  try {
    assert.equal(await transcribe("b2dnLWF1ZGlv"), "你好");
    assert.deepEqual(JSON.parse(String(request?.body)), {
      model: NVIDIA_PROJECTION_MODEL,
      messages: [
        {
          role: "user",
          content: [
            {
              type: "input_audio",
              input_audio: { data: "b2dnLWF1ZGlv", format: "ogg" },
            },
            { type: "text", text: NVIDIA_TRANSCRIPTION_PROMPT },
          ],
        },
      ],
      max_tokens: 2048,
      reasoning_effort: "none",
      thinking_level: "off",
    });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

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
