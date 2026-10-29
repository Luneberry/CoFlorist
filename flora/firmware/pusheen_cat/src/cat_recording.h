#pragma once
#include <stdint.h>

#define CAT_RECORD_MS 60000u
#define CAT_RECORD_SAMPLES (16000u * CAT_RECORD_MS / 1000u)
#define CAT_RECORD_FRAMES (CAT_RECORD_MS / 60u)
#define CAT_TURN_MAX_FRAMES 500u
#define CAT_PREROLL_FRAMES 8u
#define CAT_REPLY_COOLDOWN_MS 400u

/* Unsigned elapsed time also works across the millisecond counter wrap. */
static inline unsigned cat_record_seconds_left(uint32_t now, uint32_t started)
{
    uint32_t elapsed = now - started;
    return elapsed >= CAT_RECORD_MS ? 0 : (CAT_RECORD_MS - elapsed + 999u) / 1000u;
}
