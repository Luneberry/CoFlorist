# Measurements behind the README claims (2026-10-29, Nebius Token Factory, from Seoul)

Method: the server's own provider class (`server/duo_server/providers/openai_chat.py`) called `https://api.tokenfactory.nebius.com/v1/chat/completions` with the production system prompt, `temperature 0.3`, `max_tokens 200`, `chat_template_kwargs.enable_thinking=false`, `response_format json_object`. Latency is wall-clock per call from the server process. Small samples; no load testing.

## Model comparison (same prompts, same endpoint)

| Prompt (Korean) | Nemotron 3 Nano-30B-A3B | Nemotron 3.5 Lightning |
|---|---|---|
| 안녕! 너는 누구야? | 안녕! 나는 듀오야. (1,750 ms) | 나는 듀오라고 해! … (1,236 ms) |
| 오늘 기분이 좀 안 좋아. 위로해 줄래? | 그럴 때엔 쉬어가보는 게 어때? (646 ms) | 아, 미안해 들어줘서 고마워. 무슨 일 있어? … (629 ms) |
| 고양이가 좋아하는 간식 하나만 추천해 줘. | 연어 스틱! (576 ms) | 고양이들이 가장 좋아하는 간식은 '츄르'야. … (577 ms) |

Both kept the JSON schema and valid emotion tags. Lightning was chosen for fuller, more natural Korean at equal latency and price. (This early run used the previous "Duo" persona; the florist persona below was added afterwards.)

## Florist persona, flower colour (Lightning)

7 mood prompts, two runs each, after the prompt gained three examples:

| Prompt | emotion | flower colour | latency |
|---|---|---|---|
| 안녕! 너 누구야? | happy | — (none, correct) | 1,404 ms (first call) |
| 오늘 면접을 망쳐서 너무 우울해. | sad | white (백합) | 707 ms |
| 여자친구한테 고백하려고 해. | loving | red (장미) | 621 ms |
| 동생이 대학에 붙었어! | happy | yellow (튤립) | 627 ms |
| 할머니 생신인데 차분하고 품위 있는 걸로. | neutral | purple (바이올렛) | 693 ms |
| 그냥 날씨 얘기나 하자. 오늘 덥다. | neutral | — (none, correct) | 651 ms |
| 엄마한테 고맙다고 전하고 싶어. | neutral | white (카네이션) | 563 ms |

Colour was correct in 6/6 prompts that call for a flower; small talk returned no flower. In a later live run the model recommended a flower during small talk after earlier flower turns; one more rule and a weather example in the prompt fixed it (verified twice on the same 4-turn sequence).

## Live end-to-end (device-less text turns through the running server and the dashboard)

3 turns + 1 retest on 2026-10-29: consolation → purple, 60 Hz stimulus, MN9 5 Hz → OFFER; celebration → yellow, 120 Hz, MN9 75 Hz → REACH; small talk (after the prompt fix) → no flower, 20 Hz, MN9 0 Hz → WAIT. Server latency per turn 0.7–1.3 s (first call after a restart up to 7.6 s once).

## Thinking on vs off

With the default (thinking on) the same request spent all 200 output tokens on a hidden reasoning transcript and returned no answer text (`finish_reason: length`). With `enable_thinking=false` the answer arrived in 18–22 completion tokens.

## Prices

Nebius Token Factory price page, read 2026-10-29: Nemotron 3.5 Lightning and Nemotron 3 Nano-30B-A3B $0.06 / $0.24 per 1M input / output tokens; 3.5 Super VL $0.30 / $0.90; 3 Ultra $1.00 / $3.00. Per-turn cost is an estimate from these list prices (~250 tokens per turn); no invoice is included.
