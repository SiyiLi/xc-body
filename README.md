# XC Body

XC Body gives an existing OpenClaw agent a physical presence through the
M5Stack StackChan K151/CoreS3 robot. OpenClaw owns judgment and semantic
intentions; deterministic XC Body code owns expressions, timing, LEDs, servo
motion, and safe return to idle.

## North Star

XC Body is not a second assistant or a moving status display. StackChan is the
home body of the same OpenClaw identity the user already knows.

- **Continuity:** thoughts and interactions remain part of one identity across
  software and physical channels.
- **Initiative:** OpenClaw may offer something meaningful without waiting for a
  direct command.
- **Restraint:** silence, deferral, consent, and protection of private
  information are first-class behavior.

Long-term success is experiential: after living with StackChan for a week,
turning it off should make the room feel a little emptier.

## Current Status

- Milestones 1 and 2 have physical acceptance. The Milestone 3 recovery
  matrix and unhealthy-boot OTA rollback have physical acceptance.
- Native OpenClaw integration observes successful agent, subagent, and cron
  completions, chooses `offer` or `skip`, and submits accepted summaries to the
  authenticated VM service.
- The source implements a 30-minute in-process offer lifetime, bounded
  submission retries, robot-session avatar restoration, supervisor recovery,
  and connected idle display dimming. OpenClaw, route, robot, gateway, pending
  service, and offer-expiry recovery passed the physical matrix.
- OpenClaw and StackChan connect outbound to an isolated deployment on the
  configured cloud rendezvous host; raw service ports remain private.
- Milestone 4 direct conversation and appliance UX remain under physical
  acceptance. Exact candidate identifiers live in source metadata and release
  manifests. The six-element idle clock and weather view has physical display
  acceptance; the remaining Milestone 4 criteria stay open.
- Deliberate tap-to-talk microphone input is part of Milestone 4. Camera input,
  always-on capture, durable queues, quiet hours, free-form motion, Home
  Assistant, and Stick S3 integration are not part of the current scope.

The active scope and remaining acceptance work are in
[`docs/MILESTONE_4.md`](docs/MILESTONE_4.md). The current system structure is
in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Future milestone direction
is in [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Repository Checks

```sh
python3 -m unittest discover -s tests -v
npm test --prefix openclaw-plugin
npm run build --prefix openclaw-plugin
python3 scripts/check_line_lengths.py
git diff --check
```

The plugin test suite includes a real request to the fixed projection model and
uses the protected local key file.

## USB Maintenance

The CoreS3 USB channel reports status, updates the saved gateway configuration,
queues verified firmware updates, streams logs, and requests a normal
application reboot. It never returns the saved bearer token.

```sh
scripts/stackchan_usb.py status
scripts/stackchan_usb.py configure --url wss://<public-host>
scripts/stackchan_usb.py automatic-ota disable
scripts/stackchan_usb.py automatic-ota enable
scripts/stackchan_usb.py update \
  --manifest https://<public-host>/firmware/manifest.json
scripts/stackchan_usb.py reboot
scripts/stackchan_usb.py monitor --seconds 30
```

Flashing firmware requires separate explicit permission.

Routine OTA starting with `0.1.2` requires only publishing a newer release and
rebooting the robot. Starting with `0.1.8`, the same release also updates the
assets partition when its verified manifest digest differs. Assets OTA is
single-partition and therefore non-atomic under power loss; the application
stays bootable with static fallback and retries on a later boot. The
authenticated `upgrade_firmware` gateway tool provides a no-USB bridge from
`0.1.1` and an emergency fallback. USB remains the local maintenance and
recovery path.

## Deployment

The VM services and local OpenClaw plugin have separate deployment boundaries:

```sh
XC_BODY_DEPLOY_TARGET=user@host.example \
XC_BODY_PUBLIC_URL=https://body.example \
XC_BODY_REGISTRY_REPOSITORY=registry.example/xc-body scripts/deploy.sh

XC_BODY_DEPLOY_TARGET=user@host.example \
XC_BODY_PUBLIC_URL=https://body.example \
XC_BODY_PROJECTION_API_KEY_FILE=/path/to/model-api-key \
XC_BODY_OPENCLAW_SESSION_KEY=agent:main:telegram:direct:CHAT_ID \
XC_BODY_TELEGRAM_TARGET=CHAT_ID scripts/deploy-openclaw-plugin.sh
```

Private per-installation wrappers should own these deployment values. The
tracked controllers require explicit inputs and contain no private topology.
Omit both OpenClaw session variables for a completion-only plugin installation.
Firmware publishing must run through the private per-installation wrapper,
which writes the ignored local OTA URL before invoking the tracked publisher.
Do not invoke `scripts/publish-firmware-release.sh` directly. Each command
requires fresh permission for its own deployment surface. None of them flashes
the robot. Direct conversation requires the exact existing
session and fixed Telegram target as one pair; setting only one fails closed.
The controller builds and installs a fresh compiled runtime, restarts OpenClaw,
and probes the loaded plugin. Projection uses the fixed model request with
reasoning and thinking disabled; its owner-readable API key file stays outside
Git. Source changes do not reach the installed plugin until the controller runs
again.

The optional Milestone 4 weather display uses QWeather current conditions.
Keep these values in the VM's private `gateway.env`:

```text
XC_BODY_QWEATHER_API_HOST=<dedicated QWeather API hostname>
XC_BODY_QWEATHER_API_KEY=<private API key>
```

On the first Wi-Fi connection after boot, firmware resolves the robot's
approximate coordinates through a keyless HTTPS public-IP lookup on a
low-priority worker and caches the result. Successful results survive
same-SSID reconnects; after a failed lookup, the next Wi-Fi connection event
retries. Weather refreshes read the cache. VPNs and shared network exits can
therefore select the wrong city. An empty or failed lookup falls back to
central Shanghai. When either QWeather value is absent, weather
synchronization is disabled; the idle clock and date remain available.

## Start Here

Read these files in order before changing the repository:

1. [`AGENTS.md`](AGENTS.md)
2. [`docs/MILESTONE_3.md`](docs/MILESTONE_3.md)
3. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
4. [`docs/MILESTONE_2.md`](docs/MILESTONE_2.md)
5. [`docs/MILESTONE_1.md`](docs/MILESTONE_1.md)

The machine-readable boundaries are the
[`embodiment intent contract`][intent-contract] and
[`pending-thought contract`][pending-thought-contract].

## Repository Layout

```text
contracts/      Versioned OpenClaw-to-body contracts
deploy/         VM image, Compose, proxy, and install definitions
docs/           Current architecture and milestone acceptance
firmware/       XC Body CoreS3 firmware
gateway/        Semantic and pending-thought orchestration
openclaw-plugin/ Native OpenClaw completion integration
scripts/        Checks, deployment controllers, and maintenance tools
stackchan/      Deterministic recipes, calibration, and device adapter
stackchan_mcp/  StackChan gateway
tests/          Standard-library contract and behavior tests
```

XC Body is separate from `xc-buddy`. Do not move StackChan work into the Stick
S3 project or modify `xc-buddy` from this repository.

[intent-contract]: contracts/embodiment-intent.schema.json
[pending-thought-contract]: contracts/pending-thought.schema.json
