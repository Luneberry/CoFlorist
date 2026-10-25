# CoFlorist implementation

## Current scope

CoFlorist is a local hackathon MVP with **메인 / 설정** navigation. It combines Korean conversation, a browser camera view, operator-confirmed flower tasks and a measured visualization of an actual FlyWire-derived neural circuit. Software proposes and records actions. It sends no motor commands, reads no robot telemetry and performs no camera-based perception.

The intended narrative is that a small biological connection structure helps a conversation become a flower-giving action. That is a design interpretation, not a claim that the fly circuit understands Korean, feels emotion or recognizes human intentions.

## Runtime

| Component | Implementation |
| --- | --- |
| Dashboard | Browser HTML/CSS/JavaScript; two tabs; Server-Sent Events for state |
| Server | Node.js 24, Express 5; `npm start`; `http://localhost:4317` |
| Language model | HTTPS calls to Nebius Token Factory `/v1/chat/completions`; NVIDIA Nemotron model |
| Neural computation | Node `child_process.spawn`, no shell; Python 3.12/Brian2 2.9; JSONL pipes |
| Persistence | Built-in `node:sqlite`; `.runtime/coflorist.sqlite` |
| Speech | Browser `SpeechRecognition`/`webkitSpeechRecognition`, `ko-KR`; `speechSynthesis` |
| Camera | User-selected local device or browser-compatible media URL |
| Physical actions | Human operator controls the robot separately and reports outcomes |

The selected Nemotron model is used for real Korean replies and structured intent extraction. The default ID is `nvidia/nemotron-3-super-120b-a12b`. The model selector filters the provider catalog to NVIDIA Nemotron IDs. Access errors, empty replies, malformed model JSON and timeouts remain errors; no fallback fabricates a successful model call.

The implementation uses Nebius Token Factory, not a separately deployed Nebius AI Cloud VM. Browser speech is not asserted to be NVIDIA-powered. Confirm the event's exact eligibility wording if it imposes conditions beyond using Nebius infrastructure and a NVIDIA open model.

## Functional flow

1. Settings provide a chosen recipient and optional previous conversation/preferences. No history is fetched from an external account. Example history must be marked as demo data. The session initially contains no assistant message; the human starts.
2. A typed or recognized Korean request goes to Express. Nemotron returns a validated reply, intent and remaining flower placements. Supported colors are white/red/yellow/purple, positions are center/left/right, and total arrangements are bounded to 12 flowers.
3. The session state machine validates the plan. Missing preferences should cause a question. Completed placements remain completed when the remaining plan changes. Active physical work cannot silently be replaced by a new plan.
4. An asynchronous brain job runs an application-selected input rate against the real subset. The current mapping uses 80 Hz for gift intent, 120 Hz for retry and 100 Hz otherwise; pause takes an immediate control path instead of waiting for this calculation. The network response contributes a `propose`/`confirm`/retry suggestion through explicit application rules.
5. The dashboard displays measured activity and the proposed work. An operator starts a flower task, physically performs it and reports completion or failure. A recorded failure causes a subsequent retry to choose another registered method. These named methods are application state, not executable robot trajectories.
6. Approval is possible only after every placement has been confirmed complete. The operator then records lift, handover, actual receipt and release in that order. `received` confirms that the person has taken the bouquet; `release` separately confirms the gripper has let go and completes the session.

Model messages never constitute evidence of physical success. Camera frames are not sent to Nemotron, so the application cannot claim to see a flower, hand or grasp.

## Pause and stale results

The pause button and direct Korean wait/stop commands use a local path. They cancel an outstanding language-model request and move the session into a paused state. New operator task actions are rejected while paused. Revision and epoch checks prevent an old asynchronous plan or neural result from changing a newer paused/reset session.

The neural worker does not block Express. It bounds its queue and computation time, rejects invalid input and cleans up pending requests when stopped. A completed brain visualization may remain available as the last measured result; it is not authorization for the next movement. Physical stopping and resuming belong to the human operator.

## Data and credentials

- UI-entered API keys are held only in server memory. They are omitted from settings responses, SQLite and JSON exports. Restarting clears them.
- `.env` is an optional user-managed local secret file, loaded by Node at startup; `.env.example` has empty placeholders. The application does not create or update `.env`.
- SQLite retains nonsecret settings, the latest session snapshot and audit entries. These can include recipient names/history and dialogue. The current server starts a fresh live session after a restart; it does not automatically resume physical work from the saved snapshot.
- Reset starts a new session, while historical audit records remain. Export includes the current state and recent audit data, excluding API credentials.
- The service listens on `127.0.0.1` and checks local request hosts/origins. Public hosting, LAN use, authentication and multiuser sessions are outside this MVP.

## Real FlyWire subset

The data comes from `philshiu/Drosophila_brain_model`, commit `91bdd1e7dcf193f3e7ca5a8933497fcef63b7960`, with the v630 completeness and connectivity files. The source lists 127,400 neurons. `python/prepare_data.py` starts with the 21 sugar sensory neuron IDs from the upstream example, deterministically expands downstream by absolute connection strength to 384 neurons and retains the 20,516 original nonzero connections among those nodes.

Each retained connection preserves its signed upstream effective weight, scaled by 0.275 mV. Brian2 runs the LIF equations with the upstream membrane, threshold, decay, refractory and delay constants. Each trial resets to rest. Poisson stimulation is seeded; outgoing connections of requested silenced neurons are zeroed for that trial. NumPy code generation avoids a C++ compiler dependency.

This is an adaptation for a bounded demonstration, not an exact reproduction of the older upstream software environment. It uses Python 3.12 and Brian2 2.9. The neuron reset contains the existing `v` and `g` state only, omitting the upstream unused `w` assignment. Setup also applies a narrow Windows CPU-cache compatibility change only inside `.venv`, directing Brian2's import-time cache into `.work-tmp/brian-cpu` instead of the user profile.

`data/flywire/manifest.json` records pinned URLs, source sizes/hashes, the extraction rule and the prepared subset hash. The worker verifies that hash at startup. All FlyWire IDs are JSON strings because their values exceed JavaScript's safe-integer limit.

| Initial prepared artifact | Value |
| --- | --- |
| Dataset version | FlyWire v630 |
| Neurons / connections | 384 / 20,516 |
| Subset SHA-256 | `676a9acf91386abbcd57fb90e29de6df410c3529a445481c391bbfe5f986bd84` |
| Default Python | `<project>/.venv/Scripts/python.exe` on Windows |
| Default data | `<project>/data/flywire/subset.json` |

Regenerating data also updates creation metadata, so consult the current manifest for that preparation's hash. Neurons outside the subset and their boundary inputs are absent. Results are therefore isolated-subgraph dynamics. There is no neural learning, full-brain emulation, learned robot control or evidence of biological social cognition. Application preference/failure memory and authored action mappings are separate from the neural model.

## Interfaces and setup

The fixed process boundary is:

```text
Browser → Express → child_process.spawn(Python, shell:false)
                    stdin: JSONL simulate request
                    stdout: JSONL ready/started/result/error
                    stderr: diagnostics
```

`BrainWorker` exposes `start()`, `run(payload)`, `stop()` and `snapshot()`. A run accepts `stimulusHz` (0–250), `durationMs` (50–500), integer `seed` and optional known string `silenceIds`. Results include true spike counts, rates, source identity and measured wall time. A 200 ms simulation interval is biological model time, not a promise of a 200 ms service response. The dashboard output is limited to selected neurons/edges/spikes while aggregate counts cover the entire selected circuit.

```powershell
npm install
powershell -File scripts/setup-brain.ps1 -PythonExecutable "C:\path\to\python.exe"
npm start
```

On this prepared workspace, setup need not be repeated. The setup script creates its own `.venv`, installs `python/requirements.txt`, downloads pinned source files and builds the subset. For Python failures, confirm the executable path points to that environment and run `node python/test-brain-worker.mjs` before debugging the browser. Details are in [python/README.md](../python/README.md).

## Presentation checklist

Local numerical checks passed for the prepared circuit: no-input baseline; stimulus propagation; repeatability at a fixed seed; all stimulus outputs silenced; invalid request recovery; stopping before an awaiting request is queued. For 100 Hz over 200 ms, the recorded check produced 1,260 spikes, 139 active neurons and 11.4738 Hz average downstream activity. Silencing stimulus outputs reduced downstream activity to zero. Warm computation was approximately 0.75–0.98 seconds on this machine; this is not a general latency guarantee.

Run repository checks:

```powershell
npm test
node python/test-brain-worker.mjs
```

The following checks require the actual account, browser and demonstration equipment. They are **not established by local software tests**:

- [x] In Settings, connect with the user's Nebius key and available Nemotron model. Actual Korean recommendation and placement replies were verified on 2026-10-25; model/usage audit data is available for export.
- [ ] Set the actual recipient/history or mark supplied example history as demo. Start a new session and verify the human initiates the conversation. With empty history, verify the model asks rather than inventing preferences.
- [ ] In the selected Chrome/Edge on `localhost`, permit the microphone and recognize “흰 꽃을 가운데 놓아줘”, “잠깐 기다려”, and “계속해줘”. Verify final recognized text before a physical action.
- [ ] Select an available Korean voice and hear the output on the actual speaker. Confirm speech stops when requested and is not re-recognized as the next user request. Browser speech services/voices vary by device and may require internet.
- [ ] Connect the intended camera and verify it is a live view. A remote URL must be directly renderable by the browser; RTSP is not implemented. Confirm the fallback reference photograph is not presented as a live feed.
- [x] Start and test the brain worker; inspect real counts, time, dataset version and subset provenance. API calculation and browser visualization of measured results were verified.
- [ ] With the operator, complete one flower placement, record one failure, retry with another registered method, and request pause while a reply/calculation is in progress. Confirm no old result resumes work.
- [ ] Finish all placements, approve the bouquet, record lift and handover, then confirm **actual receipt before release**. Verify the session completes only after the operator confirms release.
- [ ] Export the demonstration record and verify that no API key is present. Keep operator-assisted execution and the isolated-subset scope explicit in presentation claims.

The user confirmed Korean speech input/output on 2026-10-25. Detailed stage-by-stage voice rehearsal remains part of the physical demo. An actual in-flight Nemotron request was cancelled by pause with HTTP 409 and unchanged placements. Camera/screen capture, physical grasp success and handoff have not yet been verified. See [camera integration](CAMERA.md) for the installed LeLab source and direct-camera/screen-sharing choices.

## Official references and attribution

- [Nebius OpenAI-compatible inference API](https://api.tokenfactory.nebius.com/docs).
- [NVIDIA Nemotron 3 Super](https://build.nvidia.com/nvidia/nemotron-3-super-120b-a12b/build).
- [Pinned Drosophila brain model source](https://github.com/philshiu/Drosophila_brain_model/tree/91bdd1e7dcf193f3e7ca5a8933497fcef63b7960), [upstream MIT license](https://github.com/philshiu/Drosophila_brain_model/blob/91bdd1e7dcf193f3e7ca5a8933497fcef63b7960/LICENSE), copyright © 2023 Philip Shiu and Nico Spiller. The downloaded license is retained with the source files; preserve it when redistributing those materials.
- [Brian2 documentation](https://brian2.readthedocs.io/).
- [Node child processes](https://nodejs.org/api/child_process.html#child_processspawncommand-args-options).
- [MDN SpeechRecognition](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition), [available synthesis voices](https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesis/getVoices), [camera/microphone permissions](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia).
- [Microsoft Korean TTS voice availability](https://support.microsoft.com/ko-kr/accessibility/windows/narrator/appendix-a-supported-languages-and-voices).

Upstream scientific code, model/provider services, browser voices and dependencies retain their respective licenses/terms. The upstream code's MIT license does not imply a license grant for every independent model or service used by this application.
