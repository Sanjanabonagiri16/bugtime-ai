"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ExternalLink, FolderPlus, RefreshCw, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type { RepositorySummary } from "@/lib/types";
import { formatDate, shortSha } from "@/lib/utils";
import {
  Button,
  Card,
  EmptyState,
  InlineError,
  Input,
  PageHeader,
} from "@/components/ui";
import { StatusBadge } from "@/components/status-badge";
import { useToast } from "@/components/toast";

export default function RepositoriesPage() {
  const { push } = useToast();
  const [q, setQ] = useState("");
  const [items, setItems] = useState<RepositorySummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = () => {
    api
      .listRepositories(q)
      .then((res) => {
        setItems(res.items);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div>
      <PageHeader
        title="Repositories"
        description="Search and manage imported public GitHub repositories."
        actions={
          <>
            <Link href="/repositories/new">
              <Button>
                <FolderPlus className="h-4 w-4" />
                Add repository
              </Button>
            </Link>
            <Button
              variant="secondary"
              onClick={async () => {
                setBusy("demo");
                try {
                  const repo = await api.loadDemo();
                  push("Demo repository loaded", "success");
                  window.location.href = `/repositories/${repo.id}`;
                } catch (e) {
                  setError((e as Error).message);
                } finally {
                  setBusy(null);
                }
              }}
              disabled={busy === "demo"}
            >
              Load demo repository
            </Button>
          </>
        }
      />

      <div className="mb-4 flex gap-2">
        <Input
          placeholder="Search owner or name"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && load()}
        />
        <Button variant="secondary" onClick={load}>
          Search
        </Button>
      </div>

      {error ? (
        <div className="mb-4">
          <InlineError message={error} onRetry={load} />
        </div>
      ) : null}

      {items.length === 0 && !error ? (
        <EmptyState
          title="No repositories yet"
          description="Add a public GitHub URL or load the demo repository to begin indexing."
          action={
            <Link href="/repositories/new">
              <Button>Add repository</Button>
            </Link>
          }
        />
      ) : (
        <Card className="overflow-x-auto p-0">
          <table className="w-full text-left text-sm">
            <thead className="text-meta text-mute">
              <tr>
                <th className="px-4 py-2 font-medium">Owner/name</th>
                <th className="px-4 py-2 font-medium">Branch</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 font-medium">Commit</th>
                <th className="px-4 py-2 font-medium">Indexed</th>
                <th className="px-4 py-2 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr key={r.id} className="border-t border-line">
                  <td className="px-4 py-2.5">
                    <Link href={`/repositories/${r.id}`}>
                      {r.owner}/{r.name}
                    </Link>
                    <a
                      href={r.github_url}
                      target="_blank"
                      rel="noreferrer"
                      className="ml-2 inline-flex items-center text-mute"
                      aria-label="Open on GitHub"
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                  </td>
                  <td className="px-4 py-2.5 font-mono text-meta">{r.branch}</td>
                  <td className="px-4 py-2.5">
                    <StatusBadge status={r.status} />
                  </td>
                  <td className="px-4 py-2.5 font-mono text-meta">
                    {shortSha(r.last_indexed_commit)}
                  </td>
                  <td className="px-4 py-2.5 text-mute">{formatDate(r.last_indexed_at)}</td>
                  <td className="px-4 py-2.5">
                    <div className="flex gap-1">
                      <Link href={`/repositories/${r.id}`}>
                        <Button variant="ghost" size="sm">
                          Open
                        </Button>
                      </Link>
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label="Reindex"
                        disabled={busy === r.id}
                        onClick={async () => {
                          setBusy(r.id);
                          try {
                            await api.reindexRepository(r.id);
                            push("Reindex queued", "info");
                            load();
                          } catch (e) {
                            setError((e as Error).message);
                          } finally {
                            setBusy(null);
                          }
                        }}
                      >
                        <RefreshCw className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label="Delete"
                        disabled={busy === r.id}
                        onClick={async () => {
                          if (!confirm(`Delete ${r.owner}/${r.name} and all related data?`)) {
                            return;
                          }
                          setBusy(r.id);
                          try {
                            await api.deleteRepository(r.id);
                            setTimeout(load, 800);
                          } catch (e) {
                            setError((e as Error).message);
                          } finally {
                            setBusy(null);
                          }
                        }}
                      >
                        <Trash2 className="h-4 w-4 text-danger" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
