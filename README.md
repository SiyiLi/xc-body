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

- Milestones 1 and 2 have physical acceptance. Milestone 3 is in progress.
- Native OpenClaw integration observes successful agent, subagent, and cron
  completions, chooses `offer` or `skip`, and submits accepted summaries to the
  authenticated VM service.
- The source implements a 30-minute in-process offer lifetime, bounded
  submission retries, robot-session avatar restoration, supervisor recovery,
  and connected idle display dimming. The complete recovery matrix still needs
  one exact, versioned physical acceptance run.
- OpenClaw and StackChan connect outbound to an isolated deployment on the
  cloud rendezvous host. The public route is `https://43.143.37.91`; raw
  service ports remain private.
- The robot and source run firmware `0.1.11`.
  The last fully recorded OTA acceptance covered consecutive updates from
  `0.1.4` to `0.1.5` and from `0.1.5` to `0.1.6`. Forced unhealthy-boot
  rollback remains untested.
- Camera and microphone input, durable queues, quiet hours, free-form motion,
  Home Assistant, and Stick S3 integration are not part of the current scope.

The active scope and remaining acceptance work are in
[`docs/MILESTONE_3.md`](docs/MILESTONE_3.md). The current system structure is
in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Future milestone direction
is in [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Repository Checks

```sh
python3 -m unittest discover -s tests -v
python3 scripts/check_line_lengths.py
git diff --check
```

## USB Maintenance

The CoreS3 USB channel reports status, updates the saved gateway configuration,
queues verified firmware updates, streams logs, and requests a normal
application reboot. It never returns the saved bearer token.

```sh
scripts/stackchan_usb.py status
scripts/stackchan_usb.py configure --url wss://43.143.37.91
scripts/stackchan_usb.py automatic-ota disable
scripts/stackchan_usb.py automatic-ota enable
scripts/stackchan_usb.py update \
  --manifest https://43.143.37.91/firmware/manifest.json
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
scripts/deploy.sh
scripts/deploy-openclaw-plugin.sh
scripts/publish-firmware-release.sh
```

Each command requires fresh permission for its own deployment surface. None of
them flashes the robot.

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
