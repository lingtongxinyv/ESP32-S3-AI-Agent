# 唯一需要改的配置文件：WiFi / 网关 / 引脚 / 音频参数
# 改完直接保存到设备根目录即可

# ---------------- 网络 ----------------
WIFI_SSID = "你的WiFi名"
WIFI_PASS = "你的WiFi密码"

# 网关地址：内网调试用 ws://；上线换 wss:// 并配好证书
# 例：ws://192.168.1.10:8765/ws/audio?dev=robot01
GATEWAY = "ws://192.168.1.10:8765/ws/audio?dev=robot01"
DEV_ID = "robot01"
FW_VER = "1.0.0"

# ---------------- OLED (SSD1306) ----------------
I2C_SCL = 12
I2C_SDA = 11
I2C_FREQ = 400000
OLED_W = 128
OLED_H = 64

# ---------------- 麦克风 ICS43434 -> I2S0 RX ----------------
MIC_SCK = 5
MIC_WS = 6
MIC_SD = 7
MIC_RATE = 16000
MIC_BITS = 32          # ICS43434 是 24bit 左对齐，用 32bit 读再右移 16
MIC_FRAME_MS = 40      # 上行分帧，40ms = 640 采样 = 1280 字节
MIC_IBUF = 8192

# ---------------- 功放 MAX98357A -> I2S1 TX ----------------
# 注意：板子上 MAX98357A 的 SD/MODE 和 GAIN_SLOT 引脚没引出！
# 产品页接口表只有 VCC/GND/LRCLK/BCLK/DIN/WS/SCK/SD(=麦克风数据输出) 共 8 脚
# 所以 MAX98357A 在板子上硬接：SD_MODE→VIN(常开), GAIN_SLOT→固定电阻
SPK_BCLK = 15
SPK_LRC = 16
SPK_DIN = 17
SPK_SD = None           # 板子没引 SD/MODE，设 None 跳过
SPK_RATE = 24000
SPK_IBUF = 24576

# ---------------- 按键 / 灯 ----------------
BTN_PIN = 4
LED_PIN = 48           # 板载 WS2812，不用就留空

# ---------------- VAD 阈值（先跑 M1 看日志再定）----------------
VAD_ON = 700           # 单帧平均绝对值超过这个算"在说话"
VAD_MIN_SPEECH_MS = 160
VAD_SILENCE_MS = 600   # 连续静音这么久判停
PLAY_QUEUE_MAX = 12    # 下行抖动缓冲，12 帧 ≈ 480ms
