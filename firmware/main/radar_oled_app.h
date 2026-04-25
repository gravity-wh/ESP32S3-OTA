#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

typedef struct {
    uint8_t slave_id;
    uint16_t distance_mm;
    bool online;
    uint32_t poll_ok_count;
    uint32_t poll_error_count;
    char status_text[8];
} radar_state_t;

typedef struct {
    bool wifi_connected;
    bool ota_available;
    bool ota_in_progress;
    uint16_t distance_mm;
    uint8_t slave_id;
    uint8_t radar_count;
    uint8_t occupied_count;
    uint32_t poll_ok_count;
    uint32_t poll_error_count;
    radar_state_t radars[RADAR_MAX_COUNT];
    char wifi_text[20];
    char ota_text[20];
    char modbus_text[20];
    char app_version[32];
} radar_oled_snapshot_t;

esp_err_t radar_oled_app_init(void);
esp_err_t radar_oled_app_start(void);
void radar_oled_set_wifi_status(bool connected, const char *text);
void radar_oled_set_ota_status(bool available, bool in_progress, const char *text);
void radar_oled_set_app_version(const char *version);
void radar_oled_get_snapshot(radar_oled_snapshot_t *snapshot);
esp_err_t radar_oled_execute_cloud_command(const char *action,
                                           uint8_t address,
                                           uint16_t start_register,
                                           uint16_t quantity,
                                           uint16_t write_register,
                                           uint16_t value,
                                           uint8_t target_address,
                                           char *result_json,
                                           size_t result_len);
