from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parents[1]


def _int_env(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


@dataclass(slots=True)
class Settings:
    database_url: str
    firmware_dir: Path
    dialogue_provider: str
    access_token_minutes: int
    refresh_token_days: int
    pairing_token_minutes: int
    max_audio_bytes: int
    audio_frame_bytes: int
    siliconflow_api_key: str
    asr_model: str
    tts_model: str
    tts_voice: str
    tts_speed: float
    openai_base_url: str
    openai_api_key: str
    llm_model: str
    llm_system: str
    llm_history_messages: int
    guard_max_turns_per_min: int
    guard_max_chars_per_hour: int
    guard_failure_threshold: int
    guard_cooldown_seconds: int
    xiaoling_base_url: str
    xiaoling_admin_token: str
    xiaoling_to: str
    xiaoling_public_daily_tokens: int
    voice_service_token: str
    panel_base_url: str
    panel_timeout: float
    public_base_url: str

    @classmethod
    def from_env(cls) -> "Settings":
        siliconflow_key = os.getenv("SILICONFLOW_API_KEY", "")
        explicit_llm_key = os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")
        # 未单独配置 LLM 密钥时，复用 SiliconFlow 的 OpenAI 兼容接口。
        use_siliconflow_llm = not explicit_llm_key and bool(siliconflow_key)
        default_base_url = (
            "https://api.siliconflow.cn/v1"
            if use_siliconflow_llm
            else "https://api.deepseek.com/v1"
        )
        default_llm_model = (
            "deepseek-ai/DeepSeek-V3.2" if use_siliconflow_llm else "deepseek-chat"
        )
        return cls(
            database_url=os.getenv(
                "DATABASE_URL", f"sqlite:///{SERVER_DIR / 'data' / 'agent.db'}"
            ),
            firmware_dir=Path(
                os.getenv("FIRMWARE_DIR", str(SERVER_DIR / "data" / "firmware"))
            ).expanduser(),
            dialogue_provider=os.getenv("DIALOGUE_PROVIDER", "echo").strip().lower(),
            access_token_minutes=_int_env("ACCESS_TOKEN_MINUTES", 30),
            refresh_token_days=_int_env("REFRESH_TOKEN_DAYS", 30),
            pairing_token_minutes=_int_env("PAIRING_TOKEN_MINUTES", 10),
            max_audio_bytes=_int_env("MAX_AUDIO_BYTES", 4 * 1024 * 1024),
            audio_frame_bytes=_int_env("AUDIO_FRAME_BYTES", 640),
            siliconflow_api_key=siliconflow_key,
            asr_model=os.getenv("ASR_MODEL", "FunAudioLLM/SenseVoiceSmall"),
            tts_model=os.getenv("TTS_MODEL", "FunAudioLLM/CosyVoice2-0.5B"),
            tts_voice=os.getenv("TTS_VOICE", "anna"),
            tts_speed=float(os.getenv("TTS_SPEED", "1.0")),
            openai_base_url=(
                os.getenv("OPENAI_BASE_URL") or default_base_url
            ).rstrip("/"),
            openai_api_key=explicit_llm_key or siliconflow_key,
            llm_model=os.getenv("LLM_MODEL") or default_llm_model,
            llm_system=os.getenv(
                "LLM_SYSTEM", "你是 ESP32-S3 上的语音助手，回答简短、自然。"
            ),
            llm_history_messages=_int_env("LLM_HISTORY_MESSAGES", 12),
            # 成本围栏：0 表示关闭对应闸门。
            guard_max_turns_per_min=_int_env("GUARD_MAX_TURNS_PER_MIN", 12),
            guard_max_chars_per_hour=_int_env("GUARD_MAX_CHARS_PER_HOUR", 400_000),
            guard_failure_threshold=_int_env("GUARD_FAILURE_THRESHOLD", 5),
            guard_cooldown_seconds=_int_env("GUARD_COOLDOWN_SECONDS", 60),
            xiaoling_base_url=os.getenv(
                "XIAOLING_BASE_URL", "http://127.0.0.1:8010"
            ).rstrip("/"),
            xiaoling_admin_token=os.getenv("XIAOLING_ADMIN_TOKEN", ""),
            xiaoling_to=os.getenv("XIAOLING_TO", "小綾"),
            xiaoling_public_daily_tokens=_int_env("XIAOLING_PUBLIC_DAILY_TOKENS", 20_000),
            voice_service_token=os.getenv("VOICE_SERVICE_TOKEN", ""),
            panel_base_url=os.getenv("PANEL_BASE_URL", "http://127.0.0.1:8010").rstrip("/"),
            panel_timeout=float(os.getenv("PANEL_TIMEOUT", "5")),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
        )
