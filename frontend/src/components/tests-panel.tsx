"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Copy,
  Loader2,
  Play,
  Search,
  Square,
  TerminalSquare,
} from "lucide-react";
import { api } from "@/lib/api";
import type { ExecutionDetail, ExecutionSummary } from "@/lib/types";
import { formatDate } from "@/lib/utils";
import { Button, Card, Hint, Input, InlineError, Textarea } from "@/components/ui";
import { StatusBadge } from "@/components/status-badge";
import { useToast } from "@/components/toast";
import { getTerminalAutoScroll, setTerminalAutoScroll } from "@/lib/prefs";

const ACTIVE = new Set(["queued", "preparing", "running"]);

function outcomeLabel(run: ExecutionDetail | ExecutionSummary) {
  if (run.confirmed) return "Bug reproduced — developer confirmed";
  if (run.status === "timed_out") return "Timed out";
  if (run.status === "cancelled") return "Cancelled";
  if (run.error_category === "environment_setup_unsupported") return "Environment error";
  if (run.run_type === "reproduction" && run.status === "completed" && run.failed > 0) {
    return "Failure observed";
  }
  if (run.run_type === "reproduction" && run.status === "completed" && run.failed === 0) {
    return "Selected tests passed";
  }
  return null;
}

export function TestsPanel({
  investigationId,
  selected,
  onSelectedChange,
}: {
  investigationId: string;
  selected: Set<string>;
  onSelectedChange: (next: Set<string>) => void;
}) {
  const { push } = useToast();
  const [runs, setRuns] = useState<ExecutionSummary[]>([]);
  const [active, setActive] = useState<ExecutionDetail | null>(null);
  const [discoveryDetail, setDiscoveryDetail] = useState<ExecutionDetail | null>(null);
  const [query, setQuery] = useState("");
  const [output, setOutput] = useState("");
  const [outputQuery, setOutputQuery] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmTests, setConfirmTests] = useState<Set<string>>(new Set());
  const [explanation, setExplanation] = useState("");
  const terminalRef = useRef<HTMLPreElement>(null);
  const userScrolledUp = useRef(false);

  useEffect(() => {
    setAutoScroll(getTerminalAutoScroll());
  }, []);

  const refresh = async () => {
    const list = await api.listExecutions(investigationId);
    setRuns(list);
    const latestActive = list.find((r) => ACTIVE.has(r.status));
    const focusId =
      latestActive?.id ||
      list.find((r) => r.run_type === "reproduction")?.id ||
      list[0]?.id;
    if (focusId) {
      const detail = await api.getExecution(focusId);
      setActive(detail);
      const page = await api.getExecutionOutput(detail.id);
      setOutput(page.content);
    }
    const latestDiscovery = list.find(
      (r) => r.run_type === "collection" && r.status === "completed",
    );
    if (latestDiscovery) {
      setDiscoveryDetail(await api.getExecution(latestDiscovery.id));
    }
  };

  useEffect(() => {
    refresh().catch((e: Error) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [investigationId]);

  const polling = !!(active && ACTIVE.has(active.status));
  useEffect(() => {
    if (!polling) return;
    const t = setInterval(() => {
      refresh().catch(() => undefined);
    }, 2000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [polling, active?.id]);

  useEffect(() => {
    if (!autoScroll || userScrolledUp.current) return;
    const el = terminalRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [output, autoScroll]);

  const tests = useMemo(() => {
    const items =
      discoveryDetail?.test_results.filter((t) => t.outcome === "collected") || [];
    const q = query.trim().toLowerCase();
    const filtered = q
      ? items.filter((t) => t.test_identifier.toLowerCase().includes(q))
      : items;
    return [...filtered].sort((a, b) => {
      if (a.in_evidence !== b.in_evidence) return a.in_evidence ? -1 : 1;
      return a.test_identifier.localeCompare(b.test_identifier);
    });
  }, [discoveryDetail, query]);

  const failedFromActive =
    active?.run_type === "reproduction"
      ? active.test_results.filter((t) => t.outcome === "failed" || t.outcome === "error")
      : [];

  const filteredOutput = useMemo(() => {
    if (!outputQuery.trim()) return output;
    const q = outputQuery.toLowerCase();
    return output
      .split("\n")
      .filter((line) => line.toLowerCase().includes(q))
      .join("\n");
  }, [output, outputQuery]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-ink">Reproduction</h2>
          <Hint>
            Discover existing pytest tests, run them at the pinned commit, then confirm a matching
            failure.
          </Hint>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            disabled={busy || polling}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await api.discoverTests(investigationId);
                await refresh();
                push("Test discovery queued", "info");
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <Search className="h-4 w-4" />
            Discover tests
          </Button>
          <Button
            disabled={busy || polling || selected.size === 0}
            title={selected.size === 0 ? "Select one or more tests first" : undefined}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await api.startExecution(investigationId, [...selected]);
                await refresh();
                push("Reproduction started", "info");
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <Play className="h-4 w-4" />
            Run selected tests
          </Button>
          {active && ACTIVE.has(active.status) ? (
            <Button
              variant="danger"
              onClick={async () => {
                await api.cancelExecution(active.id);
                await refresh();
                push("Cancellation requested", "info");
              }}
            >
              <Square className="h-4 w-4" />
              Cancel
            </Button>
          ) : null}
        </div>
      </div>

      {selected.size === 0 ? (
        <Hint>Run selected tests is disabled until you choose at least one test.</Hint>
      ) : null}

      {error ? <InlineError message={error} onRetry={() => refresh()} /> : null}

      {active ? (
        <Card elevated className="grid gap-3 sm:grid-cols-4">
          <div>
            <div className="text-meta text-mute">Stage</div>
            <div className="mt-1 flex items-center gap-2">
              <StatusBadge status={active.status} />
              {polling ? <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" /> : null}
            </div>
          </div>
          <div>
            <div className="text-meta text-mute">Counts</div>
            <div className="mt-1 text-sm">
              {active.run_type === "collection"
                ? `${active.collected} collected`
                : `${active.passed} passed · ${active.failed} failed · ${active.skipped} skipped`}
            </div>
          </div>
          <div>
            <div className="text-meta text-mute">Duration</div>
            <div className="mt-1 font-mono text-sm">
              {active.duration_ms != null ? `${(active.duration_ms / 1000).toFixed(2)}s` : "—"}
            </div>
          </div>
          <div>
            <div className="text-meta text-mute">Outcome</div>
            <div className="mt-1 text-sm text-ink">{outcomeLabel(active) || "In progress"}</div>
          </div>
        </Card>
      ) : null}

      <Card className="p-0 overflow-hidden">
        <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
          <h3 className="text-sm font-medium">Tests</h3>
          <Input
            className="max-w-xs"
            placeholder="Search tests"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <ul className="max-h-64 divide-y divide-line overflow-auto bt-scroll">
          {tests.map((t) => {
            const [file, name] = t.test_identifier.includes("::")
              ? t.test_identifier.split("::")
              : [t.test_identifier, t.test_identifier];
            const latest =
              active?.run_type === "reproduction"
                ? active.test_results.find((r) => r.test_identifier === t.test_identifier)
                : undefined;
            return (
              <li key={t.test_identifier}>
                <label className="flex cursor-pointer items-start gap-3 px-3 py-2.5 hover:bg-elevated/70">
                  <input
                    type="checkbox"
                    className="mt-1 accent-[var(--bt-accent)]"
                    checked={selected.has(t.test_identifier)}
                    onChange={(e) => {
                      const next = new Set(selected);
                      if (e.target.checked) next.add(t.test_identifier);
                      else next.delete(t.test_identifier);
                      onSelectedChange(next);
                    }}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm text-ink">{name}</div>
                    <div className="truncate font-mono text-meta text-mute">{file}</div>
                  </div>
                  <div className="text-right">
                    {latest ? (
                      <StatusBadge
                        status={
                          latest.outcome === "passed"
                            ? "completed"
                            : latest.outcome === "failed"
                              ? "failed"
                              : "queued"
                        }
                        label={latest.outcome}
                      />
                    ) : (
                      <span className="text-meta text-mute">
                        {t.in_evidence ? "in evidence" : "collected"}
                      </span>
                    )}
                    {latest?.duration_ms != null ? (
                      <div className="mt-1 font-mono text-meta text-mute">
                        {(latest.duration_ms / 1000).toFixed(2)}s
                      </div>
                    ) : null}
                  </div>
                </label>
              </li>
            );
          })}
          {tests.length === 0 ? (
            <li className="px-3 py-8 text-center text-sm text-mute">
              No collected tests yet. Run Discover tests.
            </li>
          ) : null}
        </ul>
      </Card>

      {failedFromActive.length > 0 ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <h3 className="text-sm font-medium">Failure details</h3>
            <ul className="mt-3 space-y-3">
              {failedFromActive.map((t) => (
                <li
                  key={t.id}
                  className="rounded-panel border border-danger/30 bg-danger/5 p-3"
                >
                  <div className="font-mono text-meta text-danger">{t.test_identifier}</div>
                  <p className="mt-1 text-sm">{t.failure_message || "Failure observed"}</p>
                  {t.traceback ? (
                    <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap font-mono text-meta text-mute bt-scroll">
                      {t.traceback}
                    </pre>
                  ) : null}
                </li>
              ))}
            </ul>
          </Card>

          <Card>
            <h3 className="text-sm font-medium">Bug-match confirmation</h3>
            {active?.confirmed ? (
              <p className="mt-3 text-sm text-success">
                Bug reproduced — developer confirmed
              </p>
            ) : (
              <div className="mt-3 space-y-3">
                <Hint>
                  A failing test means “Failure observed.” Confirm only if it matches the reported
                  bug.
                </Hint>
                <ul className="space-y-1 text-sm">
                  {failedFromActive.map((t) => (
                    <li key={t.id}>
                      <label className="flex gap-2">
                        <input
                          type="checkbox"
                          className="accent-[var(--bt-accent)]"
                          checked={confirmTests.has(t.test_identifier)}
                          onChange={(e) => {
                            setConfirmTests((prev) => {
                              const next = new Set(prev);
                              if (e.target.checked) next.add(t.test_identifier);
                              else next.delete(t.test_identifier);
                              return next;
                            });
                          }}
                        />
                        <span className="font-mono text-meta">{t.test_identifier}</span>
                      </label>
                    </li>
                  ))}
                </ul>
                <Textarea
                  rows={3}
                  placeholder="Short explanation of why this failure matches the bug"
                  value={explanation}
                  onChange={(e) => setExplanation(e.target.value)}
                />
                <Button
                  disabled={confirmTests.size === 0 || explanation.trim().length < 5}
                  title={
                    confirmTests.size === 0 || explanation.trim().length < 5
                      ? "Select a failed test and add an explanation"
                      : undefined
                  }
                  onClick={async () => {
                    if (!active) return;
                    try {
                      await api.confirmReproduction(active.id, {
                        matching_failed_tests: [...confirmTests],
                        explanation: explanation.trim(),
                      });
                      await refresh();
                      push("Bug match confirmed", "success");
                    } catch (e) {
                      setError((e as Error).message);
                    }
                  }}
                >
                  Confirm bug reproduced
                </Button>
              </div>
            )}
          </Card>
        </div>
      ) : null}

      <Card className="p-0 overflow-hidden">
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
          <TerminalSquare className="h-4 w-4 text-mute" />
          <span className="text-sm font-medium">Terminal</span>
          <Input
            className="ml-auto max-w-[200px]"
            placeholder="Search output"
            value={outputQuery}
            onChange={(e) => setOutputQuery(e.target.value)}
          />
          <Button
            size="sm"
            variant="ghost"
            onClick={async () => {
              await navigator.clipboard.writeText(output);
              push("Output copied", "info");
            }}
          >
            <Copy className="h-3.5 w-3.5" />
            Copy
          </Button>
          <label className="flex items-center gap-1.5 text-meta text-mute">
            <input
              type="checkbox"
              className="accent-[var(--bt-accent)]"
              checked={autoScroll}
              onChange={(e) => {
                setAutoScroll(e.target.checked);
                setTerminalAutoScroll(e.target.checked);
                userScrolledUp.current = false;
              }}
            />
            Auto-scroll
          </label>
        </div>
        <pre
          ref={terminalRef}
          className="max-h-64 overflow-auto whitespace-pre-wrap bg-canvas p-3 font-mono text-meta text-mute bt-scroll"
          onScroll={(e) => {
            const el = e.currentTarget;
            const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
            if (!atBottom) {
              userScrolledUp.current = true;
              if (autoScroll) {
                setAutoScroll(false);
                setTerminalAutoScroll(false);
              }
            } else {
              userScrolledUp.current = false;
            }
          }}
        >
          {filteredOutput || "No output captured."}
        </pre>
      </Card>

      <Card>
        <h3 className="text-sm font-medium">Execution history</h3>
        <ul className="mt-3 space-y-2">
          {runs.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                className="flex w-full flex-wrap items-center justify-between gap-2 rounded-control border border-line px-3 py-2 text-left hover:border-accent/40"
                onClick={async () => {
                  const detail = await api.getExecution(r.id);
                  setActive(detail);
                  const page = await api.getExecutionOutput(r.id);
                  setOutput(page.content);
                }}
              >
                <span className="text-sm">
                  <span className="capitalize">{r.run_type}</span>
                  <span className="ml-2 text-meta text-mute">{formatDate(r.created_at)}</span>
                </span>
                <span className="flex items-center gap-2">
                  <StatusBadge status={r.status} />
                  {r.confirmed ? (
                    <span className="text-meta text-success">confirmed</span>
                  ) : null}
                </span>
              </button>
            </li>
          ))}
          {runs.length === 0 ? (
            <li className="text-sm text-mute">No execution attempts yet.</li>
          ) : null}
        </ul>
      </Card>
    </div>
  );
}
