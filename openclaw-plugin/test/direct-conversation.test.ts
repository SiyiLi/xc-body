import assert from "node:assert/strict";
import test from "node:test";

import { resolveDirectAnswer } from "../direct-conversation.ts";

const TELEGRAM_TARGET = "12345";
const OPENCLAW_TELEGRAM_TARGET = `telegram:${TELEGRAM_TARGET}`;
const OPENCLAW_STREAM_ERROR_FALLBACK_TEXT =
  "[assistant turn failed before producing content]";

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
