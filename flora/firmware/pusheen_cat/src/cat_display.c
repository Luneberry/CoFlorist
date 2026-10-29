#include "cat_app.h"
#include "cat_setup_qr.h"
#include "tdl_display_manage.h"
#include "board_com_api.h"
#include <string.h>
extern const uint8_t *const cat_frames[16];
extern const size_t         cat_frame_bytes[16];
extern const uint8_t        cat_font[95][13];
static MUTEX_HANDLE         lock;
static cat_emotion_t        emotion         = CAT_NEUTRAL;
static char                 status_line[40] = "SCAN QR TO CONNECT";
static THREAD_HANDLE        thread;
void                        cat_face(cat_emotion_t value)
{
    if (value > CAT_SPEAKING)
        return;
    tal_mutex_lock(lock);
    emotion = value;
    tal_mutex_unlock(lock);
}
void cat_status(const char *text)
{
    tal_mutex_lock(lock);
    snprintf(status_line, sizeof(status_line), "%s", text);
    tal_mutex_unlock(lock);
}
void cat_face_name(const char *n)
{
    if (!n)
        return;
    if (!strcmp(n, "happy") || !strcmp(n, "joy") || !strcmp(n, "love") || !strcmp(n, "loving") ||
        !strcmp(n, "laughing") || !strcmp(n, "wink") || !strcmp(n, "winking") || !strcmp(n, "funny"))
        cat_face(CAT_HAPPY);
    else if (!strcmp(n, "sad") || !strcmp(n, "crying"))
        cat_face(CAT_SAD);
    else if (!strcmp(n, "angry"))
        cat_face(CAT_ANGRY);
    else if (!strcmp(n, "surprised") || !strcmp(n, "shocked"))
        cat_face(CAT_SURPRISED);
    else if (!strcmp(n, "thinking") || !strcmp(n, "confused") || !strcmp(n, "dizzy") || !strcmp(n, "look_around"))
        cat_face(CAT_THINKING);
    else
        cat_face(CAT_NEUTRAL);
}
/* RGB driver calls free_cb from VSYNC ISR. Never allocate/free there. */
static volatile uint8_t frame_available[2] = {1, 1};
static void frame_released(TDL_DISP_FRAME_BUFF_T *fb)
{
    *(volatile uint8_t *)fb->free_arg = 1;
}
static void display_task(void *arg)
{
    (void)arg;
    TDL_DISP_HANDLE_T   dev = tdl_disp_find_dev(DISPLAY_NAME);
    TDL_DISP_DEV_INFO_T info;
    if (!dev || tdl_disp_dev_get_info(dev, &info) != OPRT_OK || info.width != 320 || info.height != 480 ||
        tdl_disp_dev_open(dev) != OPRT_OK) {
        PR_ERR("Cat display init failed");
        return;
    }
    if (tdl_disp_set_brightness(dev, 100) != OPRT_OK) { PR_ERR("Brightness setup failed"); return; }
    unsigned displayed = 0;
    TDL_DISP_FRAME_BUFF_T *buffers[2] = {NULL, NULL};
    for (unsigned i=0; i<2; i++) {
        buffers[i] = tdl_disp_create_frame_buff(DISP_FB_TP_PSRAM, 320*480*2);
        if (!buffers[i]) { PR_ERR("Cat frame allocation failed"); return; }
        buffers[i]->free_cb = frame_released;
        buffers[i]->free_arg = (void *)&frame_available[i];
    }
    int      frame = 0, last = -1;
    uint32_t deadline = 0;
    for (;;) {
        char text[40];
        tal_mutex_lock(lock);
        int current = emotion;
        memcpy(text, status_line, sizeof(text));
        tal_mutex_unlock(lock);
        uint32_t now = tal_system_get_millisecond();
        if (current != last) {
            frame    = 0;
            last     = current;
            deadline = 0;
        }
        if ((int32_t)(now - deadline) < 0) {
            tal_system_sleep(30);
            continue;
        }
        unsigned slot = frame_available[0] ? 0 : 1;
        if (!frame_available[slot]) { tal_system_sleep(5); continue; }
        frame_available[slot] = 0;
        TDL_DISP_FRAME_BUFF_T *fb = buffers[slot];
        fb->fmt              = info.fmt;
        fb->width            = 320;
        fb->height           = 480;
        fb->x_start          = 0;
        fb->y_start          = 0;
        unsigned       index = current * 2 + frame;
        const uint8_t *r     = cat_frames[index];
        uint16_t      *dst   = (uint16_t *)fb->frame;
        size_t         pos   = 0;
        for (size_t i = 0; i + 2 < cat_frame_bytes[index]; i += 3) {
            uint16_t value = r[i + 1] | ((uint16_t)r[i + 2] << 8);
            if (info.is_swap)
                value = (value >> 8) | (value << 8);
            for (unsigned j = 0; j < r[i] && pos < 320 * 480; j++)
                dst[pos++] = value;
        }
        if (pos != 320 * 480) {
            frame_available[slot] = 1;
            PR_ERR("Invalid cat frame");
            return;
        }
        if (!cat_is_provisioned()) {
            const int scale = 5, quiet = 4;
            const int edge = (CAT_QR_SIZE + quiet * 2) * scale;
            const int left = (320 - edge) / 2, top = 240;
            for (int y = 0; y < edge; y++) for (int x = 0; x < edge; x++) {
                int mx = x / scale - quiet, my = y / scale - quiet;
                bool black = mx >= 0 && my >= 0 && mx < CAT_QR_SIZE && my < CAT_QR_SIZE &&
                             cat_setup_qr[my * CAT_QR_SIZE + mx];
                dst[(top + y) * 320 + left + x] = black ? 0 : 0xffff;
            }
        }
        int x0 = (320 - (int)strlen(text) * 8) / 2;
        if (x0 < 0)
            x0 = 0;
        for (size_t k = 0; text[k] && k < 39; k++) {
            unsigned c = (unsigned char)text[k];
            if (c < 32 || c > 126)
                continue;
            for (int y = 0; y < 13; y++)
                for (int x = 0; x < 8; x++)
                    if (cat_font[c - 32][y] & (0x80 >> x)) {
                        int px = x0 + k * 8 + x;
                        if (px < 320)
                            dst[(456 + y) * 320 + px] = 0xffff;
                    }
        }
        if (tdl_disp_dev_flush(dev, fb) != OPRT_OK)
            frame_available[slot] = 1;
        if (++displayed == 3) PR_WARN("Cat animation running: 320x480, brightness 100");
        deadline = now + (current == CAT_NEUTRAL ? (frame ? 160 : 1400) : (current == CAT_SPEAKING ? 240 : 650));
        frame ^= 1;
    }
}
void cat_display_start(void)
{
    tal_mutex_create_init(&lock);
    THREAD_CFG_T cfg = {.stackDepth = 4096, .priority = THREAD_PRIO_2, .thrdname = "cat_display"};
    tal_thread_create_and_start(&thread, NULL, NULL, display_task, NULL, &cfg);
}
