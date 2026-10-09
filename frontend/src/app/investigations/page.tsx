"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type { InvestigationSummary, RepositorySummary } from "@/lib/types";
import { formatDate } from "@/lib/utils";
import {
  Button,
  Card,
  EmptyState,
  InlineError,
  Input,
  PageHeader,
} from "@/components/ui";
import { StatusBadge } from "@/components/status-badge";

const selectClass =
  "w-full rounded-control border border-line bg-canvas px-3 py-2 text-sm text-ink outline-none bt-transition focus:border-accent/50";

export default function InvestigationsHistoryPage() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [repositoryId, setRepositoryId] = useState("");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [items, setItems] = useState<InvestigationSummary[]>([]);
  const [repos, setRepos] = useState<RepositorySummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    api
      .listInvestigations({
        q,
        status: status || undefined,
        repository_id: repositoryId || undefined,
        page,
      })
      .then((res) => {
        setItems(res.items);
        setTotal(res.total);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  };

  useEffect(() => {
    api.listRepositories().then((r) => setRepos(r.items));
  }, []);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const pages = Math.max(1, Math.ceil(total / 20));

  return (
    <div>
      <PageHeader
        title="History"
        description="Filter and reopen previous investigations."
        actions={
          <Link href="/investigations/new">
            <Button>New investigation</Button>
          </Link>
        }
      />

      <div className="mb-4 grid gap-2 md:grid-cols-4">
        <Input
          placeholder="Search by title"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && (setPage(1), load())}
        />
        <select
          className={selectClass}
          value={repositoryId}
          onChange={(e) => setRepositoryId(e.target.value)}
        >
          <option value="">All repositories</option>
          {repos.map((r) => (
            <option key={r.id} value={r.id}>
              {r.owner}/{r.name}
            </option>
          ))}
        </select>
        <select
          className={selectClass}
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        >
          <option value="">All statuses</option>
          <option value="queued">Queued</option>
          <option value="retrieving">Retrieving</option>
          <option value="evidence_ready">Evidence ready</option>
          <option value="failed">Failed</option>
        </select>
        <Button
          variant="secondary"
          onClick={() => {
            setPage(1);
            load();
          }}
        >
          Apply filters
        </Button>
      </div>

      {error ? (
        <div className="mb-4">
          <InlineError message={error} onRetry={load} />
        </div>
      ) : null}

      {items.length === 0 && !error ? (
        <EmptyState
          title="No investigations yet"
          description="Create an investigation against a ready repository to retrieve evidence and reproduce failures."
          action={
            <Link href="/investigations/new">
              <Button>New investigation</Button>
            </Link>
          }
        />
      ) : (
        <Card className="overflow-x-auto p-0">
          <table className="w-full text-left text-sm">
            <thead className="text-meta text-mute">
              <tr>
                <th className="px-4 py-2 font-medium">Title</th>
                <th className="px-4 py-2 font-medium">Repository</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 font-medium">Created</th>
                <th className="px-4 py-2 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={i.id} className="border-t border-line">
                  <td className="px-4 py-2.5">
                    <Link href={`/investigations/${i.id}`}>{i.title}</Link>
                  </td>
                  <td className="px-4 py-2.5 text-mute">{i.repository_name}</td>
                  <td className="px-4 py-2.5">
                    <StatusBadge status={i.status} />
                  </td>
                  <td className="px-4 py-2.5 text-mute">{formatDate(i.created_at)}</td>
                  <td className="px-4 py-2.5">
                    <Button
                      variant="ghost"
                      aria-label="Delete investigation"
                      onClick={async () => {
                        if (!confirm("Delete this investigation?")) return;
                        await api.deleteInvestigation(i.id);
                        load();
                      }}
                    >
                      <Trash2 className="h-4 w-4 text-danger" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <div className="mt-4 flex items-center justify-between text-sm text-mute">
        <span>
          Page {page} of {pages} · {total} total
        </span>
        <div className="flex gap-2">
          <Button variant="secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            Previous
          </Button>
          <Button
            variant="secondary"
            disabled={page >= pages}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  );
}
