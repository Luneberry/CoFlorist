# CoFlorist

A local Korean conversation dashboard for arranging flowers with an operator-assisted robot demonstration. The interface has two tabs: **메인** and **설정**. NVIDIA Nemotron on Nebius Token Factory interprets requests; an actual FlyWire-derived circuit runs through **Express → `child_process.spawn()` → Python/Brian2**. A person controls the robot and confirms physical outcomes.

## Run

Use **Node.js 24**, a recent desktop Chrome or Edge, and a Nebius Token Factory key with access to a NVIDIA Nemotron model. On macOS, Homebrew's keg-only Node 24 can be invoked at `/opt/homebrew/opt/node@24/bin/node` without switching the default Node version.

```powershell
npm install
npm start
```

Open [http://localhost:4317](http://localhost:4317). The server binds to the local computer; it is not configured as a public or LAN deployment.

In **설정 → 대화 연결**, enter the key, select a model and press **저장하고 연결 확인**. This sends a real request to Nebius; there is no simulated success response. The default model ID is `nvidia/nemotron-3-super-120b-a12b`; availability depends on your account and the provider catalog.

Keys entered in Settings stay in server memory and must be entered again after restarting. They are excluded from SQLite, API responses and exports. Alternatively, copy `.env.example` to `.env` and set `NEBIUS_API_KEY` there yourself. In that case the secret is stored in your local `.env`; the application does not write it. Never commit that file. `.env.example` also documents `PORT`, `NEBIUS_MODEL` and `PYTHON_EXECUTABLE`.

## Prepare the brain worker

On macOS, run `sh scripts/setup-brain-macos.sh` with Python 3.13 and `uv` (or `pip`) available. On Windows, run:

```powershell
powershell -File scripts/setup-brain.ps1 -PythonExecutable "C:\path\to\python.exe"
node python/test-brain-worker.mjs
```

The setup creates `.venv`, installs pinned packages and downloads about 90 MB of upstream sources/data. It builds `data/flywire/subset.json` and a provenance manifest. It does not modify the supplied base Python runtime. In **설정 → 초파리 신경망**, use `.venv\Scripts\python.exe` and `data\flywire\subset.json` from this project, then start and test the worker. The application fills in absolute defaults.

This is a **384-neuron isolated subset with 20,407 real connections** in the current pinned-MN9 preparation, not the entire fly brain. Dialogue-to-stimulus and activity-to-action rules are engineered mappings; preference/failure records are application memory, not neural learning. See [the worker notes](python/README.md), [measured input sweep](docs/FLORA_BRAIN_SWEEP.md), and [implementation details](docs/IMPLEMENTATION.md).

If you prepared `data/flywire/` before the MN9 pinning change, rerun `python/prepare_data.py` (or the setup script) before starting the worker. The generated subset and its SHA-256 manifest change when MN9 is included.

For the device dialogue feed, open **설정 → 외부 대화**, enter the local SSE endpoint, and connect. Each assistant event with `stimulus_hz` runs a fresh 200 ms trial using the assistant turn number as the seed; the reply bubble shows active neurons, downstream rate, MN9 rate, and a display-only wait/reach/offer badge. A nonempty `flower_color` adds a pending placement, which the operator must confirm through the existing controls. The sweetness bar shows the latest input rate and the sparkline shows measured downstream rates across turns. To check without a device, run `node examples/conversation-stream.mjs --demo` in another terminal and connect to its local `/events` endpoint. The example sends two synthetic dialogue turns, but the brain calculation uses the prepared real FlyWire subset.

### Pluggable conversation server

CoFlorist accepts any conversation server that follows the [external SSE feed contract](docs/EXTERNAL_FEED_CONTRACT.md). The conversation server can be replaced without changing the dashboard: it supplies dialogue, flower colour and stimulus Hz, while CoFlorist runs the circuit and shows operator suggestions. To inspect the full contract without a device or model key, run `node examples/conversation-stream.mjs --flora` and connect **설정 → 외부 대화** to its local `/events` endpoint. This replays the [synthetic four-turn sample](examples/flora-feed-sample.json) with stable event IDs, including WAIT, OFFER, REACH and an error state. The Flora DUO server is a separate reference implementation; its repository link can be added when public.

## 시연 순서

1. **설정**에서 받는 사람과 이전 이야기를 저장하세요. 만들어 넣은 사례라면 **시연용 예시 이야기**를 표시하세요. 근거가 없으면 모델이 취향을 확인하도록 되어 있습니다.
2. 카메라를 직접 선택하거나 **LeLab 화면 공유**에서 기존 LeLab 탭/창을 선택하고 한국어 목소리를 시험하세요. 카메라는 화면 표시용이며 꽃이나 사람을 인식하지 않습니다. 연결 전 사진은 참고 사진으로 표시됩니다. [카메라 연결 설명](docs/CAMERA.md)
3. **메인**에서 사람이 먼저 말합니다. “그 사람에게 꽃을 선물하고 싶어.” → 근거 기반 흰색·빨간색 추천 → “흰 꽃을 가운데 한 송이, 빨간 꽃을 오른쪽 한 송이 놓아줘.”
4. 운영자가 실제 로봇을 조종하고 작업 시작·배치 완료·실패·재시도를 기록합니다. “잠깐 기다려” 또는 대기 버튼은 모델 응답을 기다리지 않고 앱을 대기 상태로 바꿉니다. 실제 로봇 정지는 운영자가 수행합니다.
5. 배치를 모두 마친 뒤 사용자가 승인합니다. 이후 **들어 올리기 → 건네기 → 실제 수령 확인 → 그리퍼 해제 확인** 순서로 진행합니다. 마지막 두 확인 전에는 전달 완료가 아닙니다.

## Verification

```powershell
npm test
node python/test-brain-worker.mjs
```

The brain integration has been exercised with real data: 100 Hz input over 200 ms produced 1,260 spikes in 139 active neurons; no input produced zero spikes; silencing all stimulus outputs removed downstream propagation. Identical seeds reproduced the node rates. These are local numerical checks, not robot experiments.

On 2026-10-25, actual account-authenticated Nemotron calls produced the expected white/red recommendation and two placement tasks. A concurrent pause cancelled an in-flight model request (HTTP 409) without altering placements. The user confirmed Korean speech input and output. All 21 local state/parser/API tests passed. **The chosen camera/screen stream and physical handoff still need validation** using the [device checklist](docs/IMPLEMENTATION.md#presentation-checklist).

Nonsecret settings, the latest session snapshot and audit events are stored in `.runtime/coflorist.sqlite`. A server restart begins a fresh active session while retaining settings. Export includes recipient notes and session/audit data but no API key. Session reset starts a new demonstration; it does not delete historical audit records.

## Sources and attribution

- [Nebius Token Factory API](https://api.tokenfactory.nebius.com/docs): actual hosted model requests.
- [NVIDIA Nemotron 3 Super](https://build.nvidia.com/nvidia/nemotron-3-super-120b-a12b/build): the default NVIDIA model family; model/provider terms apply separately.
- [Drosophila brain model](https://github.com/philshiu/Drosophila_brain_model/tree/91bdd1e7dcf193f3e7ca5a8933497fcef63b7960): copyright © 2023 Philip Shiu and Nico Spiller, MIT license. The upstream license is preserved in `data/flywire/source/LICENSE`; source URLs and SHA-256 hashes are in `data/flywire/manifest.json`.
- [Brian2](https://brian2.readthedocs.io/): neural simulation; [Web Speech API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Speech_API): browser speech input/output.
