import type {
  ApiError,
  ActivityEvent,
  CommitDiff,
  CommitSearchDetail,
  CommitSearchStep,
  CommitSearchSummary,
  DashboardResponse,
  EvidenceItem,
  ExecutionDetail,
  ExecutionSummary,
  HealthResponse,
  InvestigationDetail,
  InvestigationSummary,
  OutputPage,
  Paginated,
  GitHubStatus,
  PatchAttemptDetail,
  PatchReview,
  PullRequestPublication,
  RepairDetail,
  RepairSummary,
  RepoCommit,
  RepositoryDetail,
  RepositorySummary,
  RerankDatasetSummary,
  RerankEvalRunSummary,
  RerankInventory,
  SourceFileSummary,
} from "./types";

const API_BASE =
  typeof window === "undefined"
    ? process.env.API_INTERNAL_URL ||
      process.env.NEXT_PUBLIC_API_URL ||
      "http://localhost:8000"
    : process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}/api/v1${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    let body: ApiError | null = null;
    try {
      body = (await res.json()) as ApiError;
    } catch {
      /* ignore */
    }
    const message = body?.error?.message || `Request failed (${res.status})`;
    const err = new Error(message) as Error & { status?: number; body?: ApiError };
    err.status = res.status;
    err.body = body || undefined;
    throw err;
  }
  if (res.status === 204) {
    return undefined as T;
  }
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("text/markdown") || contentType.includes("text/plain")) {
    return (await res.text()) as T;
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => request<HealthResponse>("/health"),
  dashboard: () => request<DashboardResponse>("/dashboard"),
  listRepositories: (q = "", page = 1) =>
    request<Paginated<RepositorySummary>>(
      `/repositories?q=${encodeURIComponent(q)}&page=${page}&page_size=20`,
    ),
  createRepository: (body: {
    github_url: string;
    branch?: string;
    display_name?: string;
  }) =>
    request<RepositoryDetail>("/repositories", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  loadDemo: () =>
    request<RepositoryDetail>("/repositories/demo", { method: "POST" }),
  getRepository: (id: string) => request<RepositoryDetail>(`/repositories/${id}`),
  deleteRepository: (id: string) =>
    request<{ status: string }>(`/repositories/${id}`, { method: "DELETE" }),
  reindexRepository: (id: string) =>
    request<RepositoryDetail>(`/repositories/${id}/reindex`, { method: "POST" }),
  listFiles: (id: string, q = "", page = 1) =>
    request<Paginated<SourceFileSummary>>(
      `/repositories/${id}/files?q=${encodeURIComponent(q)}&page=${page}&page_size=50`,
    ),
  getFile: (id: string) =>
    request<{
      id: string;
      path: string;
      language: string | null;
      content: string | null;
      line_count: number;
    }>(`/files/${id}`),
  listInvestigations: (params: {
    q?: string;
    repository_id?: string;
    status?: string;
    page?: number;
  }) => {
    const sp = new URLSearchParams();
    if (params.q) sp.set("q", params.q);
    if (params.repository_id) sp.set("repository_id", params.repository_id);
    if (params.status) sp.set("status", params.status);
    sp.set("page", String(params.page || 1));
    sp.set("page_size", "20");
    return request<Paginated<InvestigationSummary>>(`/investigations?${sp}`);
  },
  createInvestigation: (body: {
    repository_id: string;
    title: string;
    description: string;
    expected_behavior?: string;
    actual_behavior?: string;
    logs?: string;
  }) =>
    request<InvestigationDetail>("/investigations", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getInvestigation: (id: string) =>
    request<InvestigationDetail>(`/investigations/${id}`),
  getEvidence: (id: string) =>
    request<EvidenceItem[]>(`/investigations/${id}/evidence`),
  getEvents: (id: string) =>
    request<ActivityEvent[]>(`/investigations/${id}/events`),
  retryInvestigation: (id: string) =>
    request<InvestigationDetail>(`/investigations/${id}/retry`, {
      method: "POST",
    }),
  deleteInvestigation: (id: string) =>
    request<void>(`/investigations/${id}`, { method: "DELETE" }),
  exportReportUrl: (id: string) => `${API_BASE}/api/v1/investigations/${id}/report`,
  discoverTests: (investigationId: string) =>
    request<ExecutionDetail>(`/investigations/${investigationId}/test-discovery`, {
      method: "POST",
    }),
  startExecution: (investigationId: string, test_identifiers: string[]) =>
    request<ExecutionDetail>(`/investigations/${investigationId}/executions`, {
      method: "POST",
      body: JSON.stringify({ test_identifiers }),
    }),
  listExecutions: (investigationId: string) =>
    request<ExecutionSummary[]>(`/investigations/${investigationId}/executions`),
  getExecution: (id: string) => request<ExecutionDetail>(`/executions/${id}`),
  getExecutionOutput: (id: string, offset = 0) =>
    request<OutputPage>(`/executions/${id}/output?offset=${offset}&limit=32000`),
  cancelExecution: (id: string) =>
    request<ExecutionDetail>(`/executions/${id}/cancel`, { method: "POST" }),
  confirmReproduction: (
    id: string,
    body: { matching_failed_tests: string[]; explanation: string },
  ) =>
    request<ExecutionDetail>(`/executions/${id}/confirmation`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listRepoCommits: (repoId: string, anchor: string, page = 1) =>
    request<Paginated<RepoCommit>>(
      `/repositories/${repoId}/commits?anchor=${encodeURIComponent(anchor)}&page=${page}&page_size=40`,
    ),
  createCommitSearch: (
    investigationId: string,
    body: {
      good_sha: string;
      test_node_id: string;
      confirmed_execution_id: string;
      failure_exception_type?: string;
      failure_message_fragment?: string;
      max_commits?: number;
      max_executions?: number;
      max_duration_seconds?: number;
    },
  ) =>
    request<CommitSearchDetail>(`/investigations/${investigationId}/commit-searches`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listCommitSearches: (investigationId: string) =>
    request<CommitSearchSummary[]>(`/investigations/${investigationId}/commit-searches`),
  getCommitSearch: (id: string) => request<CommitSearchDetail>(`/commit-searches/${id}`),
  listCommitSearchSteps: (id: string) =>
    request<CommitSearchStep[]>(`/commit-searches/${id}/steps`),
  getCommitSearchDiff: (id: string) => request<CommitDiff>(`/commit-searches/${id}/diff`),
  cancelCommitSearch: (id: string) =>
    request<CommitSearchDetail>(`/commit-searches/${id}/cancel`, { method: "POST" }),
  commitSearchReportUrl: (id: string) => `${API_BASE}/api/v1/commit-searches/${id}/report`,
  startRepair: (
    investigationId: string,
    body: { commit_search_id: string; max_attempts?: number; max_duration_seconds?: number },
  ) =>
    request<RepairDetail>(`/investigations/${investigationId}/repairs`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listRepairs: (investigationId: string) =>
    request<RepairSummary[]>(`/investigations/${investigationId}/repairs`),
  getRepair: (id: string) => request<RepairDetail>(`/repairs/${id}`),
  cancelRepair: (id: string) =>
    request<RepairDetail>(`/repairs/${id}/cancel`, { method: "POST" }),
  getPatchAttempt: (id: string) => request<PatchAttemptDetail>(`/patch-attempts/${id}`),
  reviewPatch: (
    id: string,
    body: { decision: "accepted" | "rejected"; note?: string; patch_hash: string },
  ) =>
    request<PatchReview>(`/patch-attempts/${id}/review`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  patchDownloadUrl: (id: string) => `${API_BASE}/api/v1/patch-attempts/${id}/patch`,
  repairReportUrl: (id: string) => `${API_BASE}/api/v1/repairs/${id}/report`,
  listRerankDatasets: () => request<RerankDatasetSummary[]>("/evaluations/datasets"),
  seedSmokeDataset: () =>
    request<{ path: string; validation: Record<string, unknown> }>(
      "/evaluations/seed-smoke-dataset",
      { method: "POST" },
    ),
  rerankInventory: () => request<RerankInventory>("/evaluations/inventory"),
  listEvalRuns: () => request<RerankEvalRunSummary[]>("/evaluations/runs"),
  getEvalRun: (id: string) => request<Record<string, unknown>>(`/evaluations/runs/${id}`),
  startEvalRun: (body: {
    dataset_name: string;
    split?: string;
    label?: string;
    finetuned_path?: string | null;
  }) =>
    request<Record<string, unknown>>("/evaluations/runs", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  evalReportUrl: (id: string) => `${API_BASE}/api/v1/evaluations/runs/${id}/report.md`,
  getRerankerStatus: () =>
    request<{
      mode: string;
      model_path: string | null;
      candidate_limit: number;
      base_model: string;
      finetuned_exists: boolean;
      experimental: boolean;
    }>("/evaluations/reranker"),
  activateReranker: (body: { mode: string; model_path?: string | null }) =>
    request<Record<string, unknown>>("/evaluations/activate", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listReviewCases: () =>
    request<
      {
        instance_id: string;
        repository_id: string;
        commit_sha: string;
        query_preview: string;
        pool_hash?: string;
        failure_flags?: Record<string, boolean>;
        relevant_in_top50?: string[];
        relevant_missing_from_top50?: string[];
        review_stats: { total: number; unreviewed: number; human: number };
      }[]
    >("/evaluations/review/cases"),
  getReviewCase: (instanceId: string) =>
    request<Record<string, unknown>>(`/evaluations/review/cases/${instanceId}`),
  saveReviewAnnotations: (
    instanceId: string,
    updates: { example_id: string; label: string; annotation_note?: string }[],
  ) =>
    request<Record<string, unknown>>(`/evaluations/review/cases/${instanceId}`, {
      method: "POST",
      body: JSON.stringify({ updates }),
    }),
  reviewCoverage: () => request<Record<string, unknown>>("/evaluations/review/coverage"),
  reviewProgress: () => request<Record<string, unknown>>("/evaluations/review/progress"),
  reviewBatchReport: (ids?: string) =>
    request<Record<string, unknown>>(
      `/evaluations/review/batch-report${ids ? `?ids=${encodeURIComponent(ids)}` : ""}`,
    ),
  exportReviewDataset: () =>
    request<Record<string, unknown>>("/evaluations/review/export", { method: "POST" }),
  runReviewBaseline: () =>
    request<Record<string, unknown>>("/evaluations/review/baseline", { method: "POST" }),
  githubStatus: () => request<GitHubStatus>("/integrations/github/status"),
  preparePrPreview: (attemptId: string) =>
    request<PullRequestPublication>(`/patch-attempts/${attemptId}/pr-preview`, {
      method: "POST",
    }),
  getPublication: (id: string) => request<PullRequestPublication>(`/publications/${id}`),
  listPublications: (investigationId: string) =>
    request<PullRequestPublication[]>(`/investigations/${investigationId}/publications`),
  updatePublication: (id: string, body: { title?: string; body?: string }) =>
    request<PullRequestPublication>(`/publications/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  revalidatePublication: (id: string) =>
    request<PullRequestPublication>(`/publications/${id}/revalidate`, { method: "POST" }),
  publishPublication: (id: string, preview_hash: string) =>
    request<PullRequestPublication>(`/publications/${id}/publish`, {
      method: "POST",
      body: JSON.stringify({ preview_hash }),
    }),
  retryPublication: (id: string) =>
    request<PullRequestPublication>(`/publications/${id}/retry`, { method: "POST" }),
};
