#include "cat_app.h"
#include "cat_bridge.h"
#include "cJSON.h"
#include "mbedtls/base64.h"
#include "tuya_tls.h"
#include <stdio.h>
#include <string.h>

typedef struct {
    char *json;
    char id[32];
} bridge_item_t;
static QUEUE_HANDLE controls, audio_events;
static THREAD_HANDLE bridge_thread;
static char boot_id[9], turn_id[32];
static unsigned event_sequence, turn_sequence, audio_sequence[2], audio_drops;
static bool audio_open[2], enabled;

/* All producers run on the network task. The UART writer owns queued strings. */
static cJSON *event_create(const char *type)
{
    cJSON *event = cJSON_CreateObject();
    if (!event) return NULL;
    char id[32];
    snprintf(id, sizeof(id), "%s-%u", boot_id, ++event_sequence);
    cJSON_AddStringToObject(event, "id", id);
    cJSON_AddStringToObject(event, "type", type);
    cJSON_AddStringToObject(event, "turn_id", turn_id);
    return event;
}

static void event_queue(cJSON *event, bool audio)
{
    if (!event) return;
    const cJSON *id = cJSON_GetObjectItemCaseSensitive(event, "id");
    bridge_item_t item = {0};
    if (enabled && cJSON_IsString(id)) {
        snprintf(item.id, sizeof(item.id), "%s", id->valuestring);
        item.json = cJSON_PrintUnformatted(event);
    }
    cJSON_Delete(event);
    if (!item.json) return;
    if (strlen(item.json) > 8192 ||
        tal_queue_post(audio ? audio_events : controls, &item, 0) != OPRT_OK) {
        cJSON_free(item.json);
        if (audio) audio_drops++;
    }
}

static void write_event(const bridge_item_t *item)
{
    size_t length = strlen(item->json);
    unsigned total = (length + 71) / 72;
    for (unsigned part = 0; part < total; part++) {
        size_t count = length - part * 72;
        if (count > 72) count = 72;
        unsigned char encoded[97];
        size_t used = 0;
        if (mbedtls_base64_encode(encoded, sizeof(encoded), &used,
                                  (const unsigned char *)item->json + part * 72, count)) return;
        encoded[used] = 0;
        /* No raw text or '%' reaches the SDK's second printf formatting pass. */
        tal_log_print_raw("CAT_EVT {\"v\":1,\"id\":\"%s\",\"part\":%u,\"total\":%u,\"data\":\"%s\"}\r\n",
                          item->id, part, total, encoded);
    }
}

static void bridge_task(void *arg)
{
    (void)arg;
    for (;;) {
        bridge_item_t item;
        if (tal_queue_fetch(controls, &item, 0) != OPRT_OK &&
            tal_queue_fetch(audio_events, &item, 20) != OPRT_OK) continue;
        write_event(&item);
        cJSON_free(item.json);
    }
}

int cat_bridge_init(void)
{
    uint8_t random[4];
    if (tuya_tls_random(random, sizeof(random)) != OPRT_OK) return -1;
    snprintf(boot_id, sizeof(boot_id), "%02x%02x%02x%02x", random[0], random[1], random[2], random[3]);
    snprintf(turn_id, sizeof(turn_id), "%s-0", boot_id);
    if (tal_queue_create_init(&controls, sizeof(bridge_item_t), 16) != OPRT_OK ||
        tal_queue_create_init(&audio_events, sizeof(bridge_item_t), 64) != OPRT_OK) return -1;
    THREAD_CFG_T cfg = {.stackDepth = 8192, .priority = THREAD_PRIO_3, .thrdname = "cat_usb"};
    if (tal_thread_create_and_start(&bridge_thread, NULL, NULL, bridge_task, NULL, &cfg) != OPRT_OK) return -1;
    enabled = true;
    PR_WARN("Cat USB bridge ready baud=460800 fragment_bytes=72");
    return 0;
}

void cat_bridge_new_turn(void)
{
    cat_bridge_audio_end(true);
    cat_bridge_audio_end(false);
    snprintf(turn_id, sizeof(turn_id), "%s-t%u", boot_id, ++turn_sequence);
}

void cat_bridge_state(const char *state)
{
    cJSON *event = event_create("state");
    if (event) cJSON_AddStringToObject(event, "state", state);
    event_queue(event, false);
}

void cat_bridge_message(const char *role, const char *text)
{
    if (!text || !*text) return;
    cJSON *event = event_create("message");
    if (event) {
        cJSON_AddStringToObject(event, "role", role);
        cJSON_AddStringToObject(event, "text", text);
    }
    event_queue(event, false);
}

static cJSON *audio_event(const char *type, bool input)
{
    cJSON *event = event_create(type);
    char stream[40];
    snprintf(stream, sizeof(stream), "%s-%c", turn_id, input ? 'u' : 'a');
    if (event) {
        cJSON_AddStringToObject(event, "stream_id", stream);
        cJSON_AddStringToObject(event, "role", input ? "user" : "assistant");
    }
    return event;
}

void cat_bridge_audio_packet(bool input, const uint8_t *packet, size_t size)
{
    unsigned index = input ? 0 : 1;
    if (!enabled || !packet || !size || size > 1275) return;
    if (!audio_open[index]) {
        cJSON *start = audio_event("audio_start", input);
        if (start) {
            cJSON_AddNumberToObject(start, "sample_rate", input ? 16000 : 24000);
            cJSON_AddNumberToObject(start, "channels", 1);
            cJSON_AddNumberToObject(start, "frame_ms", 60);
        }
        event_queue(start, true);
        audio_sequence[index] = 0;
        audio_open[index] = true;
    }
    unsigned char *encoded = tal_malloc(1701);
    if (!encoded) { audio_sequence[index]++; audio_drops++; return; }
    size_t used = 0;
    if (mbedtls_base64_encode(encoded, 1701, &used, packet, size)) {
        tal_free(encoded); audio_sequence[index]++; audio_drops++; return;
    }
    encoded[used] = 0;
    cJSON *event = audio_event("audio_chunk", input);
    if (event) {
        cJSON_AddNumberToObject(event, "seq", audio_sequence[index]);
        cJSON_AddStringToObject(event, "opus", (const char *)encoded);
    }
    audio_sequence[index]++;
    tal_free(encoded);
    event_queue(event, true);
}

void cat_bridge_audio_end(bool input)
{
    unsigned index = input ? 0 : 1;
    if (!audio_open[index]) return;
    cJSON *event = audio_event("audio_end", input);
    if (event) cJSON_AddNumberToObject(event, "frames", audio_sequence[index]);
    event_queue(event, true);
    audio_open[index] = false;
}

unsigned cat_bridge_audio_drops(void)
{
    return audio_drops;
}
