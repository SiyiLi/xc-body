import assert from "node:assert/strict";
import test from "node:test";

import {
  DirectConversationService,
  resolveDirectAnswer,
} from "../direct-conversation.ts";

const TELEGRAM_TARGET = "12345";
const OPENCLAW_TELEGRAM_TARGET = `telegram:${TELEGRAM_TARGET}`;
const OPENCLAW_STREAM_ERROR_FALLBACK_TEXT =
  "[assistant turn failed before producing content]";

async function within<T>(promise: Promise<T>, message: string): Promise<T> {
  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<never>((_, reject) => {
        timeout = setTimeout(() => reject(new Error(message)), 1_000);
      }),
    ]);
  } finally {
    if (timeout !== undefined) {
      clearTimeout(timeout);
    }
  }
}

test("resolves complete visible answers without duplicate delivery", () => {
  const cases = [
    {
      result: {
        payloads: [{ text: "visible answer" }],
        meta: {},
      },
      expected: { text: "visible answer", delivered: false },
    },
    {
      result: {
        meta: {},
        didDeliverSourceReplyViaMessageTool: true,
        messagingToolSourceReplyPayloads: [
          { text: "part one" },
          { text: "part two" },
        ],
      },
      expected: { text: "part one\n\npart two", delivered: false },
    },
    {
      result: {
        meta: { finalAssistantVisibleText: "final answer" },
        didDeliverSourceReplyViaMessageTool: true,
        messagingToolSourceReplyPayloads: [{ text: "progress update" }],
      },
      expected: { text: "final answer", delivered: false },
    },
    {
      result: {
        meta: { finalAssistantVisibleText: "final answer" },
        messagingToolSentTargets: [
          {
            provider: "telegram",
            to: OPENCLAW_TELEGRAM_TARGET,
            text: "final answer",
          },
        ],
      },
      expected: { text: "final answer", delivered: true },
    },
    {
      result: {
        payloads: [
          { text: "part one" },
          { text: "failure", isError: true },
          { text: "reasoning", isReasoning: true },
          { text: "progress", isCommentary: true },
          { text: "part two" },
        ],
        meta: {},
      },
      expected: { text: "part one\n\npart two", delivered: false },
    },
    {
      result: {
        payloads: [{ text: "NO_REPLY" }],
        meta: { finalAssistantVisibleText: "NO_REPLY" },
        didDeliverSourceReplyViaMessageTool: true,
        messagingToolSentTexts: ["delivered answer"],
        messagingToolSentTargets: [
          {
            provider: "telegram",
            to: OPENCLAW_TELEGRAM_TARGET,
            text: "delivered answer",
          },
        ],
      },
      expected: { text: "delivered answer", delivered: true },
    },
    {
      result: {
        payloads: [{ text: OPENCLAW_STREAM_ERROR_FALLBACK_TEXT }],
        meta: {
          finalAssistantVisibleText:
            OPENCLAW_STREAM_ERROR_FALLBACK_TEXT,
        },
        messagingToolSentTargets: [
          {
            provider: "telegram",
            to: OPENCLAW_TELEGRAM_TARGET,
            text: "delivered answer after stream failure",
          },
        ],
      },
      expected: {
        text: "delivered answer after stream failure",
        delivered: true,
      },
    },
    {
      result: {
        payloads: [
          { text: "part one" },
          { text: "part two" },
        ],
        meta: {},
        messagingToolSentTargets: [
          {
            provider: "telegram",
            to: OPENCLAW_TELEGRAM_TARGET,
            text: "part one",
          },
          {
            provider: "telegram",
            to: OPENCLAW_TELEGRAM_TARGET,
            text: "part two",
          },
        ],
      },
      expected: { text: "part one\n\npart two", delivered: true },
    },
    {
      result: {
        meta: { finalAssistantVisibleText: "other target answer" },
        messagingToolSentTexts: ["other target answer"],
        messagingToolSentTargets: [
          {
            provider: "telegram",
            to: "telegram:different-target",
            text: "other target answer",
          },
        ],
      },
      expected: { text: "other target answer", delivered: false },
    },
  ];

  for (const { result, expected } of cases) {
    assert.deepEqual(
      resolveDirectAnswer(result, TELEGRAM_TARGET),
      expected,
    );
  }
});

test("rejects the OpenClaw stream-error fallback without a reply", () => {
  assert.throws(
    () =>
      resolveDirectAnswer(
        {
          payloads: [{ text: OPENCLAW_STREAM_ERROR_FALLBACK_TEXT }],
          meta: {
            finalAssistantVisibleText:
              OPENCLAW_STREAM_ERROR_FALLBACK_TEXT,
          },
        },
        TELEGRAM_TARGET,
      ),
    /OpenClaw produced no visible direct answer/,
  );
});

test("orders transcript and result delivery without gating the agent", async () => {
  let resolveQuestion: (() => void) | undefined;
  const questionDelivery = new Promise<void>((resolve) => {
    resolveQuestion = resolve;
  });
  let resolveQuestionStarted: (() => void) | undefined;
  const questionStarted = new Promise<void>((resolve) => {
    resolveQuestionStarted = resolve;
  });
  let resolveAgentStarted: (() => void) | undefined;
  const agentStarted = new Promise<void>((resolve) => {
    resolveAgentStarted = resolve;
  });
  let resolveProjection: ((value: string) => void) | undefined;
  const projection = new Promise<string>((resolve) => {
    resolveProjection = resolve;
  });
  let resolveProjectionStarted: (() => void) | undefined;
  const projectionStarted = new Promise<void>((resolve) => {
    resolveProjectionStarted = resolve;
  });
  let resolveProjectionFinished: (() => void) | undefined;
  const projectionFinished = new Promise<void>((resolve) => {
    resolveProjectionFinished = resolve;
  });
  let resolveAnswerDelivery: (() => void) | undefined;
  const answerDelivery = new Promise<void>((resolve) => {
    resolveAnswerDelivery = resolve;
  });
  let resolveAnswerDeliveryStarted: (() => void) | undefined;
  const answerDeliveryStarted = new Promise<void>((resolve) => {
    resolveAnswerDeliveryStarted = resolve;
  });
  let questionCount = 0;
  let agentOptions: Record<string, unknown> | undefined;
  const delivered: string[] = [];
  const warnings: string[] = [];
  const originalFetch = globalThis.fetch;
  let answerPosts = 0;
  globalThis.fetch = async (input) => {
    if (String(input).endsWith("/answer")) {
      answerPosts += 1;
    }
    return new Response("{}", { status: 200 });
  };

  const api = {
    config: {},
    logger: {
      warn(message: string) {
        warnings.push(message);
      },
    },
    runtime: {
      channel: {
        outbound: {
          async loadAdapter() {
            return {
              async sendText({ text }: { text: string }) {
                if (text.startsWith("🎙️")) {
                  questionCount += 1;
                  if (questionCount === 1) {
                    resolveQuestionStarted?.();
                    await questionDelivery;
                    delivered.push(text);
                    return;
                  }
                  throw new Error("Telegram unavailable");
                }
                resolveAnswerDeliveryStarted?.();
                await answerDelivery;
                delivered.push(text);
              },
            };
          },
        },
      },
      agent: {
        session: {
          resolveStorePath() {
            return "/tmp";
          },
          getSessionEntry() {
            return { sessionId: "session-id" };
          },
          async runWithWorkAdmission(
            _options: unknown,
            run: (signal: AbortSignal) => Promise<unknown>,
          ) {
            return run(new AbortController().signal);
          },
        },
        resolveAgentWorkspaceDir() {
          return "/tmp";
        },
        resolveAgentTimeoutMs() {
          return 1_000;
        },
        async runEmbeddedAgent(options: Record<string, unknown>) {
          agentOptions ??= options;
          resolveAgentStarted?.();
          return { payloads: [{ text: "- 直接回答。" }], meta: {} };
        },
      },
    },
  };
  const service = new DirectConversationService(api as never, {
    voiceUrl: "https://body.example/xc-body/voice/v1/",
    token: "token",
    sessionKey: "agent:main:telegram:direct:12345",
    telegramTarget: TELEGRAM_TARGET,
    agentId: "main",
    transcribe: async () => "测试问题",
    complete: async () => {
      resolveProjectionStarted?.();
      const text = await projection;
      resolveProjectionFinished?.();
      return { text };
    },
    pollMs: 250,
    timeoutMs: 1_000,
  });
  const handle = (
    service as unknown as {
      handle(capture: { turn_id: string; audio_base64: string }): Promise<void>;
    }
  ).handle.bind(service);

  try {
    const firstTurn = handle({
      turn_id: "robot:ordered",
      audio_base64: "YXVkaW8=",
    });
    await within(
      Promise.all([questionStarted, agentStarted]),
      "mirror or agent did not start",
    );
    assert.equal(agentOptions?.sourceReplyDeliveryMode, "automatic");
    assert.equal(agentOptions?.disableMessageTool, true);
    assert.deepEqual(delivered, []);
    resolveQuestion?.();
    await within(
      Promise.all([projectionStarted, answerDeliveryStarted]),
      "projection or answer delivery did not start",
    );
    assert.deepEqual(delivered, ["🎙️ Louis via XC Body: 测试问题"]);
    resolveProjection?.("直接回答。");
    await within(projectionFinished, "projection did not finish");
    assert.equal(answerPosts, 0);
    resolveAnswerDelivery?.();
    await firstTurn;
    assert.equal(answerPosts, 1);
    assert.deepEqual(delivered, [
      "🎙️ Louis via XC Body: 测试问题",
      "- 直接回答。",
    ]);

    await handle({
      turn_id: "robot:mirror-failure",
      audio_base64: "YXVkaW8=",
    });
    assert.deepEqual(delivered, [
      "🎙️ Louis via XC Body: 测试问题",
      "- 直接回答。",
      "- 直接回答。",
    ]);
    assert.deepEqual(warnings, [
      "XC Body transcript mirror failed: Error: Telegram unavailable",
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
