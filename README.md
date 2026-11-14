# ESP32-S3 AI Agent

一套从 **ESP32-S3 固件、自建服务端到 Android 用户端和桌面管理端** 的开源语音机器人工程。设备通过公网 HTTPS/WSS 连接服务器，支持中文离线唤醒、流式语音对话、OLED 情绪动画、BLE 配网与改网、账号隔离和 OTA 升级。

当前快照：**2026-11-15**；固件 **1.0.13**；Android **0.2.0**；设备协议 **v1**。

[开发历程与成果](docs/development-history.md) · [第三方声明](THIRD_PARTY_NOTICES.md) · [Apache-2.0 许可证](LICENSE)

## 已实现

- **中文离线唤醒**：ESP-SR MultiNet7，自定义中文唤醒短语，识别后进入人工对话。
- **完整语音链路**：16 kHz 录音、VAD 判停、ASR、LLM、TTS、双 I2S 播放。
- **流式与稳定性处理**：PSRAM 下行帧池、顺序播放、短尾帧补齐、空 ASR 降级回复、情绪标记跨分片过滤。
- **OLED 交互**：八种本地情绪动画；自动表情与手动表情分离；语音状态不整页覆盖表情。
- **BLE 配网与授权改网**：手机写入 WiFi 和服务器配置；改网保留设备 ID、设备令牌与账号归属。
- **账号和权限**：管理员与普通用户隔离；可选的小綾路由、长期记忆和 `/task` 二次确认。
- **OTA**：版本清单、定向发布、手机触发、双应用分区、启动自检和失败回滚。
- **多终端**：Android 用户端、PySide6 管理端、WebSocket 虚拟设备，以及归档的 MicroPython 原型。

## 工程结构

| 目录 | 技术栈 | 说明 |
| --- | --- | --- |
| [`server/`](server/) | Python、FastAPI、SQLAlchemy、Alembic | 账号、设备、语音会话、配置转发、记忆、固件与 OTA API |
| [`android/`](android/) | Kotlin、Jetpack Compose、Room、OkHttp、BLE | 用户登录、机器人绑定、配置、改网、对话与远程 OTA |
| [`desktop/`](desktop/) | Python、PySide6 | 设备、用户、会话和固件管理 |
| [`firmware_idf/`](firmware_idf/) | ESP-IDF 6.1、C++17、ESP-SR | 当前正式固件与 1.0.13 验收镜像 |
| [`tools/`](tools/) | Python、WebSockets | 无硬件语音联调虚拟设备 |
| [`单片机/`](单片机/) | MicroPython、AIOHTTP | 早期教学、硬件验证与 L7.5 原型，**不是当前主固件** |

```text
Android / Desktop
       │ HTTPS
       ▼
FastAPI Server ─────► ASR / OpenAI 兼容 LLM / TTS
       ▲                    或可选小綾上游
       │ WSS + JSON + 二进制 PCM 帧
       ▼
ESP32-S3 ───────────► 麦克风 / 扬声器 / OLED / BLE / MultiNet7
```

> 小綾/dsh-suite 是可选外部上游，不包含在本仓库中。没有该服务时，可使用 `echo` 做协议联调，或使用 `cloud` 接入兼容 ASR、LLM 和 TTS 服务。

## 硬件

当前固件针对 **ESP32-S3-N16R8（16 MB Flash、8 MB Octal PSRAM）**，使用 SSD1306 OLED、ICS43434 麦克风和 MAX98357A 功放。

| 模块 | 信号 | GPIO |
| --- | --- | --- |
| OLED | SDA / SCL | 11 / 12 |
| 麦克风 | BCLK / WS / DATA | 5 / 6 / 7 |
| 功放 | BCLK / WS / DATA | 15 / 16 / 17 |
| 对话按键 | BOOT | 0 |

引脚和音频参数集中在 [`firmware_idf/main/app_config.h`](firmware_idf/main/app_config.h)。不同开发板请先核对原理图和供电，不要直接照搬。

## 快速开始

### 1. 服务端

要求 Python 3.10+。

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`：

- 将 `DATABASE_URL`、`FIRMWARE_DIR` 改为本机可写的绝对路径。
- 协议联调可保留 `DIALOGUE_PROVIDER=echo`，无需云密钥。
- 真实对话使用 `DIALOGUE_PROVIDER=cloud`，填写 `SILICONFLOW_API_KEY`；也可另配 `OPENAI_BASE_URL`、`OPENAI_API_KEY` 和 `LLM_MODEL`。
- 只有接入可选小綾上游时才需要 `XIAOLING_*` 配置。

启动前显式加载环境变量，并创建首个管理员：

```bash
set -a
source .env
set +a
python manage.py create-user admin --admin
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8781 --no-access-log
```

服务不开放公开注册；其他账号也由管理命令或管理端创建。公网部署必须启用 HTTPS/WSS，可参考 [`server/deploy/`](server/deploy/) 的 nginx 与 systemd 示例。WebSocket 访问日志应保持关闭，避免设备令牌进入日志。

### 2. Android 用户端

要求 JDK 17、Android SDK 36。

```bash
cd android
./gradlew assembleDebug
```

Debug APK 位于 `app/build/outputs/apk/debug/app-debug.apk`。默认服务器地址可在应用设置中修改；自建部署也可修改 `SessionStore.kt` 后重新构建。

**开源版本不预置任何 API Key。** 首次使用时在“设置 → 服务商配置”填写自己的 LLM、ASR、TTS 凭据。“套用默认服务商”只填写服务地址和模型。配置在本机加密保存，推送时由服务器内存转发给设备，不写入服务端数据库。

### 3. 桌面管理端

要求 Python 3.10+。

```bash
cd desktop
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
esp32-s3-ai-agent
```

开发依赖与测试：

```bash
pip install -e '.[dev]'
pytest
```

### 4. ESP32-S3 固件

要求 ESP-IDF **6.1.x**。依赖及版本锁定见 `main/idf_component.yml` 和 `dependencies.lock`。

```bash
source /path/to/esp-idf/export.sh
cd firmware_idf
idf.py set-target esp32s3
idf.py build
```

首次使用，或从旧分区布局升级到含 MultiNet7 模型的版本时，需要完整烧录应用、分区表和模型。仓库保留了与真机验收一致的正式产物：

| 偏移 | 文件 |
| --- | --- |
| `0x0` | `artifacts/bootloader.bin` |
| `0x8000` | `artifacts/partition-table.bin` |
| `0xF000` | `artifacts/ota_data_initial.bin` |
| `0x20000` | `artifacts/esp32_s3_ai_agent_v1.0.13.bin` |
| `0xD40000` | `artifacts/srmodels.bin` |

```bash
cd firmware_idf
esptool.py --chip esp32s3 --port /dev/ttyUSB0 --baud 460800 write_flash \
  0x0 artifacts/bootloader.bin \
  0x8000 artifacts/partition-table.bin \
  0xF000 artifacts/ota_data_initial.bin \
  0x20000 artifacts/esp32_s3_ai_agent_v1.0.13.bin \
  0xD40000 artifacts/srmodels.bin
```

烧录前先备份 Flash 并停止串口监视。除非明确要清除设备身份和配网，否则不要整片擦除 NVS。完成首次模型分区迁移后，应用迭代可使用 `idf.py -p /dev/ttyUSB0 app-flash monitor`。

正式 1.0.13 应用镜像大小为 **1,610,208 字节**，SHA256：

```text
f4b34401796e3cf7e98bb1a5c5724a192248af08875ae5474663d991e97017dd
```

### 5. 虚拟设备联调

服务端运行后，使用数据库中的设备 ID 和设备令牌：

```bash
DEVICE_TOKEN='你的设备令牌' python tools/virtual_device.py \
  --url ws://127.0.0.1:8781/ws/v1/device \
  --device-id your-device-id \
  --input input.wav \
  --expect-echo
```

输入必须是 16 kHz、单声道、s16le WAV 或 PCM。不要把真实设备令牌写进命令历史或公开日志。

## 验证

```bash
cd server && .venv/bin/python -m pytest
cd desktop && .venv/bin/python -m pytest
cd android && ./gradlew testDebugUnitTest assembleDebug
cd firmware_idf && idf.py build
```

本次开源发布复验：服务端 **95 项测试通过**，桌面端 **12 项测试通过**；Android 构建和 ESP-IDF 完整构建通过。Android 当前没有本地单元测试源集，`testDebugUnitTest` 显示 `NO-SOURCE`。1.0.13 正式镜像已真机验证唤醒、管理员语音对话、OLED 自动表情和完整尾帧播放。源码构建成功与真机验收是不同证据，后续修改仍需重新验证。

## 当前边界

- OTA 尚未实现 semver 防降级；不要重新发布旧版本。
- 改网错误密码、服务不可达后的恢复，以及唤醒词持久化/噪声边界仍需更多真机回归。
- `/task` 创建与确认执行尚未完成真机端到端验收。
- TTS 首包等待、偶发外部网络断连、情绪语义映射仍可优化。
- 手机脱敏日志尚未实现；桌面端仍有已知显示问题；固件保留一个未初始化 `ssid` 构建警告待清理。
- MicroPython 目录仅作原型和硬件排障参考，主开发架构是 `firmware_idf/`。

## 安全与许可

真实 `.env`、数据库、用户语音、设备令牌、签名文件、构建目录和原始运维日志均被忽略，不应提交。若凭据曾进入源码、APK 或日志，应立即轮换，不能仅依赖后续删除。

本项目原创代码采用 [Apache-2.0](LICENSE)。第三方组件、模型和素材遵循各自许可，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
