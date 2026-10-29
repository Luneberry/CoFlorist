#pragma once
#include <stdbool.h>
#include <stdint.h>

int cat_vad_init(void);
void cat_vad_reset(void);
bool cat_vad_speech(int16_t *pcm);
