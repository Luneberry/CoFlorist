/**
 * @file example_http_service_portal.c
 * @brief SoftAP HTTP service portal example with static HTML/CSS/JS pages.
 * @copyright Copyright (c) 2021-2026 Tuya Inc.
 */

#include "tuya_cloud_types.h"

#include "tal_api.h"
#include "cat_app.h"
#include "board_com_api.h"
#include "cJSON.h"
#include "tal_wifi.h"
#include "http_host.h"
#include "http_site.h"
#include "portal_web_assets.h"
#include "http_captive.h"
#include "tkl_output.h"
#if defined(ENABLE_LIBLWIP) && (ENABLE_LIBLWIP == 1)
#include "lwip_init.h"
#endif
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include "cat_form.h"

/* ---------------------------------------------------------------------------
 * Macros
 * --------------------------------------------------------------------------- */
#define WEB_HTTP_PORT         80
#define WEB_LISTEN_BACKLOG    2
#define WEB_REQ_BUF_SIZE      3072
#define WEB_JSON_BUF_SIZE     256
#define WEB_AP_CHANNEL        6
#define WEB_SWITCH_DELAY_MS   500
#define WEB_SWITCH_GRACE_MS   2500
#define WEB_RECV_TIMEOUT_MS   2500
#define WEB_SEND_TIMEOUT_MS   2500
#define WEB_CLIENT_WAIT_SLICE 30
#define WEB_CLIENT_WAIT_MAX   120
#define WEB_AP_SSID           "Pusheen-Cat-Setup"
#define WEB_AP_PASSWD         "12345678"
#define WEB_AP_IP             "192.168.50.1"
#define WEB_AP_MASK           "255.255.255.0"
#define WEB_AP_GATEWAY        "192.168.50.1"

/* ---------------------------------------------------------------------------
 * File scope variables
 * --------------------------------------------------------------------------- */
static bool s_provisioned;
bool cat_is_provisioned(void) { return __atomic_load_n(&s_provisioned, __ATOMIC_ACQUIRE); }

static HTTP_HOST_HANDLE_T s_http_host                       = NULL;
static HTTP_SITE_HANDLE_T s_http_site                       = NULL;
static BOOL_T             s_sta_connect_pending             = FALSE;
static SYS_TIME_T         s_sta_switch_due_ms               = 0;
static char               s_sta_ssid[WIFI_SSID_LEN + 1]     = {0};
static char               s_sta_passwd[WIFI_PASSWD_LEN + 1] = {0};

/* ---------------------------------------------------------------------------
 * Forward declarations
 * --------------------------------------------------------------------------- */
static OPERATE_RET __portal_site_init(void);
static OPERATE_RET __portal_api_status_cb(const HTTP_HOST_REQUEST_T *req, void *user_ctx);
static OPERATE_RET __portal_api_networks_cb(const HTTP_HOST_REQUEST_T *req, void *user_ctx);
static OPERATE_RET __portal_api_provision_cb(const HTTP_HOST_REQUEST_T *req, void *user_ctx);
static OPERATE_RET __portal_http_request_cb(const HTTP_HOST_REQUEST_T *req, void *user_ctx);
static void        __portal_http_idle_cb(HTTP_HOST_HANDLE_T host, void *user_ctx);
static OPERATE_RET __switch_to_sta_and_connect(void);
static void        __sta_switch_work_cb(void *data);

/* ---------------------------------------------------------------------------
 * Function implementations
 * --------------------------------------------------------------------------- */
/**
 * @brief Wi-Fi event callback.
 * @param[in] event Wi-Fi event.
 * @param[in] arg event payload.
 * @return none
 */
static void __wifi_event_callback(WF_EVENT_E event, void *arg)
{
    (void)arg;
    PR_DEBUG("wifi event: %d", event);
}

/**
 * @brief Extract string value from tiny JSON body.
 * @param[in] json raw JSON text.
 * @param[in] key object key.
 * @param[out] out output string.
 * @param[in] out_len output buffer size.
 * @return OPRT_OK on success.
 */
/**
 * @brief Register SoftAP demo pages and API routes on the HTTP site.
 * @return OPRT_OK on success.
 */
static OPERATE_RET __portal_site_init(void)
{
    OPERATE_RET rt = OPRT_OK;

    TUYA_CALL_ERR_RETURN(http_site_create(&s_http_site, HTTP_SITE_DEFAULT_MAX_ROUTES));
    TUYA_CALL_ERR_RETURN(portal_web_attach(s_http_site));
    TUYA_CALL_ERR_RETURN(http_site_add_static(s_http_site, "GET", "/probe", "200 OK", "text/html; charset=utf-8",
        "<!doctype html><html><meta name=viewport content=width=device-width>"
        "<style>html,body{margin:0;padding:0;background:white}div{position:fixed;top:0;left:0;right:0;margin:0;padding:0;background:#ffeb00;color:#000;font:24px/1.3 sans-serif}</style>"
        "<body><div>CAT_HTTP_OK</div></body></html>"));
    TUYA_CALL_ERR_RETURN(http_site_add_static(s_http_site, "GET", "/simple", "200 OK", "text/html; charset=utf-8",
        "<!doctype html><html><meta name=viewport content=width=device-width><title>Cat setup</title><style>html,body,h1{margin:0;padding:0}h1{font-size:24px}</style><body>"
        "<h1>Cat Wi-Fi setup</h1><form method=post action=/api/provision>"
        "<p>Wi-Fi name<br><input name=ssid required maxlength=32></p>"
        "<p>Password<br><input name=password type=password maxlength=64></p>"
        "<button>Connect</button></form></body></html>"));
    TUYA_CALL_ERR_RETURN(http_site_add_handler(s_http_site, "GET", "/api/status", __portal_api_status_cb, NULL));
    TUYA_CALL_ERR_RETURN(http_site_add_handler(s_http_site, "POST", "/api/provision", __portal_api_provision_cb, NULL));
    TUYA_CALL_ERR_RETURN(http_site_add_handler(s_http_site, "GET", "/api/networks", __portal_api_networks_cb, NULL));
    return rt;
}

/**
 * @brief Return device Wi-Fi status as JSON.
 * @param[in] req HTTP request context.
 * @param[in] user_ctx unused.
 * @return OPRT_OK on success.
 */
static OPERATE_RET __portal_api_status_cb(const HTTP_HOST_REQUEST_T *req, void *user_ctx)
{
    char              json[WEB_JSON_BUF_SIZE] = {0};
    WF_WK_MD_E        mode                    = WWM_STATION;
    WF_STATION_STAT_E sta_stat                = WSS_IDLE;

    (void)user_ctx;
    if (req == NULL) {
        return OPRT_INVALID_PARM;
    }

    tal_wifi_get_work_mode(&mode);
    tal_wifi_station_get_status(&sta_stat);
    (void)snprintf(json, sizeof(json), "{\"mode\":%d,\"sta_status\":%d}", mode, sta_stat);
    return http_host_reply(req->client_fd, "200 OK", "application/json; charset=utf-8", json);
}

/**
 * @brief Parse provision payload and return success page.
 * @param[in] req HTTP request context.
 * @param[in] user_ctx unused.
 * @return OPRT_OK on success.
 */
static OPERATE_RET __portal_api_provision_cb(const HTTP_HOST_REQUEST_T *req, void *user_ctx)
{
    (void)user_ctx;
    if (!req) {
        return OPRT_INVALID_PARM;
    }
    if (!req->body || s_sta_connect_pending) {
        return http_host_reply(req->client_fd, "400 Bad Request", "text/plain", "Invalid request");
    }
    cJSON *j = NULL;
    char content_type[96] = {0};
    http_host_get_header_value(req->raw, req->raw_len, "Content-Type", content_type, sizeof(content_type));
    if (!strncmp(content_type, "application/x-www-form-urlencoded", 33)) {
        char name[WIFI_SSID_LEN+1], secret[WIFI_PASSWD_LEN+1], timestamp[24];
        if (cat_form_value(req->body, req->body_len, "ssid", name, sizeof(name)) == 0 &&
            cat_form_value(req->body, req->body_len, "password", secret, sizeof(secret)) == 0) {
            j = cJSON_CreateObject();
            if (j) {
                cJSON_AddStringToObject(j, "ssid", name);
                cJSON_AddStringToObject(j, "password", secret);
                double epoch = (double)tal_time_get_posix();
                if (cat_form_value(req->body, req->body_len, "time", timestamp, sizeof(timestamp)) == 0 && timestamp[0])
                    epoch = strtod(timestamp, NULL);
                cJSON_AddNumberToObject(j, "time", epoch);
            }
        }
        memset(name, 0, sizeof(name)); memset(secret, 0, sizeof(secret));
    } else {
        j = cJSON_ParseWithLength(req->body, req->body_len);
    }
    const cJSON *ssid     = cJSON_GetObjectItemCaseSensitive(j, "ssid");
    const cJSON *password = cJSON_GetObjectItemCaseSensitive(j, "password");
    const cJSON *epoch    = cJSON_GetObjectItemCaseSensitive(j, "time");
    bool valid = cJSON_IsString(ssid) && strlen(ssid->valuestring) > 0 && strlen(ssid->valuestring) <= WIFI_SSID_LEN &&
                 cJSON_IsString(password) && strlen(password->valuestring) <= WIFI_PASSWD_LEN && cJSON_IsNumber(epoch) && epoch->valuedouble > 1700000000 && epoch->valuedouble < 4102444800;
    if (!valid) {
        cJSON_Delete(j);
        return http_host_reply(req->client_fd, "400 Bad Request", "text/plain; charset=utf-8",
                               "입력 내용을 확인해 주세요.");
    }
    strcpy(s_sta_ssid, ssid->valuestring);
    strcpy(s_sta_passwd, password->valuestring);
    if (cJSON_IsNumber(epoch) && epoch->valuedouble > 1700000000 && epoch->valuedouble < 4102444800) {
        tal_time_set_posix((TIME_T)epoch->valuedouble, 0);
    }
    cJSON_Delete(j);
    __atomic_store_n(&s_provisioned, true, __ATOMIC_RELEASE);
    s_sta_connect_pending = TRUE;
    s_sta_switch_due_ms   = tal_system_get_millisecond() + WEB_SWITCH_GRACE_MS;
    return http_host_reply(req->client_fd, "200 OK", "text/html; charset=utf-8", portal_web_provision_ok_html());
}

/**
 * @brief Dispatch HTTP requests through the site route table.
 * @param[in] req HTTP request context.
 * @param[in] user_ctx unused.
 * @return OPRT_OK on success.
 */
static OPERATE_RET __portal_http_request_cb(const HTTP_HOST_REQUEST_T *req, void *user_ctx)
{
    OPERATE_RET rt = OPRT_OK;

    (void)user_ctx;
    if ((req == NULL) || (req->raw == NULL) || (req->raw_len == 0)) {
        return OPRT_INVALID_PARM;
    }

    /* Fixed route labels only: never log raw paths, headers or form contents. */
    unsigned route = !strcmp(req->path, "/") ? 1 : !strcmp(req->path, "/api/status") ? 2 :
                     !strcmp(req->path, "/api/networks") ? 3 : !strcmp(req->path, "/api/provision") ? 4 : 0;
    PR_WARN("Cat setup request route=%u bytes=%u", route, req->raw_len);

    rt = http_captive_handle_http(req);
    if (rt == OPRT_OK) {
        PR_WARN("Cat setup captive response sent");
        return rt;
    }

    rt = http_site_dispatch(s_http_site, req);
    PR_WARN("Cat setup response route=%u result=%d", route, rt);
    if (rt == OPRT_NOT_FOUND) {
        if (strcmp(req->method, "GET") == 0) {
            return http_captive_redirect_to_portal(req);
        }
        return http_host_reply_not_found(req->client_fd);
    }

    return rt;
}

/**
 * @brief Runs STA switch scheduled from HTTP server (runs on system work queue).
 * @param[in] data unused.
 * @return none
 */
static void __sta_switch_work_cb(void *data)
{
    OPERATE_RET rt = OPRT_OK;
    (void)data;
    TUYA_CALL_ERR_LOG(__switch_to_sta_and_connect());
}

/**
 * @brief Idle hook: close HTTP listener after provision grace, then queue STA switch.
 * @param[in] host HTTP host handle.
 * @param[in] user_ctx unused.
 * @return none
 */
static void __portal_http_idle_cb(HTTP_HOST_HANDLE_T host, void *user_ctx)
{
    (void)user_ctx;

    if (s_sta_connect_pending != TRUE) {
        return;
    }

    if (tal_system_get_millisecond() < s_sta_switch_due_ms) {
        return;
    }

    PR_NOTICE("provision grace elapsed; closing listener, STA switch queued on system workq");
    (void)http_host_request_shutdown(host);
    s_http_host = NULL;

    if (tal_workq_schedule(WORKQ_SYSTEM, __sta_switch_work_cb, NULL) != OPRT_OK) {
        PR_ERR("workq schedule failed, falling back to switch in current context");
        (void)__switch_to_sta_and_connect();
    }
}

/**
 * @brief Switch device to station mode and connect router.
 * @return OPRT_OK on success.
 * @note It stops SoftAP before station connection.
 */
static OPERATE_RET __switch_to_sta_and_connect(void)
{
    OPERATE_RET rt = OPRT_OK;

    if (s_sta_ssid[0] == '\0') {
        return OPRT_INVALID_PARM;
    }

    cat_status("CONNECTING TO WIFI");
    (void)http_captive_stop();
    TUYA_CALL_ERR_LOG(tal_wifi_ap_stop());
    tal_system_sleep(WEB_SWITCH_DELAY_MS);

    TUYA_CALL_ERR_GOTO(tal_wifi_set_work_mode(WWM_STATION), __EXIT);
    TUYA_CALL_ERR_GOTO(tal_wifi_station_connect((int8_t *)s_sta_ssid, (int8_t *)s_sta_passwd), __EXIT);
    PR_NOTICE("station connect request sent");

__EXIT:
    memset(s_sta_ssid, 0, sizeof(s_sta_ssid));
    memset(s_sta_passwd, 0, sizeof(s_sta_passwd));
    s_sta_connect_pending = FALSE;
    s_sta_switch_due_ms   = 0;
    return rt;
}

void user_main(void)
{
    OPERATE_RET     rt         = OPRT_OK;
    WF_AP_CFG_IF_S  ap_cfg     = {0};
    NW_IP_S         nw_ip      = {0};
    HTTP_HOST_CFG_T host_cfg   = {0};
    uint32_t        ssid_len   = strlen(WEB_AP_SSID);
    uint32_t        passwd_len = strlen(WEB_AP_PASSWD);

    tal_log_init(TAL_LOG_LEVEL_WARN, 1024, (TAL_LOG_OUTPUT_CB)tkl_log_output);

    PR_NOTICE("Application information:");
    PR_NOTICE("Project name:        %s", PROJECT_NAME);
    PR_NOTICE("App version:         %s", PROJECT_VERSION);
    PR_NOTICE("Compile time:        %s", __DATE__);
    PR_NOTICE("TuyaOpen version:    %s", OPEN_VERSION);
    PR_NOTICE("TuyaOpen commit-id:  %s", OPEN_COMMIT);
    PR_NOTICE("Platform chip:       %s", PLATFORM_CHIP);
    PR_NOTICE("Platform board:      %s", PLATFORM_BOARD);
    PR_NOTICE("Platform commit-id:  %s", PLATFORM_COMMIT);

    tal_sw_timer_init();
    tal_workq_init();
    TUYA_CALL_ERR_GOTO(board_register_hardware(), __EXIT);
    cat_display_start();
    if (cat_voice_start() != 0)
        goto __EXIT;
    cat_network_start();

#if defined(ENABLE_LIBLWIP) && (ENABLE_LIBLWIP == 1)
    TUYA_LwIP_Init();
#endif

    TUYA_CALL_ERR_GOTO(tal_wifi_init(__wifi_event_callback), __EXIT);
    TUYA_CALL_ERR_GOTO(tal_wifi_set_work_mode(WWM_SOFTAP), __EXIT);

    (void)snprintf(nw_ip.ip, sizeof(nw_ip.ip), "%s", WEB_AP_IP);
    (void)snprintf(nw_ip.mask, sizeof(nw_ip.mask), "%s", WEB_AP_MASK);
    (void)snprintf(nw_ip.gw, sizeof(nw_ip.gw), "%s", WEB_AP_GATEWAY);

    if ((ssid_len == 0) || (ssid_len > WIFI_SSID_LEN) || (passwd_len > WIFI_PASSWD_LEN)) {
        PR_ERR("invalid ap credentials");
        goto __EXIT;
    }

    ap_cfg.s_len       = (uint8_t)ssid_len;
    ap_cfg.p_len       = (uint8_t)passwd_len;
    ap_cfg.chan        = WEB_AP_CHANNEL;
    ap_cfg.md          = WAAM_WPA2_PSK;
    ap_cfg.max_conn    = 4;
    ap_cfg.ms_interval = 100;
    ap_cfg.ip          = nw_ip;

    memcpy(ap_cfg.ssid, WEB_AP_SSID, ssid_len);
    ap_cfg.ssid[ssid_len] = '\0';
    memcpy(ap_cfg.passwd, WEB_AP_PASSWD, passwd_len);
    ap_cfg.passwd[passwd_len] = '\0';

    HTTP_CAPTIVE_CFG_T captive_cfg = {
        .ap_ip                  = WEB_AP_IP,
        .redirect_unmatched_get = TRUE,
    };

    TUYA_CALL_ERR_GOTO(tal_wifi_ap_start(&ap_cfg), __EXIT);
    cat_status("SCAN QR TO CONNECT");

    TUYA_CALL_ERR_GOTO(http_captive_start(&captive_cfg), __EXIT);

    TUYA_CALL_ERR_GOTO(__portal_site_init(), __EXIT);

    host_cfg.port                   = WEB_HTTP_PORT;
    host_cfg.backlog                = WEB_LISTEN_BACKLOG;
    host_cfg.listen_recv_timeout_ms = WEB_RECV_TIMEOUT_MS;
    host_cfg.listen_send_timeout_ms = WEB_SEND_TIMEOUT_MS;
    host_cfg.client_recv_slice_ms   = WEB_CLIENT_WAIT_SLICE;
    host_cfg.client_send_timeout_ms = WEB_SEND_TIMEOUT_MS;
    host_cfg.client_wait_max_rounds = WEB_CLIENT_WAIT_MAX;
    host_cfg.max_request_size       = WEB_REQ_BUF_SIZE;
    host_cfg.stack_depth            = 1024 * 8;
    host_cfg.priority               = THREAD_PRIO_2;
    host_cfg.thread_name            = "softap_httpd";
    TUYA_CALL_ERR_GOTO(http_host_start(&s_http_host, &host_cfg, __portal_http_request_cb, __portal_http_idle_cb, NULL),
                       __EXIT);
    tal_system_sleep(500);
    cat_http_selftest();

__EXIT:
    return;
}

/**
 * @brief Main entry on Linux.
 * @param[in] argc command argument count.
 * @param[in] argv command argument values.
 * @return none
 */
#if OPERATING_SYSTEM == SYSTEM_LINUX
void main(int argc, char *argv[])
{
    (void)argc;
    (void)argv;

    user_main();
    while (1) {
        tal_system_sleep(1000);
    }
}
#else

static THREAD_HANDLE s_app_thread = NULL;

/**
 * @brief App thread body.
 * @param[in] arg thread argument.
 * @return none
 */
static void __app_thread(void *arg)
{
    (void)arg;
    user_main();
    tal_thread_delete(s_app_thread);
    s_app_thread = NULL;
}

/**
 * @brief Tuya app entry on MCU platforms.
 * @return none
 */
void tuya_app_main(void)
{
    THREAD_CFG_T thrd_param = {0};
    thrd_param.stackDepth   = 1024 * 6;
    thrd_param.priority     = THREAD_PRIO_1;
    thrd_param.thrdname     = "tuya_app_main";
    tal_thread_create_and_start(&s_app_thread, NULL, NULL, __app_thread, NULL, &thrd_param);
}
#endif

void cat_network_failed(void)
{
    cat_status("RETURNING TO SETUP");
    tal_system_sleep(1500);
    tal_system_reset();
}

/* Scan results stay in RAM and are returned only to the local setup page. */
static OPERATE_RET __portal_api_networks_cb(const HTTP_HOST_REQUEST_T *req, void *user_ctx)
{
    (void)user_ctx;
    AP_IF_S *aps = NULL;
    uint32_t count = 0;
    if (!req) return OPRT_INVALID_PARM;
    if (cat_is_provisioned() || tal_wifi_all_ap_scan(&aps, &count) != OPRT_OK)
        return http_host_reply(req->client_fd, "503 Service Unavailable", "application/json", "[]");
    cJSON *list = cJSON_CreateArray();
    if (list) for (uint32_t i=0; i<count && i<32; i++) {
        char name[WIFI_SSID_LEN+1];
        size_t n = aps[i].s_len > WIFI_SSID_LEN ? WIFI_SSID_LEN : aps[i].s_len;
        memcpy(name, aps[i].ssid, n); name[n] = 0;
        if (n && strcmp(name, WEB_AP_SSID)) {
            cJSON *item = cJSON_CreateString(name);
            if (item) cJSON_AddItemToArray(list, item);
        }
    }
    if (aps) tal_wifi_release_ap(aps);
    char *body = list ? cJSON_PrintUnformatted(list) : NULL;
    OPERATE_RET ret = http_host_reply(req->client_fd, body ? "200 OK" : "503 Service Unavailable",
                                     "application/json; charset=utf-8", body ? body : "[]");
    if (body) cJSON_free(body);
    cJSON_Delete(list);
    return ret;
}
