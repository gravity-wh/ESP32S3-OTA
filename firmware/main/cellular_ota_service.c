#include <ctype.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "driver/uart.h"
#include "esp_app_desc.h"
#include "esp_check.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "app_config.h"
#include "cellular_ota_service.h"
#include "device_service.h"
#include "radar_oled_app.h"

static const char *TAG = "cellular_ota";

#define AT_RESPONSE_BUFFER_SIZE    8192
#define AT_SHORT_TIMEOUT_MS        1500
#define AT_NETWORK_TIMEOUT_MS      15000
#define AT_HTTP_TIMEOUT_MS         60000

static char s_at_response[AT_RESPONSE_BUFFER_SIZE];
static char s_cellular_ip[24];
static bool s_uart_ready;
static bool s_network_ready;

static esp_err_t cellular_uart_init(void)
{
    if (s_uart_ready) {
        return ESP_OK;
    }

    const uart_config_t uart_cfg = {
        .baud_rate = CELLULAR_UART_BAUD_RATE,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };

    ESP_RETURN_ON_ERROR(uart_driver_install(CELLULAR_UART_PORT,
                                            CELLULAR_UART_BUFFER_SIZE,
                                            CELLULAR_UART_BUFFER_SIZE,
                                            0,
                                            NULL,
                                            0),
                        TAG, "install cellular UART failed");
    ESP_RETURN_ON_ERROR(uart_param_config(CELLULAR_UART_PORT, &uart_cfg), TAG, "config cellular UART failed");
    ESP_RETURN_ON_ERROR(uart_set_pin(CELLULAR_UART_PORT,
                                     CELLULAR_UART_TX_GPIO,
                                     CELLULAR_UART_RX_GPIO,
                                     UART_PIN_NO_CHANGE,
                                     UART_PIN_NO_CHANGE),
                        TAG, "set cellular UART pins failed");
    ESP_RETURN_ON_ERROR(uart_set_rx_timeout(CELLULAR_UART_PORT, 3), TAG, "set cellular RX timeout failed");

    s_uart_ready = true;
    return ESP_OK;
}

static int read_until(char *buffer, size_t buffer_size, const char *needle, int timeout_ms)
{
    const TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(timeout_ms);
    int total = 0;

    if (buffer_size == 0) {
        return 0;
    }
    buffer[0] = '\0';

    while (xTaskGetTickCount() < deadline && total < (int)buffer_size - 1) {
        int got = uart_read_bytes(CELLULAR_UART_PORT,
                                  (uint8_t *)buffer + total,
                                  buffer_size - 1 - (size_t)total,
                                  pdMS_TO_TICKS(100));
        if (got > 0) {
            total += got;
            buffer[total] = '\0';
            if ((needle != NULL && strstr(buffer, needle) != NULL) ||
                strstr(buffer, "\r\nERROR\r\n") != NULL ||
                strstr(buffer, "\r\n+CME ERROR:") != NULL) {
                break;
            }
        }
    }

    return total;
}

static esp_err_t at_command(const char *cmd, const char *expect, int timeout_ms)
{
    ESP_LOGI(TAG, "AT> %s", cmd);
    ESP_RETURN_ON_ERROR(uart_flush_input(CELLULAR_UART_PORT), TAG, "flush cellular input failed");

    if (uart_write_bytes(CELLULAR_UART_PORT, cmd, strlen(cmd)) < 0 ||
        uart_write_bytes(CELLULAR_UART_PORT, "\r\n", 2) < 0) {
        return ESP_FAIL;
    }
    ESP_RETURN_ON_ERROR(uart_wait_tx_done(CELLULAR_UART_PORT, pdMS_TO_TICKS(1000)), TAG, "cellular tx timeout");

    int len = read_until(s_at_response, sizeof(s_at_response), expect, timeout_ms);
    ESP_LOGI(TAG, "AT< %.*s", len, s_at_response);

    if (strstr(s_at_response, "\r\nERROR\r\n") != NULL || strstr(s_at_response, "\r\n+CME ERROR:") != NULL) {
        return ESP_FAIL;
    }
    if (expect != NULL && strstr(s_at_response, expect) == NULL) {
        return ESP_ERR_TIMEOUT;
    }
    return ESP_OK;
}

static esp_err_t at_command_retry(const char *cmd, const char *expect, int timeout_ms, int retries)
{
    esp_err_t last = ESP_FAIL;

    for (int i = 0; i < retries; ++i) {
        last = at_command(cmd, expect, timeout_ms);
        if (last == ESP_OK) {
            vTaskDelay(pdMS_TO_TICKS(200));
            return ESP_OK;
        }
        ESP_LOGW(TAG, "AT command retry %d/%d failed: %s", i + 1, retries, cmd);
        vTaskDelay(pdMS_TO_TICKS(700));
    }
    return last;
}

static esp_err_t at_probe(void)
{
    for (int i = 0; i < 5; ++i) {
        if (at_command("AT", "\r\nOK\r\n", AT_SHORT_TIMEOUT_MS) == ESP_OK) {
            return ESP_OK;
        }
        vTaskDelay(pdMS_TO_TICKS(500));
    }

    ESP_LOGW(TAG, "No AT response on TX=%d RX=%d; trying swapped pins once",
             CELLULAR_UART_TX_GPIO, CELLULAR_UART_RX_GPIO);
    ESP_RETURN_ON_ERROR(uart_set_pin(CELLULAR_UART_PORT,
                                     CELLULAR_UART_RX_GPIO,
                                     CELLULAR_UART_TX_GPIO,
                                     UART_PIN_NO_CHANGE,
                                     UART_PIN_NO_CHANGE),
                        TAG, "swap UART pins failed");
    for (int i = 0; i < 5; ++i) {
        if (at_command("AT", "\r\nOK\r\n", AT_SHORT_TIMEOUT_MS) == ESP_OK) {
            ESP_LOGW(TAG, "AT module responded only after pin swap; check wiring direction");
            return ESP_OK;
        }
        vTaskDelay(pdMS_TO_TICKS(500));
    }

    ESP_RETURN_ON_ERROR(uart_set_pin(CELLULAR_UART_PORT,
                                     CELLULAR_UART_TX_GPIO,
                                     CELLULAR_UART_RX_GPIO,
                                     UART_PIN_NO_CHANGE,
                                     UART_PIN_NO_CHANGE),
                        TAG, "restore UART pins failed");
    return ESP_ERR_TIMEOUT;
}

static bool cellular_parse_qiact_ip(void)
{
    if (strstr(s_at_response, "+QIACT: 1") == NULL) {
        return false;
    }

    const char *quote = strchr(s_at_response, '"');
    if (quote != NULL) {
        quote++;
        const char *end = strchr(quote, '"');
        if (end != NULL && (size_t)(end - quote) < sizeof(s_cellular_ip)) {
            memcpy(s_cellular_ip, quote, (size_t)(end - quote));
            s_cellular_ip[end - quote] = '\0';
        }
    }
    if (s_cellular_ip[0] == '\0') {
        strlcpy(s_cellular_ip, "active", sizeof(s_cellular_ip));
    }
    return true;
}

static esp_err_t cellular_confirm_context(void)
{
    for (int i = 0; i < 5; ++i) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        if (at_command("AT+QIACT?", "\r\nOK\r\n", 5000) == ESP_OK &&
            cellular_parse_qiact_ip()) {
            return ESP_OK;
        }
    }
    return ESP_ERR_TIMEOUT;
}

static esp_err_t cellular_prepare_network(void)
{
    ESP_RETURN_ON_ERROR(at_probe(), TAG, "module did not respond to AT");
    vTaskDelay(pdMS_TO_TICKS(500));
    ESP_RETURN_ON_ERROR(at_command_retry("ATE0", "\r\nOK\r\n", AT_SHORT_TIMEOUT_MS, 4), TAG, "disable echo failed");
    at_command("ATI", "\r\nOK\r\n", AT_SHORT_TIMEOUT_MS);
    ESP_RETURN_ON_ERROR(at_command_retry("AT+CPIN?", "\r\nOK\r\n", AT_SHORT_TIMEOUT_MS, 3), TAG, "SIM check failed");
    at_command_retry("AT+CSQ", "\r\nOK\r\n", AT_SHORT_TIMEOUT_MS, 2);

    for (int i = 0; i < 12; ++i) {
        if (at_command("AT+CGATT?", "\r\nOK\r\n", AT_SHORT_TIMEOUT_MS) == ESP_OK &&
            strstr(s_at_response, "+CGATT: 1") != NULL) {
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(2000));
        if (i == 11) {
            return ESP_ERR_TIMEOUT;
        }
    }

    if (cellular_confirm_context() == ESP_OK) {
        ESP_LOGI(TAG, "4G PDP context already active: %s", s_cellular_ip);
        s_network_ready = true;
        return ESP_OK;
    }

    for (int i = 0; i < 3; ++i) {
        if (at_command_retry("AT+QICSGP=1,1,\"cmnet\",\"\",\"\",1", "\r\nOK\r\n", 5000, 2) == ESP_OK) {
            break;
        }
        if (i == 2) {
            return ESP_ERR_TIMEOUT;
        }
        vTaskDelay(pdMS_TO_TICKS(1000));
    }

    if (at_command("AT+QIACT=1", "\r\nOK\r\n", AT_NETWORK_TIMEOUT_MS) != ESP_OK) {
        ESP_LOGW(TAG, "QIACT activation failed once; deactivating context and retrying");
        at_command("AT+QIDEACT=1", "\r\nOK\r\n", 5000);
        ESP_RETURN_ON_ERROR(at_command("AT+QIACT=1", "\r\nOK\r\n", AT_NETWORK_TIMEOUT_MS),
                            TAG, "activate Quectel PDP context failed");
    }

    if (cellular_confirm_context() != ESP_OK) {
        ESP_LOGW(TAG, "QIACT=1 returned OK but QIACT? did not confirm; continuing to validate by HTTP");
        strlcpy(s_cellular_ip, "activated", sizeof(s_cellular_ip));
        s_network_ready = true;
        return ESP_OK;
    }

    s_network_ready = true;
    return ESP_OK;
}

static esp_err_t extract_json_string(const char *json, const char *key, char *out, size_t out_len)
{
    char pattern[32];
    const char *start;
    const char *colon;
    const char *quote;
    size_t i = 0;

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

static esp_err_t extract_json_int(const char *json, const char *key, int *out)
{
    char pattern[32];
    const char *start;
    const char *colon;

    snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    start = strstr(json, pattern);
    if (start == NULL) {
        return ESP_ERR_NOT_FOUND;
    }

    colon = strchr(start + strlen(pattern), ':');
    if (colon == NULL) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    colon++;
    while (*colon == ' ' || *colon == '\t') {
        colon++;
    }
    if (!isdigit((unsigned char)*colon)) {
        return ESP_ERR_INVALID_RESPONSE;
    }

    *out = atoi(colon);
    return ESP_OK;
}

static int compare_version_string(const char *lhs, const char *rhs)
{
    const char *a = lhs;
    const char *b = rhs;

    while (*a != '\0' || *b != '\0') {
        while (*a != '\0' && !isdigit((unsigned char)*a)) {
            a++;
        }
        while (*b != '\0' && !isdigit((unsigned char)*b)) {
            b++;
        }

        long va = 0;
        long vb = 0;
        while (isdigit((unsigned char)*a)) {
            va = (va * 10) + (*a - '0');
            a++;
        }
        while (isdigit((unsigned char)*b)) {
            vb = (vb * 10) + (*b - '0');
            b++;
        }

        if (va != vb) {
            return va < vb ? -1 : 1;
        }
        if (*a == '\0' && *b == '\0') {
            return 0;
        }
    }
    return 0;
}

static esp_err_t parse_qhttpget(int *status_code, int *data_len)
{
    const char *line = strstr(s_at_response, "+QHTTPGET:");
    int err = 0;

    if (line == NULL) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    if (sscanf(line, "+QHTTPGET: %d,%d,%d", &err, status_code, data_len) != 3) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    if (err != 0) {
        return ESP_FAIL;
    }
    return ESP_OK;
}

static esp_err_t qhttp_set_url(const char *url)
{
    char cmd[64];

    for (int attempt = 0; attempt < 3; ++attempt) {
        if (attempt > 0) {
            at_command("AT+QHTTPSTOP", "\r\nOK\r\n", 2000);
            vTaskDelay(pdMS_TO_TICKS(500));
        }

        snprintf(cmd, sizeof(cmd), "AT+QHTTPURL=%u,80", (unsigned)strlen(url));
        if (at_command(cmd, "CONNECT", 5000) != ESP_OK) {
            ESP_LOGW(TAG, "QHTTPURL CONNECT failed, attempt %d", attempt + 1);
            continue;
        }

        ESP_LOGI(TAG, "AT> %s", url);
        if (uart_write_bytes(CELLULAR_UART_PORT, url, strlen(url)) < 0) {
            return ESP_FAIL;
        }
        ESP_RETURN_ON_ERROR(uart_wait_tx_done(CELLULAR_UART_PORT, pdMS_TO_TICKS(1000)), TAG, "URL tx timeout");
        read_until(s_at_response, sizeof(s_at_response), "\r\nOK\r\n", 5000);
        ESP_LOGI(TAG, "AT< %s", s_at_response);
        if (strstr(s_at_response, "\r\nOK\r\n") != NULL) {
            return ESP_OK;
        }
    }

    return ESP_ERR_TIMEOUT;
}

static esp_err_t qhttp_get(const char *url, int *status_code, int *data_len)
{
    ESP_RETURN_ON_ERROR(qhttp_set_url(url), TAG, "set QHTTP URL failed");
    ESP_RETURN_ON_ERROR(at_command("AT+QHTTPGET=120", "+QHTTPGET:", AT_HTTP_TIMEOUT_MS), TAG, "QHTTPGET failed");
    ESP_RETURN_ON_ERROR(parse_qhttpget(status_code, data_len), TAG, "parse QHTTPGET failed");
    return ESP_OK;
}

static esp_err_t wait_literal(const char *literal, int timeout_ms);

static esp_err_t qhttp_read_text(char *out, size_t out_len, int expected_len, int *body_len)
{
    char cmd[32];
    int total = 0;
    TickType_t idle_deadline;

    if (out == NULL || out_len == 0 || body_len == NULL || expected_len <= 0 ||
        expected_len >= (int)out_len) {
        return ESP_ERR_INVALID_ARG;
    }

    *body_len = 0;
    out[0] = '\0';
    snprintf(cmd, sizeof(cmd), "AT+QHTTPREAD\r\n");

    ESP_LOGI(TAG, "AT> AT+QHTTPREAD");
    ESP_RETURN_ON_ERROR(uart_flush_input(CELLULAR_UART_PORT), TAG, "flush before QHTTPREAD failed");
    ESP_RETURN_ON_FALSE(uart_write_bytes(CELLULAR_UART_PORT, cmd, strlen(cmd)) == (int)strlen(cmd),
                        ESP_FAIL, TAG, "QHTTPREAD write failed");
    ESP_RETURN_ON_ERROR(uart_wait_tx_done(CELLULAR_UART_PORT, pdMS_TO_TICKS(1000)),
                        TAG, "QHTTPREAD tx timeout");
    ESP_RETURN_ON_ERROR(wait_literal("CONNECT\r\n", 35000), TAG, "QHTTPREAD CONNECT timeout");

    idle_deadline = xTaskGetTickCount() + pdMS_TO_TICKS(10000);
    while (total < expected_len && xTaskGetTickCount() < idle_deadline) {
        int got = uart_read_bytes(CELLULAR_UART_PORT,
                                  (uint8_t *)out + total,
                                  (size_t)(expected_len - total),
                                  pdMS_TO_TICKS(500));
        if (got > 0) {
            total += got;
            idle_deadline = xTaskGetTickCount() + pdMS_TO_TICKS(3000);
        }
    }

    if (total != expected_len) {
        out[total < (int)out_len ? total : (int)out_len - 1] = '\0';
        ESP_LOGW(TAG, "QHTTPREAD body length mismatch: got=%d expected=%d partial=%s", total, expected_len, out);
        return ESP_ERR_INVALID_SIZE;
    }

    out[total] = '\0';
    *body_len = total;
    read_until(s_at_response, sizeof(s_at_response), "\r\nOK\r\n", 5000);
    ESP_LOGI(TAG, "AT< %s", s_at_response);
    return ESP_OK;
}

static esp_err_t wait_literal(const char *literal, int timeout_ms)
{
    const TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(timeout_ms);
    size_t matched = 0;
    const size_t literal_len = strlen(literal);

    while (xTaskGetTickCount() < deadline) {
        uint8_t ch = 0;
        int got = uart_read_bytes(CELLULAR_UART_PORT, &ch, 1, pdMS_TO_TICKS(100));
        if (got <= 0) {
            continue;
        }

        if ((char)ch == literal[matched]) {
            matched++;
            if (matched == literal_len) {
                return ESP_OK;
            }
        } else {
            matched = ((char)ch == literal[0]) ? 1 : 0;
        }
    }

    return ESP_ERR_TIMEOUT;
}

static esp_err_t cellular_stream_ota_image(const char *url, int expected_size)
{
    const esp_partition_t *update_partition;
    esp_ota_handle_t ota_handle = 0;
    uint8_t buffer[1024];
    int status_code = 0;
    int data_len = 0;
    int remaining;
    esp_err_t ret;

    ESP_LOGI(TAG, "4G OTA download start: %s", url);
    ESP_RETURN_ON_ERROR(qhttp_get(url, &status_code, &data_len), TAG, "firmware QHTTPGET failed");
    ESP_LOGI(TAG, "Firmware QHTTP status=%d len=%d expected=%d", status_code, data_len, expected_size);

    if (status_code != 200 || data_len <= 0) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    if (expected_size > 0 && data_len != expected_size) {
        ESP_LOGW(TAG, "Manifest size and HTTP size differ: manifest=%d http=%d", expected_size, data_len);
    }

    update_partition = esp_ota_get_next_update_partition(NULL);
    if (update_partition == NULL) {
        return ESP_ERR_NOT_FOUND;
    }
    ESP_LOGI(TAG, "Writing OTA partition %s at 0x%08lx", update_partition->label, update_partition->address);

    ESP_RETURN_ON_ERROR(esp_ota_begin(update_partition, data_len, &ota_handle), TAG, "ota begin failed");

    ESP_LOGI(TAG, "AT> AT+QHTTPREAD=120");
    ESP_GOTO_ON_FALSE(uart_write_bytes(CELLULAR_UART_PORT, "AT+QHTTPREAD=120\r\n", 18) == 18,
                      ESP_FAIL, fail, TAG, "QHTTPREAD write failed");
    ESP_GOTO_ON_ERROR(uart_wait_tx_done(CELLULAR_UART_PORT, pdMS_TO_TICKS(1000)),
                      fail, TAG, "QHTTPREAD tx timeout");
    ESP_GOTO_ON_ERROR(wait_literal("CONNECT\r\n", AT_HTTP_TIMEOUT_MS),
                      fail, TAG, "QHTTPREAD CONNECT timeout");

    remaining = data_len;
    while (remaining > 0) {
        const int want = remaining > (int)sizeof(buffer) ? (int)sizeof(buffer) : remaining;
        int got = uart_read_bytes(CELLULAR_UART_PORT, buffer, want, pdMS_TO_TICKS(AT_HTTP_TIMEOUT_MS));
        if (got <= 0) {
            ret = ESP_ERR_TIMEOUT;
            goto fail;
        }

        ret = esp_ota_write(ota_handle, buffer, (size_t)got);
        if (ret != ESP_OK) {
            goto fail;
        }
        remaining -= got;

        if ((remaining % (64 * 1024)) < got || remaining == 0) {
    ESP_LOGI(TAG, "4G OTA progress: %d/%d", data_len - remaining, data_len);
            radar_oled_set_ota_status(true, true, "4GOTA");
        }
    }

    read_until(s_at_response, sizeof(s_at_response), "\r\nOK\r\n", AT_SHORT_TIMEOUT_MS);
    ESP_LOGI(TAG, "AT< %s", s_at_response);

    ESP_GOTO_ON_ERROR(esp_ota_end(ota_handle), fail_no_abort, TAG, "ota end failed");
    ESP_RETURN_ON_ERROR(esp_ota_set_boot_partition(update_partition), TAG, "set boot partition failed");
    ESP_LOGI(TAG, "4G OTA write complete; rebooting");
    radar_oled_set_ota_status(true, false, "REBOOT");
    device_service_set_ota_state("REBOOT", "success", "4G OTA success, rebooting", NULL, NULL);
    device_service_report_now();
    vTaskDelay(pdMS_TO_TICKS(1000));
    esp_restart();
    return ESP_OK;

fail:
    esp_ota_abort(ota_handle);
fail_no_abort:
    return ret;
}

static esp_err_t cellular_http_get_manifest(void)
{
    char manifest[2048];
    char remote_version[32] = {0};
    char remote_url[256] = {0};
    const esp_app_desc_t *running = esp_app_get_description();
    int status_code = 0;
    int data_len = 0;
    int body_len = 0;
    int remote_size = 0;

    ESP_RETURN_ON_ERROR(at_command("AT+QHTTPCFG=\"contextid\",1", "\r\nOK\r\n", AT_SHORT_TIMEOUT_MS),
                        TAG, "set QHTTP context failed");

    ESP_RETURN_ON_ERROR(qhttp_get(OTA_MANIFEST_URL, &status_code, &data_len), TAG, "manifest QHTTPGET failed");

    ESP_LOGI(TAG, "Manifest QHTTP status=%d len=%d", status_code, data_len);
    if (status_code != 200 || data_len <= 0 || data_len >= (int)sizeof(manifest)) {
        return ESP_ERR_INVALID_RESPONSE;
    }

    ESP_RETURN_ON_ERROR(qhttp_read_text(manifest, sizeof(manifest), data_len, &body_len), TAG, "QHTTPREAD manifest failed");
    ESP_LOGI(TAG, "4G manifest body(%d): %s", body_len, manifest);

    ESP_RETURN_ON_ERROR(extract_json_string(manifest, "version", remote_version, sizeof(remote_version)), TAG, "manifest version missing");
    ESP_RETURN_ON_ERROR(extract_json_string(manifest, "url", remote_url, sizeof(remote_url)), TAG, "manifest url missing");
    extract_json_int(manifest, "size", &remote_size);

    ESP_LOGI(TAG, "4G manifest ok: current=%s remote=%s url=%s", running->version, remote_version, remote_url);
    if (compare_version_string(remote_version, running->version) > 0) {
        ESP_LOGW(TAG, "4G OTA candidate found: %s", remote_version);
        radar_oled_set_ota_status(true, false, "4GUP");
        device_service_set_ota_state("4GUP", "available", "New firmware available via 4G", running->version, remote_version);
        device_service_report_now();
        return cellular_stream_ota_image(remote_url, remote_size);
    } else {
        radar_oled_set_ota_status(false, false, "4GOK");
        device_service_set_ota_state("LATEST", "ok", "Current firmware is latest by 4G manifest", running->version, remote_version);
        device_service_report_now();
    }

    return ESP_OK;
}

static void cellular_ota_task(void *arg)
{
    (void)arg;

    vTaskDelay(pdMS_TO_TICKS(CELLULAR_TASK_DELAY_MS));
    radar_oled_set_ota_status(false, false, "4G");

    if (cellular_uart_init() != ESP_OK) {
        ESP_LOGE(TAG, "UART2 init failed");
        radar_oled_set_ota_status(false, false, "4GUART");
        device_service_set_ota_state("4GUART", "failed", "4G UART init failed", NULL, NULL);
        device_service_report_now();
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "Cellular UART2 ready: TX=%d RX=%d baud=%d",
             CELLULAR_UART_TX_GPIO, CELLULAR_UART_RX_GPIO, CELLULAR_UART_BAUD_RATE);

    if (cellular_prepare_network() != ESP_OK) {
        ESP_LOGE(TAG, "4G network prepare failed");
        radar_oled_set_ota_status(false, false, "4GNET");
        device_service_set_ota_state("4GNET", "failed", "4G network prepare failed", NULL, NULL);
        device_service_report_now();
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "4G network is ready");
    radar_oled_set_ota_status(false, false, "4GIP");

    device_service_set_ota_state("4GIP", "running", "4G OTA network is ready", NULL, NULL);
    device_service_report_now();

    TickType_t last_ota_check = 0;
    while (true) {
        TickType_t now_tick = xTaskGetTickCount();
        if (last_ota_check == 0 ||
            now_tick - last_ota_check >= pdMS_TO_TICKS(OTA_CHECK_INTERVAL_MS)) {
            device_service_set_ota_state("4GCHK", "running", "Fetching manifest over 4G", NULL, NULL);
            if (cellular_http_get_manifest() != ESP_OK) {
                ESP_LOGE(TAG, "4G manifest download failed");
                radar_oled_set_ota_status(false, false, "4GHTTP");
                device_service_set_ota_state("4GHTTP", "failed", "4G manifest download failed", NULL, NULL);
                device_service_report_now();
            }
            last_ota_check = now_tick;
        }

        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

esp_err_t cellular_ota_service_start(void)
{
#if CELLULAR_OTA_ENABLED
    if (xTaskCreate(cellular_ota_task,
                    "cellular_ota",
                    CELLULAR_OTA_TASK_STACK,
                    NULL,
                    3,
                    NULL) != pdPASS) {
        return ESP_FAIL;
    }
#endif
    return ESP_OK;
}
