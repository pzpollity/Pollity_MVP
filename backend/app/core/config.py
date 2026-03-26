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

    # App
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"


settings = Settings()
