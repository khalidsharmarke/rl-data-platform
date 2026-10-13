from fastapi import FastAPI

from app.routes import auth, health

app = FastAPI(title="RL Data Platform")
app.include_router(health.router)
app.include_router(auth.router)
