idf_component_register(
    SRCS
        "main.cpp"
        "app_control.cpp"
        "audio_engine.cpp"
        "ble_provisioning.cpp"
        "device_config.cpp"
        "display.cpp"
        "network.cpp"
        "ota_manager.cpp"
        "provisioning.cpp"
        "wake_word_detector.cpp"
    INCLUDE_DIRS "."
    REQUIRES
        app_update
        bt
        driver
        esp_driver_gpio
        esp_driver_i2c
        esp_driver_i2s
        esp_driver_uart
        esp_event
        esp_http_client
        esp_https_ota
        esp_lcd
        esp_netif
        esp_timer
        esp_wifi
        esp-tls
        espressif__cjson
        mbedtls
        nvs_flash
        espressif__esp_websocket_client
        espressif__esp-sr
)

target_compile_options(${COMPONENT_LIB} PRIVATE -std=gnu++17 -Wall -Wextra -Wno-missing-field-initializers)
