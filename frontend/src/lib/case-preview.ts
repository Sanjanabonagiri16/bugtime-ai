import type {
  ExecutionDetail,
  ExecutionSummary,
  InvestigationDetail,
} from "./types";
import { investigationOutcomeLabel } from "./milestones";

export type TrailEvent = {
  key: string;
  label: string;
  at: string;
  detail?: string;
};

export function shortCaseId(id: string) {
  return id.replace(/-/g, "").slice(0, 8);
}

/** Build a vertical activity trail from persisted state only. */
export function buildActivityTrail(
  inv: InvestigationDetail,
  executions: ExecutionSummary[],
  latestRepro?: ExecutionDetail | null,
): TrailEvent[] {
  const trail: TrailEvent[] = [];

  if (inv.status === "evidence_ready" || inv.evidence_count > 0) {
    trail.push({
      key: "evidence",
      label: "Evidence retrieved",
      at: inv.completed_at || inv.created_at,
      detail:
        inv.evidence_count > 0
          ? `${inv.evidence_count} candidate${inv.evidence_count === 1 ? "" : "s"}`
          : undefined,
    });
  }

  const discovery = executions.find(
    (e) => e.run_type === "collection" && e.status === "completed",
  );
  if (discovery) {
    trail.push({
      key: "discovery",
      label: "Tests discovered",
      at: discovery.completed_at || discovery.created_at,
      detail:
        discovery.collected > 0
          ? `${discovery.collected} collected`
          : undefined,
    });
  }

  const repro =
    executions.find((e) => e.run_type === "reproduction" && e.confirmed) ||
    executions.find(
      (e) => e.run_type === "reproduction" && e.status === "completed",
    ) ||
    executions.find(
      (e) =>
        e.run_type === "reproduction" &&
        ["failed", "timed_out", "cancelled"].includes(e.status),
    );
  if (repro) {
    const outcome = investigationOutcomeLabel(inv, executions);
    const counts =
      repro.status === "completed" || repro.confirmed
        ? `${repro.passed} passed · ${repro.failed} failed · `
        : "";
    trail.push({
      key: "execution",
      label: "Execution result",
      at: repro.completed_at || repro.created_at,
      detail: `${counts}${outcome}`,
    });
  }

  const confirmed = executions.find((e) => e.confirmed);
  if (confirmed) {
    const confirmedAt =
      latestRepro?.confirmation?.created_at ||
      confirmed.completed_at ||
      confirmed.created_at;
    trail.push({
      key: "confirmed",
      label: "Developer confirmation",
      at: confirmedAt,
      detail: "Bug reproduced — developer confirmed",
    });
  }

  return trail;
}

export function latestFailedAssertion(detail: ExecutionDetail | null) {
  if (!detail || detail.run_type !== "reproduction") return null;
  const failed = detail.test_results.find(
    (t) => t.outcome === "failed" || t.outcome === "error",
  );
  if (!failed) return null;
  return {
    test: failed.test_identifier,
    message: failed.failure_message || "Failure observed",
  };
}
