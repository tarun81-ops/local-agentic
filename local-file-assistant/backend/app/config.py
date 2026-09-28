import os
from pathlib import Path

from pydantic_settings import BaseSettings

# %APPDATA%\LocalFileAssistant, e.g. C:\Users\<user>\AppData\Roaming\LocalFileAssistant.
# Override any of data_dir/db_path/vector_db_dir individually via env vars or .env.
_DEFAULT_DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "LocalFileAssistant"


class Settings(BaseSettings):
    api_token: str = ""
    host: str = "127.0.0.1"
    port: int = 8756

    ollama_url: str = "http://localhost:11434"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "qwen3-vl:2b-instruct"
    embedding_model: str = "granite-embedding:278m"

    data_dir: Path = _DEFAULT_DATA_DIR
    db_path: Path = _DEFAULT_DATA_DIR / "index.db"
    vector_db_dir: Path = _DEFAULT_DATA_DIR / "lancedb"

    watch_folders: str = ""  # comma-separated folders the watcher indexes on file changes

    class Config:
        env_file = ".env"


settings = Settings()
