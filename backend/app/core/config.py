from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://bugtime:bugtime@localhost:5432/bugtime"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    cors_origins: str = "http://localhost:3000"

    data_dir: str = "./data"
    repos_dir: str = "./data/repos"
    demo_repo_path: str = "../demo-repo-v2"
    commit_search_max_commits: int = 20
    commit_search_max_executions: int = 30
    commit_search_max_duration_seconds: int = 600

    # P3 model provider (OpenAI-compatible or local Ollama)
    # Ollama example:
    #   MODEL_PROVIDER=ollama
    #   MODEL_API_KEY=ollama
    #   MODEL_NAME=qwen2.5-coder:7b
    #   MODEL_BASE_URL=http://host.docker.internal:11434/v1
    model_provider: str = "openai"
    model_api_key: str | None = None
    model_name: str = "gpt-4o-mini"
    model_base_url: str = "https://api.openai.com/v1"
    model_max_tokens: int = 4000
    model_request_timeout_seconds: float = 300.0
    model_json_mode: bool = True
    repair_max_attempts: int = 3
    repair_max_duration_seconds: int = 600
    repair_max_files: int = 5
    repair_max_changed_lines: int = 300

    max_index_files: int = 2000
    max_file_bytes: int = 262144
    max_total_index_bytes: int = 20971520
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimension: int = 384

    qdrant_collection: str = "bugtime_chunks"

    # P4 reranker (disabled | base | finetuned)
    reranker_mode: str = "disabled"
    reranker_model_path: str = ""
    reranker_candidate_limit: int = 50
    evidence_limit: int = 12
    evidence_per_file_limit: int = 3

    # P1 execution sandbox
    runner_image: str = "bugtime-pytest-runner:latest"
    data_volume_name: str = "bugtimeai_app_data"
    execution_timeout_seconds: int = 120
    execution_memory_limit: str = "1g"
    execution_nano_cpus: int = 1_000_000_000
    execution_pids_limit: int = 128
    max_output_bytes: int = 1_048_576
    docker_host: str | None = None

    # P5 GitHub draft PR publishing (server-side token only — never expose to clients)
    github_token: str | None = None
    github_api_base: str = "https://api.github.com"
    github_default_target_branch: str = "main"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
