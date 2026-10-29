#pragma once
#include "tal_api.h"
#include <stdint.h>
#include <stdbool.h>
typedef enum {
    CAT_NEUTRAL,
    CAT_HAPPY,
    CAT_SAD,
    CAT_ANGRY,
    CAT_SURPRISED,
    CAT_THINKING,
    CAT_LISTENING,
    CAT_SPEAKING
} cat_emotion_t;
extern char cat_ota_url[512];
bool cat_is_provisioned(void);
void        cat_display_start(void);
void        cat_face(cat_emotion_t emotion);
void        cat_status(const char *text);
int         cat_voice_start(void);
bool        cat_voice_playing(void);
void        cat_network_start(void);
void        cat_network_failed(void);
void        cat_face_name(const char *name);
int         cat_voice_take(int16_t *pcm);
void        cat_voice_record(bool active);
void        cat_voice_record_for(uint32_t duration_ms);
void        cat_voice_play(const int16_t *pcm, int samples);
void        cat_voice_stop(void);
bool        cat_button_take_press(void);
bool        cat_voice_recording(void);
unsigned    cat_voice_dropped_frames(void);

void cat_http_selftest(void);
