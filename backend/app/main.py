from fastapi import FastAPI

from app.routes import audit, auth, contracts, health, orgs, projects, quality, work

app = FastAPI(title="RL Data Platform")
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(orgs.router)
app.include_router(audit.router)
app.include_router(contracts.router)
app.include_router(projects.router)
app.include_router(work.router)
app.include_router(quality.router)
