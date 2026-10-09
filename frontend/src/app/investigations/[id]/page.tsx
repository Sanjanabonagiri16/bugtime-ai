"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BookOpen,
  Code2,
  Copy,
  Download,
  ExternalLink,
  FlaskConical,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  ActivityEvent,
  EvidenceItem,
  ExecutionSummary,
  InvestigationDetail,
} from "@/lib/types";
import { cn, formatDate, shortSha } from "@/lib/utils";
import {
  Button,
  Card,
  EmptyState,
  Hint,
  InlineError,
  Skeleton,
} from "@/components/ui";
import { StatusBadge } from "@/components/status-badge";
import { CodeViewer } from "@/components/code-viewer";
import { TestsPanel } from "@/components/tests-panel";
import { CommitsPanel } from "@/components/commits-panel";
import { ChangesPanel } from "@/components/changes-panel";
import { ResizeHandle } from "@/components/resize-handle";
import { deriveMilestones, investigationOutcomeLabel } from "@/lib/milestones";
import { useToast } from "@/components/toast";

type CenterView = "overview" | "evidence" | "tests" | "commits" | "changes" | "activity";

function fileIcon(sourceType: string) {
  if (sourceType === "test") return FlaskConical;
  if (sourceType === "doc" || sourceType === "documentation") return BookOpen;
  return Code2;
}

function groupEvidence(items: EvidenceItem[]) {
  const groups: { key: string; label: string; items: EvidenceItem[] }[] = [
    { key: "code", label: "Code", items: [] },
    { key: "test", label: "Tests", items: [] },
    { key: "doc", label: "Documentation", items: [] },
  ];
  for (const item of items) {
    const t = (item.source_type || "").toLowerCase();
    if (t.includes("test")) groups[1].items.push(item);
    else if (t.includes("doc")) groups[2].items.push(item);
    else groups[0].items.push(item);
  }
  return groups.filter((g) => g.items.length > 0);
}

function MilestoneStrip({
  milestones,
}: {
  milestones: ReturnType<typeof deriveMilestones>;
}) {
  return (
    <ol className="flex flex-wrap items-center gap-1 rounded-panel border border-line bg-panel px-3 py-2">
      {milestones.map((m, i) => (
        <li key={m.key} className="flex items-center gap-1">
          {i > 0 ? <span className="mx-1 text-mute/50">→</span> : null}
          <span
            className={cn(
              "rounded-control px-2 py-1 text-meta",
              m.done && "bg-success/10 text-success",
              m.current && !m.done && "bg-accent-soft text-accent",
              !m.done && !m.current && "text-mute",
              m.upcoming && "opacity-60",
            )}
          >
            {m.label}
            {m.upcoming ? " (upcoming)" : null}
          </span>
        </li>
      ))}
    </ol>
  );
}

export default function InvestigationWorkspacePage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const { push } = useToast();
  const [inv, setInv] = useState<InvestigationDetail | null>(null);
  const [evidence, setEvidence] = useState<EvidenceItem[]>([]);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [executions, setExecutions] = useState<ExecutionSummary[]>([]);
  const [center, setCenter] = useState<CenterView>("overview");
  const [selected, setSelected] = useState<EvidenceItem | null>(null);
  const [fileContent, setFileContent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [selectedTests, setSelectedTests] = useState<Set<string>>(new Set());
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [terminalOpen, setTerminalOpen] = useState(true);
  const [leftW, setLeftW] = useState(280);
  const [rightW, setRightW] = useState(300);
  const [bottomH, setBottomH] = useState(180);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [i, e, ev, runs] = await Promise.all([
        api.getInvestigation(id),
        api.getEvidence(id),
        api.getEvents(id),
        api.listExecutions(id).catch(() => [] as ExecutionSummary[]),
      ]);
      setInv(i);
      setEvidence(e);
      setEvents(ev);
      setExecutions(runs);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  const activeRetrieval = useMemo(
    () => inv && (inv.status === "queued" || inv.status === "retrieving"),
    [inv],
  );

  useEffect(() => {
    if (!activeRetrieval) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [activeRetrieval, load]);

  useEffect(() => {
    if (!selected) {
      setFileContent("");
      return;
    }
    api
      .getFile(selected.source_file_id)
      .then((f) => setFileContent(f.content || ""))
      .catch(() => setFileContent(""));
  }, [selected]);

  const milestones = useMemo(
    () => deriveMilestones(inv, evidence, executions, selectedTests.size),
    [inv, evidence, executions, selectedTests.size],
  );

  const outcome = investigationOutcomeLabel(inv, executions);
  const groups = useMemo(() => groupEvidence(evidence), [evidence]);

  const primaryAction = useMemo(() => {
    if (!inv) return null;
    if (inv.status === "failed") {
      return {
        label: "Retry retrieval",
        run: async () => {
          await api.retryInvestigation(id);
          await load();
          push("Retrieval queued", "info");
        },
      };
    }
    if (inv.status === "queued" || inv.status === "retrieving") {
      return { label: "Find evidence", disabled: true, reason: "Retrieval in progress" };
    }
    if (evidence.length === 0 && inv.status !== "evidence_ready") {
      return {
        label: "Find evidence",
        run: async () => {
          await api.retryInvestigation(id);
          await load();
        },
      };
    }
    const hasDiscovery = executions.some(
      (e) => e.run_type === "collection" && e.status === "completed",
    );
    if (!hasDiscovery) {
      return {
        label: "Discover tests",
        run: async () => {
          setCenter("tests");
          await api.discoverTests(id);
          await load();
          push("Test discovery queued", "info");
        },
      };
    }
    const confirmed = executions.some((e) => e.confirmed);
    if (confirmed) {
      return {
        label: "Find breaking commit",
        run: () => setCenter("commits"),
      };
    }
    if (selectedTests.size === 0) {
      return {
        label: "Run selected tests",
        disabled: true,
        reason: "Select one or more tests in the Tests view",
        run: () => setCenter("tests"),
      };
    }
    return {
      label: "Run selected tests",
      run: async () => {
        setCenter("tests");
        await api.startExecution(id, [...selectedTests]);
        await load();
        push("Reproduction started", "info");
      },
    };
  }, [inv, evidence.length, executions, selectedTests, id, load, push]);

  const selectEvidence = (item: EvidenceItem) => {
    setSelected(item);
    setCenter("evidence");
    if (typeof window !== "undefined" && window.innerWidth < 1024) {
      setDrawerOpen(true);
    }
  };

  if (!inv && !error) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-96" />
        <div className="grid gap-3 lg:grid-cols-[280px_1fr_300px]">
          <Skeleton className="h-64" />
          <Skeleton className="h-64" />
          <Skeleton className="h-64" />
        </div>
      </div>
    );
  }

  if (!inv) {
    return <InlineError message={error || "Investigation not found"} onRetry={load} />;
  }

  const elapsed =
    inv.created_at &&
    Math.max(
      0,
      Math.round(
        ((inv.completed_at ? new Date(inv.completed_at) : new Date()).getTime() -
          new Date(inv.created_at).getTime()) /
          1000,
      ),
    );

  return (
    <div className="flex min-h-[calc(100vh-7rem)] flex-col gap-3">
      {/* Header */}
      <div className="flex flex-col gap-3 rounded-panel border border-line bg-panel p-4 shadow-soft lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <h1 className="text-heading tracking-tight text-ink">{inv.title}</h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-meta text-mute">
            <span>
              {inv.repository_owner}/{inv.repository_name}
            </span>
            <span className="font-mono">{inv.branch}</span>
            <span className="font-mono" title={inv.commit_sha || undefined}>
              {shortSha(inv.commit_sha)}
            </span>
            <StatusBadge status={inv.status} />
            <span className="text-ink">{outcome}</span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {inv.status === "evidence_ready" ? (
            <a href={api.exportReportUrl(id)}>
              <Button variant="secondary" size="sm">
                <Download className="h-3.5 w-3.5" />
                Export
              </Button>
            </a>
          ) : null}
          {primaryAction ? (
            <div className="flex flex-col items-end gap-1">
              <Button
                size="sm"
                disabled={busy || !!primaryAction.disabled}
                title={primaryAction.reason}
                onClick={async () => {
                  if (!primaryAction.run) return;
                  setBusy(true);
                  try {
                    await primaryAction.run();
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {primaryAction.label === "Discover tests" ||
                primaryAction.label === "Find evidence" ||
                primaryAction.label === "Find breaking commit" ? (
                  <Search className="h-3.5 w-3.5" />
                ) : (
                  <RefreshCw className="h-3.5 w-3.5" />
                )}
                {primaryAction.label}
              </Button>
              {primaryAction.disabled && primaryAction.reason ? (
                <Hint>{primaryAction.reason}</Hint>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>

      <MilestoneStrip milestones={milestones} />

      {error ? <InlineError message={error} onRetry={load} /> : null}

      {/* Desktop panes */}
      <div className="hidden min-h-0 flex-1 flex-col lg:flex">
        <div className="flex min-h-0 flex-1">
          {/* Left: evidence navigator */}
          <aside
            className="flex shrink-0 flex-col overflow-hidden rounded-panel border border-line bg-panel"
            style={{ width: leftW }}
          >
            <div className="border-b border-line px-3 py-2">
              <h2 className="text-sm font-semibold text-ink">Evidence</h2>
              <Hint>{evidence.length} candidates</Hint>
            </div>
            <div className="flex-1 overflow-auto bt-scroll">
              {evidence.length === 0 ? (
                <p className="p-3 text-sm text-mute">
                  {activeRetrieval
                    ? "Retrieving evidence…"
                    : "No evidence yet. Wait for retrieval or retry."}
                </p>
              ) : (
                groups.map((g) => (
                  <div key={g.key} className="border-b border-line last:border-0">
                    <div className="sticky top-0 bg-elevated px-3 py-1.5 text-meta font-medium uppercase tracking-wide text-mute">
                      {g.label}
                    </div>
                    <ul>
                      {g.items.map((item) => {
                        const Icon = fileIcon(item.source_type);
                        const active = selected?.id === item.id;
                        return (
                          <li key={item.id}>
                            <button
                              type="button"
                              onClick={() => selectEvidence(item)}
                              className={cn(
                                "flex w-full gap-2 border-l-2 px-3 py-2.5 text-left bt-transition",
                                active
                                  ? "border-accent bg-accent-soft"
                                  : "border-transparent hover:bg-elevated/80",
                              )}
                            >
                              <Icon className="mt-0.5 h-4 w-4 shrink-0 text-mute" />
                              <div className="min-w-0">
                                <div className="truncate font-mono text-meta text-ink">
                                  {item.file_path}
                                </div>
                                <div className="mt-0.5 text-meta text-mute">
                                  {item.symbol_name ? (
                                    <span className="text-accent">{item.symbol_name} · </span>
                                  ) : null}
                                  L{item.start_line}–L{item.end_line}
                                </div>
                                <div className="mt-0.5 truncate text-meta text-mute">
                                  {item.match_reason}
                                </div>
                              </div>
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                ))
              )}
            </div>
          </aside>

          <ResizeHandle
            direction="horizontal"
            onDrag={(d) => setLeftW((w) => Math.min(420, Math.max(220, w + d)))}
          />

          {/* Center */}
          <main className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-panel border border-line bg-panel">
            <div
              role="tablist"
              className="flex flex-wrap gap-1 border-b border-line px-2 py-1.5"
            >
              {(
                [
                  ["overview", "Overview"],
                  ["evidence", "Source"],
                  ["tests", "Tests"],
                  ["commits", "Commits"],
                  ["changes", "Changes"],
                  ["activity", "Activity"],
                ] as const
              ).map(([idTab, label]) => (
                <button
                  key={idTab}
                  type="button"
                  role="tab"
                  aria-selected={center === idTab}
                  onClick={() => setCenter(idTab)}
                  className={cn(
                    "rounded-control px-3 py-1.5 text-sm bt-transition",
                    center === idTab
                      ? "bg-accent-soft text-accent"
                      : "text-mute hover:bg-elevated hover:text-ink",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>

            <div className="min-h-0 flex-1 overflow-auto p-4 bt-scroll">
              {center === "overview" ? (
                <OverviewPanel inv={inv} evidence={evidence} />
              ) : null}

              {center === "evidence" ? (
                selected ? (
                  <EvidenceViewer
                    selected={selected}
                    fileContent={fileContent}
                    onCopy={async () => {
                      await navigator.clipboard.writeText(selected.file_path);
                      push("Path copied", "info");
                    }}
                  />
                ) : (
                  <EmptyState
                    title="Select evidence"
                    description="Choose a candidate from the navigator to open it in Monaco with highlighted lines."
                  />
                )
              ) : null}

              {center === "tests" ? (
                <TestsPanel
                  investigationId={id}
                  selected={selectedTests}
                  onSelectedChange={setSelectedTests}
                />
              ) : null}

              {center === "commits" ? <CommitsPanel investigation={inv} /> : null}

              {center === "changes" ? <ChangesPanel investigation={inv} /> : null}

              {center === "activity" ? (
                <ActivityList events={events} />
              ) : null}
            </div>
          </main>

          <ResizeHandle
            direction="horizontal"
            onDrag={(d) => setRightW((w) => Math.min(420, Math.max(240, w - d)))}
          />

          {/* Right: progress */}
          <aside
            className="flex shrink-0 flex-col overflow-hidden rounded-panel border border-line bg-panel"
            style={{ width: rightW }}
          >
            <div className="border-b border-line px-3 py-2">
              <h2 className="text-sm font-semibold">Progress</h2>
            </div>
            <div className="flex-1 space-y-4 overflow-auto p-3 bt-scroll">
              <dl className="space-y-3 text-sm">
                <div>
                  <dt className="text-meta text-mute">Outcome</dt>
                  <dd className="mt-0.5 text-ink">{outcome}</dd>
                </div>
                <div>
                  <dt className="text-meta text-mute">Stage</dt>
                  <dd className="mt-1">
                    <StatusBadge status={inv.status} />
                  </dd>
                </div>
                <div>
                  <dt className="text-meta text-mute">Elapsed</dt>
                  <dd className="font-mono">{elapsed != null ? `${elapsed}s` : "—"}</dd>
                </div>
                <div>
                  <dt className="text-meta text-mute">Evidence</dt>
                  <dd>{inv.evidence_count}</dd>
                </div>
                <div>
                  <dt className="text-meta text-mute">Index</dt>
                  <dd className="font-mono text-meta">{inv.index_version}</dd>
                </div>
                <div>
                  <dt className="text-meta text-mute">Pinned SHA</dt>
                  <dd className="break-all font-mono text-meta">{inv.commit_sha || "—"}</dd>
                </div>
              </dl>

              <div>
                <h3 className="text-sm font-medium">Execution history</h3>
                <ul className="mt-2 space-y-1.5">
                  {executions.map((r) => (
                    <li
                      key={r.id}
                      className="rounded-control border border-line px-2 py-1.5 text-meta"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="capitalize">{r.run_type}</span>
                        <StatusBadge status={r.status} />
                      </div>
                      <div className="mt-0.5 text-mute">{formatDate(r.created_at)}</div>
                      {r.confirmed ? (
                        <div className="mt-0.5 text-success">Bug match confirmed</div>
                      ) : null}
                    </li>
                  ))}
                  {executions.length === 0 ? (
                    <li className="text-mute">No executions yet.</li>
                  ) : null}
                </ul>
              </div>

              <Link
                href={`/repositories/${inv.repository_id}`}
                className="text-meta text-accent"
              >
                Open repository
              </Link>
            </div>
          </aside>
        </div>

        <ResizeHandle
          direction="vertical"
          onDrag={(d) => {
            if (!terminalOpen) setTerminalOpen(true);
            setBottomH((h) => Math.min(360, Math.max(100, h - d)));
          }}
        />

        {/* Bottom terminal strip summary */}
        <div
          className="overflow-hidden rounded-panel border border-line bg-panel"
          style={{ height: terminalOpen ? bottomH : 40 }}
        >
          <button
            type="button"
            className="flex w-full items-center justify-between border-b border-line px-3 py-2 text-left text-sm"
            onClick={() => setTerminalOpen((v) => !v)}
          >
            <span className="font-medium">Terminal / output</span>
            <span className="text-meta text-mute">
              {terminalOpen ? "Collapse" : "Expand"} · open Tests for full controls
            </span>
          </button>
          {terminalOpen ? (
            <div className="p-3">
              <Hint>
                Full terminal search, copy, and auto-scroll live in the Tests view during
                executions.
              </Hint>
              <pre className="mt-2 max-h-[calc(100%-2rem)] overflow-auto font-mono text-meta text-mute bt-scroll">
                {executions[0]
                  ? `Latest: ${executions[0].run_type} · ${executions[0].status}${
                      executions[0].run_type === "reproduction"
                        ? ` · ${executions[0].passed} passed / ${executions[0].failed} failed / ${executions[0].skipped} skipped`
                        : ""
                    }`
                  : "No execution output yet. Discover or run tests to stream logs."}
              </pre>
            </div>
          ) : null}
        </div>
      </div>

      {/* Stacked layout < 1024 */}
      <div className="flex flex-col gap-3 lg:hidden">
        <Card className="p-0 overflow-hidden">
          <div className="border-b border-line px-3 py-2 text-sm font-semibold">Evidence</div>
          <ul className="max-h-56 divide-y divide-line overflow-auto bt-scroll">
            {evidence.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className="w-full px-3 py-2 text-left text-meta"
                  onClick={() => selectEvidence(item)}
                >
                  <div className="truncate font-mono text-ink">{item.file_path}</div>
                  <div className="text-mute">
                    L{item.start_line}–L{item.end_line} · {item.match_reason}
                  </div>
                </button>
              </li>
            ))}
            {evidence.length === 0 ? (
              <li className="px-3 py-6 text-center text-sm text-mute">No evidence yet.</li>
            ) : null}
          </ul>
        </Card>

        <div
          role="tablist"
          className="flex flex-wrap gap-1 rounded-panel border border-line bg-panel p-1"
        >
          {(
            [
              ["overview", "Overview"],
              ["evidence", "Source"],
              ["tests", "Tests"],
              ["commits", "Commits"],
              ["changes", "Changes"],
              ["activity", "Activity"],
            ] as const
          ).map(([idTab, label]) => (
            <button
              key={idTab}
              type="button"
              onClick={() => setCenter(idTab)}
              className={cn(
                "rounded-control px-3 py-1.5 text-sm",
                center === idTab ? "bg-accent-soft text-accent" : "text-mute",
              )}
            >
              {label}
            </button>
          ))}
        </div>

        <Card>
          {center === "overview" ? <OverviewPanel inv={inv} evidence={evidence} /> : null}
          {center === "evidence" && selected ? (
            <EvidenceViewer
              selected={selected}
              fileContent={fileContent}
              onCopy={async () => {
                await navigator.clipboard.writeText(selected.file_path);
                push("Path copied", "info");
              }}
            />
          ) : null}
          {center === "evidence" && !selected ? (
            <p className="text-sm text-mute">Select evidence above.</p>
          ) : null}
          {center === "tests" ? (
            <TestsPanel
              investigationId={id}
              selected={selectedTests}
              onSelectedChange={setSelectedTests}
            />
          ) : null}
          {center === "commits" ? <CommitsPanel investigation={inv} /> : null}
          {center === "changes" ? <ChangesPanel investigation={inv} /> : null}
          {center === "activity" ? <ActivityList events={events} /> : null}
        </Card>

        <Card>
          <h2 className="text-sm font-semibold">Progress</h2>
          <p className="mt-2 text-sm">{outcome}</p>
          <div className="mt-2">
            <StatusBadge status={inv.status} />
          </div>
        </Card>
      </div>

      {/* Mobile evidence drawer */}
      {drawerOpen && selected ? (
        <div className="fixed inset-0 z-50 flex flex-col bg-canvas/80 p-3 backdrop-blur-sm lg:hidden">
          <Card className="flex max-h-full flex-1 flex-col overflow-hidden p-0">
            <div className="flex items-center justify-between border-b border-line px-3 py-2">
              <span className="truncate font-mono text-meta">{selected.file_path}</span>
              <Button size="sm" variant="ghost" onClick={() => setDrawerOpen(false)}>
                <X className="h-4 w-4" />
              </Button>
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-3">
              <EvidenceViewer
                selected={selected}
                fileContent={fileContent}
                onCopy={async () => {
                  await navigator.clipboard.writeText(selected.file_path);
                  push("Path copied", "info");
                }}
              />
            </div>
          </Card>
        </div>
      ) : null}
    </div>
  );
}

function OverviewPanel({
  inv,
  evidence,
}: {
  inv: InvestigationDetail;
  evidence: EvidenceItem[];
}) {
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold">Bug report</h2>
        <p className="mt-2 whitespace-pre-wrap text-sm text-ink">{inv.description}</p>
      </div>
      {(inv.expected_behavior || inv.actual_behavior) && (
        <div className="grid gap-4 md:grid-cols-2">
          <div className="rounded-panel border border-line bg-elevated p-3">
            <h3 className="text-sm font-medium">Expected</h3>
            <p className="mt-2 whitespace-pre-wrap text-sm text-mute">
              {inv.expected_behavior || "—"}
            </p>
          </div>
          <div className="rounded-panel border border-line bg-elevated p-3">
            <h3 className="text-sm font-medium">Actual</h3>
            <p className="mt-2 whitespace-pre-wrap text-sm text-mute">
              {inv.actual_behavior || "—"}
            </p>
          </div>
        </div>
      )}
      {inv.logs ? (
        <div>
          <h3 className="text-sm font-medium">Logs</h3>
          <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-panel border border-line bg-canvas p-3 font-mono text-meta text-mute bt-scroll">
            {inv.logs}
          </pre>
        </div>
      ) : null}
      <div className="rounded-panel border border-line bg-elevated p-3">
        <h3 className="text-sm font-medium">Retrieval outcome</h3>
        <p className="mt-2 text-sm text-mute">
          {inv.status === "evidence_ready"
            ? evidence.length
              ? `${evidence.length} candidate evidence items retrieved.`
              : "No relevant evidence found. Refine the report and create a new investigation."
            : inv.status === "failed"
              ? inv.error_message || "Retrieval failed."
              : "Retrieval in progress…"}
        </p>
        <p className="mt-3 text-meta text-mute">
          Retrieved files are candidates, not a verified root cause. Use Tests to reproduce with
          existing pytest tests.
        </p>
      </div>
    </div>
  );
}

function EvidenceViewer({
  selected,
  fileContent,
  onCopy,
}: {
  selected: EvidenceItem;
  fileContent: string;
  onCopy: () => void;
}) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-sm text-ink">{selected.file_path}</div>
          <div className="text-meta text-mute">
            L{selected.start_line}–L{selected.end_line}
            {selected.symbol_name ? ` · ${selected.symbol_name}` : ""}
          </div>
        </div>
        <Button size="sm" variant="secondary" onClick={onCopy}>
          <Copy className="h-3.5 w-3.5" />
          Copy path
        </Button>
        <a href={selected.github_url} target="_blank" rel="noreferrer">
          <Button size="sm" variant="secondary">
            <ExternalLink className="h-3.5 w-3.5" />
            GitHub @ pin
          </Button>
        </a>
      </div>
      <CodeViewer
        path={selected.file_path}
        content={fileContent}
        highlightStart={selected.start_line}
        highlightEnd={selected.end_line}
        height="480px"
        showPath={false}
      />
      <Card elevated>
        <div className="text-meta text-mute">Match reason</div>
        <p className="mt-1 text-sm">{selected.match_reason}</p>
        {selected.snippet ? (
          <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap font-mono text-meta text-mute bt-scroll">
            {selected.snippet}
          </pre>
        ) : null}
      </Card>
    </div>
  );
}

function ActivityList({ events }: { events: ActivityEvent[] }) {
  return (
    <ul className="space-y-3">
      {events.map((ev) => (
        <li key={ev.id} className="border-b border-line pb-3 text-sm last:border-0">
          <div className="flex flex-wrap gap-2 text-meta text-mute">
            <span>{formatDate(ev.created_at)}</span>
            <span className="uppercase">{ev.stage}</span>
            <span className={ev.level === "error" ? "text-danger" : ""}>{ev.level}</span>
          </div>
          <p className="mt-1 text-ink">{ev.message}</p>
        </li>
      ))}
      {events.length === 0 ? <li className="text-mute">No activity events yet.</li> : null}
    </ul>
  );
}
