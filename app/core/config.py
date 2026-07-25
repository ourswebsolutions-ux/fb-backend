from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    emergent_llm_key: str = ""
    # Fernet key for encrypting FB account passwords at rest.
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    encryption_key: str = ""

    class Config:
        # docker-compose.yml loads ./fb-auto-backend/.env
        # Use that same file by default so required Supabase keys are present.
        env_file = (".env", ".env.local")
        extra = "ignore"



settings = Settings()
