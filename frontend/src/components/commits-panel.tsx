"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  ExternalLink,
  GitCommitHorizontal,
  Loader2,
  Search,
  SkipForward,
  Square,
  XCircle,
} from "lucide-react";
import Editor, { DiffEditor } from "@monaco-editor/react";
import { useTheme } from "next-themes";
import { api } from "@/lib/api";
import type {
  CommitDiff,
  CommitSearchDetail,
  CommitSearchStep,
  ExecutionDetail,
  InvestigationDetail,
  RepoCommit,
} from "@/lib/types";
import { cn, formatDate, shortSha } from "@/lib/utils";
import {
  Button,
  Card,
  EmptyState,
  Hint,
  InlineError,
  Input,
  Label,
} from "@/components/ui";
import { StatusBadge } from "@/components/status-badge";
import { useToast } from "@/components/toast";

const ACTIVE = new Set(["queued", "validating", "searching", "verifying"]);

function classificationIcon(c: string) {
  if (c === "good") return CheckCircle2;
  if (c === "bad") return XCircle;
  if (c === "skip") return SkipForward;
  if (c === "testing") return Loader2;
  return AlertTriangle;
}

function classificationLabel(c: string) {
  if (c === "good") return "Good";
  if (c === "bad") return "Target failure";
  if (c === "skip") return "Skipped";
  if (c === "testing") return "Testing";
  if (c === "abort") return "Aborted";
  return c;
}

function outcomeBanner(search: CommitSearchDetail) {
  if (search.outcome === "verified_boundary") {
    return {
      tone: "success" as const,
      title: "Verified failure boundary",
      body: "The reproduction passes at the parent and fails at this commit under the recorded environment.",
    };
  }
  if (search.outcome === "inconclusive") {
    return {
      tone: "warn" as const,
      title: "Inconclusive result",
      body: search.error_message || "Skipped revisions prevented a unique verified boundary.",
    };
  }
  if (search.outcome === "invalid_range") {
    return {
      tone: "danger" as const,
      title: "Invalid search range",
      body: search.error_message || "Endpoint validation failed.",
    };
  }
  if (search.outcome === "inconsistent_reproduction") {
    return {
      tone: "danger" as const,
      title: "Inconsistent reproduction",
      body: search.error_message || "Repeated endpoint runs disagreed.",
    };
  }
  if (search.outcome === "unsupported_history") {
    return {
      tone: "warn" as const,
      title: "Unsupported history",
      body: "Merge history is not supported in this milestone.",
    };
  }
  if (search.outcome === "budget_exhausted") {
    return {
      tone: "warn" as const,
      title: "Budget exhausted",
      body: search.error_message || "Execution or time budget was exhausted.",
    };
  }
  if (search.status === "cancelled") {
    return {
      tone: "warn" as const,
      title: "Cancelled",
      body: "Search cancelled by user.",
    };
  }
  if (search.status === "timed_out") {
    return {
      tone: "danger" as const,
      title: "Timed out",
      body: search.error_message || "Overall search budget elapsed.",
    };
  }
  return null;
}

export function CommitsPanel({
  investigation,
}: {
  investigation: InvestigationDetail;
}) {
  const { push } = useToast();
  const { resolvedTheme } = useTheme();
  const [searches, setSearches] = useState<CommitSearchDetail[]>([]);
  const [active, setActive] = useState<CommitSearchDetail | null>(null);
  const [steps, setSteps] = useState<CommitSearchStep[]>([]);
  const [diff, setDiff] = useState<CommitDiff | null>(null);
  const [selectedFile, setSelectedFile] = useState(0);
  const [selectedStep, setSelectedStep] = useState<CommitSearchStep | null>(null);
  const [stepOutput, setStepOutput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [setupOpen, setSetupOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  // Setup form
  const [confirmed, setConfirmed] = useState<ExecutionDetail | null>(null);
  const [commits, setCommits] = useState<RepoCommit[]>([]);
  const [goodSha, setGoodSha] = useState("");
  const [testId, setTestId] = useState("");
  const [excType, setExcType] = useState("AssertionError");
  const [frag, setFrag] = useState("");

  const refresh = async () => {
    const list = await api.listCommitSearches(investigation.id);
    const details = await Promise.all(list.map((s) => api.getCommitSearch(s.id)));
    setSearches(details);
    const focus =
      details.find((d) => ACTIVE.has(d.status)) ||
      details[0] ||
      null;
    if (focus) {
      setActive(focus);
      const st = await api.listCommitSearchSteps(focus.id);
      setSteps(st);
      if (
        focus.verified_sha &&
        focus.parent_sha &&
        ["completed"].includes(focus.status)
      ) {
        try {
          const d = await api.getCommitSearchDiff(focus.id);
          setDiff(d);
          setSelectedFile(0);
        } catch {
          setDiff(null);
        }
      } else {
        setDiff(null);
      }
    }
  };

  useEffect(() => {
    refresh().catch((e: Error) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [investigation.id]);

  const polling = !!(active && ACTIVE.has(active.status));
  useEffect(() => {
    if (!polling) return;
    const t = setInterval(() => {
      refresh().catch(() => undefined);
    }, 2500);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [polling, active?.id]);

  const openSetup = async () => {
    setError(null);
    setBusy(true);
    try {
      const runs = await api.listExecutions(investigation.id);
      const confirmedRun = runs.find((r) => r.confirmed);
      if (!confirmedRun) {
        setError("Confirm a reproduction failure on the Tests tab before searching.");
        return;
      }
      const detail = await api.getExecution(confirmedRun.id);
      setConfirmed(detail);
      const tests = detail.confirmation?.matching_failed_tests || [];
      setTestId(tests[0] || "");
      const failed = detail.test_results.find(
        (t) => t.test_identifier === tests[0] && (t.outcome === "failed" || t.outcome === "error"),
      );
      if (failed?.failure_message) setFrag(failed.failure_message.slice(0, 200));
      setExcType("AssertionError");
      const hist = await api.listRepoCommits(
        investigation.repository_id,
        investigation.commit_sha || "HEAD",
      );
      setCommits(hist.items);
      // Suggest a commit a few revisions before HEAD
      const suggestion = hist.items[Math.min(6, hist.items.length - 1)];
      setGoodSha(suggestion?.sha || "");
      setSetupOpen(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const banner = active ? outcomeBanner(active) : null;
  const elapsedSec =
    active?.started_at
      ? Math.max(
          0,
          Math.round(
            ((active.completed_at ? new Date(active.completed_at) : new Date()).getTime() -
              new Date(active.started_at).getTime()) /
              1000,
          ),
        )
      : null;
  const remainingBudget =
    active && elapsedSec != null
      ? Math.max(0, active.max_duration_seconds - elapsedSec)
      : null;

  const file = diff?.files[selectedFile];
  const original = useMemo(() => {
    if (!file?.patch) return "";
    return file.patch
      .split("\n")
      .filter((l) => l.startsWith("-") && !l.startsWith("---"))
      .map((l) => l.slice(1))
      .join("\n");
  }, [file]);
  const modified = useMemo(() => {
    if (!file?.patch) return "";
    return file.patch
      .split("\n")
      .filter((l) => l.startsWith("+") && !l.startsWith("+++"))
      .map((l) => l.slice(1))
      .join("\n");
  }, [file]);

  const githubCommitUrl = (sha: string) => {
    const base = investigation.github_url.replace(/\.git$/, "");
    return `${base}/commit/${sha}`;
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-ink">Find breaking commit</h2>
          <Hint>
            Search assumes the bug was introduced once within the selected range. Bisect does not
            prove there were no intermediate fixes and reintroductions.
          </Hint>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            disabled={busy || polling}
            onClick={openSetup}
            title={!confirmed && !setupOpen ? "Requires a confirmed reproduction" : undefined}
          >
            <Search className="h-4 w-4" />
            Find breaking commit
          </Button>
          {active && ACTIVE.has(active.status) ? (
            <Button
              variant="danger"
              onClick={async () => {
                await api.cancelCommitSearch(active.id);
                await refresh();
                push("Cancellation requested", "info");
              }}
            >
              <Square className="h-4 w-4" />
              Cancel
            </Button>
          ) : null}
          {active && active.status === "completed" ? (
            <a href={api.commitSearchReportUrl(active.id)}>
              <Button variant="secondary">
                <Download className="h-4 w-4" />
                Export report
              </Button>
            </a>
          ) : null}
        </div>
      </div>

      {error ? <InlineError message={error} onRetry={() => refresh()} /> : null}

      {!active && !setupOpen ? (
        <EmptyState
          title="No commit search yet"
          description="After a developer-confirmed reproduction, choose a candidate good commit and search for the introducing revision."
          action={
            <Button onClick={openSetup} disabled={busy}>
              Find breaking commit
            </Button>
          }
        />
      ) : null}

      {active ? (
        <>
          <Card elevated className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <div className="text-meta text-mute">Good / Bad</div>
              <div className="mt-1 font-mono text-meta">
                {shortSha(active.good_sha)} → {shortSha(active.bad_sha)}
              </div>
            </div>
            <div>
              <div className="text-meta text-mute">Stage</div>
              <div className="mt-1 flex items-center gap-2">
                <StatusBadge status={active.status} />
                {polling ? <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" /> : null}
              </div>
            </div>
            <div>
              <div className="text-meta text-mute">Tested / budget</div>
              <div className="mt-1 text-sm">
                {active.commits_tested} commits · {active.executions_used}/{active.max_executions}{" "}
                runs
              </div>
            </div>
            <div>
              <div className="text-meta text-mute">Time</div>
              <div className="mt-1 text-sm">
                {elapsedSec != null ? `${elapsedSec}s elapsed` : "—"}
                {remainingBudget != null && polling ? ` · ${remainingBudget}s left` : ""}
              </div>
            </div>
          </Card>

          {banner ? (
            <div
              className={cn(
                "rounded-panel border px-4 py-3",
                banner.tone === "success" && "border-success/40 bg-success/10 text-success",
                banner.tone === "warn" && "border-warn/40 bg-warn/10 text-warn",
                banner.tone === "danger" && "border-danger/40 bg-danger/10 text-danger",
              )}
            >
              <div className="text-sm font-semibold">{banner.title}</div>
              <p className="mt-1 text-sm opacity-90">{banner.body}</p>
            </div>
          ) : null}

          {active.verified_sha ? (
            <Card>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="text-meta uppercase tracking-wide text-mute">Candidate</div>
                  <div className="mt-1 font-mono text-sm text-ink">{active.verified_sha}</div>
                  {active.verified_metadata ? (
                    <div className="mt-2 space-y-1 text-sm">
                      <div>{active.verified_metadata.subject}</div>
                      <div className="text-meta text-mute">
                        {active.verified_metadata.author_name} ·{" "}
                        {formatDate(active.verified_metadata.authored_at)}
                      </div>
                    </div>
                  ) : null}
                  {active.parent_sha ? (
                    <div className="mt-2 text-meta text-mute">
                      Parent {shortSha(active.parent_sha)}
                    </div>
                  ) : null}
                </div>
                <a
                  href={githubCommitUrl(active.verified_sha)}
                  target="_blank"
                  rel="noreferrer"
                >
                  <Button size="sm" variant="secondary">
                    <ExternalLink className="h-3.5 w-3.5" />
                    GitHub
                  </Button>
                </a>
              </div>
            </Card>
          ) : null}

          <div className="grid gap-4 lg:grid-cols-[1fr_1.2fr]">
            <Card className="p-0 overflow-hidden">
              <div className="border-b border-line px-3 py-2 text-sm font-medium">
                Chronological probes
              </div>
              <ul className="max-h-80 divide-y divide-line overflow-auto bt-scroll">
                {steps.map((s) => {
                  const Icon = classificationIcon(s.classification);
                  return (
                    <li key={s.id}>
                      <button
                        type="button"
                        className={cn(
                          "flex w-full items-start gap-2 px-3 py-2.5 text-left bt-transition hover:bg-elevated/70",
                          selectedStep?.id === s.id && "bg-accent-soft",
                        )}
                        onClick={async () => {
                          setSelectedStep(s);
                          if (s.execution_id) {
                            try {
                              const page = await api.getExecutionOutput(s.execution_id);
                              setStepOutput(page.content);
                            } catch {
                              setStepOutput("");
                            }
                          } else {
                            setStepOutput("");
                          }
                        }}
                      >
                        <Icon
                          className={cn(
                            "mt-0.5 h-4 w-4 shrink-0",
                            s.classification === "good" && "text-success",
                            s.classification === "bad" && "text-danger",
                            s.classification === "skip" && "text-warn",
                            s.classification === "testing" && "animate-spin text-accent",
                          )}
                        />
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-mono text-meta text-ink">
                              {shortSha(s.sha)}
                            </span>
                            <span className="text-meta text-mute">
                              {classificationLabel(s.classification)}
                            </span>
                            <span className="text-meta text-mute capitalize">{s.phase.replace(/_/g, " ")}</span>
                          </div>
                          {s.classification_reason ? (
                            <div className="mt-0.5 truncate text-meta text-mute">
                              {s.classification_reason}
                            </div>
                          ) : null}
                        </div>
                      </button>
                    </li>
                  );
                })}
                {steps.length === 0 ? (
                  <li className="px-3 py-8 text-center text-sm text-mute">
                    Waiting for probe results…
                  </li>
                ) : null}
              </ul>
            </Card>

            <Card>
              <h3 className="text-sm font-medium">Execution evidence</h3>
              {selectedStep ? (
                <div className="mt-3 space-y-2">
                  <div className="font-mono text-meta">
                    {selectedStep.sha} · {classificationLabel(selectedStep.classification)}
                  </div>
                  <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-control border border-line bg-canvas p-3 font-mono text-meta text-mute bt-scroll">
                    {stepOutput || "No output captured for this step."}
                  </pre>
                </div>
              ) : (
                <p className="mt-3 text-sm text-mute">Select a probed commit to view logs.</p>
              )}
            </Card>
          </div>

          {diff && diff.files.length > 0 ? (
            <Card className="p-0 overflow-hidden">
              <div className="border-b border-line px-3 py-2 text-sm font-medium">
                Parent vs candidate diff
              </div>
              <div className="grid lg:grid-cols-[220px_1fr]">
                <ul className="max-h-[420px] divide-y divide-line overflow-auto border-r border-line bt-scroll">
                  {diff.files.map((f, i) => (
                    <li key={f.path}>
                      <button
                        type="button"
                        className={cn(
                          "flex w-full flex-col gap-0.5 px-3 py-2 text-left text-meta bt-transition",
                          i === selectedFile ? "bg-accent-soft" : "hover:bg-elevated/70",
                        )}
                        onClick={() => setSelectedFile(i)}
                      >
                        <span className="truncate font-mono text-ink">{f.path}</span>
                        <span className="text-mute">
                          {f.status}
                          {f.in_evidence ? " · in evidence" : ""}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
                <div className="min-w-0 p-2">
                  {file ? (
                    file.patch ? (
                      <DiffEditor
                        height="400px"
                        theme={resolvedTheme === "light" ? "light" : "vs-dark"}
                        language={
                          file.path.endsWith(".py")
                            ? "python"
                            : file.path.endsWith(".md")
                              ? "markdown"
                              : "plaintext"
                        }
                        original={original}
                        modified={modified}
                        options={{
                          readOnly: true,
                          renderSideBySide: true,
                          minimap: { enabled: false },
                          fontSize: 12,
                          fontFamily: "var(--font-geist-mono), ui-monospace, monospace",
                        }}
                      />
                    ) : (
                      <Editor
                        height="400px"
                        theme={resolvedTheme === "light" ? "light" : "vs-dark"}
                        language="diff"
                        value={`# ${file.path}\n(no textual patch)`}
                        options={{ readOnly: true, minimap: { enabled: false } }}
                      />
                    )
                  ) : null}
                </div>
              </div>
            </Card>
          ) : null}

          {searches.length > 1 ? (
            <Card>
              <h3 className="text-sm font-medium">Previous searches</h3>
              <ul className="mt-2 space-y-1">
                {searches.map((s) => (
                  <li key={s.id}>
                    <button
                      type="button"
                      className="flex w-full items-center justify-between rounded-control border border-line px-3 py-2 text-left text-sm hover:border-accent/40"
                      onClick={async () => {
                        setActive(s);
                        setSteps(await api.listCommitSearchSteps(s.id));
                        if (s.verified_sha && s.parent_sha) {
                          try {
                            setDiff(await api.getCommitSearchDiff(s.id));
                          } catch {
                            setDiff(null);
                          }
                        }
                      }}
                    >
                      <span className="text-meta text-mute">{formatDate(s.created_at)}</span>
                      <StatusBadge status={s.status} label={s.outcome || s.status} />
                    </button>
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}
        </>
      ) : null}

      {setupOpen ? (
        <div className="fixed inset-0 z-50 flex justify-end bg-canvas/70 backdrop-blur-sm">
          <div className="flex h-full w-full max-w-lg flex-col border-l border-line bg-panel shadow-panel">
            <div className="flex items-center justify-between border-b border-line px-4 py-3">
              <div className="flex items-center gap-2">
                <GitCommitHorizontal className="h-4 w-4 text-accent" />
                <h2 className="text-sm font-semibold">Commit search setup</h2>
              </div>
              <Button size="sm" variant="ghost" onClick={() => setSetupOpen(false)}>
                Close
              </Button>
            </div>
            <div className="flex-1 space-y-4 overflow-auto p-4 bt-scroll">
              <Hint>
                Search assumes the bug was introduced once within the selected range. The good
                commit is a candidate until execution verifies it.
              </Hint>
              <div>
                <Label>Bad commit (pinned)</Label>
                <Input
                  readOnly
                  className="font-mono"
                  value={investigation.commit_sha || ""}
                />
              </div>
              <div>
                <Label hint="Select from history or paste a full/abbreviated SHA.">
                  Candidate good commit
                </Label>
                <Input
                  className="font-mono"
                  value={goodSha}
                  onChange={(e) => setGoodSha(e.target.value)}
                  placeholder="SHA"
                />
                <ul className="mt-2 max-h-40 overflow-auto rounded-control border border-line bt-scroll">
                  {commits.map((c) => (
                    <li key={c.sha}>
                      <button
                        type="button"
                        className={cn(
                          "flex w-full flex-col gap-0.5 px-3 py-2 text-left text-meta hover:bg-elevated",
                          goodSha === c.sha && "bg-accent-soft",
                        )}
                        onClick={() => setGoodSha(c.sha)}
                      >
                        <span className="font-mono text-ink">
                          {c.short_sha} · {c.subject}
                        </span>
                        <span className="text-mute">
                          {c.author_name} · {formatDate(c.authored_at)}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <Label>Reproduction test</Label>
                <select
                  className="w-full rounded-control border border-line bg-canvas px-3 py-2 text-sm"
                  value={testId}
                  onChange={(e) => setTestId(e.target.value)}
                >
                  {(confirmed?.confirmation?.matching_failed_tests || []).map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <Label>Failure matcher — exception type</Label>
                <Input value={excType} onChange={(e) => setExcType(e.target.value)} />
              </div>
              <div>
                <Label hint="Stable assertion / message fragment used to recognize the target bug.">
                  Failure matcher — message fragment
                </Label>
                <Input value={frag} onChange={(e) => setFrag(e.target.value)} />
              </div>
              <div className="rounded-control border border-line bg-elevated/50 px-3 py-2 text-meta text-mute">
                Limits: 20 distinct commits · 30 executions · 10 minutes overall
              </div>
            </div>
            <div className="border-t border-line p-4">
              <Button
                className="w-full"
                disabled={busy || !goodSha || !testId || !confirmed}
                onClick={async () => {
                  if (!confirmed) return;
                  setBusy(true);
                  setError(null);
                  try {
                    const created = await api.createCommitSearch(investigation.id, {
                      good_sha: goodSha.trim(),
                      test_node_id: testId,
                      confirmed_execution_id: confirmed.id,
                      failure_exception_type: excType.trim() || undefined,
                      failure_message_fragment: frag.trim() || undefined,
                    });
                    setSetupOpen(false);
                    setActive(created);
                    await refresh();
                    push("Commit search queued", "info");
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Start search
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
