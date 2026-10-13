from fastapi import FastAPI

from app.routes import audit, auth, health, orgs

app = FastAPI(title="RL Data Platform")
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(orgs.router)
app.include_router(audit.router)
