# XC Body

XC Body gives an existing OpenClaw agent a physical presence through the
official M5Stack StackChan K151/CoreS3 robot.

## North Star

> Make OpenClaw feel physically present at home. StackChan is its persistent,
> expressive body: it remembers, takes initiative at meaningful moments, and
> knows when to remain quiet.

The product is guided by three qualities:

- **Continuity:** the same OpenClaw identity persists across channels and
  physical interactions.
- **Initiative:** the agent may decide that something is worth expressing
  without waiting for a direct command.
- **Restraint:** silence, deferral, and summarization are first-class behavior.

## Current Focus

Milestones 1 and 2 are complete. Production acceptance proved the full
**Knock, Wait, Tell** interaction through OpenClaw and the physical robot.
Milestone 3 is next but has not started.

Milestone 2 adds a narrow initiative boundary: classify a background result as
`ignore`, `remember`, or `offer`; offer silently; wait for a deliberate
CoreS3 head pat or head stroke; then play OpenClaw-prepared Opus packets
after acknowledgment. Duplicate playback is suppressed only while a thought ID
remains in bounded recent-ID memory for the running process. Restart or
eviction may replay it; durable restart persistence, quiet hours, microphone
input, camera input, and background-event policy remain out of scope.

The pending-thought boundary has stdio and persistent HTTP services, each
exposing only `consider_thought`. One process-owned runtime uses one upstream
StackChan MCP session to receive device events. Startup restores the exact
reviewed avatar and binds readiness to the connected device session. A local
OpenClaw-side producer turns the agent's short spoken message into normalized
16 kHz mono Opus before submitting the existing pending-thought contract. Text
never crosses the gateway or reaches the robot.

## Start Here

A new coding session should read these files in order:

1. [`AGENTS.md`](AGENTS.md)
2. [`docs/HANDOFF.md`](docs/HANDOFF.md)
3. [`docs/MILESTONE_2.md`](docs/MILESTONE_2.md)
4. [`docs/MILESTONE_1.md`](docs/MILESTONE_1.md)
5. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
6. [`docs/DECISIONS.md`](docs/DECISIONS.md)
7. [`docs/ROADMAP.md`](docs/ROADMAP.md)

The tracked machine-readable boundaries are the manual embodiment
[`intent contract`][intent-contract] and the Milestone 2
[`pending-thought contract`][pending-thought-contract].

## Milestone 1 Semantic Bridge

The repository now includes a dependency-free Python 3.10+ semantic core, an
executable `embody` MCP service, and a synchronous client bridge for the XC
Body StackChan MCP daemon. The boundary loads the tracked v1 schema,
validates each request, selects an immutable symbolic recipe, invokes an
injected device port, and makes a mandatory
safe-return-to-idle attempt after every expressive intent. A full recipe is
checked for calibration before the first device call.

The opt-in `measured_k151_cores3_calibration()` factory preserves reviewed
motion evidence. Neutral maps to upstream avatar name `idle` and head command
`(0,43,30)`. Curious maps to `thinking` and `(12,50,30)` before returning to
the exact neutral command. Speed `30` represents upstream `low`. Louis confirmed
that the curious pose is clearly visible and appropriately restrained.

Avatar-name mapping is separate from visible-face verification. The measured
factory maps `idle`, `thinking`, `happy`, and `sad`; the reviewed native
320x240 payload and accepted semantic `curious` path use verified `idle` and
`thinking` faces. A successful firmware response alone is still insufficient:
visible verification is bound to the exact reviewed payload and physical run.

The import-safe runner accepts the `curious` shortcut or one v1 semantic intent
JSON object. It accepts no raw face, servo, speed, or return controls. Supported
recipes pass complete local calibration preflight before configuration loading
or MCP session creation. Before device work, both semantic entry points restore
the configured avatar archive and require the exact reviewed SHA-256; another
valid digest fails closed.

Run the dependency-free tests from the repository root:

```sh
python3 -m unittest discover -s tests -v
```

The checks cover deterministic translation, mandatory idle return, rejection
before movement, honest device failures, servo limits, and visible-face
preflight. Real hardware verified the native idle/thinking faces, measured
curious movement, and automatic neutral recovery.

## Candidate Avatar Assets

The repository includes a standard-library-only generator and validator for
a native display-resolution runtime format. It creates 14 complete 320x240
RGB565-LE frames in the required face, eye, and mouth order, plus a JSON
manifest and a labeled PNG contact sheet:

```sh
python3 scripts/build_avatar_assets.py
```

Outputs are deterministic and written beneath the ignored
`build/avatar-assets/` directory. Generation alone is not production
visible-face evidence. The measured calibration's four verified names are valid
only when runtime restoration confirms the exact physically reviewed payload.

The generator validates the local payload and manifest before writing them.
Deployment must make those exact bytes available at the configured archive
path. At startup, the service loads that archive and checks the device-reported
checksum against the reviewed payload. The firmware implements the reviewed
`layered-320x240` format directly and renders native frames at 1x while
retaining the 160x120 modes as rollback. Assets must be
reloaded after gateway or device restart unless persistence is proven.

## USB Maintenance

The firmware includes a local USB maintenance channel for the CoreS3. It
reports Wi-Fi and gateway state, updates the saved gateway URL and
token, reboots through the normal application path, and streams existing
firmware logs. It exposes no network listener and never reports the token.

```sh
scripts/stackchan_usb.py status
scripts/stackchan_usb.py configure --url wss://43.143.37.91
scripts/stackchan_usb.py reboot
scripts/stackchan_usb.py monitor --seconds 30
```

`configure` preserves the saved token unless
`XC_BODY_STACKCHAN_MCP_TOKEN` is already exported or `--token-env` names
another populated environment variable. While a USB host is connected, the
firmware keeps its normal power timer from shutting down the maintenance
channel. The firmware must be built and flashed before these commands are
available; flashing remains a separately approved hardware action.

## Rendezvous VM Deployment

Build, publish, and deploy the production images:

```sh
scripts/deploy.sh
```

Committed deployment requires clean runtime and deployment inputs. An
explicitly authorized `scripts/deploy.sh --candidate` run instead packages the
current working tree and records the deployment as a candidate. Both paths
regenerate and verify the reviewed avatar, build the direct gateway source as a
`linux/amd64` image, and push it to TC Artifactory. The VM pulls the exact image
digests and runs only the gateway,
pending-thought service, and Caddy proxy. Caddy serves the authenticated WSS,
avatar, playback, gateway MCP, and XC Body MCP routes through
`https://43.143.37.91`; raw service ports remain private. The controller also
registers the local OpenClaw producer, which calls the authenticated remote
`consider_thought` service. It does not flash firmware or reconfigure the
firewall.

The deployment script never flashes firmware. When runtime code uses a new
firmware-owned tool, flash and physically accept the matching firmware before
deploying that runtime.

## Repository Layout

```text
contracts/   Versioned contracts between OpenClaw and the physical body
deploy/      Production image, Compose, proxy, and VM install definitions
docs/        Product, architecture, milestone, decisions, and handoff material
examples/    Valid example payloads for the current contract
firmware/    XC Body CoreS3 firmware source and component licenses
gateway/     Intent validation, orchestration, and safe return
scripts/     Repository checks and deterministic build entry points
stackchan/   Symbolic recipes, calibration, and device adapter
stackchan_mcp/  XC Body StackChan gateway source
tests/       Responsibility-grouped standard-library tests
```

## Project Boundary

XC Body is separate from `xc-buddy`:

- `xc-body` is the physical home body of an existing OpenClaw agent.
- `xc-buddy` remains the Stick S3 firmware and macOS companion project.

Do not move XC Body work into `xc-buddy`, and do not modify `xc-buddy` while
working here unless the user explicitly requests a cross-project change.

## Status

- GitHub repository: <https://github.com/SiyiLi/xc-body.git>.
- Firmware and gateway sources are maintained directly in this repository.
  Their import provenance is recorded in `docs/REFERENCES.md`.
- XC Body uses an isolated service on a separate always-on cloud rendezvous;
  raw gateway ports remain private and credentials are not stored here.
- The app-only native-avatar firmware is flashed with private factory and
  tested rollback artifacts retained.
- OpenClaw runs the tracked local thought producer and uses the authenticated
  remote pending-thought MCP route; no OpenClaw runtime/source patch was made.
- The transport-independent semantic core uses dependency-free Python 3.10+.
  The tracked deployment builds the direct gateway source into one Python 3.11
  runtime image, then publishes its digest to TC Artifactory.
- Each executable service holds one upstream session while connected and exits
  on transport loss. The pending-thought service becomes unready if the device
  session changes. Internal reconnect, supervisor restart behavior, and state
  recovery remain unverified follow-up work.

[intent-contract]: contracts/embodiment-intent.schema.json
[pending-thought-contract]: contracts/pending-thought.schema.json
