# External conversation feed — contract for a pluggable conversation server

CoFlorist does not contain a conversation server. Any server that owns the device dialogue (speech in, model reply, speech out) can drive the dashboard by exposing **one Server-Sent Events (SSE) endpoint** that follows this contract. The dashboard only displays events and runs the fruit-fly circuit; it never sends audio, prompts or keys to the server, and never commands the robot.

Reference implementation: the Flora DUO server (Nemotron on Nebius Token Factory). It is kept in a separate repository so that this slot can be replaced by any Nebius-based server.

## Endpoint

- `GET <your-feed-url>` → `text/event-stream`. The URL is entered in *Settings → External conversation*. Keep secrets (if any) inside the URL path; the dashboard stores it in browser memory only.
- CORS: the browser sends `Origin: http://localhost:4317` (or `http://127.0.0.1:4317`). The server must answer with `Access-Control-Allow-Origin` echoing that origin (and may reject others with 403).
- Reconnect: `EventSource` resends the `Last-Event-ID` header; the server should resend every event after that id (or everything it still holds if the id is unknown). **Ids must be stable** — the dashboard de-duplicates by id and will not run the circuit twice for the same event.
- Keep-alive: send a comment line (`: keepalive`) at least every 15–30 s so proxies keep the connection.
- Recommended for checks: `?once=1` returns the retained history and closes.

## Wire format

No `event:` line (default `message` event). Each event is:

```
id: <stable id, 1–160 chars [A-Za-z0-9_-]>
data: <one JSON object on one line>
<blank line>
```

## Event types

```json
{"type": "state", "state": "listening", "id": "…"}
{"type": "message", "role": "user", "text": "동생이 대학에 붙었어! 축하해 주고 싶어.", "id": "…"}
{"type": "message", "role": "assistant", "text": "대학 합격 정말 축하드려요! 기쁨을 나누는 노란 튤립을 추천해요.",
 "flower": "튤립", "flower_color": "yellow", "emotion": "happy", "stimulus_hz": 120, "id": "…"}
{"type": "message", "role": "assistant", "text": "저는 꽃집 고양이 플로라예요. 오늘 기분이 어때요?",
 "flower": "", "flower_color": "", "emotion": "happy", "stimulus_hz": 20, "id": "…"}
```

| Field | Required | Values |
|---|---|---|
| `type` | yes | `state` or `message` |
| `state` | for `state` | `idle` · `listening` · `thinking` · `speaking` · `error` |
| `role` | for `message` | `user` · `assistant` |
| `text` | for `message` | 1–2000 characters |
| `flower` | assistant, optional | flower name or `""` |
| `flower_color` | assistant, optional | `white` · `red` · `yellow` · `purple` · `""`. An unsupported value is treated as `""`: the reply and circuit run remain visible, but no flower placement is queued. |
| `emotion` | assistant, optional | `neutral` · `happy` · `sad` · `angry` · `surprised` · `loving` · `winking` · `sleepy` · `confused` |
| `stimulus_hz` | assistant, optional | integer 0–250. **Only assistant messages that carry `stimulus_hz` trigger a circuit run.** A non-empty supported `flower_color` queues one pending placement for the operator. |
| `at` | optional | ISO-8601 time. The current dashboard displays the event's receipt time instead. |

Order per turn (recommended): `listening` → user message → `thinking` → assistant message → `speaking` → `idle`; on failure end with `error`.

## How to choose `stimulus_hz` (application rule, not biology)

`stimulus_hz` is how strongly the fruit-fly sugar-sensing neurons are driven for that turn. The reference server uses: no flower → 20; `loving` 150; `happy` / `surprised` / `winking` 120; `sad` 60; `confused` / `sleepy` / `angry` 40; any other emotion with a flower → 90. The dashboard then shows WAIT (downstream < 1 Hz), OFFER (downstream ≥ 1 Hz) or REACH (MN9 ≥ 15 Hz) as a suggestion to the operator. Measured response curve: `docs/FLORA_BRAIN_SWEEP.md`.

## Try it without a server

`node examples/conversation-stream.mjs --flora` replays `examples/flora-feed-sample.json` (four turns: greeting → WAIT, consolation → OFFER, celebration → REACH, then an `error` state). Point *Settings → External conversation* at its `/events` URL.
