import os
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# %APPDATA%\LocalFileAssistant, e.g. C:\Users\<user>\AppData\Roaming\LocalFileAssistant.
# Override any of data_dir/db_path/vector_db_dir individually via env vars or .env.
_DEFAULT_DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "LocalFileAssistant"
# The installed app ships its re-ranker next to the frozen backend (scripts\build-backend.ps1).
_BUNDLED_MODELS = Path(sys.executable).parent / "models"
_FROZEN = getattr(sys, "frozen", False)


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
    # Longest wait for the chat model: between streamed tokens, or for a whole non-streamed
    # reply (the organizer's plan). Generous because a CPU can take minutes; the SDK default
    # is 10 minutes with two silent retries.
    llm_timeout_s: int = 300

    # RAM: unload the chat model after this many idle seconds (0 = never), keep the embedder
    # only briefly after indexing, and skip embeddings while free RAM is below this.
    llm_idle_unload_s: int = 600
    embed_keep_alive: str = "2m"
    min_free_ram_mb: int = 1200

    data_dir: Path = _DEFAULT_DATA_DIR
    db_path: Path = _DEFAULT_DATA_DIR / "index.db"
    vector_db_dir: Path = _DEFAULT_DATA_DIR / "lancedb"
    # Conversations, memories, tasks. Never dropped automatically (unlike index.db).
    assistant_db_path: Path = _DEFAULT_DATA_DIR / "assistant.db"

    # Assistant: chat turns and tokens of history sent to the model, recalled memories per
    # reply, and seconds of chat silence before facts are extracted.
    history_turns: int = 6
    history_token_budget: int = 3000
    memory_top_k: int = 5
    extract_idle_s: int = 300

    # Cross-encoder that re-reads the top search results together with the question
    # (core/search/reranker.py). A Hugging Face repo id, fetched once into models_dir; "" = off.
    # models_dir is separate from data_dir so a throwaway index (tests, eval) reuses the models.
    rerank_model: str = ""
    models_dir: Path = _BUNDLED_MODELS if _FROZEN and _BUNDLED_MODELS.is_dir() else _DEFAULT_DATA_DIR / "models"

    # Speech models (core/assistant/voice) are downloaded by the user, so they live in the data dir,
    # which is writable even when models_dir is the installer's read-only bundle.
    voice_models_dir: Path = _DEFAULT_DATA_DIR / "models"

    # Search ranking learns from the files you open: a habitually opened file may move up at most
    # this many places for a similar search (0 = off). See core/assistant/learning.py.
    open_prior_max_shift: int = 2

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
