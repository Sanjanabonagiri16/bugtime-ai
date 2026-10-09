"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ExternalLink, RefreshCw, Search } from "lucide-react";
import { api } from "@/lib/api";
import type { RepositoryDetail, SourceFileSummary } from "@/lib/types";
import { formatDate, shortSha } from "@/lib/utils";
import {
  Button,
  Card,
  Hint,
  InlineError,
  Input,
  PageHeader,
  Skeleton,
} from "@/components/ui";
import { StatusBadge } from "@/components/status-badge";
import { useToast } from "@/components/toast";

const ACTIVE = new Set([
  "queued",
  "fetching",
  "scanning",
  "chunking",
  "embedding",
  "saving",
  "pending",
  "indexing",
]);

const STAGES = ["queued", "fetching", "scanning", "chunking", "embedding", "saving", "ready"];

export default function RepositoryDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const { push } = useToast();
  const [repo, setRepo] = useState<RepositoryDetail | null>(null);
  const [files, setFiles] = useState<SourceFileSummary[]>([]);
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const [r, f] = await Promise.all([api.getRepository(id), api.listFiles(id, q)]);
      setRepo(r);
      setFiles(f.items);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const index = repo?.active_index;
  const polling = useMemo(
    () => (!!index && ACTIVE.has(index.status)) || (!!repo && ACTIVE.has(repo.status)),
    [index, repo],
  );

  useEffect(() => {
    if (!polling) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [polling, id]);

  if (!repo && !error) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  const percent =
    index?.total_files_estimate && index.total_files_estimate > 0
      ? Math.min(100, Math.round((index.processed_files / index.total_files_estimate) * 100))
      : null;

  const stageIdx = STAGES.indexOf(index?.status || repo?.status || "");

  return (
    <div>
      <PageHeader
        title={repo ? `${repo.owner}/${repo.name}` : "Repository"}
        description={`Branch ${repo?.branch || "—"} · commit ${shortSha(index?.commit_sha)}`}
        actions={
          <>
            {repo ? (
              <a href={repo.github_url} target="_blank" rel="noreferrer">
                <Button variant="secondary">
                  <ExternalLink className="h-4 w-4" />
                  GitHub
                </Button>
              </a>
            ) : null}
            <Button
              variant="secondary"
              onClick={async () => {
                await api.reindexRepository(id);
                push("Reindex queued", "info");
                load();
              }}
            >
              <RefreshCw className="h-4 w-4" />
              Reindex
            </Button>
            <div className="flex flex-col items-end gap-1">
              <Link href={`/investigations/new?repository_id=${id}`}>
                <Button disabled={repo?.status !== "ready"}>
                  <Search className="h-4 w-4" />
                  New investigation
                </Button>
              </Link>
              {repo?.status !== "ready" ? (
                <Hint>Available when indexing status is Ready.</Hint>
              ) : null}
            </div>
          </>
        }
      />

      {error ? (
        <div className="mb-4">
          <InlineError message={error} onRetry={load} />
        </div>
      ) : null}

      <div className="mb-6 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <div className="flex flex-wrap items-center gap-3">
            <StatusBadge status={index?.status || repo?.status || "pending"} />
            <span className="text-sm text-mute">
              {index?.file_count ?? 0} files · {index?.chunk_count ?? 0} chunks ·{" "}
              {index?.skipped_count ?? 0} skipped
            </span>
          </div>

          <ol className="mt-4 flex flex-wrap gap-1">
            {STAGES.map((s, i) => (
              <li
                key={s}
                className={`rounded-control px-2 py-1 text-meta capitalize ${
                  i < stageIdx
                    ? "bg-success/10 text-success"
                    : i === stageIdx
                      ? "bg-accent-soft text-accent"
                      : "bg-elevated text-mute"
                }`}
              >
                {s}
              </li>
            ))}
          </ol>

          {polling ? (
            <div className="mt-4">
              <div className="mb-1 flex justify-between text-meta text-mute">
                <span>Stage: {index?.status}</span>
                <span>
                  {index?.processed_files ?? 0}
                  {index?.total_files_estimate != null
                    ? ` / ${index.total_files_estimate}`
                    : ""}
                  {percent != null ? ` (${percent}%)` : ""}
                </span>
              </div>
              <div className="h-2 overflow-hidden rounded-control bg-elevated">
                <div
                  className="h-full bg-accent bt-transition"
                  style={{ width: `${percent ?? 15}%` }}
                />
              </div>
            </div>
          ) : null}

          {index?.error_message ? (
            <div className="mt-4">
              <InlineError
                message={index.error_message}
                onRetry={async () => {
                  await api.reindexRepository(id);
                  load();
                }}
              />
            </div>
          ) : null}

          <dl className="mt-4 grid gap-2 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-meta text-mute">Started</dt>
              <dd>{formatDate(index?.started_at)}</dd>
            </div>
            <div>
              <dt className="text-meta text-mute">Completed</dt>
              <dd>{formatDate(index?.completed_at)}</dd>
            </div>
            <div>
              <dt className="text-meta text-mute">Embedding model</dt>
              <dd className="font-mono text-meta">{index?.embedding_model || "—"}</dd>
            </div>
            <div>
              <dt className="text-meta text-mute">Commit SHA</dt>
              <dd className="break-all font-mono text-meta">{index?.commit_sha || "—"}</dd>
            </div>
          </dl>
        </Card>

        <Card>
          <h2 className="text-sm font-semibold">Skipped files</h2>
          <ul className="mt-3 max-h-56 space-y-2 overflow-auto text-meta bt-scroll">
            {(repo?.skipped_files || []).slice(0, 50).map((s) => (
              <li key={s.path} className="border-b border-line pb-2">
                <div className="font-mono text-ink">{s.path}</div>
                <div className="text-warn">{s.reason}</div>
              </li>
            ))}
            {(repo?.skipped_files || []).length === 0 ? (
              <li className="text-mute">No skipped files recorded.</li>
            ) : null}
          </ul>
        </Card>
      </div>

      <Card>
        <div className="mb-3 flex gap-2">
          <Input
            placeholder="Search indexed files"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && load()}
          />
          <Button variant="secondary" onClick={load}>
            Search
          </Button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-meta text-mute">
              <tr>
                <th className="py-2 font-medium">Path</th>
                <th className="py-2 font-medium">Type</th>
                <th className="py-2 font-medium">Lines</th>
                <th className="py-2 font-medium">Language</th>
              </tr>
            </thead>
            <tbody>
              {files.map((f) => (
                <tr key={f.id} className="border-t border-line">
                  <td className="max-w-md truncate py-2 font-mono text-meta" title={f.path}>
                    {f.path}
                  </td>
                  <td className="py-2">{f.source_type}</td>
                  <td className="py-2 tabular-nums">{f.line_count}</td>
                  <td className="py-2 text-mute">{f.language || "—"}</td>
                </tr>
              ))}
              {files.length === 0 ? (
                <tr>
                  <td colSpan={4} className="py-8 text-center text-mute">
                    No indexed files match.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
