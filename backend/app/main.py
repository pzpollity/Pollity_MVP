import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import grievances, webhook, email_intake
from app.core.config import settings

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
)

_logger = logging.getLogger(__name__)
_logger.info("STARTUP — OPENAI_API_KEY configured: %s", bool(settings.OPENAI_API_KEY))
_logger.info("STARTUP — RESEND_API_KEY configured: %s", bool(settings.RESEND_API_KEY))

app = FastAPI(
    title="Jan-Sunwai API",
    version="0.1.0",
    description="Grievance intake and management backend for Pollity.in",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.ENVIRONMENT == "development" else ["https://pollity.in"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhook.router)
app.include_router(grievances.router)
app.include_router(email_intake.router)


@app.get("/health")
def health():
    return {"status": "ok", "env": settings.ENVIRONMENT}
