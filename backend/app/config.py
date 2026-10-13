from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://rl:rl@localhost:5433/rl"
    jwt_secret: str = "dev-only-not-a-secret"
    jwt_ttl_hours: int = 12
    # Seeded users can sign in by picking a name. Stands in for SSO in the build.
    demo_logins: bool = False


settings = Settings()
