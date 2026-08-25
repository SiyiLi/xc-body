import assert from "node:assert/strict";
import test from "node:test";

import { prepareDirectAnswerSpeech } from "../direct-conversation.ts";
import {
  needsSpeechProjection,
  parseSpokenProjection,
  prepareDirectSpeech,
  projectSpokenText,
} from "../spoken-text.ts";

test("plain answers bypass projection until a speech limit is exceeded", async () => {
  let calls = 0;
  for (const answer of [
    "九加六等于十五。",
    "Scarlett means a vivid shade of red.",
  ]) {
    const speech = await prepareDirectSpeech(async () => {
      calls += 1;
      return { text: "" };
    }, answer);
    assert.equal(speech, answer);
  }
  assert.equal(calls, 0);
  assert.equal(needsSpeechProjection("word ".repeat(200)), false);
  assert.equal(needsSpeechProjection("word ".repeat(201)), true);
  assert.equal(needsSpeechProjection("a".repeat(1_000)), false);
  assert.equal(needsSpeechProjection("a".repeat(1_001)), true);
});

test("formatted answers use one shared projection with complete input", async () => {
  const answer = [
    "| 城市 | 天气 |",
    "| --- | --- |",
    "| 上海 | 有雨 |",
  ].join("\n");
  assert.equal(needsSpeechProjection(answer), true);

  const speech = await prepareDirectSpeech(
    async (params) => {
      assert.equal(params.model, "fast/summarizer");
      assert.equal(params.messages[0]?.content, answer);
      return { text: "上海今天有雨。" };
    },
    answer,
    "fast/summarizer",
  );

  assert.equal(speech, "上海今天有雨。");
});

test("direct caller speaks an error after invalid projection output", async () => {
  let calls = 0;
  const speech = await prepareDirectAnswerSpeech(async () => {
    calls += 1;
    return { text: "not suitable for speech" };
  }, "```text\nnot suitable for speech\n```");

  assert.equal(speech, "抱歉，在生成最终答案时出了点问题。");
  assert.equal(calls, 2);
});

test("projection retry can recover from invalid output", async () => {
  let calls = 0;
  const projection = await projectSpokenText(async () => {
    calls += 1;
    return calls === 1
      ? { text: "not suitable for speech" }
      : { text: "SKIP" };
  }, "completed result");

  assert.deepEqual(projection, {
    decision: "skip",
    speech: "",
  });
  assert.equal(calls, 2);
});

test("background projection accepts SKIP or bounded Chinese speech", () => {
  assert.deepEqual(
    parseSpokenProjection("任务已经完成。"),
    { decision: "offer", speech: "任务已经完成。" },
  );
  assert.deepEqual(
    parseSpokenProjection("SKIP"),
    { decision: "skip", speech: "" },
  );
  for (const invalid of [
    "skip",
    "English only",
    Array.from({ length: 201 }, () => "完成").join(" "),
    `中${"a".repeat(1_000)}`,
  ]) {
    assert.equal(parseSpokenProjection(invalid), null);
  }
});
