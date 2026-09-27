from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    api_token: str = ""
    host: str = "127.0.0.1"
    port: int = 8756

    ollama_url: str = "http://localhost:11434"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = ""
    embedding_model: str = "granite-embedding:278m"

    data_dir: Path = Path(__file__).resolve().parents[2] / "data"
    db_path: Path = data_dir / "index.db"
    vector_db_dir: Path = data_dir / "lancedb"

    watch_folders: str = ""  # comma-separated; not consumed yet (phase 2 watcher)

    class Config:
        env_file = ".env"


settings = Settings()
