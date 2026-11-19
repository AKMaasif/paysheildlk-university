import os
from dataclasses import dataclass


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _flag(name: str, default: str = "false") -> bool:
    return _get(name, default).lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = _get(name, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class Settings:
    database_url: str
    redis_url: str
    jwt_secret: str
    gemini_api_key: str
    gemini_model: str
    meta_api_key: str
    meta_base_url: str
    meta_model: str
    firebase_project_id: str
    firebase_client_email: str
    firebase_private_key: str
    textlk_api_key: str
    textlk_api_url: str
    sms_sender_id: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    realert_seconds: int
    escalate_seconds: int
    step_delay_ms: int
    demo_assist: bool
    telco_base_url: str
    telco_token: str
    checkpoint_path: str
    checkpointer: str
    answer_pepper: str
    app_env: str
    encryption_key: str
    hmac_key: str

    @classmethod
    def from_env(cls) -> "Settings":
        private_key = _get("FIREBASE_PRIVATE_KEY").replace("\\n", "\n")
        return cls(
            database_url=_get("DATABASE_URL", "sqlite:///./data/payshield.db"),
            redis_url=_get("REDIS_URL", ""),
            jwt_secret=_get("JWT_SECRET", "dev-only-change-me-payshield-pilot"),
            gemini_api_key=_get("GEMINI_API_KEY"),
            gemini_model=_get("GEMINI_MODEL", "gemini-2.5-flash"),
            meta_api_key=_get("META_API_KEY"),
            meta_base_url=_get("META_BASE_URL", "https://api.meta.ai/v1").rstrip("/"),
            meta_model=_get("META_MODEL", "muse-ember-1.0"),
            firebase_project_id=_get("FIREBASE_PROJECT_ID"),
            firebase_client_email=_get("FIREBASE_CLIENT_EMAIL"),
            firebase_private_key=private_key,
            textlk_api_key=_get("TEXTLK_API_KEY"),
            textlk_api_url=_get("TEXTLK_API_URL", "https://app.text.lk/api/v3/sms/send"),
            sms_sender_id=_get("SMS_SENDER_ID", "PayShield"),
            smtp_host=_get("SMTP_HOST"),
            smtp_port=_int("SMTP_PORT", 587),
            smtp_user=_get("SMTP_USER"),
            smtp_password=_get("SMTP_PASSWORD"),
            smtp_from=_get("SMTP_FROM", "alerts@payshield.lk"),
            realert_seconds=_int("REALERT_SECONDS", 180),
            escalate_seconds=_int("ESCALATE_SECONDS", 600),
            step_delay_ms=_int("STEP_DELAY_MS", 400),
            demo_assist=_flag("DEMO_ASSIST", "true"),
            telco_base_url=_get("TELCO_BASE_URL").rstrip("/"),
            telco_token=_get("TELCO_TOKEN"),
            checkpoint_path=_get("CHECKPOINT_PATH", "./data/checkpoints.sqlite"),
            checkpointer=_get("CHECKPOINTER", "auto").lower(),
            answer_pepper=_get("ANSWER_PEPPER", "payshield-pilot-pepper"),
            encryption_key=_get("ENCRYPTION_KEY", "dev-only-payshield-encryption-key"),
            hmac_key=_get("HMAC_KEY", "dev-only-payshield-hmac-key"),
            app_env=_get("APP_ENV", "development").lower(),
        )

    def dev_default_secrets(self) -> list[str]:
        """Names of secrets still set to the built-in development values."""
        defaults = {
            "JWT_SECRET": (self.jwt_secret, "dev-only-change-me-payshield-pilot"),
            "ANSWER_PEPPER": (self.answer_pepper, "payshield-pilot-pepper"),
            "ENCRYPTION_KEY": (self.encryption_key, "dev-only-payshield-encryption-key"),
            "HMAC_KEY": (self.hmac_key, "dev-only-payshield-hmac-key"),
        }
        return [name for name, (value, default) in defaults.items() if not value or value == default or len(value) < 32]

    def assert_safe_for_environment(self) -> None:
        """Refuse to start a production process on development secrets or demo helpers."""
        if self.app_env != "production":
            return
        weak = self.dev_default_secrets()
        if weak:
            raise RuntimeError(
                "APP_ENV=production but these secrets are unset, default or shorter than 32 characters: "
                + ", ".join(weak)
            )
        if self.demo_assist:
            raise RuntimeError("APP_ENV=production but DEMO_ASSIST is on. It exposes live authenticator codes.")

    @property
    def jwt_is_dev_default(self) -> bool:
        return self.jwt_secret == "dev-only-change-me-payshield-pilot"

    @property
    def push_driver(self) -> str:
        if self.firebase_project_id and self.firebase_client_email and self.firebase_private_key:
            return "fcm"
        return "websocket"

    @property
    def sms_driver(self) -> str:
        return "textlk" if self.textlk_api_key else "log"

    @property
    def email_driver(self) -> str:
        return "smtp" if self.smtp_host else "log"
