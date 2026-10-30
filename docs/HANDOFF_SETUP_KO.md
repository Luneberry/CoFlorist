# CoFlorist 실행 및 외부 기기 연동 안내

## 1. 역할

담당자 프로그램은 음성 인식, 대화 모델 호출, 음성 재생과 실제 로봇·별도 디스플레이 제어를 담당합니다.

CoFlorist는 카메라 영상, 전달받은 대화 텍스트, 대화 상태에 따른 신경망 반응을 표시합니다. 실제 신경 계산은 Node.js Express → child_process → Python/Brian2로 실행합니다.

**담당자가 자기 기기와 API 키로 대화를 처리한다면 기존 사용자의 API 키를 전달할 필요가 없습니다.** 외부 대화 모드에서 대시보드는 Nebius API 키 없이 실행할 수 있습니다. Nebius API 키는 대화 모델 호출용이며, 음성 인식·합성 서비스는 사용하는 구현에 따라 별도로 준비합니다.

해커톤 조건에 맞춰 담당자 쪽 모델 실행에도 Nebius Token Factory 또는 Nebius AI Cloud와 NVIDIA 모델을 사용해야 합니다. 실제 사용한 서비스와 모델명을 제출 자료에 기록합니다.

## 2. 실행할 PC 준비

현재 검증 기준은 Windows입니다.

- Git
- Node.js 24 및 npm
- Python 3.12 및 Python Launcher(`py`)
- Chrome 또는 Edge
- 초기 패키지 설치 및 약 90MB의 뇌 데이터 다운로드를 위한 인터넷 연결
- 브라우저에서 사용할 수 있는 카메라 또는 영상 소스
- 대시보드를 표시할 디스플레이

대시보드와 음성 프로그램을 같은 PC에서 실행해도 됩니다. 서로 다른 PC에서 실행한다면 대시보드 PC가 담당자 프로그램의 SSE 주소에 접근할 수 있어야 합니다.

## 3. GitHub 업로드 전 확인

1. `package-lock.json`, `scripts/`, `python/`, `server/`, `public/`, `examples/`를 포함합니다.
2. `.env`, API 키, `.runtime/`의 개인 설정·대화 기록은 올리지 않습니다.
3. `.venv/`, `node_modules/`, `data/`는 현재 Git 제외 대상입니다. 클론한 PC에서 설치·생성합니다.
4. `public/assets/`도 현재 Git 제외 대상입니다. 카메라 연결 전 참고 사진을 제공하려면 공개 가능한 `public/assets/robot-workspace.png`를 포함하도록 `.gitignore`를 수정합니다. 제외한 채 업로드하면 해당 사진이 표시되지 않습니다.
5. 설치 스크립트의 기본 Python 경로는 원래 개발 PC 전용입니다. 아래 절차대로 `-PythonExecutable`을 명시합니다.

## 4. 클론 및 설치

PowerShell에서 실행합니다. `<저장소 주소>`와 `<프로젝트 폴더>`를 실제 값으로 바꿉니다.

```powershell
git clone <저장소 주소>
cd <프로젝트 폴더>
npm ci
```

Python 환경과 실제 FlyWire 부분회로 데이터를 준비합니다.

```powershell
$pythonPath = py -3.12 -c "import sys; print(sys.executable)"
powershell -File scripts/setup-brain.ps1 -PythonExecutable "$pythonPath"
```

설치 후 실제 신경 계산을 확인합니다.

```powershell
node python/test-brain-worker.mjs
```

대시보드를 실행합니다.

```powershell
npm start
```

실행한 PC에서 `http://localhost:4317`을 엽니다. 대시보드 서버는 기본적으로 로컬 PC에서만 접근할 수 있습니다. 다른 PC에서 이 대시보드 서버에 직접 접속하는 LAN 배포는 현재 기본 구성에 포함되어 있지 않습니다.

## 5. 담당자에게 받을 정보

| 항목 | 전달받을 내용 |
| --- | --- |
| 대화 스트림 주소 | 예: `http://192.168.0.20:4320/events` |
| 확정된 사용자 발화 | `role: user`와 텍스트 |
| 로봇의 대화 응답 | `role: assistant`와 텍스트 |
| 대화 상태 | `listening`, `thinking`, `speaking`, `idle`, `error` |
| 모델 실행 정보 | 실제 사용한 Nebius 서비스와 NVIDIA 모델명 |
| 영상 방식 | 대시보드에서 직접 카메라 사용 / LeLab 화면 공유 / 영상 URL |
| 디스플레이 종류 | 대시보드용 모니터인지, 별도 제어가 필요한 로봇 화면인지 |

대시보드에는 API 키나 오디오 파일을 보내지 않습니다. 담당자 프로그램이 웹브라우저만으로 구성되어 SSE 서버를 제공할 수 없다면, 텍스트를 중계할 서버가 추가로 필요합니다.

## 6. 외부 대화 전송 규격

담당자 서버가 SSE 스트림을 제공하고 CoFlorist 브라우저가 `EventSource`로 구독합니다. 대시보드로 POST 요청을 보내는 방식은 현재 구현되어 있지 않습니다.

응답 헤더:

```http
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache
Access-Control-Allow-Origin: http://localhost:4317
```

대시보드를 `http://127.0.0.1:4317`로 열었다면 그 Origin도 허용해야 합니다. SSE 데이터는 버퍼링하지 않고 이벤트 발생 시 전송합니다.

각 이벤트에는 고유한 문자열 `id`가 필요합니다. `message`는 확정된 한 문장/메시지 단위이며, 현재 수신기는 부분 텍스트를 이어 붙이거나 수정하는 스트리밍 토큰 방식은 지원하지 않습니다. 텍스트는 1~2,000자입니다.

전송 예시입니다. 각 이벤트 뒤의 빈 줄을 유지합니다. 별도의 `event:` 이름 없이 기본 SSE 메시지로 보냅니다.

```text
id: evt-1
data: {"id":"evt-1","type":"state","state":"listening"}

id: evt-2
data: {"id":"evt-2","type":"message","role":"user","text":"그 사람에게 꽃을 선물하고 싶어."}

id: evt-3
data: {"id":"evt-3","type":"state","state":"thinking"}

id: evt-4
data: {"id":"evt-4","type":"message","role":"assistant","text":"흰 꽃과 빨간 꽃을 추천해요."}

id: evt-5
data: {"id":"evt-5","type":"state","state":"speaking"}

id: evt-6
data: {"id":"evt-6","type":"state","state":"idle"}

```

- `listening`: 음성을 듣는 중
- `thinking`: 모델 응답 생성 중
- `speaking`: 상대 기기에서 음성 재생 중
- `idle`: 응답 재생 종료 또는 대화 대기
- `error`: 상대 프로그램 오류

네트워크 재접속 시에는 SSE `Last-Event-ID`를 활용해 누락된 이벤트를 다시 보내는 것이 좋습니다. 수신기는 최근 500개 이벤트 ID의 중복을 제거합니다. 최근 메시지 200개를 브라우저 메모리에 표시하며 외부 대화를 서버 기록으로 저장하지 않습니다.

45초 동안 새 이벤트가 없으면 대화 반응 표시가 대기로 돌아갑니다. 긴 생각·발화 구간에는 고유한 새 ID로 현재 상태를 30초 이내 간격으로 다시 보냅니다. `: keepalive` 주석은 연결 유지용이며 대화 상태 갱신을 대신하지 않습니다.

Node.js/Express 참고 구현은 `examples/conversation-stream.mjs`에 있습니다. 실제 음성·모델 프로그램에 `createConversationStream(app)`을 붙이고 반환된 `publish(event)`를 호출합니다.

예시 서버만 실행하려면:

```powershell
node examples/conversation-stream.mjs
```

가상 대화로 연결을 시험하려면:

```powershell
node examples/conversation-stream.mjs --demo
```

기본 주소는 `http://127.0.0.1:4320/events`입니다. `--demo`는 연결 확인용 예시 텍스트만 생성하며 실제 음성 인식·모델·음성 합성을 수행하지 않습니다.

다른 PC에서 구독해야 한다면 SSE 서버를 실행하는 PowerShell에서 `HOST`를 그 서버 PC의 실제 LAN IP로 설정합니다.

```powershell
$env:HOST = "192.168.0.20" # 실제 서버 PC의 LAN IP로 변경
$env:ALLOWED_ORIGIN = "http://localhost:4317"
node examples/conversation-stream.mjs
```

대시보드 PC에서 해당 IP와 포트에 접근 가능한 네트워크·방화벽 구성이 필요합니다. 예제 서버는 인증을 구현하지 않았으며, 접근을 제한한 시연 네트워크에서 사용합니다. `localhost`는 항상 해당 프로그램이 실행되는 자기 PC를 의미합니다.

## 7. 대시보드 연결 절차

1. `npm start`로 실행하고 `http://localhost:4317`을 엽니다.
2. 설정 → 초파리 신경망에서 경로를 확인하고 **저장하고 신경망 시작**을 누릅니다. 기본 경로는 클론한 프로젝트의 `.venv/Scripts/python.exe`와 `data/flywire/subset.json`입니다.
3. 담당자 음성·대화 프로그램과 SSE 서버를 실행합니다.
4. 설정 → 외부 대화 → 대화 스트림 주소에 SSE 주소를 입력합니다.
5. **외부 대화 연결**을 누르고 연결 상태를 확인합니다.
6. 메인에서 사용자·로봇 텍스트와 듣기·생각·말하기 상태를 확인합니다.
7. 외부 연결 중에는 대시보드의 대화 입력·음성 재생을 사용하지 않습니다. 상대 기기에서 음성을 처리합니다.
8. 새로고침하면 외부 연결과 표시 중인 외부 대화가 초기화됩니다. 주소를 다시 입력하고 연결합니다. 상대 서버의 재전송 지원 여부에 따라 이전 메시지를 다시 받을 수 있습니다.

## 8. 카메라와 디스플레이

1. USB 카메라를 대시보드를 여는 PC에 연결합니다.
2. 브라우저 카메라 권한을 허용합니다.
3. 설정 → 작업 공간에서 사용할 장치를 선택하고 연결합니다.
4. 담당자 프로그램이나 LeLab이 같은 카메라를 점유한다면 **LeLab 화면 공유** 또는 제공받은 영상 URL을 사용합니다. 동시 장치 열기는 카메라·드라이버에 따라 지원되지 않을 수 있습니다.
5. 영상 URL은 현재 지원하는 MJPEG/이미지 스트림 또는 브라우저에서 재생 가능한 영상 주소를 사용합니다. RTSP 주소를 직접 넣는 방식은 지원하지 않습니다.
6. 대시보드를 표시할 모니터로 브라우저 창을 옮기고 전체 화면으로 전환합니다.
7. 로봇 얼굴 화면 등 별도 디스플레이 하드웨어는 담당자 프로그램에서 제어합니다. 현재 CoFlorist에는 해당 장치 제어 드라이버가 없습니다.

## 9. 현재 구현 범위와 남은 연결

- 구현됨: 외부 대화 텍스트 수신, 상태 수신, 신경망 확대·애니메이션, 카메라 표시.
- 구현됨: 외부 상태가 `thinking`으로 바뀔 때 작업자가 비어 있으면 실제 FlyWire 부분회로 계산 1회 실행. 대화 상태 연동 애니메이션은 최근 계산 결과의 재생이며 매 프레임 새 계산을 하는 것은 아닙니다.
- 미구현: 외부 대화 텍스트에서 꽃 색·위치를 추출하여 작업 계획으로 자동 반영하는 연결.
- 미구현: 외부 프로그램의 로봇 실행 결과와 별도 디스플레이 제어 연결.
- 확인 필요: 상대 실제 기기의 SSE 주소·CORS 설정·음성 및 카메라와의 동시 실행.

외부 대화만 연결한 신규 세션에서는 꽃 배치 계획이 자동으로 만들어지지 않습니다. 전체 시연을 담당자 프로그램과 연동하려면 꽃 색·위치·작업 상태를 전달할 별도 작업 이벤트 규격과 수신 처리를 추가해야 합니다. 로봇의 실제 동작 및 전달 완료는 운영자가 확인합니다.

## 10. 최종 리허설 순서

1. 사용자 발화와 텍스트 표시 확인
2. 모델의 흰색·빨간색 추천과 음성 응답 확인
3. 생각·발화 중 신경망 반응 확인
4. 카메라 영상과 디스플레이 표시 확인
5. 추가 작업 이벤트 연동 후 꽃 배치 계획 확인
6. 수동 로봇 조종으로 배치·대기·재개 확인
7. 배치 승인 후 꽃다발 들어 올리기·건네기 수행
8. 실제 수령과 그리퍼 해제를 확인하고 완료 처리

로컬 예시 스트림의 텍스트·상태 수신과 실제 신경 계산은 확인했습니다. 상대 기기 연결 및 전체 물리 시연은 위 절차로 별도 검증해야 합니다.
