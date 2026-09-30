import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# %APPDATA%\LocalFileAssistant, e.g. C:\Users\<user>\AppData\Roaming\LocalFileAssistant.
# Override any of data_dir/db_path/vector_db_dir individually via env vars or .env.
_DEFAULT_DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "LocalFileAssistant"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Left empty, the backend generates one at startup (see app.security) — the API is never open.
    api_token: str = ""
    host: str = "127.0.0.1"
    port: int = 8756

    ollama_url: str = "http://localhost:11434"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "qwen3-vl:2b-instruct"
    embedding_model: str = "granite-embedding:278m"
    embed_batch_size: int = 32

    # "ollama", or "openai" for any OpenAI-compatible server (e.g. OpenVINO Model Server on
    # the NPU/iGPU): chat and embeddings then both go to ollama_base_url's /v1 API.
    llm_provider: str = "ollama"

    # RAM: unload the chat model after this many idle seconds (0 = never), keep the embedder
    # only briefly after indexing, and skip embeddings while free RAM is below this.
    llm_idle_unload_s: int = 600
    embed_keep_alive: str = "2m"
    min_free_ram_mb: int = 1200

    data_dir: Path = _DEFAULT_DATA_DIR
    db_path: Path = _DEFAULT_DATA_DIR / "index.db"
    vector_db_dir: Path = _DEFAULT_DATA_DIR / "lancedb"

    # Describe images with the vision model while indexing (slow on CPU; OCR works without it).
    caption_images: bool = False

    # Scan folders that were never fully scanned (e.g. after an index upgrade) at startup.
    auto_rescan: bool = True

    # Extra comma-separated folders to watch, on top of the folders added from the Index page.
    watch_folders: str = ""

    # Browser origins allowed to call the API: the Vite dev server, and the packaged app's
    # file:// pages (Chromium sends "null" or "file://" for those, depending on version).
    # Any other Origin is rejected even with a valid token.
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173,null,file://"

    @property
    def origin_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


settings = Settings()
