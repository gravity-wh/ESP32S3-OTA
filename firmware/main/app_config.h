#pragma once

#define APP_WIFI_SSID               "BYWNG"
#define APP_WIFI_PASSWORD           "SRAC2025!"

#define OTA_MANIFEST_URL            "http://116.62.218.129:8080/firmware/manifest.json"
#define DEVICE_HEARTBEAT_URL        "http://116.62.218.129:8080/api/devices/heartbeat"
#define RADAR_BINDING_RADAR_URL     "http://116.62.218.129:8081/api/radars"
#define RADAR_BINDING_SLOT_URL      "http://116.62.218.129:8081/api/slots/I-A"
#define RADAR_EDGE_HEARTBEAT_URL    "http://116.62.218.129:8081/api/edge/heartbeat"
#define RADAR_EDGE_COMMANDS_URL_FMT "http://116.62.218.129:8081/api/edge/devices/%s/commands"
#define RADAR_EDGE_COMMAND_RESULT_URL_FMT "http://116.62.218.129:8081/api/edge/devices/%s/commands/%s/result"
#define RADAR_BINDING_RADAR_ID      "esp32s3-radar-02"
#define RADAR_BINDING_SLOT_ID       "I-A"
#define OTA_CHECK_INTERVAL_MS       30000
#define OTA_HTTP_TIMEOUT_MS         20000
#define OTA_MAX_HTTP_RECV_BUFFER    1024
#define WIFI_REPORT_ENABLED         1
#define DEVICE_HEARTBEAT_INTERVAL_MS 60000
#define DEVICE_HEARTBEAT_TIMEOUT_MS  3000
#define DEVICE_COMMAND_POLL_INTERVAL_MS 5000
#define DEVICE_NAME                 "ESP32-S3 Radar OLED"

#define APP_USB_SERIAL_JTAG_LOG_ENABLED 1

#define OLED_I2C_PORT               I2C_NUM_0
#define OLED_SDA_GPIO               41
#define OLED_SCL_GPIO               42
#define OLED_MIRROR_Y_AXIS          1

#define RADAR_UART_PORT             UART_NUM_1
#define RADAR_UART_TX_GPIO          17
#define RADAR_UART_RX_GPIO          18
#define RADAR_MODBUS_BAUD_RATE      115200
#define RADAR_DEFAULT_SLAVE_ID      2
#define RADAR_MAX_COUNT             16
#define RADAR_SCAN_MIN_ADDRESS      2
#define RADAR_SCAN_MAX_ADDRESS      32
#define RADAR_NEW_DEVICE_ADDRESS    1
#define RADAR_REGISTER_DISTANCE     0x0010
#define RADAR_REGISTER_ADDRESS      0x0015
#define RADAR_REGISTER_COUNT        1
#define RADAR_POLL_INTERVAL_MS      500
#define RADAR_OCCUPIED_MIN_MM       300
#define RADAR_OCCUPIED_MAX_MM       1000

#define CELLULAR_OTA_ENABLED        1
#define CELLULAR_UART_PORT          UART_NUM_2
#define CELLULAR_UART_TX_GPIO       4
#define CELLULAR_UART_RX_GPIO       5
#define CELLULAR_UART_BAUD_RATE     115200
#define CELLULAR_UART_BUFFER_SIZE   8192
#define CELLULAR_HTTP_CID           1
#define CELLULAR_HTTP_READ_CHUNK    3000
#define CELLULAR_TASK_DELAY_MS      5000
#define CELLULAR_OTA_TASK_STACK     12288
