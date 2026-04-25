#pragma once

#include "esp_err.h"

esp_err_t ota_service_mark_running_app_valid_if_needed(void);
esp_err_t ota_service_start(void);
