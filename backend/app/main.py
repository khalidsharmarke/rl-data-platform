from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import clock
from app.db import SessionLocal

from app.routes import audit, auth, contracts, health, orgs, projects, quality, work



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
