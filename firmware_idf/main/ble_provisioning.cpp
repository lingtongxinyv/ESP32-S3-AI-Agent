#include "ble_provisioning.h"

#include <cstdio>
#include <cstring>
#include "app_config.h"
#include "cJSON.h"
#include "esp_log.h"
#include "host/ble_hs.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "os/os_mbuf.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

namespace {
constexpr char kTag[] = "ble_prov";
constexpr size_t kMaxFrame = 1024;
constexpr uint16_t kMaxWrite = 512;

// 7f1e0001-3c4a-4bd8-9f1e-6a1b2c3d4e5f，RX 写、TX 通知。
ble_uuid128_t kServiceUuid = BLE_UUID128_INIT(
    0x5f, 0x4e, 0x3d, 0x2c, 0x1b, 0x6a, 0x1e, 0x9f,
    0xd8, 0x4b, 0x4a, 0x3c, 0x01, 0x00, 0x1e, 0x7f);
ble_uuid128_t kRxUuid = BLE_UUID128_INIT(
    0x5f, 0x4e, 0x3d, 0x2c, 0x1b, 0x6a, 0x1e, 0x9f,
    0xd8, 0x4b, 0x4a, 0x3c, 0x02, 0x00, 0x1e, 0x7f);
ble_uuid128_t kTxUuid = BLE_UUID128_INIT(
    0x5f, 0x4e, 0x3d, 0x2c, 0x1b, 0x6a, 0x1e, 0x9f,
    0xd8, 0x4b, 0x4a, 0x3c, 0x03, 0x00, 0x1e, 0x7f);

ble_gatt_chr_def g_chars[3] = {};
ble_gatt_svc_def g_services[2] = {};

uint16_t g_tx_handle = 0;
uint16_t g_conn = BLE_HS_CONN_HANDLE_NONE;
uint8_t g_own_addr_type = 0;
QueueHandle_t g_queue = nullptr;
bool g_initialized = false;
bool g_advertising = false;
char g_device_id[40] = {};
char g_adv_name[24] = {};
std::string g_rx;

int gap_event(ble_gap_event *event, void *arg);

bool valid_server_url(const std::string &url)
{
    return (url.rfind("https://", 0) == 0 || url.rfind("http://", 0) == 0 ||
            url.rfind("wss://", 0) == 0 || url.rfind("ws://", 0) == 0) &&
           url.find_first_of("?#") == std::string::npos;
}

void send_json(const std::string &json)
{
    if (g_conn == BLE_HS_CONN_HANDLE_NONE || json.empty() || json.size() > kMaxFrame) return;
    std::string frame;
    frame.reserve(json.size() + 2);
    frame.push_back(static_cast<char>(json.size() & 0xff));
    frame.push_back(static_cast<char>((json.size() >> 8) & 0xff));
    frame += json;
    os_mbuf *om = ble_hs_mbuf_from_flat(frame.data(), static_cast<uint16_t>(frame.size()));
    if (!om) return;
    if (ble_gatts_notify_custom(g_conn, g_tx_handle, om) != 0) {
        ESP_LOGW(kTag, "notify failed");
    }
}

void send_object(cJSON *root)
{
    if (!root) {
        send_json("{\"ok\":false,\"error\":\"no_mem\"}");
        return;
    }
    char *json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (!json) {
        send_json("{\"ok\":false,\"error\":\"no_mem\"}");
        return;
    }
    send_json(json);
    cJSON_free(json);
}

void send_error(const char *reason)
{
    cJSON *root = cJSON_CreateObject();
    if (!root) return;
    cJSON_AddBoolToObject(root, "ok", false);
    cJSON_AddStringToObject(root, "error", reason);
    send_object(root);
}

bool queue_config(const cJSON *ssid, const cJSON *password, const cJSON *server, const cJSON *token)
{
    const bool valid = cJSON_IsString(ssid) && cJSON_IsString(password) &&
                       cJSON_IsString(server) && cJSON_IsString(token) &&
                       ssid->valuestring && password->valuestring &&
                       server->valuestring && token->valuestring &&
                       std::strlen(ssid->valuestring) > 0 && std::strlen(ssid->valuestring) <= 32 &&
                       std::strlen(password->valuestring) <= 64 &&
                       std::strlen(token->valuestring) > 0 &&
                       valid_server_url(server->valuestring);
    if (!valid) return false;
    if (!g_queue) return false;

    auto *pending = new BlePendingConfig();
    pending->config.device_id = g_device_id;
    pending->config.wifi_ssid = ssid->valuestring;
    pending->config.wifi_password = password->valuestring;
    pending->config.server_url = normalized_server_url(server->valuestring);
    pending->pairing_token = token->valuestring;
    if (xQueueSend(g_queue, &pending, 0) != pdPASS) {
        delete pending;
        return false;
    }
    return true;
}

void handle_command(const std::string &body)
{
    cJSON *root = cJSON_ParseWithLength(body.data(), body.size());
    if (!root) {
        send_error("bad_json");
        return;
    }
    const cJSON *cmd = cJSON_GetObjectItemCaseSensitive(root, "cmd");
    if (!cJSON_IsString(cmd) || !cmd->valuestring) {
        cJSON_Delete(root);
        send_error("bad_cmd");
        return;
    }

    if (std::strcmp(cmd->valuestring, "hello") == 0) {
        cJSON *reply = cJSON_CreateObject();
        if (!reply) {
            cJSON_Delete(root);
            send_error("no_mem");
            return;
        }
        cJSON_AddBoolToObject(reply, "ok", true);
        cJSON_AddStringToObject(reply, "device_id", g_device_id);
        cJSON_AddStringToObject(reply, "fw_version", config::kFirmwareVersion);
        cJSON_AddNumberToObject(reply, "protocol", config::kProtocolVersion);
        send_object(reply);
    } else if (std::strcmp(cmd->valuestring, "wifi") == 0) {
        const bool accepted = queue_config(
            cJSON_GetObjectItemCaseSensitive(root, "wifi_ssid"),
            cJSON_GetObjectItemCaseSensitive(root, "wifi_password"),
            cJSON_GetObjectItemCaseSensitive(root, "server_url"),
            cJSON_GetObjectItemCaseSensitive(root, "pairing_token"));
        if (accepted) {
            send_json("{\"ok\":true}");
            ESP_LOGI(kTag, "wifi credentials received from BLE");
        } else {
            send_error("bad_config");
        }
    } else if (std::strcmp(cmd->valuestring, "status") == 0) {
        cJSON *reply = cJSON_CreateObject();
        if (!reply) {
            cJSON_Delete(root);
            send_error("no_mem");
            return;
        }
        cJSON_AddBoolToObject(reply, "ok", true);
        cJSON_AddStringToObject(reply, "device_id", g_device_id);
        send_object(reply);
    } else {
        send_error("unsupported_cmd");
    }
    cJSON_Delete(root);
}

void ingest(const uint8_t *data, uint16_t length)
{
    g_rx.append(reinterpret_cast<const char *>(data), length);
    while (g_rx.size() >= 2) {
        const uint16_t need = static_cast<uint8_t>(g_rx[0]) |
                              (static_cast<uint16_t>(static_cast<uint8_t>(g_rx[1])) << 8);
        if (need == 0 || need > kMaxFrame) {
            g_rx.clear();
            return;
        }
        if (g_rx.size() < static_cast<size_t>(need) + 2) return;
        std::string body = g_rx.substr(2, need);
        g_rx.erase(0, need + 2);
        handle_command(body);
    }
}

int rx_access(uint16_t conn_handle, uint16_t attr_handle, ble_gatt_access_ctxt *ctxt, void *arg)
{
    (void)conn_handle;
    (void)attr_handle;
    (void)arg;
    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR) return BLE_ATT_ERR_UNLIKELY;
    const uint16_t length = OS_MBUF_PKTLEN(ctxt->om);
    if (length == 0 || length > kMaxWrite) return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    uint8_t buffer[kMaxWrite];
    if (os_mbuf_copydata(ctxt->om, 0, length, buffer) != 0) return BLE_ATT_ERR_UNLIKELY;
    ingest(buffer, length);
    return 0;
}

int tx_access(uint16_t conn_handle, uint16_t attr_handle, ble_gatt_access_ctxt *ctxt, void *arg)
{
    (void)conn_handle;
    (void)attr_handle;
    (void)ctxt;
    (void)arg;
    return 0;
}

void start_advertising()
{
    if (!g_initialized) return;

    ble_hs_adv_fields fields = {};
    fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.uuids128 = &kServiceUuid;
    fields.num_uuids128 = 1;
    fields.uuids128_is_complete = 1;
    if (ble_gap_adv_set_fields(&fields) != 0) {
        ESP_LOGW(kTag, "failed to set adv fields");
        return;
    }

    ble_hs_adv_fields response = {};
    response.name = reinterpret_cast<const uint8_t *>(g_adv_name);
    response.name_len = static_cast<uint8_t>(std::strlen(g_adv_name));
    response.name_is_complete = 1;
    if (ble_gap_adv_rsp_set_fields(&response) != 0) {
        ESP_LOGW(kTag, "failed to set scan response");
    }

    ble_gap_adv_params params = {};
    params.conn_mode = BLE_GAP_CONN_MODE_UND;
    params.disc_mode = BLE_GAP_DISC_MODE_GEN;
    if (ble_gap_adv_start(g_own_addr_type, nullptr, BLE_HS_FOREVER, &params, gap_event, nullptr) != 0) {
        ESP_LOGW(kTag, "failed to start advertising");
        return;
    }
    g_advertising = true;
    ESP_LOGI(kTag, "advertising as %s", g_adv_name);
}

void host_task(void *param)
{
    (void)param;
    nimble_port_run();
    nimble_port_freertos_deinit();
}

void on_sync()
{
    ble_hs_util_ensure_addr(0);
    ble_hs_id_infer_auto(0, &g_own_addr_type);
    start_advertising();
}

void on_reset(int reason)
{
    ESP_LOGW(kTag, "nimble host reset, reason=%d", reason);
}

int gap_event(ble_gap_event *event, void *arg)
{
    (void)arg;
    switch (event->type) {
        case BLE_GAP_EVENT_CONNECT:
            if (event->connect.status == 0) {
                g_conn = event->connect.conn_handle;
                g_rx.clear();
                ESP_LOGI(kTag, "phone connected");
            } else {
                start_advertising();
            }
            break;
        case BLE_GAP_EVENT_DISCONNECT:
            g_conn = BLE_HS_CONN_HANDLE_NONE;
            g_rx.clear();
            ESP_LOGI(kTag, "phone disconnected");
            if (g_advertising) start_advertising();
            break;
        case BLE_GAP_EVENT_ADV_COMPLETE:
            g_advertising = false;
            if (g_conn == BLE_HS_CONN_HANDLE_NONE) start_advertising();
            break;
        default:
            break;
    }
    return 0;
}

void build_definitions()
{
    g_chars[0].uuid = &kRxUuid.u;
    g_chars[0].access_cb = rx_access;
    g_chars[0].flags = BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_NO_RSP;
    g_chars[1].uuid = &kTxUuid.u;
    g_chars[1].access_cb = tx_access;
    g_chars[1].flags = BLE_GATT_CHR_F_NOTIFY;
    g_chars[1].val_handle = &g_tx_handle;
    g_chars[2].uuid = nullptr;

    g_services[0].type = BLE_GATT_SVC_TYPE_PRIMARY;
    g_services[0].uuid = &kServiceUuid.u;
    g_services[0].characteristics = g_chars;
    g_services[1].type = BLE_GATT_SVC_TYPE_END;
}
} // namespace

esp_err_t BleProvisioning::start(const std::string &device_id)
{
    std::snprintf(g_device_id, sizeof(g_device_id), "%s", device_id.c_str());
    const std::string suffix = device_id.size() >= 4 ? device_id.substr(device_id.size() - 4) : "0000";
    std::snprintf(g_adv_name, sizeof(g_adv_name), "Agent-%s", suffix.c_str());

    if (!g_queue) {
        g_queue = xQueueCreate(2, sizeof(BlePendingConfig *));
    }
    if (!g_queue) return ESP_ERR_NO_MEM;

    if (g_initialized) {
        if (g_conn == BLE_HS_CONN_HANDLE_NONE) start_advertising();
        return ESP_OK;
    }

    esp_err_t err = nimble_port_init();
    if (err != ESP_OK) {
        ESP_LOGE(kTag, "nimble init failed: %s", esp_err_to_name(err));
        return err;
    }

    ble_hs_cfg.sync_cb = on_sync;
    ble_hs_cfg.reset_cb = on_reset;
    ble_svc_gap_init();
    ble_svc_gatt_init();
    build_definitions();
    if (ble_gatts_count_cfg(g_services) != 0 || ble_gatts_add_svcs(g_services) != 0) {
        ESP_LOGE(kTag, "gatt service registration failed");
        return ESP_FAIL;
    }
    ble_svc_gap_device_name_set(g_adv_name);
    g_initialized = true;
    nimble_port_freertos_init(host_task);
    return ESP_OK;
}

void BleProvisioning::stop()
{
    if (!g_initialized) return;
    if (g_conn != BLE_HS_CONN_HANDLE_NONE) {
        ble_gap_terminate(g_conn, BLE_ERR_REM_USER_CONN_TERM);
        g_conn = BLE_HS_CONN_HANDLE_NONE;
    }
    if (g_advertising) {
        ble_gap_adv_stop();
        g_advertising = false;
    }
}

bool BleProvisioning::take_pending(BlePendingConfig &out)
{
    if (!g_queue) return false;
    BlePendingConfig *item = nullptr;
    if (xQueueReceive(g_queue, &item, 0) != pdPASS || !item) return false;
    out = *item;
    delete item;
    return true;
}

void BleProvisioning::notify_state(const char *state, const char *message)
{
    cJSON *root = cJSON_CreateObject();
    if (!root) return;
    cJSON_AddStringToObject(root, "event", "state");
    cJSON_AddStringToObject(root, "state", state ? state : "");
    if (message && message[0]) cJSON_AddStringToObject(root, "message", message);
    send_object(root);
}
