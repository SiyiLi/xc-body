import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { EXPRESSION_NAMES } from "../expression.ts";
import {
  createAudioTranscriber,
  createProjectionCompleter,
  parseDirectTranscription,
  MODEL_API_URL,
  MODEL_NAME,
  TRANSCRIPTION_PROMPT,
} from "../projection-client.ts";

const EXPECTED_TEXT = "XC_BODY_PROJECTION_MODEL_OK";

function modelTextResponse(content: string): Response {
  return new Response(
    JSON.stringify({ choices: [{ message: { content } }] }),
    { status: 200 },
  );
}

async function withTestApiKey<T>(
  run: (apiKeyFile: string) => Promise<T>,
): Promise<T> {
  const directory = await mkdtemp(join(tmpdir(), "xc-body-projection-test-"));
  const apiKeyFile = join(directory, "model-api-key");
  await writeFile(apiKeyFile, "test-key");
  try {
    return await run(apiKeyFile);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

test("sends captured Ogg directly to the configured transcriber", async () => {
  const directory = await mkdtemp(join(tmpdir(), "xc-body-projection-test-"));
  const apiKeyFile = join(directory, "model-api-key");
  await writeFile(apiKeyFile, "test-key");
  let requestUrl: string | undefined;
  let request: RequestInit | undefined;
  let requestCount = 0;
  const transcribe = createAudioTranscriber(
    { apiKeyFile, timeoutMs: 1_000 },
    async (input, init) => {
      requestCount += 1;
      requestUrl = String(input);
      request = init;
      return modelTextResponse(
        JSON.stringify({
          transcript: "你好",
          route: "conversation",
          expression: null,
        }),
      );
    },
  );

  try {
    for (const expression of EXPRESSION_NAMES) {
      assert.match(TRANSCRIPTION_PROMPT, new RegExp(`\\b${expression}:`));
    }
    assert.deepEqual(await transcribe("b2dnLWF1ZGlv"), {
      transcript: "你好",
      route: "conversation",
      expression: null,
    });
    assert.equal(requestUrl, MODEL_API_URL);
    assert.deepEqual(JSON.parse(String(request?.body)), {
      model: MODEL_NAME,
      messages: [
        { role: "system", content: TRANSCRIPTION_PROMPT },
        {
          role: "user",
          content: [
            {
              type: "input_audio",
              input_audio: { data: "b2dnLWF1ZGlv", format: "ogg" },
            },
          ],
        },
      ],
      max_tokens: 256,
      temperature: 0,
      reasoning_effort: "none",
      thinking_level: "off",
    });
    assert.equal(requestCount, 1);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("normalizes an unusable expression-only result to conversation", () => {
  for (const expression of [undefined, null, "unsupported"]) {
    const value = {
      transcript: "hello",
      route: "expression_only",
      expression,
    };
    assert.deepEqual(parseDirectTranscription(JSON.stringify(value)), {
      transcript: "hello",
      route: "conversation",
      expression: null,
    });
  }
});

test("rejects other malformed compound transcription", () => {
  for (const text of [
    "not JSON",
    JSON.stringify({
      transcript: "hello",
      route: "conversation",
      expression: "pleased",
    }),
  ]) {
    assert.throws(
      () => parseDirectTranscription(text),
      /invalid JSON|invalid structure/,
    );
  }
});

test("corrects malformed JSON and invalid transcription structure", async () => {
  const invalidResponses = [
    "not JSON",
    JSON.stringify({
      transcript: "hello",
      route: "conversation",
      expression: "pleased",
    }),
  ];
  for (const invalidResponse of invalidResponses) {
    await withTestApiKey(async (apiKeyFile) => {
      const requests: Array<Record<string, unknown>> = [];
      const responses = [
        invalidResponse,
        JSON.stringify({
          transcript: "hello",
          route: "conversation",
          expression: null,
        }),
      ];
      const transcribe = createAudioTranscriber(
        { apiKeyFile, timeoutMs: 1_000 },
        async (_input, init) => {
          requests.push(JSON.parse(String(init?.body)));
          return modelTextResponse(responses.shift() ?? "");
        },
      );

      assert.deepEqual(await transcribe("same-audio"), {
        transcript: "hello",
        route: "conversation",
        expression: null,
      });
      assert.equal(requests.length, 2);
      const firstMessages = requests[0].messages as Array<Record<string, unknown>>;
      const retryMessages = requests[1].messages as Array<Record<string, unknown>>;
      assert.deepEqual(retryMessages.slice(0, 2), firstMessages);
      assert.deepEqual(retryMessages[2], {
        role: "assistant",
        content: invalidResponse,
      });
      assert.match(
        String(retryMessages[3].content),
        /previous response was not valid JSON.*exactly transcript, route, and expression/s,
      );
    });
  }
});

test("propagates failure after one corrective retry", async () => {
  await withTestApiKey(async (apiKeyFile) => {
    let requestCount = 0;
    const transcribe = createAudioTranscriber(
      { apiKeyFile, timeoutMs: 1_000 },
      async () => {
        requestCount += 1;
        return modelTextResponse("still not JSON");
      },
    );

    await assert.rejects(
      transcribe("same-audio"),
      /Model transcription is invalid JSON/,
    );
    assert.equal(requestCount, 2);
  });
});

test("does not retry an aborted or timed-out model request", async () => {
  await withTestApiKey(async (apiKeyFile) => {
    let requestCount = 0;
    const transcribe = createAudioTranscriber(
      { apiKeyFile, timeoutMs: 20 },
      async (_input, init) => {
        requestCount += 1;
        const signal = init?.signal;
        if (signal?.aborted) {
          throw signal.reason;
        }
        return await new Promise<Response>((_resolve, reject) => {
          const keepAlive = setTimeout(
            () => reject(new Error("model request did not abort")),
            1_000,
          );
          signal?.addEventListener(
            "abort",
            () => {
              clearTimeout(keepAlive);
              reject(signal.reason);
            },
            { once: true },
          );
        });
      },
    );

    const controller = new AbortController();
    const aborted = transcribe("same-audio", controller.signal);
    controller.abort(new Error("caller cancelled"));
    await assert.rejects(aborted, /caller cancelled/);
    await assert.rejects(
      transcribe("same-audio"),
      (error: unknown) =>
        error instanceof Error && error.name === "TimeoutError",
    );
    assert.equal(requestCount, 2);
  });
});

test("configured projection model completes a live request", {
  timeout: 30_000,
}, async () => {
  const apiKeyFile = process.env.XC_BODY_PROJECTION_API_KEY_FILE ??
    join(homedir(), ".openclaw/secrets/nvidia-inference-api-key");

  let request: RequestInit | undefined;
  const complete = createProjectionCompleter(
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
