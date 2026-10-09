"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { RepositorySummary } from "@/lib/types";
import {
  Button,
  Card,
  Hint,
  InlineError,
  Input,
  Label,
  PageHeader,
  Skeleton,
  Textarea,
} from "@/components/ui";

const selectClass =
  "w-full rounded-control border border-line bg-canvas px-3 py-2 text-sm text-ink outline-none bt-transition focus:border-accent/50";

function NewInvestigationForm() {
  const router = useRouter();
  const search = useSearchParams();
  const [repos, setRepos] = useState<RepositorySummary[]>([]);
  const [repositoryId, setRepositoryId] = useState(search.get("repository_id") || "");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [expected, setExpected] = useState("");
  const [actual, setActual] = useState("");
  const [logs, setLogs] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api.listRepositories("", 1).then((res) => {
      const ready = res.items.filter((r) => r.status === "ready");
      setRepos(ready);
      if (!repositoryId && ready[0]) setRepositoryId(ready[0].id);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const inv = await api.createInvestigation({
        repository_id: repositoryId,
        title: title.trim(),
        description: description.trim(),
        expected_behavior: expected.trim() || undefined,
        actual_behavior: actual.trim() || undefined,
        logs: logs.trim() || undefined,
      });
      router.push(`/investigations/${inv.id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  const titleOk = title.trim().length >= 5;
  const descOk = description.trim().length >= 20;

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title="New investigation"
        description="Describe the bug. BugTime retrieves candidate code from the pinned ready index."
      />
      <form onSubmit={onSubmit} className="space-y-4">
        <Card>
          <h2 className="text-sm font-semibold">Context</h2>
          <div className="mt-4 space-y-4">
            <div>
              <Label htmlFor="repository" hint="Only repositories with a ready index appear here.">
                Repository
              </Label>
              <select
                id="repository"
                required
                className={selectClass}
                value={repositoryId}
                onChange={(e) => setRepositoryId(e.target.value)}
              >
                <option value="">Select a ready repository</option>
                {repos.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.owner}/{r.name}
                  </option>
                ))}
              </select>
              {repos.length === 0 ? (
                <Hint>No ready repositories. Import and wait for indexing to finish.</Hint>
              ) : null}
            </div>
            <div>
              <Label htmlFor="title" hint="5–120 characters.">
                Title
              </Label>
              <Input
                id="title"
                required
                minLength={5}
                maxLength={120}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Login fails for mixed-case usernames"
                aria-invalid={title.length > 0 && !titleOk}
              />
              {title.length > 0 && !titleOk ? (
                <p className="mt-1 text-meta text-danger">Title must be at least 5 characters.</p>
              ) : null}
            </div>
          </div>
        </Card>

        <Card>
          <h2 className="text-sm font-semibold">Bug report</h2>
          <div className="mt-4 space-y-4">
            <div>
              <Label htmlFor="description" hint="At least 20 characters. Be specific about symptoms.">
                Description
              </Label>
              <Textarea
                id="description"
                required
                minLength={20}
                maxLength={10000}
                rows={5}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                aria-invalid={description.length > 0 && !descOk}
              />
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <Label htmlFor="expected">Expected behavior</Label>
                <Textarea
                  id="expected"
                  maxLength={5000}
                  rows={4}
                  value={expected}
                  onChange={(e) => setExpected(e.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="actual">Actual behavior</Label>
                <Textarea
                  id="actual"
                  maxLength={5000}
                  rows={4}
                  value={actual}
                  onChange={(e) => setActual(e.target.value)}
                />
              </div>
            </div>
          </div>
        </Card>

        <Card>
          <h2 className="text-sm font-semibold">Stack trace / logs</h2>
          <Hint>Paste assertion failures or traceback text. Shown in a monospace editor.</Hint>
          <Textarea
            id="logs"
            className="mt-3"
            maxLength={20000}
            rows={8}
            value={logs}
            onChange={(e) => setLogs(e.target.value)}
            placeholder="Traceback (most recent call last):…"
          />
        </Card>

        {error ? <InlineError message={error} /> : null}

        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={submitting || !repositoryId || !titleOk || !descOk}>
            {submitting ? "Submitting…" : "Find relevant code"}
          </Button>
          {!repositoryId || !titleOk || !descOk ? (
            <Hint>Select a repository and complete title and description to continue.</Hint>
          ) : null}
        </div>
      </form>
    </div>
  );
}

export default function NewInvestigationPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64 w-full" />}>
      <NewInvestigationForm />
    </Suspense>
  );
}
