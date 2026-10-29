#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

int cat_bridge_init(void);
void cat_bridge_new_turn(void);
void cat_bridge_state(const char *state);
void cat_bridge_message(const char *role, const char *text);
void cat_bridge_audio_packet(bool input, const uint8_t *packet, size_t size);
void cat_bridge_audio_end(bool input);
unsigned cat_bridge_audio_drops(void);
