from pydantic import BaseModel, Field


class DatasetSummary(BaseModel):
    name: str
    path: str
    example_count: int
    labeled_count: int
    unreviewed_count: int
    split_counts: dict[str, int]
    bug_group_count: int
    repository_count: int
    dataset_hash: str
    split_strategy: str
    ok: bool
    notes: list[str]
    issues: list[dict]
    provenance_label: str = "synthetic_smoke"


class EvalRunRequest(BaseModel):
    dataset_name: str = Field(..., description="Filename under data/rerank/datasets")
    split: str = "test"
    label: str = "synthetic_smoke"
    finetuned_path: str | None = None


class ActivateRequest(BaseModel):
    mode: str = Field(..., pattern="^(disabled|base|finetuned)$")
    model_path: str | None = None


class RerankerStatus(BaseModel):
    mode: str
    model_path: str | None
    candidate_limit: int
    base_model: str
    finetuned_exists: bool
    experimental: bool = True
