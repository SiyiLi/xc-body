import assert from "node:assert/strict";
import test from "node:test";

import { resolveDirectAnswer } from "../direct-conversation.ts";

test("resolves visible answers without duplicate delivery", () => {
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
        messagingToolSourceReplyPayloads: [{ text: "delivered answer" }],
      },
      expected: { text: "delivered answer", delivered: true },
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
        didDeliverSourceReplyViaMessageTool: true,
        messagingToolSourceReplyPayloads: [{ text: "final answer" }],
      },
      expected: { text: "final answer", delivered: true },
    },
    {
      result: {
        payloads: [{ text: "already delivered" }],
        meta: {},
        didDeliverSourceReplyViaMessageTool: true,
        messagingToolSourceReplyPayloads: [],
      },
      expected: { text: "already delivered", delivered: false },
    },
    {
      result: {
        payloads: [
          { text: "visible answer" },
          { text: "failure", isError: true },
          { text: "reasoning", isReasoning: true },
          { text: "progress", isCommentary: true },
        ],
        meta: {},
      },
      expected: { text: "visible answer", delivered: false },
    },
    {
      result: {
        meta: { finalAssistantVisibleText: "part one\n\npart two" },
        didDeliverSourceReplyViaMessageTool: true,
        messagingToolSourceReplyPayloads: [
          { text: "part one" },
          { text: "part two" },
        ],
      },
      expected: { text: "part one\n\npart two", delivered: true },
    },
    {
      result: {
        payloads: [{ text: "final answer from payload" }],
        meta: {},
        didDeliverSourceReplyViaMessageTool: true,
        messagingToolSourceReplyPayloads: [{ text: "progress update" }],
      },
      expected: { text: "final answer from payload", delivered: false },
    },
    {
      result: {
        payloads: [{ text: "final answer" }],
        meta: {},
        didDeliverSourceReplyViaMessageTool: true,
        messagingToolSourceReplyPayloads: [{ text: "final answer" }],
      },
      expected: { text: "final answer", delivered: true },
    },
  ];

  for (const { result, expected } of cases) {
    assert.deepEqual(resolveDirectAnswer(result), expected);
  }
});
