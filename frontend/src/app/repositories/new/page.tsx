"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { api } from "@/lib/api";
import {
  Button,
  Card,
  Hint,
  InlineError,
  Input,
  Label,
  PageHeader,
} from "@/components/ui";

export default function NewRepositoryPage() {
  const router = useRouter();
  const [githubUrl, setGithubUrl] = useState("");
  const [branch, setBranch] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    setFieldErrors({});
    try {
      const repo = await api.createRepository({
        github_url: githubUrl.trim(),
        branch: branch.trim() || undefined,
        display_name: displayName.trim() || undefined,
      });
      router.push(`/repositories/${repo.id}`);
    } catch (err) {
      const e = err as Error & {
        body?: { error?: { message?: string; field_errors?: Record<string, string[]> } };
      };
      setError(e.body?.error?.message || e.message);
      setFieldErrors(e.body?.error?.field_errors || {});
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader
        title="Add repository"
        description="Import a public GitHub repository. Indexing runs in the background."
      />
      <Card>
        <form onSubmit={onSubmit} className="space-y-5">
          <div>
            <Label htmlFor="github_url" hint="Public HTTPS URLs only. Optional .git suffix allowed.">
              GitHub URL
            </Label>
            <Input
              id="github_url"
              required
              placeholder="https://github.com/owner/repo"
              value={githubUrl}
              onChange={(e) => setGithubUrl(e.target.value)}
              aria-invalid={!!fieldErrors.github_url}
            />
            {fieldErrors.github_url ? (
              <p className="mt-1 text-meta text-danger">{fieldErrors.github_url.join(" ")}</p>
            ) : null}
          </div>
          <div>
            <Label htmlFor="branch" hint="Leave blank to use the repository default branch.">
              Branch
            </Label>
            <Input
              id="branch"
              placeholder="main"
              value={branch}
              onChange={(e) => setBranch(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="display_name">Display name</Label>
            <Input
              id="display_name"
              maxLength={100}
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </div>
          {error ? <InlineError message={error} /> : null}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={submitting || !githubUrl.trim()}>
              {submitting ? "Importing…" : "Import repository"}
            </Button>
            <Button type="button" variant="secondary" onClick={() => router.back()}>
              Cancel
            </Button>
            {!githubUrl.trim() ? <Hint>Enter a GitHub URL to enable import.</Hint> : null}
          </div>
        </form>
      </Card>
    </div>
  );
}
