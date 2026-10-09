"use client";

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { api } from "@/lib/api";
import type { GitHubStatus, HealthResponse } from "@/lib/types";
import {
  getTerminalAutoScroll,
  setTerminalAutoScroll,
} from "@/lib/prefs";
import { Button, Card, Hint, InlineError, PageHeader } from "@/components/ui";
import { StatusBadge } from "@/components/status-badge";

export default function SettingsPage() {
  const { theme, setTheme } = useTheme();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [github, setGithub] = useState<GitHubStatus | null>(null);
  const [githubError, setGithubError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  const load = () => {
    api
      .health()
      .then((h) => {
        setHealth(h);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  };

  const loadGithub = () => {
    api
      .githubStatus()
      .then((s) => {
        setGithub(s);
        setGithubError(null);
      })
      .catch((e: Error) => setGithubError(e.message));
  };

  useEffect(() => {
    load();
    loadGithub();
    setAutoScroll(getTerminalAutoScroll());
  }, []);

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title="Settings"
        description="Appearance, service health, and execution preferences."
      />

      <div className="space-y-4">
        <Card>
          <h2 className="text-sm font-semibold">Appearance</h2>
          <Hint>Theme preference is saved in this browser.</Hint>
          <div className="mt-3 flex gap-2">
            <Button
              variant={theme === "dark" ? "primary" : "secondary"}
              onClick={() => setTheme("dark")}
            >
              Dark
            </Button>
            <Button
              variant={theme === "light" ? "primary" : "secondary"}
              onClick={() => setTheme("light")}
            >
              Light
            </Button>
            <Button
              variant={theme === "system" ? "primary" : "secondary"}
              onClick={() => setTheme("system")}
            >
              System
            </Button>
          </div>
        </Card>

        <Card>
          <h2 className="text-sm font-semibold">Services</h2>
          {error ? (
            <div className="mt-3">
              <InlineError message={`Unable to reach backend: ${error}`} onRetry={load} />
            </div>
          ) : null}
          {health ? (
            <div className="mt-3 space-y-3">
              <div className="flex items-center gap-2 text-sm">
                <span className="text-mute">Overall</span>
                <StatusBadge status={health.status === "ok" ? "ready" : "failed"} />
              </div>
              <ul className="space-y-2 text-sm">
                {health.services.map((s) => (
                  <li
                    key={s.name}
                    className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-2 last:border-0"
                  >
                    <span className="capitalize">{s.name}</span>
                    <div className="flex items-center gap-2">
                      <StatusBadge status={s.status === "ok" ? "ready" : "failed"} />
                      {s.detail ? (
                        <span className="max-w-xs truncate text-meta text-mute">{s.detail}</span>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
              <div>
                <div className="text-meta text-mute">Embedding model</div>
                <p className="mt-1 font-mono text-meta text-ink">
                  {health.embedding_model || "—"}
                </p>
              </div>
              <div>
                <div className="text-meta text-mute">Import limits</div>
                <ul className="mt-1 space-y-1 font-mono text-meta text-mute">
                  {Object.entries(health.import_limits).map(([k, v]) => (
                    <li key={k}>
                      {k}: {v}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ) : (
            !error && <p className="mt-2 text-sm text-mute">Checking services…</p>
          )}
        </Card>

        <Card>
          <h2 className="text-sm font-semibold">GitHub</h2>
          <Hint>
            Server-side fine-grained token for draft PR publishing. The token is never returned to
            this page.
          </Hint>
          {githubError ? (
            <div className="mt-3">
              <InlineError message={githubError} onRetry={loadGithub} />
            </div>
          ) : null}
          {github ? (
            <div className="mt-3 space-y-2 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-mute">Configured</span>
                <StatusBadge status={github.configured ? "ready" : "pending"} />
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-mute">Connection</span>
                {github.connected === true ? (
                  <StatusBadge status="ready" label="Connected" />
                ) : github.connected === false ? (
                  <StatusBadge status="failed" label="Failed" />
                ) : (
                  <StatusBadge status="pending" label="Not checked" />
                )}
              </div>
              {github.login ? (
                <p className="text-meta text-mute">
                  Authenticated as <span className="font-mono text-ink">{github.login}</span>
                </p>
              ) : null}
              {github.scopes_hint ? (
                <p className="text-meta text-mute">Scopes: {github.scopes_hint}</p>
              ) : null}
              {github.error ? <p className="text-meta text-mute">{github.error}</p> : null}
              <Button size="sm" variant="secondary" onClick={loadGithub}>
                Recheck connection
              </Button>
            </div>
          ) : (
            !githubError && <p className="mt-2 text-sm text-mute">Checking GitHub…</p>
          )}
        </Card>

        <Card>
          <h2 className="text-sm font-semibold">Execution</h2>
          <Hint>
            Terminal auto-scroll applies during test runs. Scrolling up disables it automatically.
          </Hint>
          <label className="mt-3 flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="accent-[var(--bt-accent)]"
              checked={autoScroll}
              onChange={(e) => {
                setAutoScroll(e.target.checked);
                setTerminalAutoScroll(e.target.checked);
              }}
            />
            Auto-scroll terminal output
          </label>
          <p className="mt-3 text-meta text-mute">
            Runner image, timeouts, and sandbox limits are configured via backend environment
            variables and are not editable here.
          </p>
        </Card>
      </div>
    </div>
  );
}
