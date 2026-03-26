import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import grievances, webhook
from app.core.config import settings

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
)

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


@app.get("/health")
def health():
    return {"status": "ok", "env": settings.ENVIRONMENT}
