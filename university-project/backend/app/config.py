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


MODES = ("deterministic", "hybrid", "external", "production")
MODE_NOTES = {
    "deterministic": "Synthetic telecom and bank data, rule-written notes, and every notification only logged. No external service is called.",
    "hybrid": "Synthetic telecom and bank data. Real SMS, email, push and AI models may be used, but only for the allow-listed test recipients.",
    "external": "Real external services where configured, including the operator's SIM-swap gateway. Real messages go only to the allow-listed test recipients. The bank ledger and SIM freeze are still simulated.",
    "production": "A live deployment. No demonstration safeguards.",
}
DEV_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")


def _list(raw: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))


def _origins(raw: str) -> tuple[str, ...]:
    """The sites allowed to call the API from a browser, comma separated. Empty means the local development addresses."""
    items = tuple(dict.fromkeys(part.strip().rstrip("/") for part in raw.split(",") if part.strip()))
    return items or DEV_ORIGINS


@dataclass
class Settings:
    database_url: str
    redis_url: str
    jwt_secret: str
    xai_api_key: str
    xai_base_url: str
    xai_model: str
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
    customer_profile: bool
    mcp_tools: bool
    face_engine: str
    face_challenge: bool
    telco_base_url: str
    telco_token: str
    checkpoint_path: str
    checkpointer: str
    answer_pepper: str
    app_env: str
    allowed_origins: tuple[str, ...]
    drills_enabled: bool
    simulated_book: bool
    bootstrap_admin_email: str
    bootstrap_admin_password: str
    demo_mode: str
    ignored_by_mode: tuple[str, ...]  # services that have credentials set but are switched off by the mode
    demo_allowed_phones: tuple[str, ...]
    demo_allowed_emails: tuple[str, ...]
    trusted_proxies: tuple[str, ...]
    cookie_secure: bool
    notify_max_per_hour: int
    notify_max_attempts: int
    encryption_key: str
    hmac_key: str

    @classmethod
    def from_env(cls) -> "Settings":
        private_key = _get("FIREBASE_PRIVATE_KEY").replace("\\n", "\n")
        production = _get("APP_ENV", "development").lower() == "production"
        mode = (_get("DEMO_MODE") or ("production" if production else "deterministic")).lower()
        if mode not in MODES:
            raise RuntimeError(f"DEMO_MODE must be one of {', '.join(MODES)}; it is {mode!r}.")
        # What the mode switches off. The credentials are read, then ignored, and the names of what was ignored are kept
        # so the presenter can see that, say, SMS credentials are present but not in use.
        textlk, smtp, fcm_project = _get("TEXTLK_API_KEY"), _get("SMTP_HOST"), _get("FIREBASE_PROJECT_ID")
        grok, meta, telco = _get("XAI_API_KEY"), _get("META_API_KEY"), _get("TELCO_BASE_URL").rstrip("/")
        ignored: list[str] = []
        if mode == "deterministic":
            for name, value in (("SMS (Text.lk)", textlk), ("email (SMTP)", smtp), ("push (Firebase)", fcm_project), ("AI model (Meta)", meta), ("AI model (Grok)", grok), ("telecom gateway", telco)):
                if value:
                    ignored.append(name)
            textlk = smtp = fcm_project = grok = meta = telco = ""
        elif mode == "hybrid" and telco:
            ignored.append("telecom gateway")
            telco = ""
        return cls(
            database_url=_get("DATABASE_URL", "sqlite:///./data/payshield.db"),
            redis_url=_get("REDIS_URL", ""),
            jwt_secret=_get("JWT_SECRET", "dev-only-change-me-payshield-pilot"),
            xai_api_key=grok,
            xai_base_url=_get("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/"),
            xai_model=_get("XAI_MODEL", "grok-4.20-0309-non-reasoning"),
            meta_api_key=meta,
            meta_base_url=_get("META_BASE_URL", "https://api.meta.ai/v1").rstrip("/"),
            meta_model=_get("META_MODEL", "muse-ember-1.0"),
            firebase_project_id=fcm_project,
            firebase_client_email=_get("FIREBASE_CLIENT_EMAIL"),
            firebase_private_key=private_key,
            textlk_api_key=textlk,
            textlk_api_url=_get("TEXTLK_API_URL", "https://app.text.lk/api/v3/sms/send"),
            sms_sender_id=_get("SMS_SENDER_ID", "PayShield"),
            smtp_host=smtp,
            smtp_port=_int("SMTP_PORT", 587),
            smtp_user=_get("SMTP_USER"),
            smtp_password=_get("SMTP_PASSWORD"),
            smtp_from=_get("SMTP_FROM", "alerts@payshield.lk"),
            realert_seconds=_int("REALERT_SECONDS", 180),
            escalate_seconds=_int("ESCALATE_SECONDS", 600),
            step_delay_ms=_int("STEP_DELAY_MS", 400),
            demo_assist=_flag("DEMO_ASSIST", "true"),
            customer_profile=_flag("CUSTOMER_PROFILE", "true"),
            mcp_tools=_flag("MCP_TOOLS", "false"),
            face_engine=_get("FACE_ENGINE", "dlib").lower(),
            face_challenge=_flag("FACE_CHALLENGE", "true"),
            telco_base_url=telco,
            telco_token=_get("TELCO_TOKEN"),
            checkpoint_path=_get("CHECKPOINT_PATH", "./data/checkpoints.sqlite"),
            checkpointer=_get("CHECKPOINTER", "auto").lower(),
            answer_pepper=_get("ANSWER_PEPPER", "payshield-pilot-pepper"),
            encryption_key=_get("ENCRYPTION_KEY", "dev-only-payshield-encryption-key"),
            hmac_key=_get("HMAC_KEY", "dev-only-payshield-hmac-key"),
            app_env=_get("APP_ENV", "development").lower(),
            allowed_origins=_origins(_get("ALLOWED_ORIGINS")),
            # Drills write pretend SIM events and payments into a customer's record, and the simulated book fills the
            # dashboards with invented payments. Both are for a demonstration, so they default to off in production.
            drills_enabled=_flag("DRILLS_ENABLED", "false" if production else "true"),
            simulated_book=_flag("SIMULATED_BOOK", "false" if production else "true"),
            bootstrap_admin_email=_get("BOOTSTRAP_ADMIN_EMAIL").lower(),
            bootstrap_admin_password=os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", ""),
            demo_mode=mode,
            ignored_by_mode=tuple(ignored),
            demo_allowed_phones=_list(_get("DEMO_ALLOWED_PHONES")),
            demo_allowed_emails=tuple(item.lower() for item in _list(_get("DEMO_ALLOWED_EMAILS"))),
            trusted_proxies=tuple(_list(_get("TRUSTED_PROXIES", "127.0.0.1,::1"))),
            cookie_secure=_flag("COOKIE_SECURE", "true" if production else "false"),
            notify_max_per_hour=_int("NOTIFY_MAX_PER_HOUR", 0 if production else 20),
            notify_max_attempts=max(1, min(_int("NOTIFY_MAX_ATTEMPTS", 2), 3)),
        )

    @property
    def mode_note(self) -> str:
        return MODE_NOTES[self.demo_mode]

    def recipient_allowed(self, kind: str, value: str | None) -> bool:
        """In the two demonstration modes with real services, a real message goes only to a listed test recipient.
        An empty list sends to nobody. A live deployment has no such list."""
        if self.demo_mode in {"deterministic", "production"}:
            return True
        if not value:
            return False
        if kind == "sms":
            from app.util import canonical_phone

            return canonical_phone(value) in {canonical_phone(p) for p in self.demo_allowed_phones}
        return value.strip().lower() in self.demo_allowed_emails

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
        if self.demo_mode != "production":
            raise RuntimeError(f"APP_ENV=production but DEMO_MODE is {self.demo_mode!r}. Leave it unset in production.")
        bad = [o for o in self.allowed_origins if o == "*" or not o.startswith("https://")]
        if self.allowed_origins == DEV_ORIGINS or bad:
            raise RuntimeError(
                "APP_ENV=production but ALLOWED_ORIGINS is not set to the real https:// site address(es)"
                + (": " + ", ".join(bad) if bad else "")
            )

    @property
    def jwt_is_dev_default(self) -> bool:
        return self.jwt_secret == "dev-only-change-me-payshield-pilot"  # noqa: S105 - compares against the known development default, it is not a credential

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
