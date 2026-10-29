# 네모트론으로 대회 규칙 맞추기 — 2026-10-29 계획

목표: 공식 규칙 "Token Factory 추론 API 런타임 호출(또는 AI Cloud 실행) + NVIDIA 오픈소스 모델 1개 이상"을 **답변 모델을 Nebius Token Factory의 Nemotron으로 바꿔** 맞춘다. 음성 인식까지 Nemotron으로 하는 것은 시간이 남을 때의 두 번째 단계.
마감(공식 규칙 원문으로 확인): 2026-10-30 10:00 PT = **10-31 02:00 KST**.

## 0. 지금 상태 (코드에서 확인)
- 서버 `.env`: `DUO_LLM=openai`(gpt-5.6), `DUO_STT=gemini`, `DUO_TTS=gemini`, `DUO_CASE=2`(솔라 기본이지만 DUO_LLM이 덮어씀). 10-14에 "업스테이지 가입이 귀찮다"며 OpenAI 키로 바꾼 상태라 **솔라도 아니고 네모트론도 아님**.
- `NEBIUS_API_KEY`, `NVIDIA_API_KEY`: 줄은 있고 값은 비어 있음.
- 네모트론 답변 경로: `config.py` `DUO_LLM=nemotron` → `providers/openai_chat.py`(OpenAI 호환 chat/completions) → Nebius Token Factory `https://api.tokenfactory.nebius.com/v1/`, 모델 기본값 `nvidia/Nemotron-3_5-Lightning`(`NEMOTRON_MODEL`로 바꿀 수 있음), 추가 옵션은 `DUO_LLM_EXTRA_BODY`(JSON). **한 번도 실제 호출 안 함.**
- 네모트론 음성 인식 경로: `DUO_STT=nvidia` → `providers/nvidia_asr.py`(riva gRPC, `grpc.nvcf.nvidia.com:443`, function-id는 서드파티 문서 값). 라이브러리(`nvidia-riva-client`, grpc 1.84)는 venv에 설치돼 있음. **한 번도 실제 호출 안 함.**
- 등록 방식에서 답변 모델이 네모트론이면 **서버의 Nebius 키 하나로 모든 등록자(DUO 기기, 고양이 기기)를 처리**한다. 등록자가 낸 OpenAI/업스테이지 키는 무시됨. 제미나이(음성) 키는 서버 키 사용.
- 서버는 PID 76462로 터미널 없이 돌고 있음(10-25 Codex 분리 실행). 설정 변경은 재시작해야 반영.

## 1. 단계 A — 답변 모델을 Nemotron으로 (필수, 30분 안팎)

| 순서 | 할 일 | 누가 |
|---|---|---|
| A1 | Nebius Token Factory 계정에서 API 키 발급. 크레딧 코드 `NEBIUS-DEVPOST-GLOBAL26`(25달러) 적용 | 사용자 |
| A2 | 키를 `code/server/.env`의 `NEBIUS_API_KEY=` 뒤에 직접 입력(채팅에 붙이지 않기) | 사용자 |
| A3 | 키로 모델 목록을 조회해 실제 모델 ID 확인. 기본값 `nvidia/Nemotron-3_5-Lightning`이 목록에 없으면 `.env`에 `NEMOTRON_MODEL=<목록의 ID>` 추가 | Claude(키 값은 출력 안 함) |
| A4 | `.env`에서 `DUO_LLM=nemotron`으로 변경(다른 줄은 그대로) | Claude, 사용자 확인 후 |
| A5 | 서버 재시작: 고양이 기기 전원 끄기 → PID 76462에 SIGINT → 같은 폴더에서 `uv run python -m duo_server`(분리 실행 유지) → `/health` 200 확인 | Claude, 사용자 승인 후 |
| A6 | 글 대화 페이지(`/d/<비밀>/chat`)로 한국어 질문 3개 → 답이 `{"text","emotion"}` 형식을 지키는지, 감정 꼬리표가 허용 목록 안인지, 지연(ms) 확인. `logs/turns.jsonl`에 `llm: nemotron:…` 기록 확인 | Claude |
| A7 | 기기 버튼으로 실제 음성 1턴(DUO 또는 고양이) | 사용자 |

조정이 필요할 수 있는 것: Nemotron이 JSON 형식을 안 지키면 `openai_chat.py`의 시스템 프롬프트를 손보거나 `DUO_LLM_EXTRA_BODY`로 모델 옵션(예: 생각 과정 끄기)을 준다. 어떤 옵션이 먹는지는 모델 문서를 보고 실측 후 결정.

## 1-1. 어떤 모델을 고를까 (Nebius Token Factory에 있는 Nemotron 4종, 2026-10-29 공개 페이지 기준)

| 모델 | 크기(활성) | 문맥 | 값(입력/출력, 100만 토큰) | 한국어 | 판단 |
|---|---|---|---|---|---|
| **Nemotron-3-Nano-30B-A3B** (2025-12) | 30B(3B) | 262K | $0.06 / $0.24 | 사전학습 데이터에 한국어 1,270억 토큰 명시. 후학습 언어 목록(EN·DE·ES·FR·IT·JA)엔 없음. Nebius 소개가 "multilingual chat" | **1순위** |
| **Nemotron-3.5-Lightning-30B-A3B** (2026-08) | 30B(3B) | 1M | $0.06 / $0.24 | 지원 언어 목록(EN·ES·FR·DE·IT·JA)에 한국어 없음. 최신 세대 | **2순위, 비교용** |
| Nemotron 3.5 Super VL (120B/12B) | 120B(12B) | 256K | $0.30 / $0.90 | 시각-언어 모델. 글만 쓰는 우리에겐 과함 | 두 소형이 한국어를 못 하면 그때 시험 |
| Nemotron-3-Ultra-550B | 550B(55B) | 1M | $1.00 / $3.00 | 느리고 비쌈 | 음성 대화엔 부적합 |

고르는 기준(순서대로): ① 한국어 답이 자연스러운가 ② `{"text","emotion"}` JSON을 지키는가 ③ 한 턴 지연(음성 대화라 첫 토큰이 1초 안쪽이어야 함) ④ 값.

왜 Nano를 먼저 보나: 활성 3B라 빠르고 싸며(둘 다 같은 값), 한국어를 학습 데이터에 넣었다고 모델 카드가 명시한 유일한 소형 모델이다. Lightning은 더 새롭지만 한국어가 지원 목록에 없다. 둘은 값·속도가 같으니 **같은 한국어 질문 3개로 A6에서 둘 다 받아 보고 ①②③으로 고른다.** 비용은 데모 1,000턴을 돌려도 몇십 원 수준이라 25달러 크레딧으로 충분.

꼭 할 설정: 두 모델 다 "생각 과정(reasoning)"이 기본으로 켜져 있어 답이 늦고 JSON이 깨질 수 있다. 끄는 방법은 모델 카드에 `chat_template_kwargs: {"enable_thinking": false}`로 나와 있다 → 서버 `.env`에 `DUO_LLM_EXTRA_BODY={"chat_template_kwargs":{"enable_thinking":false}}`. Nebius가 이 값을 그대로 넘기는지는 A6에서 확인(안 넘기면 시스템 프롬프트에 "생각 과정 없이 바로 답하라"로 대체). 짧고 일정한 답을 위해 temperature는 0.6 안팎으로 낮춰 시험.

모델 ID 주의: 서버 기본값 `nvidia/Nemotron-3_5-Lightning`은 9-29에 적은 추정값이다. A3에서 키로 목록을 받아 **실제 ID를 `NEMOTRON_MODEL`에 적는다**(Nano라면 `nvidia/Nemotron-3-Nano-30B-A3B` 모양일 가능성이 높지만 확인 전).

## 1-2. Nebius 키 발급 (사용자가 직접)
1. https://tokenfactory.nebius.com 에서 계정 만들기(구글 또는 깃허브 로그인). 공식 빠른 시작: https://docs.tokenfactory.nebius.com/quickstart
2. 콘솔에서 API 키 만들기(메뉴 이름은 문서에 안 나와 있음. 보통 왼쪽 메뉴 "API keys" → 새 키). 키는 만들 때 한 번만 보이니 바로 복사.
3. 결제/크레딧 화면에서 해커톤 코드 `NEBIUS-DEVPOST-GLOBAL26` 입력(메뉴 이름 미확인).
4. 복사한 키를 `~/projects/hackathon-duo/code/server/.env`의 `NEBIUS_API_KEY=` 뒤에 붙여 저장. 채팅·문서에는 붙이지 않기.
- 기본 주소는 `https://api.tokenfactory.nebius.com/v1/`로 서버 기본값과 같음. 모델 ID는 소문자 형식(`nvidia/nemotron-3-super-120b-a12b` 같은 꼴)이 확인됨 → 우리 서버 기본값(대문자·밑줄)은 거의 틀렸으니 A3에서 목록으로 확정.

## 1-3. 대화 내용을 대시보드로 — CoFlorist 연결 (가능, 작음)
- **CoFlorist**: 팀원(Projection3141) 저장소(10-25)이고 Luneberry 계정에는 10-29 포크가 있음. 꽃 선물 로봇 시연용 한국어 대화 대시보드(Node/Express, localhost:4317, 답변은 Nebius의 Nemotron, 초파리 신경망 시뮬레이션, 브라우저 음성). 설정 → "외부 대화"에 **우리 대화 서버의 이벤트 주소**를 넣으면 메인 화면의 대화 피드가 우리 기기의 대화를 그대로 보여 줌.
- 약속(팀원이 `examples/conversation-stream.mjs`와 `public/conversation-feed.js`에 정해 둠): SSE 스트림, 이벤트는 두 종류뿐. `{"id","type":"state","state":"idle|listening|thinking|speaking|error"}`, `{"id","type":"message","role":"user|assistant","text"}`. 소리·키는 보내지 않음. 같은 id는 무시, 최근 500개 보관, `Last-Event-ID`로 재접속 시 이어 보냄, 브라우저 출처(`http://localhost:4317`)만 허용.
- 우리 서버에 붙일 자리(app.py `DeviceEvents`): listen start → `listening`; STT 확정(`on_transcript`) → user message + `thinking`; 답변(`on_reply`) → assistant message + `speaking`; 끝(`finish`) → `idle`(실패면 `error`). 기기·펌웨어는 손대지 않음.
- 구현 크기: 모듈 1개(`duo_server/conversation_feed.py`: 메모리 기록 500 + SSE 응답 + CORS) + app.py에 경로 `GET /d/<비밀>/events`(등록별) 또는 `/events` + 시험 몇 개. 1~2시간. 서버 재시작 1회(네모트론 전환과 묶어서).
- 연결 경로: 대시보드는 노트북(localhost:4317), 우리 서버는 맥미니 → 같은 LAN 주소 또는 Tailscale 공개 https 주소. http 페이지가 https SSE를 구독하는 건 브라우저가 허용. 두 기기(DUO·고양이)가 다 보내면 피드에 섞이므로 등록별 주소로 구분하거나 글머리에 기기 이름.
- 대안(팀원 저장소를 안 고치는 길): 우리 서버가 `logs/turns.jsonl`(시각·기기·입력 글·답·감정·지연이 이미 있음)을 읽는 작은 페이지를 직접 띄우기. 다만 CoFlorist가 이미 있으니 위 연결이 더 싸다.
- 참고: CoFlorist의 Nebius 호출은 `chat_template_kwargs:{enable_thinking:false}`, `response_format:{type:"json_object"}`, temperature 0.1, `<think>` 태그 제거. 10-25에 실제 호출 성공 기록 → 우리 A6 설정의 출발점으로 그대로 쓸 만함.

## 2. 단계 B — 음성 인식도 Nemotron으로 (선택, 시간이 남을 때)

| 선택지 | 필요한 것 | 장단점 |
|---|---|---|
| **B1. NVIDIA 호스팅 API** (`DUO_STT=nvidia`) | build.nvidia.com API 키 → `.env` `NVIDIA_API_KEY`. 코드·라이브러리 준비됨 | 제일 빠름(1~2시간). function-id가 서드파티 문서 값이라 첫 호출에서 확인해야 함. 한국어 `ko-KR`. 실패하면 Gemini로 되돌리면 됨 |
| B2. Nebius GPU에 NIM 띄우기 | Nebius AI Cloud L40S(시간당 1.55달러~), NIM 컨테이너 설정 | 유료·설정 시간 큼. 마감 전엔 비추천 |
| B3. 맥미니에서 NeMo-Speech.cpp | 설치 + 서버 연결 코드 새로 작성 | 무료지만 "Nebius에서 실행" 조건에 기여 안 함. 시간 부족하면 제외 |

검증: 같은 문장을 Gemini와 NVIDIA로 각각 받아 `turns.jsonl`의 stt 결과·지연 비교.

## 3. 제출물 — 공식 규칙 원문(사용자가 10-29 붙여 넣음)으로 확정
- **마감: 2026-10-30 10:00 PT = 10-31 02:00 KST.** 심사 12-01~12-15, 발표 2027-01-11께.
- **필수 조건**: "Nebius Token Factory 추론 API를 런타임에 호출"하거나 AI Cloud에서 실행 + NVIDIA 오픈소스 모델 1개 이상 + 4개 트랙 중 하나. → 서버가 Token Factory의 Nemotron을 호출하면 충족. 제미나이 같은 제3자 API는 그 서비스 약관만 지키면 함께 써도 됨(Third Party Integrations 조항).
- **Physical AI 트랙**: 로봇·IoT·온디바이스 기기, Nemotron 등으로 구동. **시연 영상에 실제 하드웨어가 동작하는 장면 1분 이상** 필수. 데모 URL은 이 트랙엔 불필요. 심사위원이 하드웨어 실물 접근을 요구할 수 있음.
- **제출 항목**: ① 글 설명(영어 또는 영어 번역 첨부) ② **공개 저장소 URL**(GitHub 등, 소스·에셋·실행 안내 전부, **오픈소스 라이선스 파일이 About에 보이게**, README에 설치·실행법, **Nemotron을 어디에 썼고 Token Factory가 어디서 도움됐는지 명시**) ③ **3분 미만 유튜브 공개 영상**(기기에서 실제로 도는 장면, 저작권 음악·상표 금지, 하드웨어 1분 이상) ④ 트랙 선택 ⑤ **Token Factory·NVIDIA 도구 피드백**(별도 상 $100×10) ⑥ 8-26 이전부터 있던 프로젝트면 무엇을 크게 바꿨는지 설명.
- **심사**: 1단계 통과/탈락(트랙 취지에 맞고 필수 API를 실제로 씀) → 2단계 4항목 동일 가중(기술 구현·Token Factory/Nemotron 활용, 완성도, 실제 문제 해결 설득력, 아이디어 참신성).
- **상**: 종합 $20k/$10k/$6k, 트랙별 Jetson Orin Nano, Tavily 사용 보너스 $3k, **Builders & Brews 서울 행사 참가자 중 City Winner $500×20**, 피드백 $100×10.
- **크레딧**: 규칙에 프로모 코드는 없음. "Nebius Builder Program에 가입하면 Token Factory·Tavily·Academy 크레딧"이라고만 적혀 있음 → 코드가 아니라 **Builder Program 가입**이 크레딧 경로.

## 4. 시간표 (제안)
- 오늘(10-29): A1~A7. 끝나면 규칙 충족 상태.
- 내일 오전(10-30): B1 시도(2시간 상한, 안 되면 Gemini 유지). 이어서 저장소 정리·README·영상.
- 10-30 밤: 제출. 마감 10-31 02:00 KST.

## 5. 확인 못 한 것
- Nebius Builder Program 가입 절차·크레딧 액수(규칙엔 액수 없음).
- NVIDIA 음성 API의 function-id — 키가 있어야 확인됨.
- Nemotron의 한국어 품질: 10-29 1차 실측에서 Lightning이 더 자연스러움(3문항). 실제 기기 음성 턴은 미실측.

## 진행 상황 (10-29 15:45)
- A1~A4 완료(키 입력, 모델 ID 확인, .env 전환). A5 사용자가 15:17 재시작. A6 글 대화에서 빈 답 버그 발견·수정(runbook 10절) → **재시작 한 번 더 필요**, 그 뒤 A6 재확인·A7 기기 음성.
- Nebius 샘플 코드의 지역 주소 `api.tokenfactory.eu-north1.nebius.com`도 있음. 서버는 전역 주소 `api.tokenfactory.nebius.com`으로 정상 동작 중이라 그대로 둠.
- 16:0x 사용자 요청으로 답변 구조 확장: 고양이 '플로라', 분위기→꽃+색(white/red/yellow/purple) 구조화, 서버가 글에서 색 보충. runbook 10절. 다음 결정: 색을 로봇 쪽으로 보내는 통로 — (a) CoFlorist 외부 대화 SSE에 `flower` 필드 실어 보내고 포크에서 표시·배치 생성(PR) (b) 로봇 제어기에 직접 웹훅.

## 7. 깨우는 말 "안녕 플로라" (10-29 조사, 사용자 질문)
- **보드 안 엔진으로는 불가.** T5AI SDK의 로컬 깨우는 말은 벤더(Wanson/TUTUClear) 사전 빌드 라이브러리(`libtutuClear_wakeup_nihaotuya_xiaozhitongxue_heytuya_hituya_…_small_model.a`)이고 단어가 고정: 你好涂鸦, 小智同学, Hey Tuya, Hi Tuya, SmartLife(`tkl_kws.h`). 한국어·사용자 지정 단어 없음.
- **방법 A(권장, 이번 마감 안): 서버가 글로 알아듣기.** 기기는 "대기 듣기" 모드에서 이미 있는 RNN VAD로 말 조각을 잘라 서버에 보내고(`listen start` 메시지에 `mode:"wake"`), 서버는 STT만 돌려 "안녕 플로라"(플로라야/플로라 포함, 띄어쓰기·받침 흔들림 허용)가 들어 있으면 짧은 인사 TTS를 보내고 60초 대화 창을 연다. 아니면 조용히 버림(표정·소리 없음). 펌웨어: 루프에 대기 모드 추가(버튼은 그대로 유지), 2~3시간 + 빌드 환경 재생성 + 굽기. 서버: `mode:"wake"` 처리·문구 비교·인사 TTS, 1시간, 시험 포함. 단점: 말 끝나고 1초쯤 뒤에 반응, 대기 중 말소리 조각이 클라우드 STT로 감(VAD가 말일 때만), 비슷한 말에 오탐 가능.
- **방법 B(대안): 보드 내장 "Hey Tuya"로 깨우기.** 오프라인·즉시 반응이지만 이름이 다름.
- **방법 C(나중): 직접 학습한 소형 KWS 모델.** TTS로 합성한 "안녕 플로라" 표본으로 소형 모델을 학습해 보드의 NN 런타임에 올리기. 하루 이상, 위험 있음.
