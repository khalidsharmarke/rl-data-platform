from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://rl:rl@localhost:5433/rl"
    jwt_secret: str = "dev-only-not-a-secret"
    jwt_ttl_hours: int = 12
    # Seeded users can sign in by picking a name. Stands in for SSO in the build.
    demo_logins: bool = False
    # Scripted demo only: simulated seconds per real second (1 = real time).
    demo_clock_speed: int = 1
    # Width of a scorecard period, and how often vendors' gold-based numbers refresh (PRD PD2).
    scorecard_bucket_minutes: int = 60
    vendor_gold_refresh_minutes: int = 24 * 60
    # How often the background worker queues roll-ups and works the queue (real seconds).
    jobs_tick_seconds: float = 30
    # The seed's small sample project; the scripted demo creates its own project instead.
    seed_sample_project: bool = True
    # Object storage stand-in: exported datasets are written here.
    export_dir: str = "/data/exports"


settings = Settings()
