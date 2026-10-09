"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Search, X } from "lucide-react";
import { api } from "@/lib/api";
import type {
  DashboardResponse,
  ExecutionDetail,
  ExecutionSummary,
  InvestigationDetail,
  InvestigationSummary,
  PullRequestPublication,
} from "@/lib/types";
import { cn, formatDate, shortSha } from "@/lib/utils";
import {
  buildActivityTrail,
  latestFailedAssertion,
  shortCaseId,
} from "@/lib/case-preview";
import { investigationOutcomeLabel } from "@/lib/milestones";
import {
  Button,
  Card,
  EmptyState,
  Hint,
  InlineError,
  Skeleton,
} from "@/components/ui";
import { StatusBadge } from "@/components/status-badge";

export default function DashboardPage() {
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [reposOpen, setReposOpen] = useState(false);

  const [detail, setDetail] = useState<InvestigationDetail | null>(null);
  const [executions, setExecutions] = useState<ExecutionSummary[]>([]);
  const [reproDetail, setReproDetail] = useState<ExecutionDetail | null>(null);
  const [draftPr, setDraftPr] = useState<PullRequestPublication | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    api
      .dashboard()
      .then((d) => {
        setData(d);
        setError(null);
        setSelectedId((prev) => {
          if (prev && d.recent_investigations.some((i) => i.id === prev)) return prev;
          return d.recent_investigations[0]?.id ?? null;
        });
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setExecutions([]);
      setReproDetail(null);
      setDraftPr(null);
      return;
    }
    let cancelled = false;
    setPreviewLoading(true);
    setPreviewError(null);
    (async () => {
      try {
        const [inv, runs, pubs] = await Promise.all([
          api.getInvestigation(selectedId),
          api.listExecutions(selectedId).catch(() => [] as ExecutionSummary[]),
          api.listPublications(selectedId).catch(() => [] as PullRequestPublication[]),
        ]);
        if (cancelled) return;
        setDetail(inv);
        setExecutions(runs);
        setDraftPr(
          pubs.find((p) => p.status === "published" && p.pr_url) || null,
        );
        const latestRepro =
          runs.find((r) => r.run_type === "reproduction" && r.confirmed) ||
          runs.find(
            (r) => r.run_type === "reproduction" && r.status === "completed",
          ) ||
          runs.find((r) => r.run_type === "reproduction");
        if (latestRepro) {
          try {
            const full = await api.getExecution(latestRepro.id);
            if (!cancelled) setReproDetail(full);
          } catch {
            if (!cancelled) setReproDetail(null);
          }
        } else if (!cancelled) {
          setReproDetail(null);
        }
      } catch (e) {
        if (!cancelled) setPreviewError((e as Error).message);
      } finally {
        if (!cancelled) setPreviewLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const investigations = data?.recent_investigations || [];
  const empty =
    data &&
    data.recent_repositories.length === 0 &&
    investigations.length === 0;

  const selectInvestigation = (id: string) => {
    setSelectedId(id);
    setPreviewOpen(true);
  };

  const summary = useMemo(() => {
    if (!data) return null;
    const repos = data.ready_repositories;
    const invs = data.investigations_count;
    const indexing = data.active_indexing_jobs;
    return `${repos} ${repos === 1 ? "repository" : "repositories"} · ${invs} ${
      invs === 1 ? "investigation" : "investigations"
    } · ${indexing} indexing`;
  }, [data]);

  return (
    <div className="mx-auto flex min-h-[calc(100vh-3.5rem-2.5rem)] max-w-[1400px] flex-col">
      {/* Header row: title + primary action */}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-heading tracking-tight text-ink">Investigations</h1>
          <p className="mt-0.5 text-sm text-mute">
            Select a case to preview progress, then continue in the workspace.
          </p>
        </div>
        <Link href="/investigations/new" className="shrink-0">
          <Button className="min-w-[9.5rem]">
            <Search className="h-4 w-4" />
            New investigation
          </Button>
        </Link>
      </div>

      {error ? (
        <div className="mb-3">
          <InlineError
            message={`Backend unavailable: ${error}. Start Docker Compose services, then retry.`}
            onRetry={load}
          />
        </div>
      ) : null}

      {/* Compact summary strip */}
      <div className="mb-4 rounded-panel border border-line bg-panel px-3.5 py-2.5 text-sm text-mute shadow-soft">
        {loading && !data ? (
          <Skeleton className="h-4 w-64" />
        ) : (
          <span className="tabular-nums text-ink">{summary || "—"}</span>
        )}
      </div>

      {empty ? (
        <EmptyState
          title="Start your first investigation"
          description="Import a repository → retrieve evidence → reproduce the failure with existing pytest tests."
          action={
            <>
              <Link href="/repositories/new">
                <Button>Import repository</Button>
              </Link>
              <Link href="/repositories">
                <Button variant="secondary">Load demo</Button>
              </Link>
            </>
          }
        />
      ) : (
        <>
          {/* Desktop list + preview */}
          <div className="hidden min-h-0 flex-1 gap-4 lg:grid lg:grid-cols-[minmax(0,65%)_minmax(0,35%)]">
            <InvestigationList
              items={investigations}
              selectedId={selectedId}
              onSelect={selectInvestigation}
              loading={loading && !data}
            />
            <CasePreviewPanel
              detail={detail}
              executions={executions}
              reproDetail={reproDetail}
              draftPr={draftPr}
              loading={previewLoading}
              error={previewError}
            />
          </div>

          {/* Narrow: list first */}
          <div className="lg:hidden">
            <InvestigationList
              items={investigations}
              selectedId={selectedId}
              onSelect={selectInvestigation}
              loading={loading && !data}
            />
          </div>

          {/* Mobile / tablet preview drawer */}
          {previewOpen && selectedId ? (
            <div className="fixed inset-0 z-50 flex flex-col bg-canvas/80 p-3 backdrop-blur-sm lg:hidden">
              <Card className="flex max-h-full flex-1 flex-col overflow-hidden p-0">
                <div className="flex items-center justify-between border-b border-line px-3 py-2">
                  <span className="text-sm font-medium">Case preview</span>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label="Close preview"
                    onClick={() => setPreviewOpen(false)}
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </div>
                <div className="min-h-0 flex-1 overflow-auto p-4 bt-scroll">
                  <CasePreviewPanel
                    detail={detail}
                    executions={executions}
                    reproDetail={reproDetail}
                    draftPr={draftPr}
                    loading={previewLoading}
                    error={previewError}
                    bare
                  />
                </div>
              </Card>
            </div>
          ) : null}

          {/* Secondary repositories */}
          <div className="mt-4">
            <button
              type="button"
              className="flex w-full items-center gap-2 rounded-panel border border-line bg-panel px-3.5 py-2.5 text-left text-sm bt-transition hover:border-accent/30"
              onClick={() => setReposOpen((v) => !v)}
              aria-expanded={reposOpen}
            >
              {reposOpen ? (
                <ChevronDown className="h-4 w-4 text-mute" />
              ) : (
                <ChevronRight className="h-4 w-4 text-mute" />
              )}
              <span className="font-medium text-ink">Repositories</span>
              <span className="text-meta text-mute">
                {data?.recent_repositories.length ?? 0} indexed
              </span>
            </button>
            {reposOpen ? (
              <Card className="mt-2 overflow-hidden p-0">
                <ul className="divide-y divide-line">
                  {(data?.recent_repositories || []).map((r) => (
                    <li key={r.id}>
                      <Link
                        href={`/repositories/${r.id}`}
                        className="flex min-h-14 flex-wrap items-center justify-between gap-2 px-4 py-3 bt-transition hover:bg-elevated/70"
                      >
                        <div className="min-w-0">
                          <div className="truncate text-sm text-ink">
                            {r.owner}/{r.name}
                          </div>
                          <div className="mt-0.5 flex flex-wrap gap-x-3 text-meta text-mute">
                            <span className="font-mono">{r.branch}</span>
                            <span className="font-mono">
                              {shortSha(r.last_indexed_commit)}
                            </span>
                            <span>{r.indexed_files} files</span>
                          </div>
                        </div>
                        <StatusBadge status={r.status} />
                      </Link>
                    </li>
                  ))}
                  {(data?.recent_repositories || []).length === 0 ? (
                    <li className="px-4 py-6 text-center text-sm text-mute">
                      No repositories indexed.
                    </li>
                  ) : null}
                </ul>
              </Card>
            ) : null}
          </div>
        </>
      )}
    </div>
  );
}

function InvestigationList({
  items,
  selectedId,
  onSelect,
  loading,
}: {
  items: InvestigationSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  loading: boolean;
}) {
  return (
    <Card className="flex min-h-0 flex-col overflow-hidden p-0">
      <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <h2 className="text-sm font-semibold text-ink">Queue</h2>
        <Link href="/investigations" className="text-meta text-accent">
          View history
        </Link>
      </div>
      <ul className="min-h-0 flex-1 divide-y divide-line overflow-auto bt-scroll">
        {loading ? (
          <>
            <li className="px-4 py-4">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="mt-2 h-3 w-1/2" />
            </li>
            <li className="px-4 py-4">
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="mt-2 h-3 w-1/3" />
            </li>
          </>
        ) : null}
        {items.map((i) => {
          const selected = i.id === selectedId;
          return (
            <li key={i.id}>
              <button
                type="button"
                onClick={() => onSelect(i.id)}
                className={cn(
                  "flex min-h-[80px] w-full items-center gap-3 border-l-[3px] px-4 py-3 text-left bt-transition",
                  selected
                    ? "border-accent bg-accent-soft"
                    : "border-transparent hover:bg-elevated/60",
                )}
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[15px] font-medium text-ink">
                    {i.title}
                  </div>
                  <div className="mt-1 truncate text-meta text-mute">
                    {i.repository_name} · {formatDate(i.created_at)}
                  </div>
                  <div className="mt-1 font-mono text-meta text-mute">
                    {shortCaseId(i.id)}
                  </div>
                </div>
                <StatusBadge status={i.status} />
              </button>
            </li>
          );
        })}
        {!loading && items.length === 0 ? (
          <li className="px-4 py-10 text-center text-sm text-mute">
            No investigations yet.
          </li>
        ) : null}
      </ul>
    </Card>
  );
}

function CasePreviewPanel({
  detail,
  executions,
  reproDetail,
  draftPr,
  loading,
  error,
  bare = false,
}: {
  detail: InvestigationDetail | null;
  executions: ExecutionSummary[];
  reproDetail: ExecutionDetail | null;
  draftPr: PullRequestPublication | null;
  loading: boolean;
  error: string | null;
  bare?: boolean;
}) {
  const outcome = investigationOutcomeLabel(detail, executions);
  const trail = detail ? buildActivityTrail(detail, executions, reproDetail) : [];
  const assertion = latestFailedAssertion(reproDetail);

  const body = (
    <>
      {error ? <InlineError message={error} /> : null}

      {loading && !detail ? (
        <div className="space-y-3">
          <Skeleton className="h-6 w-4/5" />
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : null}

      {!loading && !detail && !error ? (
        <p className="text-sm text-mute">Select an investigation to preview.</p>
      ) : null}

      {detail ? (
        <div className="space-y-4">
          <div>
            <div className="flex flex-wrap items-start justify-between gap-2">
              <h2 className="text-lg font-semibold leading-snug text-ink">
                {detail.title}
              </h2>
              <StatusBadge status={detail.status} />
            </div>
            <div className="mt-1 font-mono text-meta text-mute">
              Case {shortCaseId(detail.id)}
            </div>
          </div>

          <div className="rounded-control border border-line bg-elevated/60 px-3 py-2.5">
            <div className="text-meta uppercase tracking-wide text-mute">Repository</div>
            <div className="mt-0.5 truncate text-sm text-ink">
              {detail.repository_owner}/{detail.repository_name}
            </div>
            <div className="mt-1 flex flex-wrap gap-x-3 font-mono text-meta text-mute">
              <span>{detail.branch}</span>
              <span title={detail.commit_sha || undefined}>
                {shortSha(detail.commit_sha)}
              </span>
            </div>
          </div>

          <div>
            <div className="text-meta uppercase tracking-wide text-mute">Outcome</div>
            <p className="mt-1 text-sm font-medium text-ink">{outcome}</p>
          </div>

          <div>
            <div className="mb-2 text-meta uppercase tracking-wide text-mute">
              Activity
            </div>
            {trail.length === 0 ? (
              <Hint>No recorded activity yet.</Hint>
            ) : (
              <ol className="relative space-y-0 border-l border-line pl-4">
                {trail.map((ev) => (
                  <li key={ev.key} className="relative pb-4 last:pb-0">
                    <span className="absolute -left-[1.28rem] top-1.5 h-2.5 w-2.5 rounded-full border border-accent bg-accent-soft" />
                    <div className="text-sm font-medium text-ink">{ev.label}</div>
                    <div className="text-meta text-mute">{formatDate(ev.at)}</div>
                    {ev.detail ? (
                      <div className="mt-0.5 text-meta text-mute">{ev.detail}</div>
                    ) : null}
                  </li>
                ))}
              </ol>
            )}
          </div>

          {assertion ? (
            <div className="rounded-control border border-danger/30 bg-danger/5 px-3 py-2.5">
              <div className="text-meta uppercase tracking-wide text-danger">
                Latest failed assertion
              </div>
              <div className="mt-1 break-all font-mono text-meta text-ink">
                {assertion.test}
              </div>
              <p className="mt-1 text-sm text-mute">{assertion.message}</p>
            </div>
          ) : null}

          {draftPr?.pr_url ? (
            <div className="rounded-control border border-line bg-elevated/60 px-3 py-2.5">
              <div className="text-meta uppercase tracking-wide text-mute">Draft PR</div>
              <a
                href={draftPr.pr_url}
                target="_blank"
                rel="noreferrer"
                className="mt-1 block text-sm text-accent hover:underline"
              >
                #{draftPr.pr_number} · {draftPr.feature_branch}
              </a>
            </div>
          ) : null}

          <Link href={`/investigations/${detail.id}`} className="block">
            <Button className="w-full">Continue investigation</Button>
          </Link>
        </div>
      ) : null}
    </>
  );

  if (bare) return body;

  return (
    <Card className="flex min-h-0 flex-col overflow-hidden p-0">
      <div className="border-b border-line px-4 py-2.5">
        <h2 className="text-sm font-semibold text-ink">Case preview</h2>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-4 bt-scroll">{body}</div>
    </Card>
  );
}
