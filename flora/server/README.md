# DUO 서버

기기가 보낸 말소리를 글로 바꾸고, 답을 만들고, 그 답을 소리로 바꿔 기기에 돌려주는 서버예요. 기기는 xiaozhi 2.0.5 펌웨어의 규칙으로 접속합니다.

## 구성

| 파일 | 하는 일 |
|---|---|
| `duo_server/app.py` | 접속 안내(`/xiaozhi/ota/`)와 음성 연결(`/xiaozhi/v1/`) |
| `duo_server/protocol.py` | 기기와 주고받는 메시지 규칙 |
| `duo_server/pipeline.py` | 음성→글 → 답변 → 글→음성 진행, 단계별 시간 측정 |
| `duo_server/opus.py` | 소리 압축 묶기·풀기 |
| `duo_server/providers/` | Gemini, Nemotron·Solar, NVIDIA 음성 인식, 가짜 AI |
| `duo_server/config.py` | 설정 읽기 |
| `duo_server/metrics.py` | 대화마다 한 줄 기록 |
| `duo_server/fake_device.py` | 기기 없이 시험하는 가짜 기기 |

## 준비

```bash
brew install opus
```

```bash
uv sync --project /Users/pusheen/projects/hackathon-duo/code/server
```

`.env.example`을 `.env`로 복사하고 값을 채웁니다. 키는 채팅이나 저장소에 넣지 않아요.

## 인터넷에 열어 먼 기기를 받기 (등록 방식)

`.env`에 `DUO_INVITE_CODE`를 적으면 등록 방식이 켜져요. 이때 공용 주소 `/xiaozhi/ota/`는 꺼지고, 기기를 붙이려는 사람이 직접 등록해서 자기만의 주소를 받습니다.

1. 기기 주인이 `https://<공개 주소>/register`를 열어 이름, 초대 코드, 답변 모델 키(서버 설정에 따라 업스테이지 또는 OpenAI, "(필수)"라고 적힌 것 하나), 음성을 쓰려면 제미나이 키를 넣어요. 제미나이 키를 비우면 등록은 되지만 음성은 "키가 없어요"로 실패하고, 글 대화만 돼요. 서버 `.env`에 `DUO_REGISTER_ASK_KEYS=0`을 적으면 키 칸이 모두 사라지고 이름·초대 코드만 받아요. 이때는 모든 사람의 대화가 서버의 키로 처리돼요(서버에 답변 모델 키가 있어야 켜져요).
2. 화면에 주소 두 개가 떠요. 둘 다 그 사람만 알아야 해요.
   - `https://<공개 주소>/d/<비밀>/chat` — 소리 없이 글로 묻고 답하는 페이지. 기기 없이 서버와 답변 모델을 먼저 확인할 때 써요.
   - `https://<공개 주소>/d/<비밀>/xiaozhi/ota/` — 기기에 넣는 접속 주소.
3. 기기의 설정 페이지(192.168.4.1) 고급 탭 `Custom OTA URL`에 접속 주소를 넣으면 기기가 붙어요.

실제로 바깥 네트워크에서 밟아 본 순서와 다른 사람에게 보내는 안내문은 `docs/2026-10-16-remote-access-runbook.md`에 있어요.

등록한 사람의 키는 `data/registrations.json`에 남고(본인만 읽을 수 있는 권한), 그 사람의 기기 대화는 그 키로 처리돼요. 등록 목록은 키를 가린 채 볼 수 있어요.

```bash
uv run python -m duo_server.registry
```

공개 주소는 Tailscale Funnel로 만들어요 (한 번 켜 두면 유지됨).

```bash
tailscale funnel --bg 8765
```

| 설정 | 뜻 |
|---|---|
| `DUO_INVITE_CODE` | 등록 페이지를 여는 초대 코드. 비우면 등록 방식 꺼짐 |
| `DUO_PUBLIC_URL` | 바깥에서 보이는 주소. 비우면 요청 헤더로 알아냄 |
| `DUO_REGISTRY_PATH` | 등록 장부 파일 (기본 `data/registrations.json`) |
| `DUO_LEGACY_ROUTES=1` | 같은 와이파이용 공용 주소를 함께 켬. 인터넷에 열 때는 켜지 말 것 |
| `DUO_REGISTER_ASK_KEYS=0` | 등록 화면의 키 칸을 숨김. 모든 사람의 대화를 서버의 키로 처리(서버에 답변 모델 키 필요) |
| `DUO_DEVICE_VOLUME=50` | 기기가 붙을 때마다 스피커 볼륨(0~100)을 이 값으로 맞춤. 기기가 저장하므로 한 번 되면 유지. 비우면 건드리지 않음 |

## 두 가지 경우 고르기

`.env`에서 고릅니다.

| 하고 싶은 것 | 설정 |
|---|---|
| 경우 1: 네모트론 + 제미나이 | `DUO_CASE=1` |
| 경우 2: 솔라 + 제미나이 | `DUO_CASE=2` |
| 음성 인식을 Nemotron으로 | `DUO_STT=nvidia` |
| 답변을 OpenAI 모델로 (업스테이지 없이) | `DUO_LLM=openai` + `OPENAI_API_KEY` |
| 키 없이 흐름만 확인 | `DUO_STT=fake`, `DUO_LLM=fake`, `DUO_TTS=fake` |

고른 조합에 필요한 키만 있으면 됩니다.

| 설정 | 필요한 키 |
|---|---|
| `DUO_STT=gemini` 또는 `DUO_TTS=gemini` | `GEMINI_API_KEY` |
| `DUO_STT=nvidia` | `NVIDIA_API_KEY` |
| 답변 nemotron | `NEBIUS_API_KEY` |
| 답변 solar | `UPSTAGE_API_KEY` (등록 방식에서는 등록하는 사람이 낼 수 있음) |
| 답변 openai | `OPENAI_API_KEY` (등록 방식에서는 등록하는 사람이 낼 수 있음) |

## 실행

서버 폴더에서 실행해야 `.env`와 `logs/`를 찾습니다.

```bash
cd /Users/pusheen/projects/hackathon-duo/code/server
```

```bash
uv run python -m duo_server
```

켜져 있는지 확인:

```bash
curl http://127.0.0.1:8765/health
```

## 기기 없이 시험

```bash
uv run python -m duo_server.fake_device --server http://127.0.0.1:8765 --out logs/reply.wav
```

녹음 파일을 보내려면 `--wav 질문.wav`를 붙입니다. 파일은 16kHz 16비트 모노 WAV여야 해요.

## 시험

```bash
uv run pytest -q
```

## 기록

| 파일 | 내용 |
|---|---|
| `logs/server.log` | 서버 동작 기록 |
| `logs/turns.jsonl` | 대화마다 한 줄: 알아들은 말, 답변, 단계별 걸린 시간, 쓴 AI 이름 |
| `logs/audio/` | 기기 마이크로 들어온 소리 (`DUO_SAVE_AUDIO_DIR`를 적었을 때만) |

`logs/`에는 말한 내용과 목소리가 들어가므로 저장소에 올리지 않습니다.

## 알려진 한계

- **공용 주소(`DUO_LEGACY_ROUTES=1`)의 접속 암호는 같은 네트워크 안에서는 비밀이 아닙니다.** 기기 펌웨어의 규칙상 서버가 접속 안내 응답에 암호를 담아 보내기 때문이에요. 인터넷에 열 때는 등록 방식만 켜세요. 등록 방식은 주소 자체가 비밀이라 그 주소를 아는 사람만 접속 안내를 받습니다.
- 등록 방식에서는 다른 사람의 API 키를 이 서버가 보관합니다. 장부 파일을 다른 곳에 복사하거나 저장소에 올리지 마세요.
- 글 대화 페이지(`/d/<비밀>/chat`)의 화면 쪽 동작은 자동 시험에 들어 있지 않습니다(스크립트 문법 검사와 서버 왕복만). 화면이 이상하면 브라우저 콘솔을 보세요.
- 답변을 말하는 도중 버튼을 눌러 끊는 기능은 없습니다. 말이 끝난 뒤에 다음 말을 듣습니다.
- 실제 AI 연결 코드(Gemini, Nemotron, Solar, NVIDIA)는 요청 형식을 공식 문서 예제와 맞췄지만, 키가 없어 실제 호출은 아직 하지 못했습니다.
- NVIDIA 음성 인식의 호스팅 주소와 function-id는 서드파티 문서에서 온 값입니다.
