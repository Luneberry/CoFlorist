# Flora — the cat florist device and its DUO voice server (reference implementation)

This folder is the device + server half of the CoFlorist project: a talking cat that answers with **NVIDIA Nemotron on Nebius Token Factory** and feeds the dashboard in the repository root. The dashboard treats the conversation server as a replaceable component (`../docs/EXTERNAL_FEED_CONTRACT.md`); this is the implementation shown in the demo video.

**Nebius × NVIDIA Global AI Hackathon 2026 — Physical AI track.**

Flora is a small cat-shaped device. You press its button and talk; Flora listens for a minute, answers in Korean as a florist cat, and names **a flower and its colour** that fits your mood. In the submitted configuration every spoken reply is produced by **NVIDIA Nemotron 3.5 Lightning served by Nebius Token Factory**. The answer is spoken by the cat, and its emotion, flower colour and a "sweetness" level are published to an operator dashboard, where a real **FlyWire fruit-fly sugar-sensing circuit** (384 neurons, Brian2) is stimulated in proportion to how sweet the words were. The dashboard maps the measured circuit activity to a **WAIT / OFFER / REACH suggestion**; a human operator controls and confirms the robot arm. No robot command is sent automatically.

```
 you ──▶ cat device (Tuya T5AI, mic/speaker/LCD) ──Opus/WSS──▶ DUO server (flora/server, Mac)
                                                                  ├─ speech → text   (Gemini)
                                                                  ├─ text → reply    (Nemotron on Nebius Token Factory)  ◀── required stack
                                                                  ├─ reply → speech  (Gemini)
                                                                  └─ SSE feed: text + flower colour + emotion + stimulus Hz
                                                                              │
                                             CoFlorist dashboard (repository root) ◀──┘  (fruit-fly circuit → WAIT/OFFER/REACH suggestion; operator confirms the robot arm)
```

## Why it had to be Nemotron 3.5 Lightning

A talking cat is a hard customer for a language model: the answer is **spoken**, so it must come back in about a second, be one or two short Korean sentences, and still carry machine-readable structure (an emotion for the face, a flower colour the robot can act on). We measured the candidates on exactly that job through Nebius Token Factory and chose **`nvidia/Nemotron-3_5-Lightning`** (30B mixture-of-experts, 3B active, OpenMDW open weights).

| What the cat needs | What Lightning gives | Evidence (all measured through Token Factory on 2026-10-29) |
|---|---|---|
| A reply fast enough to feel like conversation | 3B active parameters; thinking can be switched off per request | 0.6–0.9 s per turn after the first call (first call 1.2–1.5 s), 20+ consecutive turns in tests, 7 live dashboard turns |
| Strict structured output the device and robot can trust | Follows a JSON schema and few-shot examples precisely, with `response_format: json_object` | emotion tag always from our 9-value list; flower colour correct in 6/6 mood prompts; small-talk turns correctly return `flower: null` after one added example |
| Natural, warm Korean in two sentences | Better Korean than Nemotron 3 Nano in a side-by-side test on the same prompts (e.g. it knows 츄르, the Korean cat treat) | Nano vs Lightning comparison, same 3+7 prompts, same endpoint — see `docs/2026-10-29-nemotron-submission-plan.md` §1-1 |
| Cheap enough to leave running all day | $0.06 / $0.24 per 1M input/output tokens (Nebius Token Factory price list, read 2026-10-29) | estimate: a turn is ~250 tokens ≈ $0.00005; every live call in this project ran on the account's initial $1 trial credit, which was never topped up |
| A path to self-hosting later | Open weights (OpenMDW-1.1); NVIDIA publishes the model for supported GPUs | not implemented or verified in this project — the submitted system calls Token Factory at runtime; our provider class is OpenAI-compatible, so the prompt and parser could be pointed at a self-hosted endpoint in future work |

Nemotron 3 Ultra and 3.5 Super VL were not measured; by size and price they are less suited to this short, text-only voice loop, and the cat never sends images. Numbers above are our own measurements with sample sizes and method in `docs/MEASUREMENTS.md`.

## What Nebius Token Factory gave us

- **Zero-infrastructure Nemotron.** No GPU, no NIM container, no model download. The server already had an OpenAI-compatible provider for other models; pointing it at `https://api.tokenfactory.nebius.com/v1/` and setting `DUO_LLM=nemotron` switched the whole product to Nemotron in one afternoon with no pipeline code changes (`server/duo_server/providers/openai_chat.py`, `config.py`).
- **A catalog we could A/B in minutes.** `GET /v1/models` lists every Nemotron variant; we ran the identical florist prompt against Nano-30B-A3B and 3.5 Lightning on the same endpoint and picked by measured Korean quality and latency.
- **Control over reasoning at the API level.** Token Factory passes `chat_template_kwargs.enable_thinking=false` through, which turned a reasoning model into a ~0.7 s spoken-reply model without touching the prompt. With thinking on, the same request spent its whole token budget on a hidden thinking process and returned no answer — we saw this and fixed it with one body field.
- **Reliable structured output.** `response_format: {"type": "json_object"}` is honoured. The server still validates the JSON and falls back safely (`parse_reply` in `openai_chat.py`), but in our runs the schema was kept.
- **Pay-per-token with trial credit.** The live API work — model comparison, prompt iteration, the real dashboard runs — fit inside the initial $1 trial credit. The 175 automated tests use fake providers and make no API calls.
- **One model family across the project.** The dashboard (CoFlorist) has an optional, separate Nemotron chat mode that also runs on Token Factory. Flora's external-feed path does not make a second model call: the dashboard only receives the server's result and runs the circuit.

In the submitted configuration speech recognition and synthesis use Gemini (a third-party API used alongside the required stack). An NVIDIA Nemotron ASR provider (`providers/nvidia_asr.py`) is implemented but was not used in the demo.

## Repository layout

| Path | What |
|---|---|
| `server/` | DUO server (FastAPI). xiaozhi-compatible device protocol, Nemotron/Gemini providers, text chat page, registration, SSE feed for the dashboard. 175 tests. |
| `firmware/pusheen_cat/` | Cat firmware (TuyaOpen app for T5AI). QR setup, VAD, Opus, display, USB bridge. |
| `firmware/patches/` | Two small SDK patches required to build/run. |
| `assets/` | Cat expression frames (8 emotions × 2) and the image-generation prompts. |
| `tools/` | Swift scripts that turn frames into the C asset table and the setup QR. |
| `docs/` | Fly-brain design, model selection and measurements. The feed contract is at the repository root: `../docs/EXTERNAL_FEED_CONTRACT.md`. |

## Quick start (server)

```bash
brew install opus          # libopus
cd server && uv sync
cp .env.example .env       # fill NEBIUS_API_KEY and GEMINI_API_KEY; set DUO_LLM=nemotron
uv run pytest -q           # 175 passed
uv run python -m duo_server
```

Open `http://127.0.0.1:8765/register` (set `DUO_INVITE_CODE` in `.env` first) to get a personal text-chat page `/d/<secret>/chat` and a device OTA URL `/d/<secret>/xiaozhi/ota/`. Try the chat page first — it needs no hardware. The dashboard feed for that registration is `/d/<secret>/events` (contract: `../docs/EXTERNAL_FEED_CONTRACT.md`).

Local use: the dashboard and the server on the same machine work out of the box (the feed allows the dashboard origins `http://localhost:4317` and `http://127.0.0.1:4317`).

Remote device: the cat connects over WSS, so the server must sit behind an HTTPS reverse proxy that forwards WebSocket upgrades to port 8765 (we used a Tailscale Funnel). Viewing the dashboard from another machine is not configured in this submission: CoFlorist binds to `127.0.0.1` and the feed's origin allow-list is fixed in `server/duo_server/app.py` (`feed_allowed_origins`); both would need an explicit host/origin setup, and an HTTPS page cannot subscribe to a plain-HTTP feed.

## Quick start (device)

See `firmware/README.md` (pinned TuyaOpen commits, patches, build, flash). Put the OTA URL from the register page into `cat_profile_private.h`, build, flash, power on, scan the QR with your phone, enter Wi-Fi, press the button and talk.

## Quick start (dashboard)

The dashboard is this repository's root project (see the root `README.md`). Run it on the same machine as the server and set *Settings → External conversation* to `http://127.0.0.1:8765/d/<secret>/events`. Without a device or server, `node examples/conversation-stream.mjs --flora` replays a sample conversation.

## What is honest about the fruit-fly part

The circuit is a real but isolated 384-neuron induced subset of the FlyWire v630 connectome around 21 sugar-sensing neurons, simulated with the leaky integrate-and-fire model of Shiu et al., *Nature* 2024 (code: philshiu/Drosophila_brain_model, MIT). Mapping the conversation's emotion to a stimulus rate, and turning measured activity into a WAIT/OFFER/REACH suggestion (REACH when the pinned feeding motor neuron MN9 fires at ≥ 15 Hz, OFFER when downstream activity reaches 1 Hz, with a 7 Hz downstream fallback when MN9 is absent), are application rules we designed and measured (`docs/2026-10-29-flora-fly-brain-design.md`, CoFlorist `docs/FLORA_BRAIN_SWEEP.md`); they are labelled as such on screen. The suggestion never moves the arm by itself. Nothing here claims the circuit understands speech, flowers or emotion.

## Project history

This is a new project: it started on 2026-09-29, inside the submission period (which opened 2026-08-26). The ESP32 prototype ("DUO") was replaced by the T5AI cat on 2026-10-25; Nemotron on Token Factory, the florist persona, the flower-colour contract, the SSE feed and the dashboard integration were added on 2026-10-29.

## Licenses and attribution

- This folder (server, firmware app, assets, tools, docs): MIT (see `LICENSE`, same as the repository), except the TuyaOpen-derived portal files listed in `THIRD_PARTY_NOTICES.md`, which stay Apache-2.0 (license copy in `firmware/LICENSE-APACHE-2.0`).
- Model: NVIDIA Nemotron 3.5 Lightning, OpenMDW-1.1, accessed as a hosted service on Nebius Token Factory. No model weights are included here.
- Firmware SDK: TuyaOpen and its T5AI platform, Apache-2.0, not included; pinned commits and the two patches we apply are in `firmware/README.md` and `firmware/patches/`.
- Device protocol: compatible with the MIT-licensed xiaozhi-esp32 reference implementation (https://github.com/78/xiaozhi-esp32). The server implements the protocol; no xiaozhi code is copied into this repository.
- Fruit-fly model: Shiu et al., *Nature* 2024, code MIT (philshiu/Drosophila_brain_model); FlyWire v630 connectome data under FlyWire's citation and use terms. Both are used by the dashboard repository and are not included here.
- Cat expression images were generated with an image model from the prompts in `assets/`.

---

### 한국어 요약

플로라는 고양이 모양 기기예요. 버튼을 누르고 말하면 1분 동안 듣고, 꽃집 고양이 플로리스트로서 한국어로 답하며 **기분에 어울리는 꽃과 색**을 골라 줘요. 답은 **Nebius Token Factory의 NVIDIA Nemotron 3.5 Lightning**이 만들고, 기기가 소리로 읽어요. 답의 감정·꽃 색·"달콤함" 값은 대시보드로 흘러가 실제 **초파리 단맛 회로(FlyWire, 384뉴런)**를 자극하고, 회로 활동을 읽어 "대기/추천/손 뻗기" 제안 배지를 보여 주고, 로봇 팔은 운영자가 직접 움직이고 확인해요(자동 명령 없음). 서버는 `flora/server/`, 펌웨어는 `flora/firmware/`, 대시보드는 이 저장소의 루트예요.
