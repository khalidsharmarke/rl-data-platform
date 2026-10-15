from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import clock
from app.db import SessionLocal

from app.routes import audit, auth, cases, catalog, contracts, data, detection, environments, health, orgs, projects, quality, scorecards, work



@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with SessionLocal() as session:
        await clock.start(session)
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
