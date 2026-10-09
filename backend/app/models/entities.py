import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class RepositoryStatus(str, enum.Enum):
    pending = "pending"
    indexing = "indexing"
    ready = "ready"
    failed = "failed"


class IndexStatus(str, enum.Enum):
    queued = "queued"
    fetching = "fetching"
    scanning = "scanning"
    chunking = "chunking"
    embedding = "embedding"
    saving = "saving"
    ready = "ready"
    failed = "failed"


class InvestigationStatus(str, enum.Enum):
    queued = "queued"
    retrieving = "retrieving"
    evidence_ready = "evidence_ready"
    failed = "failed"


class JobKind(str, enum.Enum):
    import_repo = "import_repo"
    reindex = "reindex"
    cleanup = "cleanup"
    retrieve = "retrieve"
    test_discovery = "test_discovery"
    reproduction = "reproduction"
    commit_search = "commit_search"
    repair = "repair"


class RunType(str, enum.Enum):
    collection = "collection"
    reproduction = "reproduction"
    commit_probe = "commit_probe"
    repair_probe = "repair_probe"
    repair_suite = "repair_suite"


class RepairStatus(str, enum.Enum):
    queued = "queued"
    preparing = "preparing"
    baseline = "baseline"
    retrieving = "retrieving"
    proposing = "proposing"
    validating = "validating"
    verifying = "verifying"
    deciding = "deciding"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    timed_out = "timed_out"


class RepairOutcome(str, enum.Enum):
    verified_fix = "verified_fix"
    partial_verification = "partial_verification"
    verification_failed = "verification_failed"
    baseline_invalid = "baseline_invalid"
    budget_exhausted = "budget_exhausted"


class ReviewDecision(str, enum.Enum):
    accepted = "accepted"
    rejected = "rejected"


class CommitSearchStatus(str, enum.Enum):
    queued = "queued"
    validating = "validating"
    searching = "searching"
    verifying = "verifying"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    timed_out = "timed_out"


class CommitSearchOutcome(str, enum.Enum):
    verified_boundary = "verified_boundary"
    inconclusive = "inconclusive"
    invalid_range = "invalid_range"
    inconsistent_reproduction = "inconsistent_reproduction"
    unsupported_history = "unsupported_history"
    budget_exhausted = "budget_exhausted"


class StepPhase(str, enum.Enum):
    validate_good = "validate_good"
    validate_bad = "validate_bad"
    search = "search"
    verify_parent = "verify_parent"
    verify_candidate = "verify_candidate"


class StepClassification(str, enum.Enum):
    good = "good"
    bad = "bad"
    skip = "skip"
    abort = "abort"
    testing = "testing"


class ExecutionStatus(str, enum.Enum):
    queued = "queued"
    preparing = "preparing"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    timed_out = "timed_out"


class TestOutcome(str, enum.Enum):
    collected = "collected"
    passed = "passed"
    failed = "failed"
    skipped = "skipped"
    error = "error"


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"


class MatchReason(str, enum.Enum):
    semantic = "semantic"
    exact = "exact"
    both = "both"


class SourceType(str, enum.Enum):
    code = "code"
    test = "test"
    documentation = "documentation"


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner: Mapped[str] = mapped_column(String(200), nullable=False)
    github_url: Mapped[str] = mapped_column(String(500), nullable=False)
    branch: Mapped[str] = mapped_column(String(200), nullable=False, default="main")
    status: Mapped[RepositoryStatus] = mapped_column(
        Enum(RepositoryStatus, name="repository_status"),
        default=RepositoryStatus.pending,
        nullable=False,
    )
    active_index_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repository_indexes.id", use_alter=True), nullable=True
    )
    local_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    indexes: Mapped[list["RepositoryIndex"]] = relationship(
        back_populates="repository",
        foreign_keys="RepositoryIndex.repository_id",
        cascade="all, delete-orphan",
    )
    investigations: Mapped[list["Investigation"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )


class RepositoryIndex(Base):
    __tablename__ = "repository_indexes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[IndexStatus] = mapped_column(
        Enum(IndexStatus, name="index_status"), default=IndexStatus.queued, nullable=False
    )
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    processed_files: Mapped[int] = mapped_column(Integer, default=0)
    total_files_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    repository: Mapped["Repository"] = relationship(
        back_populates="indexes", foreign_keys=[repository_id]
    )
    source_files: Mapped[list["SourceFile"]] = relationship(
        back_populates="index", cascade="all, delete-orphan"
    )
    chunks: Mapped[list["CodeChunk"]] = relationship(
        back_populates="index", cascade="all, delete-orphan"
    )


class SourceFile(Base):
    __tablename__ = "source_files"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    index_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repository_indexes.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    language: Mapped[str | None] = mapped_column(String(50), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    line_count: Mapped[int] = mapped_column(Integer, default=0)
    indexing_status: Mapped[str] = mapped_column(String(50), default="indexed")
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(50), default=SourceType.code.value)

    index: Mapped["RepositoryIndex"] = relationship(back_populates="source_files")
    chunks: Mapped[list["CodeChunk"]] = relationship(
        back_populates="source_file", cascade="all, delete-orphan"
    )


class CodeChunk(Base):
    __tablename__ = "code_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    source_file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_files.id", ondelete="CASCADE"), nullable=False
    )
    index_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repository_indexes.id", ondelete="CASCADE"), nullable=False
    )
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    vector_point_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    source_file: Mapped["SourceFile"] = relationship(back_populates="chunks")
    index: Mapped["RepositoryIndex"] = relationship(back_populates="chunks")


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    index_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repository_indexes.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    expected_behavior: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual_behavior: Mapped[str | None] = mapped_column(Text, nullable=True)
    logs: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[InvestigationStatus] = mapped_column(
        Enum(InvestigationStatus, name="investigation_status"),
        default=InvestigationStatus.queued,
        nullable=False,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # P4 reranker provenance (recorded per investigation; historical rows remain unchanged)
    reranker_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="disabled")
    reranker_model_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reranker_checkpoint_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reranker_fallback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    repository: Mapped["Repository"] = relationship(back_populates="investigations")
    evidence_items: Mapped[list["Evidence"]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan"
    )


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("code_chunks.id"), nullable=False
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    semantic_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    exact_match: Mapped[bool] = mapped_column(Boolean, default=False)
    match_reason: Mapped[MatchReason] = mapped_column(
        Enum(MatchReason, name="match_reason"), nullable=False
    )

    investigation: Mapped["Investigation"] = relationship(back_populates="evidence_items")
    chunk: Mapped["CodeChunk"] = relationship()


class ActivityEvent(Base):
    __tablename__ = "activity_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=True
    )
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=True
    )
    stage: Mapped[str] = mapped_column(String(100), nullable=False)
    level: Mapped[str] = mapped_column(String(20), default="info")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    kind: Mapped[JobKind] = mapped_column(Enum(JobKind, name="job_kind"), nullable=False)
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True
    )
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="SET NULL"), nullable=True
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("execution_runs.id", ondelete="SET NULL", use_alter=True, name="fk_jobs_execution"),
        nullable=True,
    )
    commit_search_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("commit_searches.id", ondelete="SET NULL", use_alter=True, name="fk_jobs_commit_search"),
        nullable=True,
    )
    repair_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("repair_runs.id", ondelete="SET NULL", use_alter=True, name="fk_jobs_repair_run"),
        nullable=True,
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status"), default=JobStatus.queued, nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExecutionRun(Base):
    __tablename__ = "execution_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    pinned_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    run_type: Mapped[RunType] = mapped_column(Enum(RunType, name="run_type"), nullable=False)
    selected_tests: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    runner_image: Mapped[str | None] = mapped_column(String(300), nullable=True)
    runner_image_digest: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[ExecutionStatus] = mapped_column(
        Enum(ExecutionStatus, name="execution_status"),
        default=ExecutionStatus.queued,
        nullable=False,
    )
    stage: Mapped[str] = mapped_column(String(50), default="queued")
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    container_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    investigation: Mapped["Investigation"] = relationship()
    test_results: Mapped[list["TestResult"]] = relationship(
        back_populates="execution", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list["ExecutionArtifact"]] = relationship(
        back_populates="execution", cascade="all, delete-orphan"
    )
    confirmation: Mapped["ReproductionConfirmation | None"] = relationship(
        back_populates="execution", uselist=False, cascade="all, delete-orphan"
    )


class TestResult(Base):
    __tablename__ = "test_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("execution_runs.id", ondelete="CASCADE"), nullable=False
    )
    test_identifier: Mapped[str] = mapped_column(String(500), nullable=False)
    outcome: Mapped[TestOutcome] = mapped_column(
        Enum(TestOutcome, name="test_outcome"), nullable=False
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    traceback: Mapped[str | None] = mapped_column(Text, nullable=True)
    in_evidence: Mapped[bool] = mapped_column(Boolean, default=False)

    execution: Mapped["ExecutionRun"] = relationship(back_populates="test_results")


class ExecutionArtifact(Base):
    __tablename__ = "execution_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("execution_runs.id", ondelete="CASCADE"), nullable=False
    )
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)

    execution: Mapped["ExecutionRun"] = relationship(back_populates="artifacts")


class ReproductionConfirmation(Base):
    __tablename__ = "reproduction_confirmations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("execution_runs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    matching_failed_tests: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    execution: Mapped["ExecutionRun"] = relationship(back_populates="confirmation")


class ReproductionBundle(Base):
    __tablename__ = "reproduction_bundles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    source_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    test_node_id: Mapped[str] = mapped_column(String(500), nullable=False)
    file_manifest: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    artifact_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    failure_exception_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    failure_message_fragment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CommitSearch(Base):
    __tablename__ = "commit_searches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    confirmed_execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("execution_runs.id", ondelete="CASCADE"), nullable=False
    )
    bundle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reproduction_bundles.id", ondelete="CASCADE"), nullable=False
    )
    bad_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    good_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    test_node_id: Mapped[str] = mapped_column(String(500), nullable=False)
    runner_image: Mapped[str | None] = mapped_column(String(300), nullable=True)
    runner_image_digest: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[CommitSearchStatus] = mapped_column(
        Enum(CommitSearchStatus, name="commit_search_status"),
        default=CommitSearchStatus.queued,
        nullable=False,
    )
    outcome: Mapped[CommitSearchOutcome | None] = mapped_column(
        Enum(CommitSearchOutcome, name="commit_search_outcome"), nullable=True
    )
    stage: Mapped[str] = mapped_column(String(50), default="queued")
    max_commits: Mapped[int] = mapped_column(Integer, default=20)
    max_executions: Mapped[int] = mapped_column(Integer, default=30)
    max_duration_seconds: Mapped[int] = mapped_column(Integer, default=600)
    executions_used: Mapped[int] = mapped_column(Integer, default=0)
    commits_tested: Mapped[int] = mapped_column(Integer, default=0)
    candidate_shas: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    verified_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    assumption_note: Mapped[str] = mapped_column(
        Text,
        default=(
            "Search assumes the bug was introduced once within the selected range. "
            "Bisect does not prove there were no intermediate fixes and reintroductions."
        ),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    bundle: Mapped["ReproductionBundle"] = relationship()
    steps: Mapped[list["CommitSearchStep"]] = relationship(
        back_populates="search", cascade="all, delete-orphan", order_by="CommitSearchStep.created_at"
    )


class CommitSearchStep(Base):
    __tablename__ = "commit_search_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    search_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("commit_searches.id", ondelete="CASCADE"), nullable=False
    )
    sha: Mapped[str] = mapped_column(String(64), nullable=False)
    phase: Mapped[StepPhase] = mapped_column(Enum(StepPhase, name="step_phase"), nullable=False)
    classification: Mapped[StepClassification] = mapped_column(
        Enum(StepClassification, name="step_classification"), nullable=False
    )
    classification_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("execution_runs.id", ondelete="SET NULL"), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    search: Mapped["CommitSearch"] = relationship(back_populates="steps")


class CommitMetadata(Base):
    __tablename__ = "commit_metadata"
    __table_args__ = (UniqueConstraint("repository_id", "sha", name="uq_commit_metadata_repo_sha"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    sha: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_shas: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    author_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    author_email: Mapped[str | None] = mapped_column(String(300), nullable=True)
    authored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RepairRun(Base):
    __tablename__ = "repair_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    commit_search_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("commit_searches.id", ondelete="CASCADE"), nullable=False
    )
    bundle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reproduction_bundles.id", ondelete="CASCADE"), nullable=False
    )
    target_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    introducing_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bundle_content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    runner_image: Mapped[str | None] = mapped_column(String(300), nullable=True)
    runner_image_digest: Mapped[str | None] = mapped_column(String(200), nullable=True)
    graph_thread_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[RepairStatus] = mapped_column(
        Enum(RepairStatus, name="repair_status"),
        default=RepairStatus.queued,
        nullable=False,
    )
    outcome: Mapped[RepairOutcome | None] = mapped_column(
        Enum(RepairOutcome, name="repair_outcome"), nullable=True
    )
    stage: Mapped[str] = mapped_column(String(50), default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    max_duration_seconds: Mapped[int] = mapped_column(Integer, default=600)
    max_tokens: Mapped[int] = mapped_column(Integer, default=8000)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    selected_attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    limitations: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    attempts: Mapped[list["PatchAttempt"]] = relationship(
        back_populates="repair_run",
        cascade="all, delete-orphan",
        order_by="PatchAttempt.attempt_number",
    )


class PatchAttempt(Base):
    __tablename__ = "patch_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    repair_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repair_runs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(50), default="p3-v1")
    context_manifest: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    assumptions: Mapped[str | None] = mapped_column(Text, nullable=True)
    limitations: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_references: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    patch_artifact_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    patch_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    changed_files: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    new_test_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    validation_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    validation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_prompt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_completion: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    repair_run: Mapped["RepairRun"] = relationship(back_populates="attempts")
    verification: Mapped["VerificationComparison | None"] = relationship(
        back_populates="attempt", uselist=False, cascade="all, delete-orphan"
    )
    review: Mapped["PatchReview | None"] = relationship(
        back_populates="attempt", uselist=False, cascade="all, delete-orphan"
    )


class VerificationComparison(Base):
    __tablename__ = "verification_comparisons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patch_attempts.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    baseline_repro_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    baseline_suite_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    patched_repro_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    patched_suite_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    new_test_pre_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    new_test_post_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reproduction_before: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    reproduction_after: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    suite_before: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    suite_after: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    new_failures: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    missing_tests: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    unchanged_failures: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    outcome: Mapped[RepairOutcome | None] = mapped_column(
        Enum(RepairOutcome, name="repair_outcome", create_constraint=False), nullable=True
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    attempt: Mapped["PatchAttempt"] = relationship(back_populates="verification")


class PatchReview(Base):
    __tablename__ = "patch_reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patch_attempts.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    patch_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    decision: Mapped[ReviewDecision] = mapped_column(
        Enum(ReviewDecision, name="review_decision"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    attempt: Mapped["PatchAttempt"] = relationship(back_populates="review")


class PublicationStatus(str, enum.Enum):
    preview = "preview"
    stale_base = "stale_base"
    revalidating = "revalidating"
    ready = "ready"
    publishing = "publishing"
    published = "published"
    failed = "failed"
    conflict = "conflict"


class PullRequestPublication(Base):
    """Persisted draft-PR publication record (idempotent identity on repair+patch+base)."""

    __tablename__ = "pull_request_publications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    repair_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repair_runs.id", ondelete="CASCADE"), nullable=False
    )
    patch_attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patch_attempts.id", ondelete="CASCADE"), nullable=False
    )
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    identity_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    patch_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    verified_base_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    current_remote_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner: Mapped[str] = mapped_column(String(200), nullable=False)
    repo_name: Mapped[str] = mapped_column(String(200), nullable=False)
    target_branch: Mapped[str] = mapped_column(String(200), nullable=False)
    feature_branch: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    preview_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[PublicationStatus] = mapped_column(
        Enum(PublicationStatus, name="publication_status"),
        default=PublicationStatus.preview,
        nullable=False,
    )
    published_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pr_draft: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    revalidation_outcome: Mapped[str | None] = mapped_column(String(100), nullable=True)
    revalidation_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
