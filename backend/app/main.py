from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import clock
from app import registry as registry_keys
from app.config import settings
from app.db import SessionLocal

from app.routes import audit, auth, cases, catalog, contracts, data, detection, detectors, environments, hacks, health, orgs, projects, quality, registry, scorecards, task_health, tokens, training, vendor_review, work



@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with SessionLocal() as session:
        await clock.start(session)
    if settings.registry_url:
        registry_keys.ensure_keys()
    yield


app = FastAPI(title="RL Data Platform", lifespan=lifespan)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(orgs.router)
app.include_router(audit.router)
app.include_router(contracts.router)
app.include_router(projects.router)
app.include_router(work.router)
app.include_router(quality.router)
app.include_router(scorecards.router)
app.include_router(cases.router)
app.include_router(data.router)
app.include_router(detection.router)
app.include_router(environments.router)
app.include_router(catalog.router)
app.include_router(training.router)
app.include_router(hacks.router)
app.include_router(detectors.router)
app.include_router(task_health.router)
app.include_router(vendor_review.router)
app.include_router(tokens.router)
app.include_router(registry.router)
