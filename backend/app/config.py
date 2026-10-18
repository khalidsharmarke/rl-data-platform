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
    jobs_tick_seconds: float = 10
    # The seed's small sample project; the scripted demo creates its own project instead.
    seed_sample_project: bool = True
    # The detection service signs in with this token (AB13). Empty disables its routes.
    detection_token: str = ""
    # Training infrastructure signs in with this token to use the catalog (EC6). Empty disables it.
    training_token: str = ""
    # Share of regular answers sent to spot checks at random: the audit sample (AB15, D28).
    audit_rate: float = 0.03
    # Training steps per bucket in the training statistics (TO2).
    training_step_bucket: int = 500
    # Object storage stand-in: exported datasets are written here.
    export_dir: str = "/data/exports"
    # Environment content, stored once per SHA-256 (D34).
    content_dir: str = "/data/content"
    # The base image every environment version runs on (build decision B4).
    environment_base_image: str = "rl-env-python:1"
    # The container registry (D50). Empty registry_url turns vendor images off.
    registry_url: str = ""
    # How the Docker daemon that runs checks names the registry.
    registry_host: str = "localhost:5000"
    registry_service: str = "rl-registry"
    registry_issuer: str = "rl-platform"
    registry_key_dir: str = "/registry-keys"
    # The platform's own services (the sandbox) sign in to the registry with this. Empty disables it.
    registry_platform_secret: str = ""


settings = Settings()
