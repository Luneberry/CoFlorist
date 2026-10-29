#include "cat_app.h"
#include "cat_vad.h"
#include "audio_subsys_rnn_vad.h"

/* Only the network task owns this detector and feeds its 20 ms PCM frames. */
static void *detector;

int cat_vad_init(void)
{
    int before = tal_psram_get_free_heap_size();
    detector = rnn_vad_create();
    if (!detector)
        return -1;
    rnn_vad_param_in config = {.min_speech_len = 200, .max_speech_interval = 800};
    int result = rnn_vad_init(&config, detector);
    if (result != 0) {
        rnn_vad_destroy(detector);
        detector = NULL;
        return -1;
    }
    rnn_vad_set_callback(detector, -50);
    cat_vad_reset();
    PR_WARN("Cat VAD initialized result=%d psram_bytes=%d onset_ms=200 end_ms=800", result,
            before - tal_psram_get_free_heap_size());
    return 0;
}

void cat_vad_reset(void)
{
    /* stop calls nnom_vad_reset; start alone only enables processing. */
    rnn_vad_stop(detector);
    rnn_vad_start(detector);
}

bool cat_vad_speech(int16_t *pcm)
{
    bool speech = false;
    for (unsigned i = 0; i < 960; i += 320) {
        /* The SDK converts nnom_vad_run's integer speech state to float. */
        if (rnn_vad_process(detector, pcm + i) >= 1.0f)
            speech = true;
    }
    return speech;
}
