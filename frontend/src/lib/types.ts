export type RepositorySummary = {
  id: string;
  name: string;
  owner: string;
  github_url: string;
  branch: string;
  status: string;
  indexed_files: number;
  last_indexed_commit: string | null;
  last_indexed_at: string | null;
  is_demo: boolean;
};

export type IndexState = {
  id: string;
  commit_sha: string | null;
  status: string;
  embedding_model: string;
  file_count: number;
  chunk_count: number;
  skipped_count: number;
  processed_files: number;
  total_files_estimate: number | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
};

export type RepositoryDetail = RepositorySummary & {
  active_index: IndexState | null;
  skipped_files: { path: string; reason: string }[];
};

export type InvestigationSummary = {
  id: string;
  title: string;
  repository_id: string;
  repository_name: string;
  status: string;
  created_at: string;
  completed_at: string | null;
};

export type InvestigationDetail = {
  id: string;
  title: string;
  repository_id: string;
  repository_name: string;
  repository_owner: string;
  github_url: string;
  branch: string;
  index_id: string;
  commit_sha: string | null;
  status: string;
  description: string;
  expected_behavior: string | null;
  actual_behavior: string | null;
  logs: string | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
  evidence_count: number;
  index_version: string;
  reranker_mode?: string;
  reranker_model_path?: string | null;
  reranker_checkpoint_hash?: string | null;
  reranker_fallback?: boolean;
};

export type RerankDatasetSummary = {
  name: string;
  path: string;
  example_count: number;
  labeled_count: number;
  unreviewed_count: number;
  split_counts: Record<string, number>;
  bug_group_count: number;
  repository_count: number;
  dataset_hash: string;
  split_strategy: string;
  ok: boolean;
  notes: string[];
  issues: { code: string; message: string; example_id?: string | null }[];
  provenance_label: string;
};

export type RerankInventory = {
  independent_bug_groups: number;
  repositories: number;
  supports_meaningful_p4b: boolean;
  recommendation: string;
  login_demo_note: string;
  datasets: {
    name: string;
    bug_groups: number;
    repos: number;
    split_counts: Record<string, number>;
    split_strategy: string;
    label: string;
    notes: string[];
  }[];
};

export type RerankEvalRunSummary = {
  id: string;
  created_at?: string;
  label?: string;
  dataset_hash?: string;
  split?: string;
  bug_count?: number;
  path?: string;
};

export type PullRequestPublication = {
  id: string;
  repair_run_id: string;
  patch_attempt_id: string;
  investigation_id: string;
  repository_id: string;
  identity_key: string;
  patch_hash: string;
  verified_base_sha: string;
  current_remote_sha: string | null;
  owner: string;
  repo_name: string;
  target_branch: string;
  feature_branch: string;
  title: string;
  body: string;
  preview_hash: string;
  status: string;
  published_commit_sha: string | null;
  pr_number: number | null;
  pr_url: string | null;
  pr_draft: boolean | null;
  revalidation_outcome: string | null;
  revalidation_summary: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
  published_at: string | null;
  unified_diff: string | null;
  github_configured: boolean;
};

export type GitHubStatus = {
  configured: boolean;
  connected: boolean | null;
  login: string | null;
  scopes_hint: string | null;
  error: string | null;
};

export type EvidenceItem = {
  id: string;
  rank: number;
  file_path: string;
  start_line: number;
  end_line: number;
  source_type: string;
  snippet: string;
  match_reason: string;
  semantic_score: number | null;
  exact_match: boolean;
  symbol_name: string | null;
  chunk_id: string;
  source_file_id: string;
  github_url: string;
};

export type ActivityEvent = {
  id: string;
  repository_id: string | null;
  investigation_id: string | null;
  stage: string;
  level: string;
  message: string;
  created_at: string;
};

export type DashboardResponse = {
  ready_repositories: number;
  active_indexing_jobs: number;
  investigations_count: number;
  recent_repositories: RepositorySummary[];
  recent_investigations: InvestigationSummary[];
};

export type Paginated<T> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
};

export type HealthResponse = {
  status: string;
  services: { name: string; status: string; detail?: string | null }[];
  embedding_model: string;
  import_limits: Record<string, number>;
};

export type SourceFileSummary = {
  id: string;
  path: string;
  language: string | null;
  line_count: number;
  indexing_status: string;
  skip_reason: string | null;
  source_type: string;
};

export type ApiError = {
  error: {
    code: string;
    message: string;
    field_errors?: Record<string, string[]> | null;
  };
};

export type TestResultOut = {
  id: string;
  test_identifier: string;
  outcome: string;
  duration_ms: number | null;
  failure_message: string | null;
  traceback: string | null;
  in_evidence: boolean;
};

export type ConfirmationOut = {
  id: string;
  matching_failed_tests: string[];
  explanation: string;
  created_at: string;
};

export type ExecutionSummary = {
  id: string;
  investigation_id: string;
  pinned_sha: string;
  run_type: string;
  selected_tests: string[];
  status: string;
  stage: string;
  exit_code: number | null;
  duration_ms: number | null;
  error_category: string | null;
  error_message: string | null;
  runner_image: string | null;
  runner_image_digest: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  passed: number;
  failed: number;
  skipped: number;
  error_count: number;
  collected: number;
  confirmed: boolean;
};

export type ExecutionDetail = ExecutionSummary & {
  test_results: TestResultOut[];
  confirmation: ConfirmationOut | null;
};

export type OutputPage = {
  content: string;
  offset: number;
  next_offset: number;
  total_bytes: number;
  truncated: boolean;
  eof: boolean;
};

export type RepoCommit = {
  sha: string;
  short_sha: string;
  subject: string | null;
  author_name: string | null;
  authored_at: string | null;
  parent_shas: string[];
};

export type CommitMeta = {
  sha: string;
  parent_shas: string[];
  author_name: string | null;
  author_email: string | null;
  authored_at: string | null;
  subject: string | null;
};

export type CommitSearchStep = {
  id: string;
  sha: string;
  phase: string;
  classification: string;
  classification_reason: string | null;
  attempt: number;
  execution_id: string | null;
  duration_ms: number | null;
  created_at: string;
  metadata?: CommitMeta | null;
};

export type CommitSearchSummary = {
  id: string;
  investigation_id: string;
  status: string;
  outcome: string | null;
  stage: string;
  bad_sha: string;
  good_sha: string;
  test_node_id: string;
  bundle_content_hash: string | null;
  runner_image_digest: string | null;
  executions_used: number;
  commits_tested: number;
  max_commits: number;
  max_executions: number;
  max_duration_seconds: number;
  candidate_shas: string[];
  verified_sha: string | null;
  parent_sha: string | null;
  error_message: string | null;
  assumption_note: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type CommitSearchDetail = CommitSearchSummary & {
  failure_exception_type: string | null;
  failure_message_fragment: string | null;
  verified_metadata: CommitMeta | null;
  parent_metadata: CommitMeta | null;
  recent_steps: CommitSearchStep[];
};

export type DiffFile = {
  path: string;
  status: string;
  patch: string;
  in_evidence: boolean;
};

export type CommitDiff = {
  candidate_sha: string;
  parent_sha: string;
  files: DiffFile[];
};

export type PatchReview = {
  id: string;
  patch_hash: string;
  decision: string;
  note: string | null;
  created_at: string;
};

export type VerificationComparison = {
  id: string;
  outcome: string | null;
  summary: string | null;
  reproduction_before: unknown[] | null;
  reproduction_after: unknown[] | null;
  suite_before: unknown[] | null;
  suite_after: unknown[] | null;
  new_failures: string[] | null;
  missing_tests: string[] | null;
  unchanged_failures: string[] | null;
  baseline_repro_ids: string[] | null;
  patched_repro_ids: string[] | null;
  baseline_suite_id: string | null;
  patched_suite_id: string | null;
};

export type PatchAttemptSummary = {
  id: string;
  attempt_number: number;
  provider: string | null;
  model: string | null;
  status: string;
  patch_hash: string | null;
  changed_files: string[] | null;
  validation_ok: boolean | null;
  validation_error: string | null;
  explanation: string | null;
  feedback: string | null;
  tokens_prompt: number | null;
  tokens_completion: number | null;
  created_at: string;
  completed_at: string | null;
  review: PatchReview | null;
  verification_outcome: string | null;
};

export type PatchAttemptDetail = PatchAttemptSummary & {
  assumptions: string | null;
  limitations: string | null;
  source_references: string[] | null;
  unified_diff: string | null;
  new_test_path: string | null;
  verification: VerificationComparison | null;
  prompt_version: string;
};

export type RepairSummary = {
  id: string;
  investigation_id: string;
  commit_search_id: string;
  target_sha: string;
  introducing_sha: string | null;
  status: string;
  outcome: string | null;
  stage: string;
  attempt_count: number;
  max_attempts: number;
  max_duration_seconds: number;
  provider: string | null;
  model: string | null;
  bundle_content_hash: string;
  runner_image_digest: string | null;
  selected_attempt_id: string | null;
  error_message: string | null;
  limitations: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type RepairDetail = RepairSummary & {
  attempts: PatchAttemptSummary[];
};
