"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type {
  RerankDatasetSummary,
  RerankEvalRunSummary,
  RerankInventory,
} from "@/lib/types";
import { Button, Card, Hint, InlineError, PageHeader } from "@/components/ui";

type ConfigRow = {
  config: string;
  bug_count: number;
  candidate_recall: number;
  hit_at_1: number;
  hit_at_5: number;
  mrr_at_10: number;
  latency_ms_median: number;
  latency_ms_p95: number;
};

export default function EvaluationsPage() {
  const [datasets, setDatasets] = useState<RerankDatasetSummary[]>([]);
  const [inventory, setInventory] = useState<RerankInventory | null>(null);
  const [runs, setRuns] = useState<RerankEvalRunSummary[]>([]);
  const [activeRun, setActiveRun] = useState<Record<string, unknown> | null>(null);
  const [reranker, setReranker] = useState<{
    mode: string;
    base_model: string;
    finetuned_exists: boolean;
    experimental: boolean;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [d, inv, r, st] = await Promise.all([
        api.listRerankDatasets(),
        api.rerankInventory(),
        api.listEvalRuns(),
        api.getRerankerStatus(),
      ]);
      setDatasets(d);
      setInventory(inv);
      setRuns(r);
      setReranker(st);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const seed = async () => {
    setBusy(true);
    try {
      await api.seedSmokeDataset();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const runBaseline = async (name: string, label: string) => {
    setBusy(true);
    try {
      const report = await api.startEvalRun({
        dataset_name: name,
        split: "test",
        label,
      });
      setActiveRun(report);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const openRun = async (id: string) => {
    setBusy(true);
    try {
      setActiveRun(await api.getEvalRun(id));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const configs = (activeRun?.configs || []) as ConfigRow[];
  const perBug = (activeRun?.per_bug || []) as {
    bug_group_id: string;
    config: string;
    ranked_files: string[];
    relevant_files: string[];
    missed_relevant: string[];
    hit_at_1: number;
    mrr_at_10: number;
  }[];

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <PageHeader
        title="Evaluations"
        description="Code-evidence reranker datasets, baseline metrics, and experimental activation."
      />
      <p className="text-sm">
        <Link href="/evaluations/review" className="underline">
          Dataset review →
        </Link>
      </p>
      {error ? <InlineError message={error} /> : null}

      <Card>
        <h2 className="text-sm font-semibold">Inventory</h2>
        {inventory ? (
          <>
            <p className="mt-2 text-sm text-[var(--muted)]">
              {inventory.independent_bug_groups} bug group(s) · {inventory.repositories}{" "}
              repo id(s) · P4B ready:{" "}
              <strong>{inventory.supports_meaningful_p4b ? "yes" : "no"}</strong>
            </p>
            <div className="mt-2">
              <Hint>{inventory.recommendation}</Hint>
            </div>
            <Hint>{inventory.login_demo_note}</Hint>
          </>
        ) : (
          <Hint>Loading…</Hint>
        )}
      </Card>

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">Datasets</h2>
          <Button variant="secondary" disabled={busy} onClick={seed}>
            Seed login smoke dataset
          </Button>
        </div>
        {datasets.length === 0 ? (
          <div className="mt-2">
            <Hint>No datasets yet. Seed the synthetic smoke set to start.</Hint>
          </div>
        ) : (
          <ul className="mt-3 space-y-3">
            {datasets.map((d) => (
              <li key={d.name} className="border-t border-[var(--border)] pt-3 first:border-0 first:pt-0">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <div className="text-sm font-medium">
                      {d.name}{" "}
                      <span className="text-xs font-normal text-[var(--muted)]">
                        [{d.provenance_label}]
                      </span>
                    </div>
                    <div className="mt-1 text-xs text-[var(--muted)]">
                      {d.example_count} examples · {d.bug_group_count} bugs ·{" "}
                      {d.repository_count} repos · {d.split_strategy} · hash{" "}
                      {d.dataset_hash.slice(0, 12)}…
                    </div>
                    <div className="mt-1 text-xs text-[var(--muted)]">
                      splits: {JSON.stringify(d.split_counts)} · valid: {d.ok ? "yes" : "no"}
                    </div>
                    {d.notes[0] ? <Hint>{d.notes[0]}</Hint> : null}
                  </div>
                  <Button
                    disabled={busy || !d.ok}
                    onClick={() => runBaseline(d.name, d.provenance_label)}
                  >
                    Run baseline
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <h2 className="text-sm font-semibold">Training runs</h2>
        <div className="mt-2">
          <Hint>
            Fine-tuning stays experimental. Use{" "}
            <code className="text-xs">python -m app.scripts.rerank_cli train …</code> when a labeled
            train split exists. No checkpoint is promoted by default.
          </Hint>
        </div>
        {reranker ? (
          <p className="mt-2 text-sm text-[var(--muted)]">
            Runtime mode: <strong>{reranker.mode}</strong> · base {reranker.base_model} ·
            finetuned available: {reranker.finetuned_exists ? "yes" : "no"}
          </p>
        ) : null}
        <div className="mt-3 flex flex-wrap gap-2">
          <Button
            variant="secondary"
            disabled={busy}
            onClick={() => api.activateReranker({ mode: "disabled" }).then(refresh)}
          >
            Record disabled
          </Button>
          <Button
            variant="secondary"
            disabled={busy}
            onClick={() => api.activateReranker({ mode: "base" }).then(refresh)}
          >
            Record base CE
          </Button>
        </div>
        <div className="mt-2">
          <Hint>
            Activation records intent only; set RERANKER_MODE in the environment to apply at runtime.
            Historical evidence is never rewritten.
          </Hint>
        </div>
      </Card>

      <Card>
        <h2 className="text-sm font-semibold">Eval runs</h2>
        {runs.length === 0 ? (
          <div className="mt-2">
            <Hint>No runs yet.</Hint>
          </div>
        ) : (
          <ul className="mt-2 space-y-1 text-sm">
            {runs.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  className="text-[var(--accent)] underline"
                  onClick={() => openRun(r.id)}
                >
                  {r.id.slice(0, 8)}
                </button>
                <span className="text-xs text-[var(--muted)]">
                  {r.label} · {r.created_at} · bugs {r.bug_count ?? "—"}
                </span>
                <a
                  className="text-xs underline"
                  href={api.evalReportUrl(r.id)}
                  target="_blank"
                  rel="noreferrer"
                >
                  report
                </a>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {activeRun ? (
        <Card>
          <h2 className="text-sm font-semibold">
            Comparison{" "}
            <span className="text-xs font-normal text-[var(--muted)]">
              [{String(activeRun.label)}]
            </span>
          </h2>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-xs">
              <thead>
                <tr className="border-b border-[var(--border)] text-[var(--muted)]">
                  <th className="py-1 pr-2">config</th>
                  <th className="py-1 pr-2">recall</th>
                  <th className="py-1 pr-2">Hit@1</th>
                  <th className="py-1 pr-2">Hit@5</th>
                  <th className="py-1 pr-2">MRR@10</th>
                  <th className="py-1 pr-2">med ms</th>
                  <th className="py-1">p95 ms</th>
                </tr>
              </thead>
              <tbody>
                {configs.map((c) => (
                  <tr key={c.config} className="border-b border-[var(--border)]">
                    <td className="py-1 pr-2 font-medium">{c.config}</td>
                    <td className="py-1 pr-2">{c.candidate_recall.toFixed(3)}</td>
                    <td className="py-1 pr-2">{c.hit_at_1.toFixed(3)}</td>
                    <td className="py-1 pr-2">{c.hit_at_5.toFixed(3)}</td>
                    <td className="py-1 pr-2">{c.mrr_at_10.toFixed(3)}</td>
                    <td className="py-1 pr-2">{c.latency_ms_median.toFixed(1)}</td>
                    <td className="py-1">{c.latency_ms_p95.toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3 className="mt-4 text-sm font-semibold">Per-bug ranking</h3>
          <ul className="mt-2 space-y-2 text-xs text-[var(--muted)]">
            {perBug.map((b, i) => (
              <li key={`${b.bug_group_id}-${b.config}-${i}`}>
                <strong className="text-[var(--fg)]">
                  {b.bug_group_id} / {b.config}
                </strong>
                : Hit@1={b.hit_at_1} MRR@10={b.mrr_at_10}
                <br />
                ranked: {b.ranked_files.join(", ") || "—"}
                <br />
                relevant: {b.relevant_files.join(", ") || "—"}
                <br />
                missed (not in candidates): {b.missed_relevant.join(", ") || "none"}
              </li>
            ))}
          </ul>
          {(activeRun.limitations as string[] | undefined)?.length ? (
            <>
              <h3 className="mt-4 text-sm font-semibold">Limitations</h3>
              <ul className="mt-1 list-disc pl-5 text-xs text-[var(--muted)]">
                {(activeRun.limitations as string[]).map((l) => (
                  <li key={l}>{l}</li>
                ))}
              </ul>
            </>
          ) : null}
        </Card>
      ) : null}
    </div>
  );
}
