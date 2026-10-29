#include "cat_app.h"
#include "cat_recording.h"
#include "cat_vad.h"
#include "cat_bridge.h"
#include "tkl_thread.h"
#include "cat_ca.h"
#include "tal_wifi.h"
#include "tuya_tls.h"
#include "tls_transporter.h"
#include "http_client_interface.h"
#include "http_host.h"
#include "cJSON.h"
#include "opus.h"
#include "mbedtls/base64.h"
#include "mbedtls/sha1.h"
#include "mbedtls/ssl.h"
#include <string.h>
#include <strings.h>
#include <stdio.h>
#include <stdlib.h>

#include "cat_profile_private.h"
char cat_ota_url[512] = CAT_DEFAULT_OTA;
typedef struct {
    char     host[128];
    char     path[512];
    unsigned port;
} endpoint_t;
typedef struct {
    int16_t input[960];
    int16_t decoded[960];
    uint8_t packet[1275];
    int16_t preroll[CAT_PREROLL_FRAMES][960];
} codec_workspace_t;
/* Owned exclusively by the network task; no large PCM arrays on its stack. */
static codec_workspace_t *codec_work;
static THREAD_HANDLE      thread;
static tuya_transporter_t transport;
static char               device_id[24], session_id[128];
static uint8_t            rx[16384], fragmented[8192];
static size_t             rx_len, fragment_len;
static unsigned           fragment_opcode;
static OpusEncoder       *encoder;
static OpusDecoder       *decoder;
static bool               ready, busy, long_recording_supported;
static bool               tts_done;
static bool have_emotion;
static uint32_t           received_at, response_progress_at;
static int                parse_url(const char *url, const char *scheme, endpoint_t *out)
{
    size_t prefix = strlen(scheme);
    if (strncmp(url, scheme, prefix) || strpbrk(url, "\r\n\t "))
        return -1;
    const char *host = url + prefix, *path = strchr(host, '/');
    size_t      n = path ? (size_t)(path - host) : strlen(host);
    if (!n || n >= sizeof(out->host) || memchr(host, '@', n) || memchr(host, '[', n))
        return -1;
    memcpy(out->host, host, n);
    out->host[n] = 0;
    out->port    = 443;
    char *colon  = strchr(out->host, ':');
    if (colon) {
        *colon++ = 0;
        char *end;
        long  port = strtol(colon, &end, 10);
        if (*end || port < 1 || port > 65535)
            return -1;
        out->port = port;
    }
    if (!out->host[0] || strlen(path ? path : "/") >= sizeof(out->path))
        return -1;
    strcpy(out->path, path ? path : "/");
    return 0;
}
static const char *json_string(const cJSON *obj, const char *key)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(obj, key);
    return cJSON_IsString(v) ? v->valuestring : NULL;
}
static int write_all(const uint8_t *data, size_t length)
{
    while (length) {
        int n = tuya_transporter_write(transport, (uint8_t *)data, length, 3000);
        if (n <= 0 || n > (int)length)
            return -1;
        data += n;
        length -= n;
    }
    return 0;
}
static int send_frame(unsigned opcode, const uint8_t *data, size_t length)
{
    if (length > 8192)
        return -1;
    uint8_t *packet = tal_malloc(length + 8);
    if (!packet)
        return -1;
    size_t pos    = 0;
    packet[pos++] = 0x80 | opcode;
    if (length < 126) {
        packet[pos++] = 0x80 | length;
    } else {
        packet[pos++] = 0xfe;
        packet[pos++] = length >> 8;
        packet[pos++] = length;
    }
    uint8_t mask[4];
    if (tuya_tls_random(mask, sizeof(mask)) != OPRT_OK) {
        tal_free(packet);
        return -1;
    }
    memcpy(packet + pos, mask, 4);
    pos += 4;
    for (size_t i = 0; i < length; i++)
        packet[pos + i] = data[i] ^ mask[i % 4];
    int result = write_all(packet, pos + length);
    tal_free(packet);
    return result;
}
static int send_text(const char *text)
{
    return send_frame(1, (const uint8_t *)text, strlen(text));
}
static int ota_discover(endpoint_t *ws, char *token, size_t token_size)
{
    endpoint_t ota;
    if (parse_url(cat_ota_url, "https://", &ota))
        return -1;
    http_client_header_t headers[] = {{"Device-Id", device_id}, {"Content-Type", "application/json"}};
    const char body[] = "{\"application\":{\"version\":\"pusheen-cat-0.1.0\"},\"board\":{\"type\":\"tuya-t5ai\"}}";
    http_client_request_t  request  = {.host          = ota.host,
                                       .port          = ota.port,
                                       .path          = ota.path,
                                       .cacert        = (const uint8_t *)cat_ca_pem,
                                       .cacert_len    = sizeof(cat_ca_pem),
                                       .method        = "POST",
                                       .headers       = headers,
                                       .headers_count = 2,
                                       .body          = (const uint8_t *)body,
                                       .body_length   = sizeof(body) - 1,
                                       .timeout_ms    = 15000};
    http_client_response_t response = {0};
    int                    result   = -1;
    if (http_client_request(&request, &response) == HTTP_CLIENT_SUCCESS && response.status_code == 200 &&
        response.body_length < 8192) {
        cJSON      *json   = cJSON_ParseWithLength((const char *)response.body, response.body_length);
        cJSON      *socket = cJSON_GetObjectItemCaseSensitive(json, "websocket");
        const char *url = json_string(socket, "url"), *secret = json_string(socket, "token");
        if (url && secret && strlen(secret) < token_size && !strpbrk(secret, "\r\n") && !parse_url(url, "wss://", ws)) {
            strcpy(token, secret);
            result = 0;
        }
        cJSON_Delete(json);
    }
    http_client_free(&response);
    return result;
}
static int connect_ws(const endpoint_t *ws, const char *token)
{
    transport = tuya_tls_transporter_create();
    if (!transport)
        return -1;
    tuya_tls_config_t config = {.mode         = TUYA_TLS_SERVER_CERT_MODE,
                                .verify       = true,
                                .ca_cert      = (char *)cat_ca_pem,
                                .ca_cert_size = sizeof(cat_ca_pem),
                                .timeout      = 10000};
    if (tuya_transporter_ctrl(transport, TUYA_TRANSPORTER_SET_TLS_CONFIG, &config) != OPRT_OK ||
        tuya_transporter_connect(transport, ws->host, ws->port, 10000) != OPRT_OK)
        return -1;
    uint8_t nonce[16], key[32], digest[20], expected[32];
    size_t  keylen, expectlen;
    if (tuya_tls_random(nonce, sizeof(nonce)) != OPRT_OK ||
        mbedtls_base64_encode(key, sizeof(key), &keylen, nonce, sizeof(nonce)))
        return -1;
    key[keylen] = 0;
    char material[80];
    snprintf(material, sizeof(material), "%s258EAFA5-E914-47DA-95CA-C5AB0DC85B11", key);
    if (mbedtls_sha1((uint8_t *)material, strlen(material), digest) ||
        mbedtls_base64_encode(expected, sizeof(expected), &expectlen, digest, sizeof(digest)))
        return -1;
    expected[expectlen] = 0;
    char *header        = tal_malloc(3072);
    if (!header)
        return -1;
    int n = snprintf(
        header, 3072,
        "GET %s HTTP/1.1\r\nHost: %s:%u\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: "
        "%s\r\nSec-WebSocket-Version: 13\r\nAuthorization: Bearer %s\r\nDevice-Id: %s\r\nProtocol-Version: 1\r\n\r\n",
        ws->path, ws->host, ws->port, key, token, device_id);
    if (n <= 0 || n >= 3072 || write_all((uint8_t *)header, n)) {
        tal_free(header);
        return -1;
    }
    size_t   used  = 0;
    uint32_t start = tal_system_get_millisecond();
    while (used < 3071 && (uint32_t)(tal_system_get_millisecond() - start) < 10000) {
        int count = tuya_transporter_read(transport, (uint8_t *)header + used, 1, 1000);
        if (count == OPRT_RESOURCE_NOT_READY)
            continue;
        if (count != 1) {
            tal_free(header);
            return -1;
        }
        used++;
        header[used] = 0;
        if (used >= 4 && !memcmp(header + used - 4, "\r\n\r\n", 4))
            break;
    }
    char accept[80], upgrade[32], connection[80];
    int  valid = used >= 4 && !memcmp(header + used - 4, "\r\n\r\n", 4) && !strncmp(header, "HTTP/1.1 101 ", 13) &&
                 http_host_get_header_value(header, used, "Sec-WebSocket-Accept", accept, sizeof(accept)) == OPRT_OK &&
                 !strcmp(accept, (char *)expected) &&
                 http_host_get_header_value(header, used, "Upgrade", upgrade, sizeof(upgrade)) == OPRT_OK &&
                 !strcasecmp(upgrade, "websocket") &&
                 http_host_get_header_value(header, used, "Connection", connection, sizeof(connection)) == OPRT_OK &&
                 !strcasecmp(connection, "Upgrade");
    tal_free(header);
    if (!valid)
        return -1;
    rx_len = fragment_len = fragment_opcode = 0;
    ready = busy = tts_done = have_emotion = long_recording_supported = false;
    session_id[0]           = 0;
    received_at             = tal_system_get_millisecond();
    return send_text("{\"type\":\"hello\",\"version\":1,\"transport\":\"websocket\",\"features\":{\"listen_60s\":true},\"audio_params\":{"
                     "\"format\":\"opus\",\"sample_rate\":16000,\"channels\":1,\"frame_duration\":60}}");
}
static int handle_message(unsigned opcode, const uint8_t *data, size_t length)
{
    if (opcode == 2) {
        response_progress_at = tal_system_get_millisecond();
        int     count = opus_decode(decoder, data, length, codec_work->decoded, 960, 0);
        if (count < 0)
            return -1;
        cat_bridge_audio_packet(false, data, length);
        cat_voice_play(codec_work->decoded, count);
        return 0;
    }
    if (opcode != 1)
        return -1;
    cJSON *json = cJSON_ParseWithLength((const char *)data, length);
    if (!json)
        return -1;
    const char *type = json_string(json, "type");
    if (type && !strcmp(type, "hello")) {
        const char *id = json_string(json, "session_id");
        if (!id || strlen(id) >= sizeof(session_id)) {
            cJSON_Delete(json);
            return -1;
        }
        strcpy(session_id, id);
        ready = true;
        const cJSON *limit = cJSON_GetObjectItemCaseSensitive(json, "max_listen_ms");
        long_recording_supported = cJSON_IsNumber(limit) && limit->valuedouble >= CAT_RECORD_MS;
        (void)cat_button_take_press();
        cat_face(CAT_NEUTRAL);
        cat_status(long_recording_supported ? "PRESS TO CHAT 60s" : "SERVER UPDATE REQUIRED");
        PR_WARN("Cat voice ready record_60s=%u", (unsigned)long_recording_supported);
        cat_bridge_state("idle");
    } else if (type && !strcmp(type, "stt")) {
        cat_bridge_message("user", json_string(json, "text"));
    } else if (type && !strcmp(type, "llm")) {
        have_emotion = json_string(json, "emotion") != NULL;
        cat_face_name(json_string(json, "emotion"));
    } else if (type && !strcmp(type, "tts")) {
        const char *state = json_string(json, "state");
        if (state && !strcmp(state, "start")) {
            response_progress_at = tal_system_get_millisecond();
            busy     = true;
            tts_done = false;
            if (!have_emotion) cat_face(CAT_SPEAKING);
            cat_status("SPEAKING");
            cat_bridge_state("speaking");
            PR_WARN("Cat speech response started");
        } else if (state && !strcmp(state, "stop")) {
            cat_bridge_audio_end(false);
            tts_done = true;
        } else if (state && !strcmp(state, "sentence_start")) {
            cat_bridge_message("assistant", json_string(json, "text"));
        }
    }
    cJSON_Delete(json);
    return 0;
}
static int receive_ws(void)
{
    if (rx_len == sizeof(rx))
        return -1;
    int n = tuya_transporter_read(transport, rx + rx_len, sizeof(rx) - rx_len, 10);
    if (n == OPRT_RESOURCE_NOT_READY || n == MBEDTLS_ERR_SSL_WANT_READ || n == MBEDTLS_ERR_SSL_TIMEOUT)
        return 0;
    if (n <= 0)
        return -1;
    rx_len += n;
    received_at     = tal_system_get_millisecond();
    size_t consumed = 0;
    while (rx_len - consumed >= 2) {
        uint8_t *p      = rx + consumed;
        unsigned opcode = p[0] & 15;
        bool     final  = (p[0] & 0x80) != 0;
        size_t   header = 2, length = p[1] & 127;
        if ((p[0] & 0x70) || (p[1] & 0x80))
            return -1;
        if (length == 127)
            return -1;
        if (length == 126) {
            if (rx_len - consumed < 4)
                break;
            length = ((size_t)p[2] << 8) | p[3];
            header = 4;
            if (length < 126)
                return -1;
        }
        if (length > 8191)
            return -1;
        if (rx_len - consumed < header + length)
            break;
        uint8_t *body = p + header;
        if (opcode >= 8) {
            if (!final || length > 125)
                return -1;
            if (opcode == 8) {
                send_frame(8, body, length);
                return -1;
            }
            if (opcode == 9) {
                if (send_frame(10, body, length))
                    return -1;
            } else if (opcode != 10)
                return -1;
        } else if (opcode == 0) {
            if (!fragment_opcode || fragment_len + length > sizeof(fragmented))
                return -1;
            memcpy(fragmented + fragment_len, body, length);
            fragment_len += length;
            if (final) {
                if (handle_message(fragment_opcode, fragmented, fragment_len))
                    return -1;
                fragment_len = fragment_opcode = 0;
            }
        } else if (opcode == 1 || opcode == 2) {
            if (fragment_opcode)
                return -1;
            if (final) {
                if (handle_message(opcode, body, length))
                    return -1;
            } else {
                fragment_opcode = opcode;
                fragment_len    = length;
                memcpy(fragmented, body, length);
            }
        } else {
            return -1;
        }
        consumed += header + length;
    }
    if (consumed) {
        memmove(rx, rx + consumed, rx_len - consumed);
        rx_len -= consumed;
    }
    return 0;
}
static int listen_message(const char *state)
{
    cJSON *j = cJSON_CreateObject();
    if (!j)
        return -1;
    cJSON_AddStringToObject(j, "type", "listen");
    cJSON_AddStringToObject(j, "state", state);
    cJSON_AddStringToObject(j, "mode", "manual");
    cJSON_AddStringToObject(j, "session_id", session_id);
    char *text = cJSON_PrintUnformatted(j);
    int   ret  = text ? send_text(text) : -1;
    cJSON_free(text);
    cJSON_Delete(j);
    return ret;
}
static bool wifi_connected(void)
{
    WF_STATION_STAT_E s = WSS_IDLE;
    return tal_wifi_station_get_status(&s) == OPRT_OK && s == WSS_GOT_IP;
}
static int send_microphone_frame(const int16_t *pcm, unsigned *sent_frames)
{
    if (*sent_frames >= CAT_TURN_MAX_FRAMES)
        return 0;
    int n = opus_encode(encoder, pcm, 960, codec_work->packet, sizeof(codec_work->packet));
    if (n <= 0 || send_frame(2, codec_work->packet, n))
        return -1;
    cat_bridge_audio_packet(true, codec_work->packet, n);
    (*sent_frames)++;
    return 0;
}
static void network_task(void *arg)
{
    (void)arg;
    int      error;
    uint32_t attempts = 0;
    codec_work = tal_psram_calloc(1, sizeof(*codec_work));
    if (!codec_work) { cat_status("CODEC MEMORY ERROR"); return; }
    encoder           = opus_encoder_create(16000, 1, OPUS_APPLICATION_VOIP, &error);
    if (!encoder) {
        cat_status("ENCODER INIT FAILED");
        return;
    }
    decoder = opus_decoder_create(16000, 1, &error);
    if (!decoder) {
        cat_status("DECODER INIT FAILED");
        return;
    }
    opus_encoder_ctl(encoder, OPUS_SET_COMPLEXITY(0));
    opus_encoder_ctl(encoder, OPUS_SET_BITRATE(24000));
    opus_encoder_ctl(encoder, OPUS_SET_SIGNAL(OPUS_SIGNAL_VOICE));
    /* Exercise silence, voiced-like and noise frames locally, never sent to server. */
    uint32_t noise=1;
    unsigned tested=0;
    for (unsigned frame=0; frame<24; frame++) {
        for (unsigned i=0;i<960;i++) {
            noise=noise*1664525u+1013904223u;
            int sample = frame<8 ? 0 : frame<16 ? ((int)(i%80)-40)*300 : (int)((noise>>16)&32767)-16384;
            codec_work->input[i]=(int16_t)sample;
        }
        int n=opus_encode(encoder,codec_work->input,960,codec_work->packet,sizeof(codec_work->packet));
        if (n<=0 || opus_decode(decoder,codec_work->packet,n,codec_work->decoded,960,0)!=960) break;
        tested++;
    }
    uint32_t free_stack=0;
    tkl_thread_get_watermark(NULL,&free_stack);
    PR_WARN("Cat codec selftest passed=%u total=24 stack_free_bytes=%u",tested,(unsigned)free_stack);
    if (tested!=24 || free_stack<8192) {
        cat_status("CODEC SELFTEST FAILED");
        return;
    }
    opus_encoder_ctl(encoder,OPUS_RESET_STATE);
    opus_decoder_ctl(decoder,OPUS_RESET_STATE);
    if (cat_vad_init()) {
        cat_status("VOICE DETECTOR ERROR");
        return;
    }
    memset(codec_work->input, 0, sizeof(codec_work->input));
    unsigned silent_frames = 0;
    for (unsigned i = 0; i < 24; i++)
        if (!cat_vad_speech(codec_work->input)) silent_frames++;
    tkl_thread_get_watermark(NULL, &free_stack);
    PR_WARN("Cat VAD selftest silence=%u total=24 stack_free_bytes=%u", silent_frames, (unsigned)free_stack);
    if (silent_frames != 24 || free_stack < 8192) {
        cat_status("VOICE DETECTOR TEST FAILED");
        return;
    }
    cat_vad_reset();
    if (cat_bridge_init()) {
        cat_status("USB BRIDGE INIT FAILED");
        return;
    }
    cat_bridge_state("idle");

    NW_MAC_S mac;
    if (tal_wifi_get_mac(WF_STATION, &mac) != OPRT_OK)
        return;
    snprintf(device_id, sizeof(device_id), "%02x:%02x:%02x:%02x:%02x:%02x", mac.mac[0], mac.mac[1], mac.mac[2],
             mac.mac[3], mac.mac[4], mac.mac[5]);
    for (;;) {
        uint32_t offline_since = 0;
        while (!wifi_connected() || !cat_is_provisioned()) {
            if (cat_is_provisioned() && !offline_since) {
                offline_since = tal_system_get_millisecond();
            }
            if (offline_since && (uint32_t)(tal_system_get_millisecond() - offline_since) > 60000) {
                cat_network_failed();
            }
            tal_system_sleep(500);
        }
        cat_status("CONNECTING TO SERVER");
        endpoint_t endpoint;
        char       token[512];
        int        result = ota_discover(&endpoint, token, sizeof(token));
        if (!result)
            result = connect_ws(&endpoint, token);
        memset(token, 0, sizeof(token));
        bool conversation = false, listening = false, talking = false;
        (void)cat_button_take_press();
        unsigned sent_frames = 0, shown_seconds = 0, pre_count = 0, pre_next = 0, turns = 0;
        unsigned capture_frames = 0, vad_max_ms = 0;
        uint32_t started = 0, utterance_at = 0, cooldown_at = 0;
        uint32_t ping_at = tal_system_get_millisecond(), hello_at = ping_at;
        if (!result) {
            opus_decoder_ctl(decoder, OPUS_RESET_STATE);
            /* Retain failure count until the connection has been stable. */
        }
        while (!result && wifi_connected()) {
            uint32_t now  = tal_system_get_millisecond();
            bool     pressed = cat_button_take_press();
            if (ready && long_recording_supported && pressed && !conversation && !busy && !cat_voice_playing()) {
                cat_voice_stop();
                tts_done = false;
                have_emotion = false;
                conversation = true;
                started = tal_system_get_millisecond();
                now = started;
                cooldown_at = now - CAT_REPLY_COOLDOWN_MS;
                turns = 0;
                PR_WARN("Cat conversation started window_ms=%u", CAT_RECORD_MS);
            }
            unsigned seconds = cat_record_seconds_left(now, started);
            if (conversation && !seconds) {
                conversation = false;
                PR_WARN("Cat conversation window ended turns=%u", turns);
                if (!listening && !busy) {
                    cat_status("PRESS TO CHAT 60s");
                    cat_face(CAT_NEUTRAL);
                    cat_bridge_state("idle");
                }
            }
            if (conversation && !listening && !busy && !cat_voice_playing() &&
                (uint32_t)(now - cooldown_at) >= CAT_REPLY_COOLDOWN_MS) {
                cat_vad_reset();
                cat_voice_record_for(CAT_RECORD_MS - (uint32_t)(now - started));
                listening = true;
                talking = false;
                pre_count = pre_next = sent_frames = capture_frames = vad_max_ms = 0;
                shown_seconds = 0;
                cat_face(CAT_LISTENING);
                cat_bridge_state("listening");
                PR_WARN("Cat listening armed remaining_ms=%u", CAT_RECORD_MS - (unsigned)(now - started));
            }
            if (listening) {
                bool stopping = !conversation || !cat_voice_recording();
                if (stopping)
                    cat_voice_record(false);
                if (seconds != shown_seconds) {
                    char status[40];
                    snprintf(status, sizeof(status), "LISTENING %us", seconds);
                    cat_status(status);
                    shown_seconds = seconds;
                }
                int16_t *pcm = codec_work->input;
                for (int count = 0; (stopping || count < 20) && cat_voice_take(pcm); count++) {
                    capture_frames++;
                    if (!talking && !stopping) {
                        memcpy(codec_work->preroll[pre_next], pcm, sizeof(codec_work->input));
                        pre_next = (pre_next + 1) % CAT_PREROLL_FRAMES;
                        if (pre_count < CAT_PREROLL_FRAMES) pre_count++;
                        uint32_t vad_at = tal_system_get_millisecond();
                        bool speech = cat_vad_speech(pcm);
                        unsigned vad_ms = tal_system_get_millisecond() - vad_at;
                        if (vad_ms > vad_max_ms) vad_max_ms = vad_ms;
                        if (speech) {
                            opus_encoder_ctl(encoder, OPUS_RESET_STATE);
                            cat_bridge_new_turn();
                            cat_bridge_state("listening");
                            result = listen_message("start");
                            if (result) break;
                            talking = true;
                            have_emotion = false;
                            utterance_at = tal_system_get_millisecond();
                            for (unsigned i = 0; i < pre_count && !result; i++) {
                                unsigned index = (pre_next + CAT_PREROLL_FRAMES - pre_count + i) % CAT_PREROLL_FRAMES;
                                result = send_microphone_frame(codec_work->preroll[index], &sent_frames);
                            }
                            PR_WARN("Cat utterance started preroll_frames=%u", pre_count);
                        }
                    } else if (talking) {
                        result = send_microphone_frame(pcm, &sent_frames);
                        if (result) break;
                        if (!stopping) {
                            uint32_t vad_at = tal_system_get_millisecond();
                            bool speech = cat_vad_speech(pcm);
                            unsigned vad_ms = tal_system_get_millisecond() - vad_at;
                            if (vad_ms > vad_max_ms) vad_max_ms = vad_ms;
                            if (!speech || sent_frames >= CAT_TURN_MAX_FRAMES ||
                                (uint32_t)(tal_system_get_millisecond() - utterance_at) >= 29000) {
                                cat_voice_record(false);
                                stopping = true;
                            }
                        }
                    }
                    if (result) break;
                    if (!stopping && (uint32_t)(tal_system_get_millisecond() - started) >= CAT_RECORD_MS) {
                        cat_voice_record(false);
                        stopping = true;
                        conversation = false;
                    }
                }
                if (!result && stopping) {
                    listening = false;
                    uint32_t free_stack = 0;
                    tkl_thread_get_watermark(NULL, &free_stack);
                    PR_WARN("Cat capture stopped frames=%u sent=%u dropped=%u vad_max_ms=%u stack_free_bytes=%u",
                            capture_frames, sent_frames, cat_voice_dropped_frames(), vad_max_ms, (unsigned)free_stack);
                    if (talking) {
                        cat_bridge_audio_end(true);
                        result = listen_message("stop");
                        talking = false;
                        busy = true;
                        response_progress_at = tal_system_get_millisecond();
                        turns++;
                        cat_face(CAT_THINKING);
                        cat_status("THINKING");
                        cat_bridge_state("thinking");
                        PR_WARN("Cat utterance sent turn=%u input_ms=%u", turns, sent_frames * 60);
                        PR_WARN("Cat USB audio_dropped_events=%u", cat_bridge_audio_drops());
                    } else if (!conversation) {
                        cat_status("PRESS TO CHAT 60s");
                        cat_face(CAT_NEUTRAL);
                        cat_bridge_state("idle");
                    }
                }
            }
            if (result)
                break;
            result = receive_ws();
            now = tal_system_get_millisecond();
            if (tts_done && !cat_voice_playing()) {
                tts_done = false;
                busy     = false;
                (void)cat_button_take_press();
                cooldown_at = now;
                cat_bridge_state("idle");
                if (conversation && cat_record_seconds_left(now, started))
                    cat_status("LISTENING SOON");
                else {
                    conversation = false;
                    cat_status("PRESS TO CHAT 60s");
                }
                PR_WARN("Cat voice ready");
                if (!have_emotion) cat_face(CAT_NEUTRAL);
            }
            if (ready && (uint32_t)(now - hello_at) > 60000) {
                attempts = 0;
            }
            if ((uint32_t)(now - ping_at) >= 20000) {
                if (send_frame(9, (const uint8_t *)"cat", 3))
                    result = -1;
                ping_at = now;
            }
            /* Pongs alone must not keep a lost response stuck in THINKING forever. */
            if (busy && (uint32_t)(now - response_progress_at) > 120000 && !cat_voice_playing()) {
                PR_WARN("Cat response timeout");
                result = -1;
            }
            if ((!ready && (uint32_t)(now - hello_at) > 15000) ||
                (uint32_t)(now - received_at) > (busy ? 120000u : 60000u))
                result = -1;
        }
        cat_voice_record(false);
        cat_voice_stop();
        cat_bridge_audio_end(true);
        cat_bridge_audio_end(false);
        cat_bridge_state("error");
        ready = false;
        if (transport) {
            tuya_transporter_close(transport);
            tuya_tls_transporter_destroy(transport);
            transport = NULL;
        }
        attempts++;
        PR_WARN("Server reconnect attempt %u", (unsigned)attempts);
        cat_status("RECONNECTING");
        cat_face(CAT_SAD);
        if (attempts >= 5) {
            cat_network_failed();
            attempts = 0;
        }
        tal_system_sleep(attempts < 3 ? 2000 : 5000);
    }
}
void cat_network_start(void)
{
    tal_time_service_init();
    if (tal_time_get_posix() < CAT_BUILD_TIME)
        tal_time_set_posix(CAT_BUILD_TIME, 0);
    tuya_tls_init();
    THREAD_CFG_T cfg = {.stackDepth = 65536, .priority = THREAD_PRIO_1, .thrdname = "cat_network"};
    if (tal_thread_create_and_start(&thread, NULL, NULL, network_task, NULL, &cfg) != OPRT_OK) {
        PR_ERR("Cat network task allocation failed");
        cat_status("NETWORK MEMORY ERROR");
    }
}
