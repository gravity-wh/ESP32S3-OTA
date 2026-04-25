#include "esp_app_desc.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "nvs_flash.h"

#include "app_config.h"
#include "cellular_ota_service.h"
#include "ota_service.h"
#include "radar_oled_app.h"

static const char *TAG = "radar_ota_main";

void app_main(void)
{
    const esp_app_desc_t *app_desc = esp_app_get_description();

    esp_log_level_set("*", ESP_LOG_WARN);
    esp_log_level_set(TAG, ESP_LOG_INFO);
    esp_log_level_set("ota_service", ESP_LOG_INFO);
    esp_log_level_set("cellular_ota", ESP_LOG_INFO);

    ESP_LOGI(TAG, "Booting version: %s", app_desc->version);
    ESP_LOGI(TAG, "Communication policy: Wi-Fi reports data, 4G transports OTA packages");

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    ESP_ERROR_CHECK(ota_service_mark_running_app_valid_if_needed());
    ESP_ERROR_CHECK(radar_oled_app_init());
    radar_oled_set_app_version(app_desc->version);
    ESP_ERROR_CHECK(radar_oled_app_start());

#if WIFI_REPORT_ENABLED
    ESP_ERROR_CHECK(ota_service_start());
#endif
    ESP_ERROR_CHECK(cellular_ota_service_start());
}
