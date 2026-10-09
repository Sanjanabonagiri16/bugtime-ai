"use client";

import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  Download,
  Loader2,
  Square,
  ThumbsDown,
  ThumbsUp,
  Wand2,
  XCircle,
} from "lucide-react";
import Editor from "@monaco-editor/react";
import { useTheme } from "next-themes";
import { api } from "@/lib/api";
import type {
  InvestigationDetail,
  PatchAttemptDetail,
  PullRequestPublication,
  RepairDetail,
} from "@/lib/types";
import { cn, formatDate, shortSha } from "@/lib/utils";
import {
  Button,
  Card,
  EmptyState,
  Hint,
  InlineError,
  Label,
} from "@/components/ui";
import { StatusBadge } from "@/components/status-badge";
import { useToast } from "@/components/toast";

const ACTIVE = new Set([
  "queued",
  "preparing",
  "baseline",
  "retrieving",
  "proposing",
  "validating",
  "verifying",
  "deciding",
]);

function outcomeTone(outcome: string | null | undefined) {
  if (outcome === "verified_fix") return "success";
  if (outcome === "partial_verification") return "warn";
  if (outcome === "verification_failed" || outcome === "baseline_invalid") return "danger";
  if (outcome === "budget_exhausted") return "warn";
  return "mute";
}

export function ChangesPanel({ investigation }: { investigation: InvestigationDetail }) {
  const { push } = useToast();
  const { resolvedTheme } = useTheme();
  const [repairs, setRepairs] = useState<RepairDetail[]>([]);
  const [active, setActive] = useState<RepairDetail | null>(null);
  const [attempt, setAttempt] = useState<PatchAttemptDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [verifiedSearchId, setVerifiedSearchId] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [publication, setPublication] = useState<PullRequestPublication | null>(null);
  const [prTitle, setPrTitle] = useState("");
  const [prBody, setPrBody] = useState("");

  const canPreparePr = useMemo(() => {
    return !!(
      active?.outcome === "verified_fix" &&
      attempt?.review?.decision === "accepted" &&
      attempt.patch_hash &&
      attempt.verification?.outcome === "verified_fix"
    );
  }, [active, attempt]);

  const refresh = async () => {
    const [list, searches, pubs] = await Promise.all([
      api.listRepairs(investigation.id),
      api.listCommitSearches(investigation.id),
      api.listPublications(investigation.id).catch(() => [] as PullRequestPublication[]),
    ]);
    const verified = searches.find((s) => s.outcome === "verified_boundary");
    setVerifiedSearchId(verified?.id || null);
    const details = await Promise.all(list.map((r) => api.getRepair(r.id)));
    setRepairs(details);
    const focus =
      details.find((d) => ACTIVE.has(d.status)) ||
      details[0] ||
      null;
    setActive(focus);
    if (focus?.selected_attempt_id) {
      const a = await api.getPatchAttempt(focus.selected_attempt_id);
      setAttempt(a);
    } else if (focus?.attempts?.length) {
      const last = focus.attempts[focus.attempts.length - 1];
      const a = await api.getPatchAttempt(last.id);
      setAttempt(a);
    } else {
      setAttempt(null);
    }
    const pub =
      pubs.find((p) => p.patch_attempt_id === focus?.selected_attempt_id) ||
      pubs[0] ||
      null;
    setPublication(pub);
    if (pub) {
      setPrTitle(pub.title);
      setPrBody(pub.body);
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

  const canGenerate = !!verifiedSearchId && !polling;

  const banner = useMemo(() => {
    if (!active) return null;
    const tone = outcomeTone(active.outcome);
    if (ACTIVE.has(active.status)) {
      return {
        tone: "info" as const,
        title: `Repair in progress — ${active.stage}`,
        body: `Attempt ${active.attempt_count}/${active.max_attempts}. Target ${shortSha(active.target_sha)}; introducing ${shortSha(active.introducing_sha)}.`,
      };
    }
    if (active.outcome === "verified_fix") {
      return {
        tone: "success" as const,
        title: "Verified fix",
        body: "Reproduction passes consistently; baseline suite completed; no new failures or missing tests. Review decision is separate from verification.",
      };
    }
    return {
      tone: tone as "warn" | "danger" | "mute",
      title: active.outcome || active.status,
      body: active.error_message || active.limitations || "See attempt history for details.",
    };
  }, [active]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-ink">Generate fix</h2>
          <p className="mt-1 text-meta text-mute">
            Propose a focused patch at the pinned bad SHA, verify with the frozen reproduction, then
            accept or reject for export.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {active && ACTIVE.has(active.status) ? (
            <Button
              variant="secondary"
              size="sm"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await api.cancelRepair(active.id);
                  await refresh();
                  push("Cancellation requested", "info");
                } catch (e) {
                  setError((e as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              <Square className="h-3.5 w-3.5" />
              Cancel
            </Button>
          ) : null}
          <Button
            size="sm"
            disabled={busy || !canGenerate}
            title={!verifiedSearchId ? "Requires a verified breaking-commit result" : undefined}
            onClick={async () => {
              if (!verifiedSearchId) return;
              setBusy(true);
              setError(null);
              try {
                const created = await api.startRepair(investigation.id, {
                  commit_search_id: verifiedSearchId,
                });
                setActive(created);
                await refresh();
                push("Repair queued", "info");
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wand2 className="h-3.5 w-3.5" />}
            Generate fix
          </Button>
        </div>
      </div>

      {!verifiedSearchId ? (
        <Hint>Complete a verified commit search on the Commits tab before generating a fix.</Hint>
      ) : null}

      {error ? <InlineError message={error} onRetry={() => refresh()} /> : null}

      {!active ? (
        <EmptyState
          title="No repair runs yet"
          description="After a verified breaking commit, generate a focused patch against the pinned bad SHA."
        />
      ) : (
        <>
          {banner ? (
            <div
              className={cn(
                "rounded-panel border px-4 py-3",
                banner.tone === "success" && "border-success/30 bg-success/10 text-success",
                banner.tone === "warn" && "border-warn/30 bg-warn/10 text-warn",
                banner.tone === "danger" && "border-danger/30 bg-danger/10 text-danger",
                banner.tone === "info" && "border-accent/30 bg-accent-soft text-accent",
                banner.tone === "mute" && "border-line bg-elevated text-mute",
              )}
            >
              <div className="flex items-center gap-2 text-sm font-semibold">
                {banner.tone === "success" ? <CheckCircle2 className="h-4 w-4" /> : null}
                {banner.tone === "danger" ? <XCircle className="h-4 w-4" /> : null}
                {banner.title}
              </div>
              <p className="mt-1 text-meta opacity-90">{banner.body}</p>
            </div>
          ) : null}

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Card className="p-3">
              <div className="text-meta text-mute">Target (pinned bad)</div>
              <div className="mt-1 font-mono text-sm text-ink">{shortSha(active.target_sha)}</div>
            </Card>
            <Card className="p-3">
              <div className="text-meta text-mute">Introducing commit</div>
              <div className="mt-1 font-mono text-sm text-ink">
                {shortSha(active.introducing_sha)}
              </div>
            </Card>
            <Card className="p-3">
              <div className="text-meta text-mute">Stage / attempts</div>
              <div className="mt-1 flex items-center gap-2 text-sm text-ink">
                <StatusBadge status={active.status} />
                {active.attempt_count}/{active.max_attempts}
              </div>
            </Card>
            <Card className="p-3">
              <div className="text-meta text-mute">Model</div>
              <div className="mt-1 text-sm text-ink">
                {active.provider}/{active.model || "—"}
              </div>
            </Card>
          </div>

          {attempt ? (
            <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
              <div className="space-y-3">
                <Card className="p-4">
                  <h3 className="text-sm font-semibold text-ink">Proposed explanation</h3>
                  <p className="mt-2 whitespace-pre-wrap text-sm text-ink">
                    {attempt.explanation || "—"}
                  </p>
                  {attempt.source_references?.length ? (
                    <ul className="mt-3 list-disc pl-5 text-meta text-mute">
                      {attempt.source_references.map((r) => (
                        <li key={r}>{r}</li>
                      ))}
                    </ul>
                  ) : null}
                  {attempt.assumptions ? (
                    <p className="mt-3 text-meta text-mute">Assumptions: {attempt.assumptions}</p>
                  ) : null}
                  {attempt.limitations ? (
                    <p className="mt-1 text-meta text-mute">Limitations: {attempt.limitations}</p>
                  ) : null}
                </Card>

                <Card className="overflow-hidden p-0">
                  <div className="border-b border-line px-4 py-2 text-sm font-semibold text-ink">
                    Unified diff
                    {attempt.patch_hash ? (
                      <span className="ml-2 font-mono text-meta font-normal text-mute">
                        {attempt.patch_hash.slice(0, 12)}
                      </span>
                    ) : null}
                  </div>
                  <div className="h-[360px]">
                    <Editor
                      height="100%"
                      language="diff"
                      theme={resolvedTheme === "dark" ? "vs-dark" : "light"}
                      value={attempt.unified_diff || "// No patch yet"}
                      options={{
                        readOnly: true,
                        minimap: { enabled: false },
                        scrollBeyondLastLine: false,
                        fontSize: 12,
                      }}
                    />
                  </div>
                </Card>

                {attempt.verification ? (
                  <Card className="p-4">
                    <h3 className="text-sm font-semibold text-ink">Before / after tests</h3>
                    <p className="mt-1 text-meta text-mute">{attempt.verification.summary}</p>
                    <div className="mt-3 grid gap-3 sm:grid-cols-2">
                      <div>
                        <Label>Reproduction after</Label>
                        <pre className="mt-1 overflow-auto rounded-control border border-line bg-canvas p-2 text-meta">
                          {JSON.stringify(attempt.verification.reproduction_after, null, 2)}
                        </pre>
                      </div>
                      <div>
                        <Label>New / missing / unchanged failures</Label>
                        <pre className="mt-1 overflow-auto rounded-control border border-line bg-canvas p-2 text-meta">
                          {JSON.stringify(
                            {
                              new_failures: attempt.verification.new_failures,
                              missing_tests: attempt.verification.missing_tests,
                              unchanged_failures: attempt.verification.unchanged_failures,
                            },
                            null,
                            2,
                          )}
                        </pre>
                      </div>
                    </div>
                  </Card>
                ) : null}
              </div>

              <div className="space-y-3">
                <Card className="p-4">
                  <h3 className="text-sm font-semibold text-ink">Review</h3>
                  <p className="mt-1 text-meta text-mute">
                    Bound to patch hash. Acceptance records your decision only — nothing is pushed
                    or merged.
                  </p>
                  {attempt.review ? (
                    <div className="mt-3 text-sm text-ink">
                      Decision: <StatusBadge status={attempt.review.decision} />
                      <div className="mt-1 text-meta text-mute">
                        {formatDate(attempt.review.created_at)}
                        {attempt.review.note ? ` — ${attempt.review.note}` : ""}
                      </div>
                    </div>
                  ) : (
                    <>
                      <textarea
                        className="mt-3 w-full rounded-control border border-line bg-canvas px-3 py-2 text-sm"
                        rows={3}
                        placeholder="Optional note"
                        value={note}
                        onChange={(e) => setNote(e.target.value)}
                      />
                      <div className="mt-2 flex gap-2">
                        <Button
                          size="sm"
                          disabled={busy || !attempt.patch_hash}
                          onClick={async () => {
                            if (!attempt.patch_hash) return;
                            setBusy(true);
                            try {
                              await api.reviewPatch(attempt.id, {
                                decision: "accepted",
                                note: note || undefined,
                                patch_hash: attempt.patch_hash,
                              });
                              await refresh();
                              push("Patch accepted for export", "success");
                            } catch (e) {
                              setError((e as Error).message);
                            } finally {
                              setBusy(false);
                            }
                          }}
                        >
                          <ThumbsUp className="h-3.5 w-3.5" />
                          Accept
                        </Button>
                        <Button
                          variant="secondary"
                          size="sm"
                          disabled={busy || !attempt.patch_hash}
                          onClick={async () => {
                            if (!attempt.patch_hash) return;
                            setBusy(true);
                            try {
                              await api.reviewPatch(attempt.id, {
                                decision: "rejected",
                                note: note || undefined,
                                patch_hash: attempt.patch_hash,
                              });
                              await refresh();
                              push("Patch rejected", "info");
                            } catch (e) {
                              setError((e as Error).message);
                            } finally {
                              setBusy(false);
                            }
                          }}
                        >
                          <ThumbsDown className="h-3.5 w-3.5" />
                          Reject
                        </Button>
                      </div>
                    </>
                  )}
                  <div className="mt-3 flex flex-col gap-2">
                    {attempt.patch_hash ? (
                      <a href={api.patchDownloadUrl(attempt.id)}>
                        <Button variant="secondary" size="sm" className="w-full">
                          <Download className="h-3.5 w-3.5" />
                          Download patch
                        </Button>
                      </a>
                    ) : null}
                    <a href={api.repairReportUrl(active.id)}>
                      <Button variant="secondary" size="sm" className="w-full">
                        <Download className="h-3.5 w-3.5" />
                        Export report
                      </Button>
                    </a>
                  </div>
                </Card>

                <Card className="p-4">
                  <h3 className="text-sm font-semibold text-ink">Draft PR</h3>
                  <Hint>
                    Preview never creates remotes. Publish requires an accepted verified_fix and a
                    server-side GitHub token.
                  </Hint>
                  {!canPreparePr ? (
                    <p className="mt-2 text-meta text-mute">
                      Accept a verified_fix patch to enable Prepare draft PR.
                    </p>
                  ) : (
                    <div className="mt-3 flex flex-col gap-2">
                      <Button
                        size="sm"
                        disabled={busy}
                        onClick={async () => {
                          if (!attempt) return;
                          setBusy(true);
                          setError(null);
                          try {
                            const pub = await api.preparePrPreview(attempt.id);
                            setPublication(pub);
                            setPrTitle(pub.title);
                            setPrBody(pub.body);
                            push("Publication preview ready", "success");
                          } catch (e) {
                            setError((e as Error).message);
                          } finally {
                            setBusy(false);
                          }
                        }}
                      >
                        Prepare draft PR
                      </Button>
                    </div>
                  )}
                  {publication ? (
                    <div className="mt-3 space-y-2 text-meta">
                      <div className="flex items-center gap-2">
                        <StatusBadge status={publication.status} />
                        {publication.github_configured ? null : (
                          <span className="text-mute">GitHub token not configured</span>
                        )}
                      </div>
                      <div className="text-mute">
                        {publication.owner}/{publication.repo_name} ← {publication.target_branch}
                        <br />
                        branch: <span className="font-mono">{publication.feature_branch}</span>
                        <br />
                        verified:{" "}
                        <span className="font-mono">
                          {publication.verified_base_sha.slice(0, 12)}
                        </span>
                        {publication.current_remote_sha ? (
                          <>
                            {" "}
                            · remote:{" "}
                            <span className="font-mono">
                              {publication.current_remote_sha.slice(0, 12)}
                            </span>
                          </>
                        ) : null}
                      </div>
                      {publication.status === "stale_base" ? (
                        <div className="rounded-control border border-line bg-canvas p-2 text-mute">
                          Target branch advanced past the verified SHA. Revalidate before publishing.
                          <Button
                            className="mt-2 w-full"
                            size="sm"
                            variant="secondary"
                            disabled={busy}
                            onClick={async () => {
                              setBusy(true);
                              try {
                                const pub = await api.revalidatePublication(publication.id);
                                setPublication(pub);
                                setPrTitle(pub.title);
                                setPrBody(pub.body);
                                push("Revalidation finished", "info");
                              } catch (e) {
                                setError((e as Error).message);
                              } finally {
                                setBusy(false);
                              }
                            }}
                          >
                            Revalidate on current branch
                          </Button>
                        </div>
                      ) : null}
                      {publication.revalidation_summary ? (
                        <p className="text-mute">{publication.revalidation_summary}</p>
                      ) : null}
                      <Label>PR title</Label>
                      <input
                        className="w-full rounded-control border border-line bg-canvas px-2 py-1.5 text-sm"
                        value={prTitle}
                        disabled={publication.status === "published"}
                        onChange={(e) => setPrTitle(e.target.value)}
                        onBlur={async () => {
                          if (prTitle === publication.title) return;
                          try {
                            const pub = await api.updatePublication(publication.id, {
                              title: prTitle,
                            });
                            setPublication(pub);
                          } catch (e) {
                            setError((e as Error).message);
                          }
                        }}
                      />
                      <Label>PR body</Label>
                      <textarea
                        className="w-full rounded-control border border-line bg-canvas px-2 py-1.5 font-mono text-meta"
                        rows={8}
                        value={prBody}
                        disabled={publication.status === "published"}
                        onChange={(e) => setPrBody(e.target.value)}
                        onBlur={async () => {
                          if (prBody === publication.body) return;
                          try {
                            const pub = await api.updatePublication(publication.id, {
                              body: prBody,
                            });
                            setPublication(pub);
                          } catch (e) {
                            setError((e as Error).message);
                          }
                        }}
                      />
                      {publication.error_message ? (
                        <InlineError message={publication.error_message} />
                      ) : null}
                      {publication.status === "published" && publication.pr_url ? (
                        <a href={publication.pr_url} target="_blank" rel="noreferrer">
                          <Button size="sm" className="w-full">
                            Open PR #{publication.pr_number}
                          </Button>
                        </a>
                      ) : (
                        <div className="flex flex-col gap-2">
                          <Button
                            size="sm"
                            disabled={
                              busy ||
                              publication.status === "stale_base" ||
                              publication.status === "conflict"
                            }
                            onClick={async () => {
                              setBusy(true);
                              setError(null);
                              try {
                                const pub = await api.publishPublication(
                                  publication.id,
                                  publication.preview_hash,
                                );
                                setPublication(pub);
                                push(
                                  pub.pr_url ? "Draft PR created" : "Publication updated",
                                  "success",
                                );
                              } catch (e) {
                                setError((e as Error).message);
                                await refresh();
                              } finally {
                                setBusy(false);
                              }
                            }}
                          >
                            Create draft PR
                          </Button>
                          {publication.status === "failed" ? (
                            <Button
                              size="sm"
                              variant="secondary"
                              disabled={busy}
                              onClick={async () => {
                                setBusy(true);
                                try {
                                  const pub = await api.retryPublication(publication.id);
                                  setPublication(pub);
                                  push("Retry finished", "info");
                                } catch (e) {
                                  setError((e as Error).message);
                                } finally {
                                  setBusy(false);
                                }
                              }}
                            >
                              Retry
                            </Button>
                          ) : null}
                        </div>
                      )}
                    </div>
                  ) : null}
                </Card>

                <Card className="p-4">
                  <h3 className="text-sm font-semibold text-ink">Attempt history</h3>
                  <ul className="mt-2 space-y-2">
                    {(active.attempts || []).map((a) => (
                      <li key={a.id}>
                        <button
                          type="button"
                          className={cn(
                            "w-full rounded-control border border-line px-3 py-2 text-left text-meta hover:bg-elevated",
                            attempt?.id === a.id && "border-accent bg-accent-soft",
                          )}
                          onClick={async () => {
                            const d = await api.getPatchAttempt(a.id);
                            setAttempt(d);
                          }}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-medium text-ink">#{a.attempt_number}</span>
                            <StatusBadge status={a.verification_outcome || a.status} />
                          </div>
                          <div className="mt-1 text-mute">
                            {(a.changed_files || []).join(", ") || a.validation_error || a.feedback || "—"}
                          </div>
                        </button>
                      </li>
                    ))}
                  </ul>
                </Card>
              </div>
            </div>
          ) : null}

          {repairs.length > 1 ? (
            <Card className="p-4">
              <h3 className="text-sm font-semibold text-ink">Prior repair runs</h3>
              <ul className="mt-2 space-y-1 text-meta">
                {repairs.map((r) => (
                  <li key={r.id}>
                    <button
                      type="button"
                      className="text-accent hover:underline"
                      onClick={async () => {
                        const d = await api.getRepair(r.id);
                        setActive(d);
                        if (d.selected_attempt_id) {
                          setAttempt(await api.getPatchAttempt(d.selected_attempt_id));
                        } else if (d.attempts[0]) {
                          setAttempt(await api.getPatchAttempt(d.attempts[0].id));
                        }
                      }}
                    >
                      {shortSha(r.target_sha)} · {r.status} · {r.outcome || "—"} ·{" "}
                      {formatDate(r.created_at)}
                    </button>
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}
        </>
      )}
    </div>
  );
}
