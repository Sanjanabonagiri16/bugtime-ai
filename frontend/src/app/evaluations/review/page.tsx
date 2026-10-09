"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { Button, Card, Hint, InlineError, PageHeader } from "@/components/ui";

type CaseSummary = {
  instance_id: string;
  repository_id: string;
  commit_sha: string;
  split?: string;
  query_preview: string;
  batch1_priority?: boolean;
  failure_flags?: Record<string, boolean>;
  review_stats: {
    total: number;
    unreviewed: number;
    human: number;
    relevant?: number;
    irrelevant?: number;
    uncertain?: number;
    completed?: number;
  };
};

type ReviewItem = {
  example_id: string;
  file_path: string;
  line_range?: string;
  snippet: string;
  label: string;
  suggested_label?: string;
  suggested_label_source?: string;
  annotation_note?: string;
  hybrid_rank?: number;
};

type FileGroup = { file_path: string; items: ReviewItem[] };

type Bundle = {
  case: {
    instance_id: string;
    repository_id: string;
    commit_sha: string;
    query: string;
    query_exclusions?: string[];
    suggested_relevant_files?: string[];
  };
  split?: string;
  reviewer_only: { patch?: string; test_patch?: string; hints_text?: string };
  pool: Record<string, unknown>;
  items: ReviewItem[];
  items_by_file?: FileGroup[];
};

type UndoEntry = {
  instance_id: string;
  example_id: string;
  prev_label: string;
  prev_note: string;
  next_label: string;
  next_note: string;
};

const SHORTCUTS = "1 relevant · 2 irrelevant · 3 uncertain · n/j next · p/k prev · z undo · d diff";

export default function DatasetReviewPage() {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [bundle, setBundle] = useState<Bundle | null>(null);
  const [flatIdx, setFlatIdx] = useState(0);
  const [showPatch, setShowPatch] = useState(false);
  const [note, setNote] = useState("");
  const [progress, setProgress] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved">("idle");
  const undoStack = useRef<UndoEntry[]>([]);
  const noteRef = useRef(note);
  noteRef.current = note;

  const refreshCases = useCallback(async () => {
    try {
      const [c, prog] = await Promise.all([
        api.listReviewCases(),
        api.reviewProgress(),
      ]);
      setCases(c);
      setProgress(prog);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    refreshCases();
  }, [refreshCases]);

  const openCase = useCallback(async (id: string) => {
    setBusy(true);
    try {
      const b = (await api.getReviewCase(id)) as Bundle;
      setBundle(b);
      setActiveId(id);
      const firstUnrev = b.items.findIndex((r) => r.label === "unreviewed");
      setFlatIdx(firstUnrev >= 0 ? firstUnrev : 0);
      setNote(b.items[firstUnrev >= 0 ? firstUnrev : 0]?.annotation_note || "");
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  // Auto-open first batch-1 bug
  useEffect(() => {
    if (!activeId && cases.length) {
      const batch = cases.find((c) => c.batch1_priority && c.review_stats.unreviewed > 0);
      const next = batch || cases.find((c) => c.review_stats.unreviewed > 0) || cases[0];
      if (next) void openCase(next.instance_id);
    }
  }, [cases, activeId, openCase]);

  const flatItems = bundle?.items || [];
  const item = flatItems[flatIdx];
  const groups: FileGroup[] = useMemo(() => {
    if (bundle?.items_by_file?.length) return bundle.items_by_file;
    const map = new Map<string, ReviewItem[]>();
    for (const it of flatItems) {
      const list = map.get(it.file_path) || [];
      list.push(it);
      map.set(it.file_path, list);
    }
    return Array.from(map.entries()).map(([file_path, items]) => ({ file_path, items }));
  }, [bundle, flatItems]);

  useEffect(() => {
    setNote(item?.annotation_note || "");
  }, [item?.example_id, item?.annotation_note]);

  const applyLabel = useCallback(
    async (label: "relevant" | "irrelevant" | "uncertain", opts?: { advance?: boolean }) => {
      if (!bundle || !item || !activeId || busy) return;
      const prev_label = item.label;
      const prev_note = item.annotation_note || "";
      const next_note = noteRef.current;
      undoStack.current.push({
        instance_id: activeId,
        example_id: item.example_id,
        prev_label,
        prev_note,
        next_label: label,
        next_note,
      });
      setBusy(true);
      setSaveState("saving");
      try {
        await api.saveReviewAnnotations(activeId, [
          { example_id: item.example_id, label, annotation_note: next_note },
        ]);
        const nextItems = bundle.items.map((r) =>
          r.example_id === item.example_id
            ? { ...r, label, annotation_note: next_note }
            : r,
        );
        setBundle({ ...bundle, items: nextItems });
        setSaveState("saved");
        if (opts?.advance !== false) {
          const nextUnreviewed = nextItems.findIndex(
            (r, i) => i > flatIdx && r.label === "unreviewed",
          );
          if (nextUnreviewed >= 0) setFlatIdx(nextUnreviewed);
          else if (flatIdx < nextItems.length - 1) setFlatIdx(flatIdx + 1);
        }
        await refreshCases();
      } catch (e) {
        undoStack.current.pop();
        setError((e as Error).message);
        setSaveState("idle");
      } finally {
        setBusy(false);
      }
    },
    [bundle, item, activeId, busy, flatIdx, refreshCases],
  );

  const undo = useCallback(async () => {
    const entry = undoStack.current.pop();
    if (!entry || busy) return;
    setBusy(true);
    setSaveState("saving");
    try {
      await api.saveReviewAnnotations(entry.instance_id, [
        {
          example_id: entry.example_id,
          label: entry.prev_label,
          annotation_note: entry.prev_note,
        },
      ]);
      if (bundle && activeId === entry.instance_id) {
        const nextItems = bundle.items.map((r) =>
          r.example_id === entry.example_id
            ? { ...r, label: entry.prev_label, annotation_note: entry.prev_note }
            : r,
        );
        setBundle({ ...bundle, items: nextItems });
        const idx = nextItems.findIndex((r) => r.example_id === entry.example_id);
        if (idx >= 0) setFlatIdx(idx);
        setNote(entry.prev_note);
      }
      setSaveState("saved");
      await refreshCases();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [busy, bundle, activeId, refreshCases]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "TEXTAREA" || tag === "INPUT") {
        if (e.key === "Escape") (e.target as HTMLElement).blur();
        return;
      }
      if (e.key === "1") {
        e.preventDefault();
        void applyLabel("relevant");
      } else if (e.key === "2") {
        e.preventDefault();
        void applyLabel("irrelevant");
      } else if (e.key === "3") {
        e.preventDefault();
        void applyLabel("uncertain");
      } else if (e.key === "n" || e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        setFlatIdx((i) => Math.min(flatItems.length - 1, i + 1));
      } else if (e.key === "p" || e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        setFlatIdx((i) => Math.max(0, i - 1));
      } else if (e.key === "z") {
        e.preventDefault();
        void undo();
      } else if (e.key === "d") {
        e.preventDefault();
        setShowPatch((s) => !s);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [applyLabel, undo, flatItems.length]);

  const bySplit = (progress?.by_split || {}) as Record<
    string,
    {
      bugs: number;
      bugs_completed: number;
      snippets: number;
      snippets_reviewed: number;
      relevant: number;
      irrelevant: number;
      uncertain: number;
      unreviewed: number;
    }
  >;

  const jumpToSnippet = (exampleId: string) => {
    const i = flatItems.findIndex((r) => r.example_id === exampleId);
    if (i >= 0) setFlatIdx(i);
  };

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <PageHeader
        title="Dataset review"
        description="Snippet-level labels only. Changed files suggest relevance — they do not auto-label snippets. Hybrid retrieval stays active."
      />
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <Link href="/evaluations" className="underline text-[var(--muted)]">
          ← Evaluations
        </Link>
        <span className="text-xs text-[var(--muted)]">{SHORTCUTS}</span>
        <span className="text-xs text-[var(--muted)]">
          {saveState === "saving" ? "Saving…" : saveState === "saved" ? "Saved" : ""}
        </span>
      </div>
      {error ? <InlineError message={error} /> : null}

      <Card>
        <h2 className="text-sm font-semibold">Progress (train / validation only)</h2>
        <div className="mt-2">
          <Hint>
            Held-out test is hidden and must not guide model choices. Patch-file proxy ≠ human
            recall.
          </Hint>
        </div>
        <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
          {["train", "validation"].map((sp) => {
            const b = bySplit[sp];
            if (!b) return null;
            return (
              <div key={sp} className="rounded border border-[var(--border)] p-2">
                <strong>{sp}</strong>: bugs {b.bugs_completed}/{b.bugs} complete · snippets{" "}
                {b.snippets_reviewed}/{b.snippets} · +{b.relevant}/−{b.irrelevant}/?{b.uncertain}
              </div>
            );
          })}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button
            variant="secondary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await api.exportReviewDataset();
                await refreshCases();
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            Export reviewed pairs
          </Button>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-[260px_1fr_220px]">
        <Card>
          <h2 className="text-sm font-semibold">Bugs</h2>
          <ul className="mt-2 max-h-[70vh] space-y-1 overflow-y-auto text-xs">
            {cases.map((c) => (
              <li key={c.instance_id}>
                <button
                  type="button"
                  className={`w-full rounded px-2 py-1.5 text-left hover:bg-[var(--surface-2)] ${
                    activeId === c.instance_id ? "bg-[var(--surface-2)]" : ""
                  }`}
                  onClick={() => openCase(c.instance_id)}
                >
                  <div className="font-medium">
                    {c.batch1_priority ? "★ " : ""}
                    {c.instance_id}
                  </div>
                  <div className="text-[var(--muted)]">
                    {c.split} · {c.repository_id.split("/")[1]} ·{" "}
                    {c.review_stats.total - c.review_stats.unreviewed}/{c.review_stats.total}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </Card>

        <div className="space-y-4">
          {!bundle || !item ? (
            <Card>
              <Hint>Select a train/validation case.</Hint>
            </Card>
          ) : (
            <>
              <Card>
                <h2 className="text-sm font-semibold">
                  Issue{" "}
                  <span className="font-normal text-[var(--muted)]">
                    [{bundle.split}] {bundle.case.repository_id} @{" "}
                    {bundle.case.commit_sha.slice(0, 12)}
                  </span>
                </h2>
                <p className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-sm">
                  {bundle.case.query}
                </p>
              </Card>

              <Card>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="text-sm font-semibold">
                    {item.file_path}:{item.line_range || "?"}
                  </h2>
                  <span className="text-xs text-[var(--muted)]">
                    #{flatIdx + 1}/{flatItems.length} · hybrid {item.hybrid_rank} · {item.label}
                    {item.suggested_label
                      ? ` · suggest=${item.suggested_label} (not auto-applied)`
                      : ""}
                  </span>
                </div>
                <pre className="mt-3 max-h-72 overflow-auto rounded bg-[var(--surface-2)] p-3 text-xs leading-relaxed">
                  {item.snippet}
                </pre>
                <label className="mt-3 block text-xs text-[var(--muted)]">
                  Reason (saved with label)
                  <textarea
                    className="mt-1 w-full rounded border border-[var(--border)] bg-transparent p-2 text-sm"
                    rows={2}
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="Why this snippet helps locate/explain the bug — or why not"
                  />
                </label>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button disabled={busy} onClick={() => applyLabel("relevant")}>
                    Relevant (1)
                  </Button>
                  <Button
                    variant="secondary"
                    disabled={busy}
                    onClick={() => applyLabel("irrelevant")}
                  >
                    Irrelevant (2)
                  </Button>
                  <Button
                    variant="secondary"
                    disabled={busy}
                    onClick={() => applyLabel("uncertain")}
                  >
                    Uncertain (3)
                  </Button>
                  <Button variant="secondary" disabled={busy} onClick={() => undo()}>
                    Undo (z)
                  </Button>
                </div>
              </Card>

              <Card>
                <div className="flex items-center justify-between gap-2">
                  <h2 className="text-sm font-semibold">Reviewer-only reference diff</h2>
                  <Button variant="secondary" onClick={() => setShowPatch((s) => !s)}>
                    {showPatch ? "Hide (d)" : "Show (d)"}
                  </Button>
                </div>
                <div className="mt-2">
                  <Hint>
                    Never used as query/model input. Patch files only suggest — each snippet is
                    labeled independently.
                  </Hint>
                </div>
                {showPatch ? (
                  <pre className="mt-3 max-h-80 overflow-auto rounded bg-[var(--surface-2)] p-3 text-xs">
                    {bundle.reviewer_only.patch || "(empty)"}
                  </pre>
                ) : null}
              </Card>
            </>
          )}
        </div>

        <Card>
          <h2 className="text-sm font-semibold">By file</h2>
          <div className="mt-2">
            <Hint>Snippet labels ≠ file labels.</Hint>
          </div>
          <ul className="mt-2 max-h-[70vh] space-y-3 overflow-y-auto text-xs">
            {groups.map((g) => {
              const reviewed = g.items.filter((i) => i.label !== "unreviewed").length;
              const isSuggested = (bundle?.case.suggested_relevant_files || []).includes(
                g.file_path,
              );
              return (
                <li key={g.file_path}>
                  <div className="font-medium">
                    {isSuggested ? "◎ " : ""}
                    {g.file_path}
                  </div>
                  <div className="text-[var(--muted)]">
                    {reviewed}/{g.items.length} reviewed
                  </div>
                  <ul className="mt-1 space-y-0.5">
                    {g.items.map((it) => (
                      <li key={it.example_id}>
                        <button
                          type="button"
                          className={`w-full rounded px-1 py-0.5 text-left ${
                            item?.example_id === it.example_id
                              ? "bg-[var(--surface-2)]"
                              : "hover:bg-[var(--surface-2)]"
                          }`}
                          onClick={() => jumpToSnippet(it.example_id)}
                        >
                          L{it.line_range} · {it.label}
                        </button>
                      </li>
                    ))}
                  </ul>
                </li>
              );
            })}
          </ul>
        </Card>
      </div>
    </div>
  );
}
