from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Supabase
    SUPABASE_URL: str
    SUPABASE_SERVICE_ROLE_KEY: str
    SUPABASE_ANON_KEY: str

    # Anthropic
    ANTHROPIC_API_KEY: str

    # WhatsApp / Meta
    WA_VERIFY_TOKEN: str
    WA_ACCESS_TOKEN: str
    WA_PHONE_NUMBER_ID: str
    WA_APP_SECRET: str

    # Resend (email alerts)
    RESEND_API_KEY: str = ""

    # OpenAI (Whisper — for WhatsApp voice message transcription)
    OPENAI_API_KEY: str = ""

    # Mailgun (inbound email intake)
    MAILGUN_WEBHOOK_SIGNING_KEY: str = ""   # from Mailgun dashboard → Webhooks → Signing key
    EMAIL_INTAKE_OFFICE_ID: str = ""        # office_id that receives emailed grievances

    # Twilio (inbound phone calls)
    TWILIO_ACCOUNT_SID: str = ""            # Twilio console → Account Info
    TWILIO_AUTH_TOKEN: str = ""             # Twilio console → Account Info
    TWILIO_FROM_NUMBER: str = ""            # Your Twilio phone number in E.164 (used to send SMS ACKs)
    VOICE_FORWARD_NUMBER: str = ""          # E.164 number to transfer calls to human rep
    VOICE_OFFICE_ID: str = ""              # office_id that receives phone grievances
    BASE_URL: str = "https://your-backend.railway.app"  # public base URL of this server (no trailing slash)

    # Weekly briefing
    BRIEFING_SECRET: str = ""   # Secret token to protect /api/briefing/trigger

    # Proactive follow-up scheduler
    FOLLOWUP_SECRET: str = ""   # Secret token to protect /api/followup/run

    # App
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"


settings = Settings()
