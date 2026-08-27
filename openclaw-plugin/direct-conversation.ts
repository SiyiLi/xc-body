import { Buffer } from "node:buffer";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { performance } from "node:perf_hooks";

import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";

import {
  prepareDirectSpeech,
  type LlmCompleter,
} from "./spoken-text.ts";

export type DirectConversationConfig = {
  voiceUrl: string;
  token: string;
  sessionKey: string;
  telegramTarget: string;
  agentId: string;
  complete: LlmCompleter;
  speechModel?: string;
  pollMs: number;
  timeoutMs: number;
};

type Capture = {
  turn_id: string;
  audio_base64: string;
  metrics?: Record<string, number>;
};

type DirectAnswer = {
  text: string;
  delivered: boolean;
};

type DirectRunResult = {
  payloads?: Array<{
    text?: string;
    isError?: boolean;
    isReasoning?: boolean;
    isCommentary?: boolean;
  }>;
  meta: { finalAssistantVisibleText?: string };
  didDeliverSourceReplyViaMessageTool?: boolean;
  messagingToolSourceReplyPayloads?: Array<{ text?: string }>;
};

type DirectTurnMetrics = {
  version: 1;
  values: Record<string, number>;
  failed_stage?: string;
};

const PROJECTION_FAILURE_SPEECH = "抱歉，在生成最终答案时出了点问题。";

export async function prepareDirectAnswerSpeech(
  complete: LlmCompleter,
  answer: string,
  model?: string,
): Promise<string> {
  return (
    (await prepareDirectSpeech(complete, answer, model)) ??
    PROJECTION_FAILURE_SPEECH
  );
}

function endpoint(base: string, suffix: string): string {
  return new URL(suffix, base).toString();
}

async function measure<T>(
  metrics: DirectTurnMetrics,
  name: string,
  operation: () => Promise<T>,
): Promise<T> {
  const started = performance.now();
  try {
    return await operation();
  } finally {
    metrics.values[`plugin_${name}_ms`] = Math.round(
      performance.now() - started,
    );
  }
}

async function requestJson(
  url: string,
  token: string,
  init: RequestInit,
): Promise<Record<string, unknown>> {
  const response = await fetch(url, {
    ...init,
    headers: {
      authorization: `Bearer ${token}`,
      ...(init.headers ?? {}),
    },
    redirect: "error",
  });
  if (!response.ok) {
    throw new Error(`XC Body voice endpoint returned ${response.status}`);
  }
  const value: unknown = await response.json();
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("XC Body voice endpoint returned invalid JSON");
  }
  return value as Record<string, unknown>;
}

function usableText(value: unknown): string | undefined {
  return typeof value === "string" ? value.trim() || undefined : undefined;
}

export function resolveDirectAnswer(result: DirectRunResult): DirectAnswer {
  const deliveredReplies =
    result.didDeliverSourceReplyViaMessageTool === true
      ? (result.messagingToolSourceReplyPayloads ?? [])
          .map((payload) => usableText(payload.text))
          .filter((text): text is string => text !== undefined)
      : [];
  const finalText = usableText(result.meta.finalAssistantVisibleText);
  if (finalText) {
    const delivered =
      deliveredReplies.includes(finalText) ||
      finalText === deliveredReplies.join("\n\n");
    return { text: finalText, delivered };
  }
  const payload = [...(result.payloads ?? [])].reverse().find(
    (candidate) =>
      candidate.isError !== true &&
      candidate.isReasoning !== true &&
      candidate.isCommentary !== true &&
      usableText(candidate.text) !== undefined,
  );
  const payloadText = usableText(payload?.text);
  if (payloadText) {
    const delivered =
      deliveredReplies.includes(payloadText) ||
      payloadText === deliveredReplies.join("\n\n");
    return { text: payloadText, delivered };
  }
  const sourceReply = deliveredReplies.at(-1);
  if (sourceReply) {
    return { text: sourceReply, delivered: true };
  }
  throw new Error("OpenClaw produced no visible direct answer");
}

export class DirectConversationService {
  private readonly api: OpenClawPluginApi;
  private readonly config: DirectConversationConfig;
  private stopped = false;
  private loopPromise: Promise<void> | undefined;

  constructor(api: OpenClawPluginApi, config: DirectConversationConfig) {
    this.api = api;
    this.config = config;
  }

  start(): void {
    this.stopped = false;
    this.loopPromise = this.loop();
  }

  async stop(): Promise<void> {
    this.stopped = true;
    await this.loopPromise;
  }

  private async loop(): Promise<void> {
    while (!this.stopped) {
      try {
        const capture = await this.claim();
        if (capture !== null) {
          await this.handle(capture);
        }
      } catch (error) {
        this.api.logger.warn(
          `XC Body direct conversation failed: ${String(error)}`,
        );
      }
      if (!this.stopped) {
        await new Promise((resolve) => setTimeout(resolve, this.config.pollMs));
      }
    }
  }

  private async claim(): Promise<Capture | null> {
    const body = await requestJson(
      endpoint(this.config.voiceUrl, "capture"),
      this.config.token,
      {
        method: "GET",
        signal: AbortSignal.timeout(this.config.timeoutMs),
      },
    );
    const capture = body.capture;
    if (capture === null) {
      return null;
    }
    if (
      typeof capture !== "object" ||
      capture === null ||
      Array.isArray(capture) ||
      typeof (capture as Record<string, unknown>).turn_id !== "string" ||
      typeof (capture as Record<string, unknown>).audio_base64 !== "string" ||
      ("metrics" in capture &&
        (typeof (capture as Record<string, unknown>).metrics !== "object" ||
          (capture as Record<string, unknown>).metrics === null ||
          Array.isArray((capture as Record<string, unknown>).metrics)))
    ) {
      throw new Error("XC Body voice capture is invalid");
    }
    return capture as Capture;
  }

  private async handle(capture: Capture): Promise<void> {
    const directory = await mkdtemp(join(tmpdir(), "xc-body-voice-"));
    const audioPath = join(directory, "capture.ogg");
    const metrics: DirectTurnMetrics = {
      version: 1,
      values: { ...(capture.metrics ?? {}) },
    };
    const started = performance.now();
    let stage = "audio_decode";
    try {
      await measure(metrics, stage, () =>
        writeFile(audioPath, Buffer.from(capture.audio_base64, "base64")),
      );
      const storePath = this.api.runtime.agent.session.resolveStorePath(
        this.api.config.session?.store,
        { agentId: this.config.agentId },
      );
      await this.api.runtime.agent.session.runWithWorkAdmission(
        { storePath, sessionKey: this.config.sessionKey },
        async (abortSignal) => {
          stage = "transcription";
          const transcription = await measure(metrics, stage, () =>
            this.api.runtime.mediaUnderstanding.transcribeAudioFile({
              filePath: audioPath,
              cfg: this.api.config,
              mime: "audio/ogg",
            }),
          );
          const transcript = transcription.text?.trim();
          if (!transcript) {
            throw new Error("XC Body voice transcription was empty");
          }
          stage = "question_delivery";
          await measure(metrics, stage, () =>
            this.sendTelegram(`🎙️ Louis via XC Body: ${transcript}`),
          );
          const entry = this.api.runtime.agent.session.getSessionEntry({
            storePath,
            sessionKey: this.config.sessionKey,
          });
          if (!entry?.sessionId) {
            throw new Error("configured OpenClaw session is unavailable");
          }
          stage = "agent";
          const result = await measure(metrics, stage, () =>
            this.api.runtime.agent.runEmbeddedAgent({
              sessionId: entry.sessionId,
              sessionKey: this.config.sessionKey,
              sessionTarget: {
                agentId: this.config.agentId,
                sessionId: entry.sessionId,
                sessionKey: this.config.sessionKey,
                storePath,
              },
              agentId: this.config.agentId,
              runId: capture.turn_id,
              workspaceDir: this.api.runtime.agent.resolveAgentWorkspaceDir(
                this.api.config,
                this.config.agentId,
              ),
              config: this.api.config,
              prompt: transcript,
              trigger: "user",
              messageChannel: "telegram",
              messageProvider: "telegram",
              messageTo: this.config.telegramTarget,
              currentMessagingTarget: this.config.telegramTarget,
              senderIsOwner: true,
              sourceReplyDeliveryMode: "message_tool_only",
              extraSystemPrompt: [
                "This user turn was transcribed from XC Body.",
                "Reply normally to Louis in the existing session.",
                "Do not mention this transport unless it matters to the answer.",
              ].join(" "),
              timeoutMs: this.api.runtime.agent.resolveAgentTimeoutMs(
                this.api.config,
              ),
              abortSignal,
            }),
          );
          const answer = resolveDirectAnswer(result);
          if (!answer.delivered) {
            stage = "answer_delivery";
            await measure(metrics, stage, () =>
              this.sendTelegram(answer.text),
            );
          }
          stage = "projection";
          const speech = await measure(metrics, stage, () =>
            prepareDirectAnswerSpeech(
              this.config.complete,
              answer.text,
              this.config.speechModel,
            ),
          );
          metrics.values.plugin_total_before_answer_ms = Math.round(
            performance.now() - started,
          );
          stage = "answer_post";
          await requestJson(
            endpoint(this.config.voiceUrl, "answer"),
            this.config.token,
            {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({
                turn_id: capture.turn_id,
                answer: speech,
                metrics,
              }),
              signal: AbortSignal.timeout(this.config.timeoutMs),
            },
          );
        },
      );
    } catch (error) {
      metrics.values.plugin_total_before_answer_ms = Math.round(
        performance.now() - started,
      );
      metrics.failed_stage = stage;
      await this.abandon(capture.turn_id, metrics);
      throw error;
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  }

  private async abandon(
    turnId: string,
    metrics: DirectTurnMetrics,
  ): Promise<void> {
    try {
      await requestJson(
        endpoint(this.config.voiceUrl, "abandon"),
        this.config.token,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ turn_id: turnId, metrics }),
          signal: AbortSignal.timeout(this.config.timeoutMs),
        },
      );
    } catch (error) {
      this.api.logger.warn(
        `XC Body voice turn abandon failed: ${String(error)}`,
      );
    }
  }

  private async sendTelegram(text: string): Promise<void> {
    const send = (await this.api.runtime.channel.outbound.loadAdapter(
      "telegram",
    ))?.sendText;
    if (!send) {
      throw new Error("Telegram outbound adapter is unavailable");
    }
    await send({
      cfg: this.api.config,
      to: this.config.telegramTarget,
      text,
    });
  }

}
