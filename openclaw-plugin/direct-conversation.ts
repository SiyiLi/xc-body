import { Buffer } from "node:buffer";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";

export type DirectConversationConfig = {
  voiceUrl: string;
  token: string;
  sessionKey: string;
  telegramTarget: string;
  agentId: string;
  pollMs: number;
  timeoutMs: number;
};

type Capture = {
  turn_id: string;
  audio_base64: string;
};

type DirectAnswer = {
  text: string;
  delivered: boolean;
};

const MAX_DIRECT_RUN_IDS = 64;

function endpoint(base: string, suffix: string): string {
  return new URL(suffix, base).toString();
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

export class DirectConversationService {
  private readonly api: OpenClawPluginApi;
  private readonly config: DirectConversationConfig;
  private stopped = false;
  private loopPromise: Promise<void> | undefined;
  private activeTurnId: string | undefined;
  private readonly directRunIds = new Set<string>();

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

  isDirectRun(runId: string): boolean {
    return this.activeTurnId === runId || this.directRunIds.has(runId);
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
      typeof (capture as Record<string, unknown>).audio_base64 !== "string"
    ) {
      throw new Error("XC Body voice capture is invalid");
    }
    return capture as Capture;
  }

  private async handle(capture: Capture): Promise<void> {
    const directory = await mkdtemp(join(tmpdir(), "xc-body-voice-"));
    const audioPath = join(directory, "capture.ogg");
    try {
      await writeFile(audioPath, Buffer.from(capture.audio_base64, "base64"));
      const transcription = await this.api.runtime.mediaUnderstanding
        .transcribeAudioFile({
          filePath: audioPath,
          cfg: this.api.config,
          mime: "audio/ogg",
        });
      const transcript = transcription.text?.trim();
      if (!transcript) {
        throw new Error("XC Body voice transcription was empty");
      }
      await this.sendTelegram(`🎙️ Louis via XC Body: ${transcript}`);
      const answer = await this.runAgent(capture.turn_id, transcript);
      if (!answer.delivered) {
        await this.sendTelegram(answer.text);
      }
      await requestJson(
        endpoint(this.config.voiceUrl, "answer"),
        this.config.token,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            turn_id: capture.turn_id,
            answer: answer.text,
          }),
          signal: AbortSignal.timeout(this.config.timeoutMs),
        },
      );
    } catch (error) {
      await this.abandon(capture.turn_id);
      throw error;
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  }

  private async abandon(turnId: string): Promise<void> {
    try {
      await requestJson(
        endpoint(this.config.voiceUrl, "abandon"),
        this.config.token,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ turn_id: turnId }),
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

  private async runAgent(
    turnId: string,
    transcript: string,
  ): Promise<DirectAnswer> {
    const storePath = this.api.runtime.agent.session.resolveStorePath(
      this.api.config.session?.store,
      { agentId: this.config.agentId },
    );
    return this.api.runtime.agent.session.runWithWorkAdmission(
      { storePath, sessionKey: this.config.sessionKey },
      async (abortSignal) => {
        const entry = this.api.runtime.agent.session.getSessionEntry({
          storePath,
          sessionKey: this.config.sessionKey,
        });
        if (!entry?.sessionId) {
          throw new Error("configured OpenClaw session is unavailable");
        }
        this.activeTurnId = turnId;
        this.directRunIds.add(turnId);
        while (this.directRunIds.size > MAX_DIRECT_RUN_IDS) {
          const oldest = this.directRunIds.values().next().value;
          if (typeof oldest === "string") {
            this.directRunIds.delete(oldest);
          }
        }
        try {
          const result = await this.api.runtime.agent.runEmbeddedAgent({
            sessionId: entry.sessionId,
            sessionKey: this.config.sessionKey,
            sessionTarget: {
              agentId: this.config.agentId,
              sessionId: entry.sessionId,
              sessionKey: this.config.sessionKey,
              storePath,
            },
            agentId: this.config.agentId,
            runId: turnId,
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
            extraSystemPrompt: [
              "This user turn was transcribed from XC Body.",
              "Reply normally to Louis in the existing session.",
              "Do not mention this transport unless it matters to the answer.",
            ].join(" "),
            timeoutMs: this.api.runtime.agent.resolveAgentTimeoutMs(
              this.api.config,
            ),
            abortSignal,
          });
          const answer = result.meta.finalAssistantVisibleText;
          if (!answer?.trim()) {
            throw new Error("OpenClaw produced no visible direct answer");
          }
          return {
            text: answer,
            delivered:
              result.didDeliverSourceReplyViaMessageTool === true,
          };
        } finally {
          this.activeTurnId = undefined;
        }
      },
    );
  }
}
