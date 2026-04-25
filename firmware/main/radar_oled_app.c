#include <inttypes.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "driver/i2c_master.h"
#include "driver/uart.h"
#include "driver/usb_serial_jtag.h"
#include "esp_check.h"
#include "esp_err.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "app_config.h"
#include "radar_oled_app.h"

static const char *TAG = "radar_oled_app";

#define OLED_WIDTH                 128
#define OLED_HEIGHT                64
#define OLED_PAGE_COUNT            (OLED_HEIGHT / 8)
#define OLED_TIMEOUT_MS            1000
#define OLED_ADDR_PRIMARY          0x3C
#define OLED_ADDR_SECONDARY        0x3D

#define RADAR_RX_BUFFER_SIZE       256
#define RADAR_RESPONSE_TIMEOUT_MS  500
#define RADAR_INTER_FRAME_MS       30

typedef struct {
    char c;
    uint8_t columns[5];
} glyph_t;

static const glyph_t s_glyphs[] = {
    {' ', {0x00, 0x00, 0x00, 0x00, 0x00}},
    {'-', {0x08, 0x08, 0x08, 0x08, 0x08}},
    {'.', {0x00, 0x03, 0x03, 0x00, 0x00}},
    {'/', {0x03, 0x04, 0x08, 0x10, 0x60}},
    {':', {0x00, 0x36, 0x36, 0x00, 0x00}},
    {'0', {0x3E, 0x45, 0x49, 0x51, 0x3E}},
    {'1', {0x00, 0x21, 0x7F, 0x01, 0x00}},
    {'2', {0x23, 0x45, 0x49, 0x51, 0x21}},
    {'3', {0x22, 0x41, 0x49, 0x49, 0x36}},
    {'4', {0x18, 0x28, 0x48, 0x7F, 0x08}},
    {'5', {0x72, 0x51, 0x51, 0x51, 0x4E}},
    {'6', {0x1E, 0x29, 0x49, 0x49, 0x06}},
    {'7', {0x40, 0x47, 0x48, 0x50, 0x60}},
    {'8', {0x36, 0x49, 0x49, 0x49, 0x36}},
    {'9', {0x30, 0x49, 0x49, 0x4A, 0x3C}},
    {'A', {0x3F, 0x48, 0x48, 0x48, 0x3F}},
    {'B', {0x7F, 0x49, 0x49, 0x49, 0x36}},
    {'C', {0x3E, 0x41, 0x41, 0x41, 0x22}},
    {'D', {0x7F, 0x41, 0x41, 0x22, 0x1C}},
    {'E', {0x7F, 0x49, 0x49, 0x49, 0x41}},
    {'F', {0x7F, 0x48, 0x48, 0x48, 0x40}},
    {'G', {0x3E, 0x41, 0x49, 0x49, 0x2E}},
    {'H', {0x7F, 0x08, 0x08, 0x08, 0x7F}},
    {'I', {0x00, 0x41, 0x7F, 0x41, 0x00}},
    {'K', {0x7F, 0x08, 0x14, 0x22, 0x41}},
    {'L', {0x7F, 0x01, 0x01, 0x01, 0x01}},
    {'M', {0x7F, 0x20, 0x10, 0x20, 0x7F}},
    {'N', {0x7F, 0x20, 0x10, 0x08, 0x7F}},
    {'O', {0x3E, 0x41, 0x41, 0x41, 0x3E}},
    {'P', {0x7F, 0x48, 0x48, 0x48, 0x30}},
    {'Q', {0x3E, 0x41, 0x45, 0x42, 0x3D}},
    {'R', {0x7F, 0x48, 0x4C, 0x4A, 0x31}},
    {'S', {0x31, 0x49, 0x49, 0x49, 0x46}},
    {'T', {0x40, 0x40, 0x7F, 0x40, 0x40}},
    {'U', {0x7E, 0x01, 0x01, 0x01, 0x7E}},
    {'V', {0x7C, 0x02, 0x01, 0x02, 0x7C}},
    {'W', {0x7E, 0x01, 0x0E, 0x01, 0x7E}},
    {'X', {0x63, 0x14, 0x08, 0x14, 0x63}},
    {'Y', {0x70, 0x08, 0x07, 0x08, 0x70}},
};

static radar_oled_snapshot_t s_snapshot = {
    .slave_id = RADAR_DEFAULT_SLAVE_ID,
};
static SemaphoreHandle_t s_state_mutex;
static SemaphoreHandle_t s_bus_mutex;
static i2c_master_bus_handle_t s_i2c_bus;
static i2c_master_dev_handle_t s_oled_dev;
static bool s_i2c_ready;
static bool s_oled_ready;
static bool s_usb_ready;

static void copy_text(char *dst, size_t dst_len, const char *src)
{
    snprintf(dst, dst_len, "%s", src != NULL ? src : "");
}

static void usb_log_line(const char *fmt, ...)
{
    char line[160];
    va_list args;

    if (!s_usb_ready) {
        return;
    }

    va_start(args, fmt);
    vsnprintf(line, sizeof(line), fmt, args);
    va_end(args);
    usb_serial_jtag_write_bytes(line, strlen(line), pdMS_TO_TICKS(20));
}

static uint8_t glyph_column(char c, size_t index)
{
    for (size_t i = 0; i < sizeof(s_glyphs) / sizeof(s_glyphs[0]); ++i) {
        if (s_glyphs[i].c == c) {
            return s_glyphs[i].columns[index];
        }
    }
    return 0x00;
}

static esp_err_t oled_write_byte(uint8_t control, uint8_t data)
{
    uint8_t buffer[2] = {control, data};
    return i2c_master_transmit(s_oled_dev, buffer, sizeof(buffer), OLED_TIMEOUT_MS);
}

static inline esp_err_t oled_cmd(uint8_t cmd)
{
    return oled_write_byte(0x00, cmd);
}

static inline esp_err_t oled_data(uint8_t data)
{
    return oled_write_byte(0x40, data);
}

static esp_err_t oled_set_pos(uint8_t x, uint8_t page)
{
    x += 2;
    ESP_RETURN_ON_ERROR(oled_cmd(0xB0 + page), TAG, "set page failed");
    ESP_RETURN_ON_ERROR(oled_cmd(((x & 0xF0) >> 4) | 0x10), TAG, "set high column failed");
    ESP_RETURN_ON_ERROR(oled_cmd(x & 0x0F), TAG, "set low column failed");
    return ESP_OK;
}

static esp_err_t oled_clear(void)
{
    for (uint8_t page = 0; page < OLED_PAGE_COUNT; ++page) {
        ESP_RETURN_ON_ERROR(oled_set_pos(0, page), TAG, "set pos failed");
        for (uint8_t x = 0; x < OLED_WIDTH; ++x) {
            ESP_RETURN_ON_ERROR(oled_data(0x00), TAG, "clear failed");
        }
    }
    return ESP_OK;
}

static esp_err_t oled_draw_text(uint8_t x, uint8_t page, const char *text)
{
    while (*text != '\0' && x <= 122) {
        ESP_RETURN_ON_ERROR(oled_set_pos(x, page), TAG, "set pos failed");
        for (size_t i = 0; i < 5; ++i) {
            ESP_RETURN_ON_ERROR(oled_data(glyph_column(*text, i)), TAG, "draw text failed");
        }
        ESP_RETURN_ON_ERROR(oled_data(0x00), TAG, "draw spacing failed");
        x += 6;
        ++text;
    }
    return ESP_OK;
}

static esp_err_t oled_apply_orientation(void)
{
    ESP_RETURN_ON_ERROR(oled_cmd(0xC0), TAG, "set scan direction failed");
    ESP_RETURN_ON_ERROR(oled_cmd(OLED_MIRROR_Y_AXIS ? 0xA1 : 0xA0), TAG, "set segment remap failed");
    return ESP_OK;
}

static esp_err_t oled_init_panel(void)
{
    const uint8_t init_cmds[] = {
        0xAE, 0x02, 0x10, 0x40, 0xB0, 0x81, 0xCF,
        0xA6, 0xA8, 0x3F, 0xAD, 0x8B, 0x33, 0xD3,
        0x00, 0xD5, 0x80, 0xD9, 0x1F, 0xDA, 0x12, 0xDB, 0x40
    };

    for (size_t i = 0; i < sizeof(init_cmds); ++i) {
        ESP_RETURN_ON_ERROR(oled_cmd(init_cmds[i]), TAG, "init command failed");
    }
    ESP_RETURN_ON_ERROR(oled_apply_orientation(), TAG, "orientation failed");
    ESP_RETURN_ON_ERROR(oled_clear(), TAG, "clear failed");
    ESP_RETURN_ON_ERROR(oled_cmd(0xAF), TAG, "display on failed");
    return ESP_OK;
}

static esp_err_t oled_i2c_init(void)
{
    if (s_i2c_ready) {
        return ESP_OK;
    }

    const i2c_master_bus_config_t bus_config = {
        .i2c_port = OLED_I2C_PORT,
        .sda_io_num = OLED_SDA_GPIO,
        .scl_io_num = OLED_SCL_GPIO,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&bus_config, &s_i2c_bus), TAG, "create I2C bus failed");
    s_i2c_ready = true;
    return ESP_OK;
}

static esp_err_t oled_attach_device(void)
{
    uint8_t address = 0;

    if (i2c_master_probe(s_i2c_bus, OLED_ADDR_PRIMARY, OLED_TIMEOUT_MS) == ESP_OK) {
        address = OLED_ADDR_PRIMARY;
    } else if (i2c_master_probe(s_i2c_bus, OLED_ADDR_SECONDARY, OLED_TIMEOUT_MS) == ESP_OK) {
        address = OLED_ADDR_SECONDARY;
    } else {
        return ESP_ERR_NOT_FOUND;
    }

    const i2c_device_config_t dev_config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = address,
        .scl_speed_hz = 400000,
    };
    ESP_RETURN_ON_ERROR(i2c_master_bus_add_device(s_i2c_bus, &dev_config, &s_oled_dev), TAG, "add OLED device failed");
    return ESP_OK;
}

static uint16_t modbus_crc16(const uint8_t *data, size_t length)
{
    uint16_t crc = 0xFFFF;

    for (size_t i = 0; i < length; ++i) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; ++bit) {
            crc = (crc & 1U) ? (uint16_t)((crc >> 1) ^ 0xA001U) : (uint16_t)(crc >> 1);
        }
    }
    return crc;
}

static esp_err_t radar_uart_init(void)
{
    const uart_config_t uart_cfg = {
        .baud_rate = RADAR_MODBUS_BAUD_RATE,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };

    ESP_RETURN_ON_ERROR(uart_driver_install(RADAR_UART_PORT, RADAR_RX_BUFFER_SIZE, 0, 0, NULL, 0), TAG, "install radar UART failed");
    ESP_RETURN_ON_ERROR(uart_param_config(RADAR_UART_PORT, &uart_cfg), TAG, "config radar UART failed");
    ESP_RETURN_ON_ERROR(uart_set_pin(RADAR_UART_PORT, RADAR_UART_TX_GPIO, RADAR_UART_RX_GPIO,
                                     UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE), TAG, "set radar UART pins failed");
    ESP_RETURN_ON_ERROR(uart_set_rx_timeout(RADAR_UART_PORT, 3), TAG, "set radar rx timeout failed");
    return ESP_OK;
}

static esp_err_t usb_serial_init(void)
{
#if !APP_USB_SERIAL_JTAG_LOG_ENABLED
    s_usb_ready = false;
    return ESP_OK;
#else
    if (usb_serial_jtag_is_driver_installed()) {
        s_usb_ready = true;
        return ESP_OK;
    }

    usb_serial_jtag_driver_config_t usb_cfg = {
        .tx_buffer_size = 512,
        .rx_buffer_size = 256,
    };
    ESP_RETURN_ON_ERROR(usb_serial_jtag_driver_install(&usb_cfg), TAG, "install usb serial jtag failed");
    s_usb_ready = true;
    return ESP_OK;
#endif
}

static esp_err_t radar_read_registers_locked(uint8_t slave_id, uint16_t start_register,
                                             uint16_t register_count, uint16_t *values,
                                             size_t values_len)
{
    uint8_t request[8];
    uint8_t response[RADAR_RX_BUFFER_SIZE];
    int response_len;
    const int expected_len = 5 + (int)register_count * 2;

    if (register_count == 0 || register_count > 16 || values == NULL || values_len < register_count) {
        return ESP_ERR_INVALID_ARG;
    }
    request[0] = slave_id;
    request[1] = 0x03;
    request[2] = (uint8_t)(start_register >> 8);
    request[3] = (uint8_t)(start_register & 0xFF);
    request[4] = (uint8_t)(register_count >> 8);
    request[5] = (uint8_t)(register_count & 0xFF);

    const uint16_t crc = modbus_crc16(request, 6);
    request[6] = (uint8_t)(crc & 0xFF);
    request[7] = (uint8_t)(crc >> 8);

    ESP_RETURN_ON_ERROR(uart_flush_input(RADAR_UART_PORT), TAG, "flush radar input failed");
    if (uart_write_bytes(RADAR_UART_PORT, request, sizeof(request)) != (int)sizeof(request)) {
        return ESP_FAIL;
    }
    ESP_RETURN_ON_ERROR(uart_wait_tx_done(RADAR_UART_PORT, pdMS_TO_TICKS(RADAR_RESPONSE_TIMEOUT_MS)), TAG, "radar tx timeout");

    response_len = uart_read_bytes(RADAR_UART_PORT, response, sizeof(response), pdMS_TO_TICKS(RADAR_RESPONSE_TIMEOUT_MS));
    if (response_len <= 0) {
        return ESP_ERR_TIMEOUT;
    }
    while (response_len < (int)sizeof(response)) {
        int more = uart_read_bytes(RADAR_UART_PORT, response + response_len,
                                   sizeof(response) - (size_t)response_len, pdMS_TO_TICKS(RADAR_INTER_FRAME_MS));
        if (more <= 0) {
            break;
        }
        response_len += more;
    }

    if (response_len != expected_len) {
        return ESP_ERR_INVALID_SIZE;
    }
    if (response[0] != slave_id || response[1] != 0x03 || response[2] != register_count * 2) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    const uint16_t response_crc = (uint16_t)response[expected_len - 2] | ((uint16_t)response[expected_len - 1] << 8);
    if (response_crc != modbus_crc16(response, expected_len - 2)) {
        return ESP_ERR_INVALID_CRC;
    }

    for (uint16_t i = 0; i < register_count; ++i) {
        const size_t offset = 3 + i * 2;
        values[i] = ((uint16_t)response[offset] << 8) | response[offset + 1];
    }
    return ESP_OK;
}

static esp_err_t radar_read_registers(uint8_t slave_id, uint16_t start_register,
                                      uint16_t register_count, uint16_t *values,
                                      size_t values_len)
{
    esp_err_t ret;

    if (xSemaphoreTake(s_bus_mutex, portMAX_DELAY) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }
    ret = radar_read_registers_locked(slave_id, start_register, register_count, values, values_len);
    xSemaphoreGive(s_bus_mutex);
    return ret;
}

static esp_err_t radar_read_distance(uint8_t slave_id, uint16_t *distance_mm)
{
    uint16_t value = 0;
    esp_err_t ret = radar_read_registers(slave_id, RADAR_REGISTER_DISTANCE, RADAR_REGISTER_COUNT, &value, 1);
    if (ret == ESP_OK && distance_mm != NULL) {
        *distance_mm = value;
    }
    return ret;
}

static esp_err_t radar_write_register(uint8_t slave_id, uint16_t reg, uint16_t value)
{
    uint8_t request[8];
    uint8_t response[8];
    int response_len;
    esp_err_t ret = ESP_OK;

    request[0] = slave_id;
    request[1] = 0x06;
    request[2] = (uint8_t)(reg >> 8);
    request[3] = (uint8_t)(reg & 0xFF);
    request[4] = (uint8_t)(value >> 8);
    request[5] = (uint8_t)(value & 0xFF);

    const uint16_t crc = modbus_crc16(request, 6);
    request[6] = (uint8_t)(crc & 0xFF);
    request[7] = (uint8_t)(crc >> 8);

    if (xSemaphoreTake(s_bus_mutex, portMAX_DELAY) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }

    ESP_GOTO_ON_ERROR(uart_flush_input(RADAR_UART_PORT), cleanup, TAG, "flush radar input failed");
    if (uart_write_bytes(RADAR_UART_PORT, request, sizeof(request)) != (int)sizeof(request)) {
        ret = ESP_FAIL;
        goto cleanup;
    }
    ESP_GOTO_ON_ERROR(uart_wait_tx_done(RADAR_UART_PORT, pdMS_TO_TICKS(RADAR_RESPONSE_TIMEOUT_MS)), cleanup, TAG, "radar tx timeout");

    response_len = uart_read_bytes(RADAR_UART_PORT, response, sizeof(response), pdMS_TO_TICKS(RADAR_RESPONSE_TIMEOUT_MS));
    if (response_len != (int)sizeof(response)) {
        ret = response_len <= 0 ? ESP_ERR_TIMEOUT : ESP_ERR_INVALID_SIZE;
        goto cleanup;
    }
    if (memcmp(response, request, 6) != 0) {
        ret = ESP_ERR_INVALID_RESPONSE;
        goto cleanup;
    }
    const uint16_t response_crc = (uint16_t)response[6] | ((uint16_t)response[7] << 8);
    if (response_crc != modbus_crc16(response, 6)) {
        ret = ESP_ERR_INVALID_CRC;
        goto cleanup;
    }

cleanup:
    xSemaphoreGive(s_bus_mutex);
    return ret;
}

static bool radar_distance_is_occupied(uint16_t distance_mm)
{
    return distance_mm >= RADAR_OCCUPIED_MIN_MM && distance_mm <= RADAR_OCCUPIED_MAX_MM;
}

static void radar_sort_queue(radar_state_t *radars, uint8_t count)
{
    for (uint8_t i = 0; i < count; ++i) {
        for (uint8_t j = i + 1; j < count; ++j) {
            if (radars[j].slave_id < radars[i].slave_id) {
                radar_state_t tmp = radars[i];
                radars[i] = radars[j];
                radars[j] = tmp;
            }
        }
    }
}

static int radar_find_index_by_address(const radar_state_t *radars, uint8_t count, uint8_t address)
{
    for (uint8_t i = 0; i < count; ++i) {
        if (radars[i].slave_id == address) {
            return i;
        }
    }
    return -1;
}

static uint8_t radar_first_gap_from_two(const radar_state_t *radars, uint8_t count)
{
    uint8_t expected = RADAR_SCAN_MIN_ADDRESS;

    for (uint8_t i = 0; i < count; ++i) {
        if (radars[i].slave_id != expected) {
            return expected;
        }
        expected++;
    }
    return 0;
}

static uint8_t radar_max_address(const radar_state_t *radars, uint8_t count)
{
    uint8_t max_address = 0;
    for (uint8_t i = 0; i < count; ++i) {
        if (radars[i].slave_id > max_address) {
            max_address = radars[i].slave_id;
        }
    }
    return max_address;
}

static void radar_publish_queue(const radar_state_t *radars, uint8_t count, const char *status)
{
    uint8_t occupied_count = 0;

    if (xSemaphoreTake(s_state_mutex, portMAX_DELAY) != pdTRUE) {
        return;
    }
    memset(s_snapshot.radars, 0, sizeof(s_snapshot.radars));
    s_snapshot.radar_count = count;
    s_snapshot.slave_id = count > 0 ? radars[0].slave_id : RADAR_DEFAULT_SLAVE_ID;
    for (uint8_t i = 0; i < count && i < RADAR_MAX_COUNT; ++i) {
        s_snapshot.radars[i] = radars[i];
        if (radars[i].online && radar_distance_is_occupied(radars[i].distance_mm)) {
            occupied_count++;
        }
    }
    s_snapshot.occupied_count = occupied_count;
    if (status != NULL) {
        copy_text(s_snapshot.modbus_text, sizeof(s_snapshot.modbus_text), status);
    }
    xSemaphoreGive(s_state_mutex);
}

static void radar_initialize_queue(void)
{
    radar_state_t radars[RADAR_MAX_COUNT] = {0};
    uint8_t count = 0;
    uint16_t distance_mm = 0;

    usb_log_line("[RADAR] scanning existing addresses %u-%u\r\n",
                 RADAR_SCAN_MIN_ADDRESS, RADAR_SCAN_MAX_ADDRESS);

    for (uint8_t address = RADAR_SCAN_MIN_ADDRESS;
         address <= RADAR_SCAN_MAX_ADDRESS && count < RADAR_MAX_COUNT;
         ++address) {
        if (radar_read_distance(address, &distance_mm) == ESP_OK) {
            radars[count].slave_id = address;
            radars[count].distance_mm = distance_mm;
            radars[count].online = true;
            radars[count].poll_ok_count = 1;
            copy_text(radars[count].status_text, sizeof(radars[count].status_text), "OK");
            count++;
            usb_log_line("[RADAR] found id=%u distance=%u mm\r\n", address, distance_mm);
        }
    }

    if (radar_read_distance(RADAR_NEW_DEVICE_ADDRESS, &distance_mm) == ESP_OK) {
        uint8_t target = radar_max_address(radars, count);
        target = target >= RADAR_SCAN_MIN_ADDRESS ? target + 1 : RADAR_SCAN_MIN_ADDRESS;
        if (target <= RADAR_SCAN_MAX_ADDRESS && count < RADAR_MAX_COUNT) {
            usb_log_line("[RADAR] new id=01 detected, programming to queue tail id=%u\r\n", target);
            if (radar_write_register(RADAR_NEW_DEVICE_ADDRESS, RADAR_REGISTER_ADDRESS, target) == ESP_OK) {
                vTaskDelay(pdMS_TO_TICKS(300));
                if (radar_read_distance(target, &distance_mm) == ESP_OK) {
                    radars[count].slave_id = target;
                    radars[count].distance_mm = distance_mm;
                    radars[count].online = true;
                    radars[count].poll_ok_count = 1;
                    copy_text(radars[count].status_text, sizeof(radars[count].status_text), "OK");
                    count++;
                }
            } else {
                usb_log_line("[RADAR] id=01 address programming failed\r\n");
            }
        }
    }

    radar_sort_queue(radars, count);
    for (uint8_t guard = 0; guard < RADAR_MAX_COUNT; ++guard) {
        uint8_t gap = radar_first_gap_from_two(radars, count);
        uint8_t max_address = radar_max_address(radars, count);
        int max_index = radar_find_index_by_address(radars, count, max_address);

        if (gap == 0 || max_address <= gap || max_index < 0) {
            break;
        }

        usb_log_line("[RADAR] compacting queue: id=%u -> id=%u\r\n", max_address, gap);
        if (radar_write_register(max_address, RADAR_REGISTER_ADDRESS, gap) != ESP_OK) {
            usb_log_line("[RADAR] compacting write failed id=%u -> id=%u\r\n", max_address, gap);
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(300));
        if (radar_read_distance(gap, &distance_mm) != ESP_OK) {
            usb_log_line("[RADAR] compacting verify failed id=%u\r\n", gap);
            break;
        }
        radars[max_index].slave_id = gap;
        radars[max_index].distance_mm = distance_mm;
        radars[max_index].online = true;
        copy_text(radars[max_index].status_text, sizeof(radars[max_index].status_text), "OK");
        radar_sort_queue(radars, count);
    }

    radar_publish_queue(radars, count, count > 0 ? "READY" : "NONE");
    usb_log_line("[RADAR] queue ready, count=%u\r\n", count);
}

static void render_display_task(void *arg)
{
    (void)arg;

    while (true) {
        radar_oled_snapshot_t snapshot;
        char line0[22];
        char line1[22];
        char line2[22];
        char line3[22];

        radar_oled_get_snapshot(&snapshot);
        snprintf(line0, sizeof(line0), "RADAR %s", snapshot.wifi_connected ? "WIFI" : "OFF");
        snprintf(line1, sizeof(line1), "ID%02u %05u MM", snapshot.slave_id, snapshot.distance_mm);
        snprintf(line2, sizeof(line2), "OCC %u/%u ID%02u", snapshot.occupied_count, snapshot.radar_count, snapshot.slave_id);
        snprintf(line3, sizeof(line3), "APP %.13s", snapshot.app_version);

        if (s_oled_ready) {
            oled_clear();
            oled_draw_text(0, 0, line0);
            oled_draw_text(0, 2, line1);
            oled_draw_text(0, 4, line2);
            oled_draw_text(0, 6, line3);
        }
        vTaskDelay(pdMS_TO_TICKS(250));
    }
}

static void radar_poll_task(void *arg)
{
    (void)arg;

    radar_initialize_queue();

    while (true) {
        radar_state_t queue[RADAR_MAX_COUNT] = {0};
        uint8_t count = 0;

        radar_oled_snapshot_t snapshot;
        radar_oled_get_snapshot(&snapshot);
        count = snapshot.radar_count;
        for (uint8_t i = 0; i < count && i < RADAR_MAX_COUNT; ++i) {
            queue[i] = snapshot.radars[i];
        }

        if (count == 0) {
            vTaskDelay(pdMS_TO_TICKS(5000));
            radar_initialize_queue();
            continue;
        }

        for (uint8_t i = 0; i < count; ++i) {
            uint16_t distance_mm = 0;
            esp_err_t ret = radar_read_distance(queue[i].slave_id, &distance_mm);

            if (ret == ESP_OK) {
                queue[i].distance_mm = distance_mm;
                queue[i].online = true;
                queue[i].poll_ok_count++;
                copy_text(queue[i].status_text, sizeof(queue[i].status_text), "OK");
            } else {
                queue[i].online = false;
                queue[i].poll_error_count++;
                if (ret == ESP_ERR_TIMEOUT) {
                    copy_text(queue[i].status_text, sizeof(queue[i].status_text), "TIME");
                } else if (ret == ESP_ERR_INVALID_CRC) {
                    copy_text(queue[i].status_text, sizeof(queue[i].status_text), "CRC");
                } else {
                    copy_text(queue[i].status_text, sizeof(queue[i].status_text), "ERR");
                }
            }

            if (xSemaphoreTake(s_state_mutex, portMAX_DELAY) == pdTRUE) {
                uint8_t occupied_count = 0;
                s_snapshot.radars[i] = queue[i];
                s_snapshot.slave_id = queue[i].slave_id;
                s_snapshot.distance_mm = queue[i].distance_mm;
                s_snapshot.poll_ok_count = 0;
                s_snapshot.poll_error_count = 0;
                for (uint8_t j = 0; j < count; ++j) {
                    s_snapshot.poll_ok_count += s_snapshot.radars[j].poll_ok_count;
                    s_snapshot.poll_error_count += s_snapshot.radars[j].poll_error_count;
                    if (s_snapshot.radars[j].online && radar_distance_is_occupied(s_snapshot.radars[j].distance_mm)) {
                        occupied_count++;
                    }
                }
                s_snapshot.occupied_count = occupied_count;
                copy_text(s_snapshot.modbus_text, sizeof(s_snapshot.modbus_text), queue[i].status_text);
                xSemaphoreGive(s_state_mutex);
            }

            if (ret == ESP_OK) {
                usb_log_line("[RADAR] id=%u distance=%u mm ok=%" PRIu32 " err=%" PRIu32 "\r\n",
                             queue[i].slave_id, distance_mm, queue[i].poll_ok_count, queue[i].poll_error_count);
            } else {
                usb_log_line("[RADAR] id=%u poll failed status=%s ok=%" PRIu32 " err=%" PRIu32 "\r\n",
                             queue[i].slave_id, queue[i].status_text, queue[i].poll_ok_count, queue[i].poll_error_count);
            }

            vTaskDelay(pdMS_TO_TICKS(RADAR_POLL_INTERVAL_MS));
        }
    }
}

esp_err_t radar_oled_app_init(void)
{
    s_state_mutex = xSemaphoreCreateMutex();
    if (s_state_mutex == NULL) {
        return ESP_ERR_NO_MEM;
    }
    s_bus_mutex = xSemaphoreCreateMutex();
    if (s_bus_mutex == NULL) {
        vSemaphoreDelete(s_state_mutex);
        s_state_mutex = NULL;
        return ESP_ERR_NO_MEM;
    }

    copy_text(s_snapshot.wifi_text, sizeof(s_snapshot.wifi_text), "BOOT");
    copy_text(s_snapshot.ota_text, sizeof(s_snapshot.ota_text), "IDLE");
    copy_text(s_snapshot.modbus_text, sizeof(s_snapshot.modbus_text), "BOOT");
    copy_text(s_snapshot.app_version, sizeof(s_snapshot.app_version), "unknown");

    ESP_RETURN_ON_ERROR(usb_serial_init(), TAG, "usb serial init failed");
    ESP_RETURN_ON_ERROR(oled_i2c_init(), TAG, "oled i2c init failed");
    if (oled_attach_device() == ESP_OK) {
        ESP_RETURN_ON_ERROR(oled_init_panel(), TAG, "oled panel init failed");
        s_oled_ready = true;
    } else {
        ESP_LOGW(TAG, "OLED not detected");
    }
    ESP_RETURN_ON_ERROR(radar_uart_init(), TAG, "radar uart init failed");
    return ESP_OK;
}

esp_err_t radar_oled_app_start(void)
{
    if (xTaskCreate(render_display_task, "render_display", 4096, NULL, 4, NULL) != pdPASS) {
        return ESP_FAIL;
    }
    if (xTaskCreate(radar_poll_task, "radar_poll", 4096, NULL, 5, NULL) != pdPASS) {
        return ESP_FAIL;
    }
    return ESP_OK;
}

void radar_oled_set_wifi_status(bool connected, const char *text)
{
    if (xSemaphoreTake(s_state_mutex, portMAX_DELAY) == pdTRUE) {
        s_snapshot.wifi_connected = connected;
        copy_text(s_snapshot.wifi_text, sizeof(s_snapshot.wifi_text), text);
        xSemaphoreGive(s_state_mutex);
    }
}

void radar_oled_set_ota_status(bool available, bool in_progress, const char *text)
{
    if (xSemaphoreTake(s_state_mutex, portMAX_DELAY) == pdTRUE) {
        s_snapshot.ota_available = available;
        s_snapshot.ota_in_progress = in_progress;
        copy_text(s_snapshot.ota_text, sizeof(s_snapshot.ota_text), text);
        xSemaphoreGive(s_state_mutex);
    }
}

void radar_oled_set_app_version(const char *version)
{
    if (xSemaphoreTake(s_state_mutex, portMAX_DELAY) == pdTRUE) {
        copy_text(s_snapshot.app_version, sizeof(s_snapshot.app_version), version);
        xSemaphoreGive(s_state_mutex);
    }
}

void radar_oled_get_snapshot(radar_oled_snapshot_t *snapshot)
{
    if (snapshot == NULL) {
        return;
    }
    if (xSemaphoreTake(s_state_mutex, portMAX_DELAY) == pdTRUE) {
        *snapshot = s_snapshot;
        xSemaphoreGive(s_state_mutex);
    }
}

esp_err_t radar_oled_execute_cloud_command(const char *action,
                                           uint8_t address,
                                           uint16_t start_register,
                                           uint16_t quantity,
                                           uint16_t write_register,
                                           uint16_t value,
                                           uint8_t target_address,
                                           char *result_json,
                                           size_t result_len)
{
    esp_err_t ret = ESP_ERR_NOT_SUPPORTED;

    if (result_json != NULL && result_len > 0) {
        result_json[0] = '\0';
    }
    if (action == NULL || address == 0) {
        snprintf(result_json, result_len, "{\"error\":\"invalid action or address\"}");
        return ESP_ERR_INVALID_ARG;
    }

    if (strcmp(action, "read_distance") == 0) {
        uint16_t distance_mm = 0;
        ret = radar_read_distance(address, &distance_mm);
        if (ret == ESP_OK) {
            snprintf(result_json, result_len,
                     "{\"address\":%u,\"distanceMm\":%u,\"online\":true}",
                     address, distance_mm);
        }
        return ret;
    }

    if (strcmp(action, "read_registers") == 0) {
        uint16_t values[8] = {0};
        quantity = quantity == 0 ? 1 : quantity;
        if (quantity > sizeof(values) / sizeof(values[0])) {
            quantity = sizeof(values) / sizeof(values[0]);
        }
        ret = radar_read_registers(address, start_register, quantity, values, sizeof(values) / sizeof(values[0]));
        if (ret == ESP_OK && result_json != NULL && result_len > 0) {
            size_t used = snprintf(result_json, result_len,
                                   "{\"address\":%u,\"startRegister\":%u,\"quantity\":%u,\"values\":[",
                                   address, start_register, quantity);
            for (uint16_t i = 0; i < quantity && used < result_len; ++i) {
                used += snprintf(result_json + used, result_len - used, "%s%u", i == 0 ? "" : ",", values[i]);
            }
            if (used < result_len) {
                snprintf(result_json + used, result_len - used, "]}");
            }
        }
        return ret;
    }

    if (strcmp(action, "write_register") == 0) {
        ret = radar_write_register(address, write_register, value);
        if (ret == ESP_OK) {
            snprintf(result_json, result_len,
                     "{\"address\":%u,\"register\":%u,\"value\":%u}",
                     address, write_register, value);
        }
        return ret;
    }

    if (strcmp(action, "change_address") == 0) {
        if (target_address < 1 || target_address > 254 || target_address == address) {
            snprintf(result_json, result_len, "{\"error\":\"invalid target address\"}");
            return ESP_ERR_INVALID_ARG;
        }
        ret = radar_write_register(address, RADAR_REGISTER_ADDRESS, target_address);
        if (ret == ESP_OK) {
            vTaskDelay(pdMS_TO_TICKS(300));
            radar_initialize_queue();
            snprintf(result_json, result_len,
                     "{\"sourceAddress\":%u,\"targetAddress\":%u}",
                     address, target_address);
        }
        return ret;
    }

    snprintf(result_json, result_len, "{\"error\":\"unsupported action\"}");
    return ret;
}
