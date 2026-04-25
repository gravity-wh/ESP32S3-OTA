#pragma once

#include "esp_err.h"

esp_err_t device_service_start(const char *app_version);
void device_service_set_ota_state(const char *state, const char *result, const char *message,
                                  const char *from_version, const char *to_version);
esp_err_t device_service_report_now(void);
