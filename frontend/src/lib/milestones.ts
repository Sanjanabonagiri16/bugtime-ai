import type { EvidenceItem, ExecutionSummary, InvestigationDetail } from "./types";

export type Milestone = {
  key: string;
  label: string;
  done: boolean;
  current: boolean;
  upcoming?: boolean;
};

export function deriveMilestones(
  inv: InvestigationDetail | null,
  evidence: EvidenceItem[],
  executions: ExecutionSummary[],
  selectedTestCount: number,
): Milestone[] {
  const evidenceReady =
    !!inv && (inv.status === "evidence_ready" || evidence.length > 0);
  const repro = executions.filter((e) => e.run_type === "reproduction");
  const hadSelection = executions.some((e) => (e.selected_tests || []).length > 0);
  const testSelected = selectedTestCount > 0 || hadSelection;
  const executionComplete = repro.some((e) =>
    ["completed", "failed", "timed_out", "cancelled"].includes(e.status),
  );
  const confirmed = repro.some((e) => e.confirmed);

  const core: Milestone[] = [
    { key: "evidence_ready", label: "Evidence ready", done: evidenceReady, current: false },
    { key: "test_selected", label: "Test selected", done: testSelected, current: false },
    {
      key: "execution_complete",
      label: "Execution complete",
      done: executionComplete,
      current: false,
    },
    {
      key: "bug_match_confirmed",
      label: "Bug match confirmed",
      done: confirmed,
      current: false,
    },
    {
      key: "commit_investigation",
      label: "Commit investigation",
      done: false,
      current: false,
      upcoming: !confirmed,
    },
  ];

  const firstIncomplete = core.findIndex((m) => !m.done);
  if (firstIncomplete >= 0) {
    core[firstIncomplete].current = true;
    core[firstIncomplete].upcoming = false;
  }

  return core;
}

export function investigationOutcomeLabel(
  inv: InvestigationDetail | null,
  executions: ExecutionSummary[],
): string {
  const confirmed = executions.find((e) => e.confirmed);
  if (confirmed) return "Ready for commit search";
  const latestRepro = executions.find((e) => e.run_type === "reproduction");
  if (latestRepro) {
    if (latestRepro.status === "timed_out") return "Timed out";
    if (latestRepro.status === "cancelled") return "Cancelled";
    if (latestRepro.error_category === "environment_setup_unsupported") {
      return "Environment error";
    }
    if (latestRepro.status === "completed" && latestRepro.failed > 0) {
      return "Failure observed";
    }
    if (latestRepro.status === "completed" && latestRepro.failed === 0) {
      return "Selected tests passed";
    }
  }
  if (inv?.status === "evidence_ready") return "Evidence ready";
  if (inv?.status === "retrieving" || inv?.status === "queued") return "Retrieving evidence";
  if (inv?.status === "failed") return "Retrieval failed";
  return "In progress";
}
