#include "cat_app.h"
#include "cat_recording.h"
#include "tdl_audio_manage.h"
#include "tdl_button_manage.h"
#include "board_com_api.h"
#include <string.h>
typedef struct {
    int16_t  samples[960];
    uint16_t count;
    uint32_t epoch;
} pcm_frame_t;
static TDL_AUDIO_HANDLE_T audio;
static QUEUE_HANDLE       mic_queue, play_queue;
static MUTEX_HANDLE       state_lock, play_lock;
static bool               recording, press_pending;
static uint32_t           recorded_samples;
static uint32_t           record_started, record_duration, record_sample_limit;
static unsigned           dropped_frames;
static pcm_frame_t        input;
static uint32_t           generation;
static THREAD_HANDLE      player;
static unsigned           pending_audio;
static uint32_t           audio_until;
/* SDK test hook: continuous PCM is needed to detect the end of speech. */
extern void tkl_ai_disable_vendor_vad(void);
static void flush_partial(void)
{
    if (!input.count)
        return;
    memset(input.samples + input.count, 0, (960 - input.count) * sizeof(int16_t));
    input.count = 960;
    if (tal_queue_post(mic_queue, &input, 0) != OPRT_OK)
        dropped_frames++;
    input.count = 0;
}
static void microphone(TDL_AUDIO_FRAME_FORMAT_E format, TDL_AUDIO_STATUS_E status, uint8_t *data, uint32_t length)
{
    (void)status;
    if (format != TDL_AUDIO_FRAME_FORMAT_PCM || !data)
        return;
    tal_mutex_lock(state_lock);
    if (recording && (uint32_t)(tal_system_get_millisecond() - record_started) >= record_duration) {
        flush_partial();
        recording = false;
    }
    if (recording) {
        for (uint32_t i = 0; i + 1 < length && recording; i += 2) {
            int16_t sample;
            memcpy(&sample, data + i, 2);
            input.samples[input.count++] = sample;
            recorded_samples++;
            if (input.count == 960) {
                if (tal_queue_post(mic_queue, &input, 0) != OPRT_OK) {
                    dropped_frames++;
                    PR_WARN("Microphone queue full");
                }
                input.count = 0;
            }
            if (recorded_samples >= record_sample_limit) {
                flush_partial();
                recording = false;
            }
        }
    }
    tal_mutex_unlock(state_lock);
}
static void record_set(bool active, uint32_t duration_ms)
{
    pcm_frame_t stale;
    tal_mutex_lock(state_lock);
    if (recording && !active)
        flush_partial();
    recording   = false;
    input.count = 0;
    if (active) {
        recorded_samples = 0;
        record_duration = duration_ms;
        record_sample_limit = duration_ms * 16u;
        record_started = tal_system_get_millisecond();
        dropped_frames = 0;
        while (tal_queue_fetch(mic_queue, &stale, 0) == OPRT_OK) {
        }
    }
    recording = active;
    tal_mutex_unlock(state_lock);
}
void cat_voice_record(bool active)
{
    record_set(active, CAT_RECORD_MS);
}
void cat_voice_record_for(uint32_t duration_ms)
{
    if (duration_ms > CAT_RECORD_MS)
        duration_ms = CAT_RECORD_MS;
    record_set(duration_ms != 0, duration_ms);
}
bool cat_voice_recording(void)
{
    tal_mutex_lock(state_lock);
    bool active = recording;
    tal_mutex_unlock(state_lock);
    return active;
}
unsigned cat_voice_dropped_frames(void)
{
    tal_mutex_lock(state_lock);
    unsigned dropped = dropped_frames;
    tal_mutex_unlock(state_lock);
    return dropped;
}
int cat_voice_take(int16_t *pcm)
{
    pcm_frame_t f;
    if (tal_queue_fetch(mic_queue, &f, 0) != OPRT_OK)
        return 0;
    memcpy(pcm, f.samples, 1920);
    return 960;
}
void cat_voice_play(const int16_t *pcm, int count)
{
    if (count <= 0 || count > 960)
        return;
    pcm_frame_t f;
    memcpy(f.samples, pcm, count * 2);
    f.count = count;
    tal_mutex_lock(state_lock);
    f.epoch = generation;
    pending_audio++;
    tal_mutex_unlock(state_lock);
    if (tal_queue_post(play_queue, &f, 1000) != OPRT_OK) {
        tal_mutex_lock(state_lock);
        pending_audio--;
        tal_mutex_unlock(state_lock);
        PR_WARN("Speaker queue full");
    }
}
void cat_voice_stop(void)
{
    pcm_frame_t f;
    tal_mutex_lock(play_lock);
    tal_mutex_lock(state_lock);
    generation++;
    pending_audio = 0;
    audio_until   = 0;
    tal_mutex_unlock(state_lock);
    while (tal_queue_fetch(play_queue, &f, 0) == OPRT_OK) {
    }
    tdl_audio_play_stop(audio);
    /* T5AI clear-buffer is a no-op; allow its 40 ms hardware FIFO to drain. */
    tal_system_sleep(60);
    tal_mutex_unlock(play_lock);
}
static void play_task(void *arg)
{
    (void)arg;
    pcm_frame_t f;
    for (;;) {
        if (tal_queue_fetch(play_queue, &f, QUEUE_WAIT_FOREVER) != OPRT_OK)
            continue;
        tal_mutex_lock(play_lock);
        tal_mutex_lock(state_lock);
        bool valid = f.epoch == generation;
        tal_mutex_unlock(state_lock);
        if (valid) {
            OPERATE_RET result = tdl_audio_play(audio, (uint8_t *)f.samples, f.count * 2);
            tal_mutex_lock(state_lock);
            if (pending_audio)
                pending_audio--;
            audio_until = tal_system_get_millisecond() + 60;
            tal_mutex_unlock(state_lock);
            if (result != OPRT_OK)
                PR_WARN("Speaker playback failed: %d", result);
        }
        tal_mutex_unlock(play_lock);
    }
}
static void button_event(char *name, TDL_BUTTON_TOUCH_EVENT_E event, void *arg)
{
    (void)name;
    (void)arg;
    tal_mutex_lock(state_lock);
    if (event == TDL_BUTTON_PRESS_DOWN)
        press_pending = true;
    tal_mutex_unlock(state_lock);
}
bool cat_button_take_press(void)
{
    tal_mutex_lock(state_lock);
    bool p = press_pending;
    press_pending = false;
    tal_mutex_unlock(state_lock);
    return p;
}
int cat_voice_start(void)
{
    if (tal_mutex_create_init(&state_lock) != OPRT_OK ||
        tal_mutex_create_init(&play_lock) != OPRT_OK ||
        tal_queue_create_init(&mic_queue, sizeof(pcm_frame_t), 20) != OPRT_OK ||
        tal_queue_create_init(&play_queue, sizeof(pcm_frame_t), 32) != OPRT_OK) {
        cat_status("AUDIO MEMORY ERROR");
        return -1;
    }
    tkl_ai_disable_vendor_vad();
    if (tdl_audio_find(AUDIO_CODEC_NAME, &audio) != OPRT_OK || tdl_audio_open(audio, microphone) != OPRT_OK) {
        cat_status("AUDIO INIT FAILED");
        PR_ERR("Audio init failed");
        return -1;
    }
    if (tdl_audio_volume_set(audio, 70) != OPRT_OK)
        return -1;
    THREAD_CFG_T cfg = {.stackDepth = 8192, .priority = THREAD_PRIO_2, .thrdname = "cat_speaker"};
    if (tal_thread_create_and_start(&player, NULL, NULL, play_task, NULL, &cfg) != OPRT_OK) return -1;
    TDL_BUTTON_CFG_T  bc = {.long_start_valid_time     = 3000,
                            .long_keep_timer           = 100,
                            .button_debounce_time      = 30,
                            .button_repeat_valid_count = 2,
                            .button_repeat_valid_time  = 200};
    TDL_BUTTON_HANDLE b;
    if (tdl_button_create(BUTTON_NAME, &bc, &b) == OPRT_OK) {
        tdl_button_event_register(b, TDL_BUTTON_PRESS_DOWN, button_event);
        tdl_button_event_register(b, TDL_BUTTON_PRESS_UP, button_event);
        tdl_button_set_ready_flag(BUTTON_NAME, true);
    } else {
        cat_status("BUTTON INIT FAILED");
        return -1;
    }
    PR_WARN("Cat audio initialized: 16000 Hz, volume 70");
    return 0;
}

bool cat_voice_playing(void)
{
    tal_mutex_lock(state_lock);
    bool active = pending_audio || (int32_t)(audio_until - tal_system_get_millisecond()) > 0;
    tal_mutex_unlock(state_lock);
    return active;
}
