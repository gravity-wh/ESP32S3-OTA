#include <stdio.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

#include "esp_check.h"
#include "esp_err.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "app_config.h"
#include "device_service.h"
#include "radar_oled_app.h"

static const char *TAG = "device_service";

typedef struct {
    char app_version[32];
    char ota_state[20];
    char ota_result[20];
    char ota_message[48];
    char ota_from_version[32];
    char ota_to_version[32];
} device_state_t;

typedef struct {
    char *buffer;
    size_t buffer_len;
    size_t used;
} http_text_buffer_t;

static SemaphoreHandle_t s_device_mutex;
static device_state_t s_device_state;
static bool s_started;

static void copy_text(char *dst, size_t dst_len, const char *src)
{
    snprintf(dst, dst_len, "%s", src != NULL ? src : "");
}

static void appendf(char *buffer, size_t buffer_len, size_t *used, const char *fmt, ...)
{
    va_list args;

    if (buffer == NULL || used == NULL || *used >= buffer_len) {
        return;
    }

    va_start(args, fmt);
    int written = vsnprintf(buffer + *used, buffer_len - *used, fmt, args);
    va_end(args);
    if (written < 0) {
        return;
    }
    if ((size_t)written >= buffer_len - *used) {
        *used = buffer_len - 1;
    } else {
        *used += (size_t)written;
    }
}

static void format_mac_string(char *out, size_t out_len)
{
    uint8_t mac[6] = {0};

    if (esp_wifi_get_mac(WIFI_IF_STA, mac) == ESP_OK) {
        snprintf(out, out_len, "%02X:%02X:%02X:%02X:%02X:%02X",
                 mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
        return;
    }

    if (esp_read_mac(mac, ESP_MAC_WIFI_STA) == ESP_OK) {
        snprintf(out, out_len, "%02X:%02X:%02X:%02X:%02X:%02X",
                 mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
        return;
    }

    copy_text(out, out_len, "00:00:00:00:00:00");
}

static void build_device_id(char *out, size_t out_len)
{
    uint8_t mac[6] = {0};

    if (esp_read_mac(mac, ESP_MAC_WIFI_STA) != ESP_OK) {
        copy_text(out, out_len, "esp32s3-radar");
        return;
    }

    snprintf(out, out_len, "esp32s3-%02x%02x%02x%02x%02x%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

static void get_ip_string(char *out, size_t out_len)
{
    esp_netif_ip_info_t ip_info;
    esp_netif_t *netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");

    if (netif != NULL && esp_netif_get_ip_info(netif, &ip_info) == ESP_OK) {
        snprintf(out, out_len, IPSTR, IP2STR(&ip_info.ip));
        return;
    }

    copy_text(out, out_len, "");
}

static esp_err_t send_json(const char *url, const char *payload, esp_http_client_method_t method, int timeout_ms)
{
    esp_err_t ret = ESP_OK;
    esp_http_client_config_t config = {
        .url = url,
        .timeout_ms = timeout_ms,
        .buffer_size = 1024,
        .keep_alive_enable = false,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    int status_code;

    if (client == NULL) {
        return ESP_ERR_NO_MEM;
    }

    ESP_GOTO_ON_ERROR(esp_http_client_set_method(client, method), cleanup, TAG, "set http method failed");
    ESP_GOTO_ON_ERROR(esp_http_client_set_header(client, "Content-Type", "application/json"), cleanup, TAG, "set post header failed");
    ESP_GOTO_ON_ERROR(esp_http_client_set_post_field(client, payload, (int)strlen(payload)), cleanup, TAG, "set post body failed");
    ESP_GOTO_ON_ERROR(esp_http_client_perform(client), cleanup, TAG, "perform post failed");

    status_code = esp_http_client_get_status_code(client);
    if (status_code < 200 || status_code >= 300) {
        ESP_LOGW(TAG, "POST %s HTTP status=%d", url, status_code);
        ret = ESP_FAIL;
    }

cleanup:
    esp_http_client_cleanup(client);
    return ret;
}

static esp_err_t post_json(const char *url, const char *payload, int timeout_ms)
{
    return send_json(url, payload, HTTP_METHOD_POST, timeout_ms);
}

static esp_err_t http_text_event_handler(esp_http_client_event_t *evt)
{
    if (evt->event_id != HTTP_EVENT_ON_DATA || evt->user_data == NULL ||
        evt->data == NULL || evt->data_len <= 0) {
        return ESP_OK;
    }

    http_text_buffer_t *ctx = (http_text_buffer_t *)evt->user_data;
    if (ctx->used + (size_t)evt->data_len >= ctx->buffer_len) {
        return ESP_FAIL;
    }

    memcpy(ctx->buffer + ctx->used, evt->data, (size_t)evt->data_len);
    ctx->used += (size_t)evt->data_len;
    ctx->buffer[ctx->used] = '\0';
    return ESP_OK;
}

static void append_radars_json(char *payload, size_t payload_len, size_t *used,
                               const radar_oled_snapshot_t *snapshot)
{
    appendf(payload, payload_len, used, "[");
    for (uint8_t i = 0; i < snapshot->radar_count && i < RADAR_MAX_COUNT; ++i) {
        const radar_state_t *radar = &snapshot->radars[i];
        appendf(payload, payload_len, used,
                "%s{\"id\":\"radar-%02u\",\"address\":%u,\"queueIndex\":%u,"
                "\"distanceMm\":%u,\"online\":%s,\"occupied\":%s,"
                "\"pollOkCount\":%lu,\"pollErrorCount\":%lu,\"status\":\"%s\"}",
                i == 0 ? "" : ",",
                radar->slave_id,
                radar->slave_id,
                (unsigned)i,
                radar->distance_mm,
                radar->online ? "true" : "false",
                (radar->online && radar->distance_mm >= RADAR_OCCUPIED_MIN_MM && radar->distance_mm <= RADAR_OCCUPIED_MAX_MM) ? "true" : "false",
                (unsigned long)radar->poll_ok_count,
                (unsigned long)radar->poll_error_count,
                radar->status_text);
    }
    appendf(payload, payload_len, used, "]");
}

static void post_edge_radar_state(const char *device_id, const char *mac, const char *ip,
                                  const radar_oled_snapshot_t *snapshot)
{
    char *payload = calloc(1, 4096);
    size_t used = 0;

    if (payload == NULL) {
        ESP_LOGW(TAG, "No memory for edge radar heartbeat");
        return;
    }

    appendf(payload, 4096, &used,
            "{\"deviceId\":\"%s\",\"name\":\"%s\",\"mac\":\"%s\",\"ip\":\"%s\","
            "\"appVersion\":\"%s\",\"transport\":\"edge-esp32\","
            "\"radarCount\":%u,\"occupiedCount\":%u,\"radars\":",
            device_id,
            DEVICE_NAME,
            mac,
            ip,
            snapshot->app_version,
            snapshot->radar_count,
            snapshot->occupied_count);
    append_radars_json(payload, 4096, &used, snapshot);
    appendf(payload, 4096, &used, "}");

    if (post_json(RADAR_EDGE_HEARTBEAT_URL, payload, DEVICE_HEARTBEAT_TIMEOUT_MS) != ESP_OK) {
        ESP_LOGW(TAG, "Edge radar heartbeat failed");
    }
    free(payload);
}

static esp_err_t send_heartbeat(void)
{
    radar_oled_snapshot_t snapshot;
    char mac[18];
    char device_id[32];
    char ip[20];
    char *payload = NULL;
    size_t used = 0;
    esp_err_t ret = ESP_OK;
    esp_http_client_config_t config = {
        .url = DEVICE_HEARTBEAT_URL,
        .timeout_ms = DEVICE_HEARTBEAT_TIMEOUT_MS,
        .buffer_size = 1024,
        .keep_alive_enable = false,
    };
    esp_http_client_handle_t client;
    int status_code;

    radar_oled_get_snapshot(&snapshot);
    if (!snapshot.wifi_connected || snapshot.ota_in_progress) {
        return ESP_OK;
    }

    format_mac_string(mac, sizeof(mac));
    build_device_id(device_id, sizeof(device_id));
    get_ip_string(ip, sizeof(ip));
    payload = calloc(1, 4096);
    if (payload == NULL) {
        return ESP_ERR_NO_MEM;
    }

    if (xSemaphoreTake(s_device_mutex, portMAX_DELAY) == pdTRUE) {
        appendf(payload, 4096, &used,
                "{"
                "\"id\":\"%s\","
                "\"name\":\"%s\","
                "\"label\":\"radar-oled\","
                "\"mac\":\"%s\","
                "\"ip\":\"%s\","
                "\"status\":\"online\","
                "\"networkMode\":\"wifi\","
                "\"uplink\":\"wifi\","
                "\"modeSelectGpio\":null,"
                "\"tags\":[\"esp32s3\",\"radar\",\"oled\",\"ota\",\"edge-gateway\"],"
                "\"firmware\":\"%s\","
                "\"appVersion\":\"%s\","
                "\"telemetry\":{"
                "\"distanceMm\":%u,"
                "\"radarCount\":%u,"
                "\"occupiedCount\":%u,"
                "\"pollOkCount\":%lu,"
                "\"pollErrorCount\":%lu,"
                "\"networkMode\":\"wifi\","
                "\"uplink\":\"wifi\","
                "\"modeSelectGpio\":null,"
                "\"radars\":",
                device_id,
                DEVICE_NAME,
                mac,
                ip,
                snapshot.app_version,
                s_device_state.app_version,
                snapshot.distance_mm,
                snapshot.radar_count,
                snapshot.occupied_count,
                (unsigned long)snapshot.poll_ok_count,
                (unsigned long)snapshot.poll_error_count);
        append_radars_json(payload, 4096, &used, &snapshot);
        appendf(payload, 4096, &used,
                "},"
                "\"ota\":{"
                "\"state\":\"%s\","
                "\"result\":\"%s\","
                "\"message\":\"%s\","
                "\"fromVersion\":\"%s\","
                "\"toVersion\":\"%s\""
                "}"
                "}",
                s_device_state.ota_state,
                s_device_state.ota_result,
                s_device_state.ota_message,
                s_device_state.ota_from_version,
                s_device_state.ota_to_version);
        xSemaphoreGive(s_device_mutex);
    } else {
        free(payload);
        return ESP_FAIL;
    }

    client = esp_http_client_init(&config);
    if (client == NULL) {
        free(payload);
        return ESP_ERR_NO_MEM;
    }

    ESP_GOTO_ON_ERROR(esp_http_client_set_method(client, HTTP_METHOD_POST), cleanup, TAG, "set heartbeat method failed");
    ESP_GOTO_ON_ERROR(esp_http_client_set_header(client, "Content-Type", "application/json"), cleanup, TAG, "set heartbeat header failed");
    ESP_GOTO_ON_ERROR(esp_http_client_set_post_field(client, payload, (int)strlen(payload)), cleanup, TAG, "set heartbeat body failed");
    ESP_GOTO_ON_ERROR(esp_http_client_perform(client), cleanup, TAG, "perform heartbeat failed");

    status_code = esp_http_client_get_status_code(client);
    if (status_code < 200 || status_code >= 300) {
        ESP_LOGW(TAG, "Heartbeat HTTP status=%d", status_code);
    }

    post_edge_radar_state(device_id, mac, ip, &snapshot);

cleanup:
    esp_http_client_cleanup(client);
    free(payload);
    return ret;
}

static esp_err_t http_get_text(const char *url, char *buffer, size_t buffer_len, int *status_code)
{
    esp_err_t ret = ESP_OK;
    http_text_buffer_t text = {
        .buffer = buffer,
        .buffer_len = buffer_len,
        .used = 0,
    };
    esp_http_client_config_t config = {
        .url = url,
        .timeout_ms = DEVICE_HEARTBEAT_TIMEOUT_MS,
        .buffer_size = 1024,
        .keep_alive_enable = false,
        .event_handler = http_text_event_handler,
        .user_data = &text,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);

    if (client == NULL) {
        return ESP_ERR_NO_MEM;
    }
    if (buffer_len == 0) {
        esp_http_client_cleanup(client);
        return ESP_ERR_INVALID_ARG;
    }

    buffer[0] = '\0';
    ESP_GOTO_ON_ERROR(esp_http_client_set_header(client, "Accept", "application/json"), cleanup, TAG, "set accept header failed");
    ESP_GOTO_ON_ERROR(esp_http_client_perform(client), cleanup, TAG, "perform get failed");
    if (status_code != NULL) {
        *status_code = esp_http_client_get_status_code(client);
    }
    buffer[text.used] = '\0';

cleanup:
    esp_http_client_cleanup(client);
    return ret;
}

static esp_err_t extract_json_string(const char *json, const char *key, char *out, size_t out_len)
{
    char pattern[40];
    const char *start;
    const char *colon;
    const char *quote;
    size_t i = 0;

    if (json == NULL || key == NULL || out == NULL || out_len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    start = strstr(json, pattern);
    if (start == NULL) {
        return ESP_ERR_NOT_FOUND;
    }
    colon = strchr(start + strlen(pattern), ':');
    if (colon == NULL) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    quote = strchr(colon, '"');
    if (quote == NULL) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    quote++;
    while (quote[i] != '\0' && quote[i] != '"' && i + 1 < out_len) {
        out[i] = quote[i];
        i++;
    }
    out[i] = '\0';
    return i > 0 ? ESP_OK : ESP_ERR_INVALID_RESPONSE;
}

static int extract_json_int(const char *json, const char *key, int default_value)
{
    char pattern[40];
    const char *start;
    const char *colon;

    if (json == NULL || key == NULL) {
        return default_value;
    }
    snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    start = strstr(json, pattern);
    if (start == NULL) {
        return default_value;
    }
    colon = strchr(start + strlen(pattern), ':');
    if (colon == NULL) {
        return default_value;
    }
    return atoi(colon + 1);
}

static const char *first_command_object(const char *response)
{
    const char *commands = strstr(response, "\"commands\"");
    if (commands == NULL) {
        return NULL;
    }
    const char *array = strchr(commands, '[');
    if (array == NULL) {
        return NULL;
    }
    return strchr(array, '{');
}

static void post_command_result(const char *device_id, const char *command_id,
                                esp_err_t exec_ret, const char *result_json)
{
    char url[256];
    char payload[768];
    const bool ok = exec_ret == ESP_OK;

    snprintf(url, sizeof(url), RADAR_EDGE_COMMAND_RESULT_URL_FMT, device_id, command_id);
    snprintf(payload, sizeof(payload),
             "{\"success\":%s,\"espError\":%d,\"message\":\"%s\",\"result\":%s}",
             ok ? "true" : "false",
             (int)exec_ret,
             ok ? "OK" : esp_err_to_name(exec_ret),
             (result_json != NULL && result_json[0] != '\0') ? result_json : "{}");
    if (post_json(url, payload, DEVICE_HEARTBEAT_TIMEOUT_MS) != ESP_OK) {
        ESP_LOGW(TAG, "Post command result failed: %s", command_id);
    }
}

static void command_task(void *arg)
{
    (void)arg;

    while (true) {
        radar_oled_snapshot_t snapshot;
        char device_id[32];
        char url[256];
        char response[1536];
        char command_id[48];
        char action[32];
        char result_json[512];
        const char *command_json;
        int status_code = 0;

        vTaskDelay(pdMS_TO_TICKS(DEVICE_COMMAND_POLL_INTERVAL_MS));

        radar_oled_get_snapshot(&snapshot);
        if (!snapshot.wifi_connected || snapshot.ota_in_progress) {
            continue;
        }

        build_device_id(device_id, sizeof(device_id));
        snprintf(url, sizeof(url), RADAR_EDGE_COMMANDS_URL_FMT, device_id);
        if (http_get_text(url, response, sizeof(response), &status_code) != ESP_OK ||
            status_code < 200 || status_code >= 300) {
            continue;
        }

        command_json = first_command_object(response);
        if (command_json == NULL ||
            extract_json_string(command_json, "id", command_id, sizeof(command_id)) != ESP_OK ||
            extract_json_string(command_json, "action", action, sizeof(action)) != ESP_OK) {
            continue;
        }

        uint8_t address = (uint8_t)extract_json_int(command_json, "address", RADAR_DEFAULT_SLAVE_ID);
        uint16_t start_register = (uint16_t)extract_json_int(command_json, "startRegister", RADAR_REGISTER_DISTANCE);
        uint16_t quantity = (uint16_t)extract_json_int(command_json, "quantity", 1);
        uint16_t write_register = (uint16_t)extract_json_int(command_json, "register", RADAR_REGISTER_ADDRESS);
        uint16_t value = (uint16_t)extract_json_int(command_json, "value", 0);
        uint8_t target_address = (uint8_t)extract_json_int(command_json, "targetAddress", 0);

        ESP_LOGI(TAG, "Cloud command %s action=%s address=%u", command_id, action, address);
        esp_err_t exec_ret = radar_oled_execute_cloud_command(action,
                                                              address,
                                                              start_register,
                                                              quantity,
                                                              write_register,
                                                              value,
                                                              target_address,
                                                              result_json,
                                                              sizeof(result_json));
        post_command_result(device_id, command_id, exec_ret, result_json);
        send_heartbeat();
    }
}

static void heartbeat_task(void *arg)
{
    (void)arg;

    while (true) {
        vTaskDelay(pdMS_TO_TICKS(DEVICE_HEARTBEAT_INTERVAL_MS));
        send_heartbeat();
    }
}

esp_err_t device_service_start(const char *app_version)
{
    if (s_started) {
        return ESP_OK;
    }

    s_device_mutex = xSemaphoreCreateMutex();
    if (s_device_mutex == NULL) {
        return ESP_ERR_NO_MEM;
    }

    if (xSemaphoreTake(s_device_mutex, portMAX_DELAY) == pdTRUE) {
        copy_text(s_device_state.app_version, sizeof(s_device_state.app_version), app_version);
        copy_text(s_device_state.ota_state, sizeof(s_device_state.ota_state), "BOOT");
        copy_text(s_device_state.ota_result, sizeof(s_device_state.ota_result), "pending");
        copy_text(s_device_state.ota_message, sizeof(s_device_state.ota_message), "Device started");
        copy_text(s_device_state.ota_from_version, sizeof(s_device_state.ota_from_version), "");
        copy_text(s_device_state.ota_to_version, sizeof(s_device_state.ota_to_version), app_version);
        xSemaphoreGive(s_device_mutex);
    }

    if (xTaskCreate(heartbeat_task, "device_heartbeat", 6144, NULL, 2, NULL) != pdPASS) {
        vSemaphoreDelete(s_device_mutex);
        s_device_mutex = NULL;
        return ESP_FAIL;
    }
    if (xTaskCreate(command_task, "device_commands", 8192, NULL, 2, NULL) != pdPASS) {
        vSemaphoreDelete(s_device_mutex);
        s_device_mutex = NULL;
        return ESP_FAIL;
    }

    s_started = true;
    return ESP_OK;
}

void device_service_set_ota_state(const char *state, const char *result, const char *message,
                                  const char *from_version, const char *to_version)
{
    if (s_device_mutex == NULL) {
        return;
    }

    if (xSemaphoreTake(s_device_mutex, portMAX_DELAY) == pdTRUE) {
        if (state != NULL) {
            copy_text(s_device_state.ota_state, sizeof(s_device_state.ota_state), state);
        }
        if (result != NULL) {
            copy_text(s_device_state.ota_result, sizeof(s_device_state.ota_result), result);
        }
        if (message != NULL) {
            copy_text(s_device_state.ota_message, sizeof(s_device_state.ota_message), message);
        }
        if (from_version != NULL) {
            copy_text(s_device_state.ota_from_version, sizeof(s_device_state.ota_from_version), from_version);
        }
        if (to_version != NULL) {
            copy_text(s_device_state.ota_to_version, sizeof(s_device_state.ota_to_version), to_version);
        }
        xSemaphoreGive(s_device_mutex);
    }
}

esp_err_t device_service_report_now(void)
{
    return send_heartbeat();
}
