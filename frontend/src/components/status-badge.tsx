import { AlertCircle, CheckCircle2, Clock3, Loader2, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

const MAP: Record<
  string,
  { label: string; className: string; Icon: typeof CheckCircle2 }
> = {
  ready: {
    label: "Ready",
    className: "text-success bg-success/10 border-success/30",
    Icon: CheckCircle2,
  },
  evidence_ready: {
    label: "Evidence ready",
    className: "text-success bg-success/10 border-success/30",
    Icon: CheckCircle2,
  },
  completed: {
    label: "Completed",
    className: "text-success bg-success/10 border-success/30",
    Icon: CheckCircle2,
  },
  pending: {
    label: "Pending",
    className: "text-warn bg-warn/10 border-warn/30",
    Icon: Clock3,
  },
  queued: {
    label: "Queued",
    className: "text-warn bg-warn/10 border-warn/30",
    Icon: Clock3,
  },
  cancelled: {
    label: "Cancelled",
    className: "text-warn bg-warn/10 border-warn/30",
    Icon: AlertCircle,
  },
  indexing: {
    label: "Indexing",
    className: "text-accent bg-accent-soft border-accent/30",
    Icon: Loader2,
  },
  retrieving: {
    label: "Retrieving",
    className: "text-accent bg-accent-soft border-accent/30",
    Icon: Loader2,
  },
  preparing: {
    label: "Preparing",
    className: "text-accent bg-accent-soft border-accent/30",
    Icon: Loader2,
  },
  running: {
    label: "Running",
    className: "text-accent bg-accent-soft border-accent/30",
    Icon: Loader2,
  },
  fetching: {
    label: "Fetching",
    className: "text-accent bg-accent-soft border-accent/30",
    Icon: Loader2,
  },
  scanning: {
    label: "Scanning",
    className: "text-accent bg-accent-soft border-accent/30",
    Icon: Loader2,
  },
  chunking: {
    label: "Chunking",
    className: "text-accent bg-accent-soft border-accent/30",
    Icon: Loader2,
  },
  embedding: {
    label: "Embedding",
    className: "text-accent bg-accent-soft border-accent/30",
    Icon: Loader2,
  },
  saving: {
    label: "Saving",
    className: "text-accent bg-accent-soft border-accent/30",
    Icon: Loader2,
  },
  failed: {
    label: "Failed",
    className: "text-danger bg-danger/10 border-danger/30",
    Icon: XCircle,
  },
  timed_out: {
    label: "Timed out",
    className: "text-danger bg-danger/10 border-danger/30",
    Icon: XCircle,
  },
  validating: {
    label: "Validating",
    className: "text-accent bg-accent-soft border-accent/30",
    Icon: Loader2,
  },
  searching: {
    label: "Searching",
    className: "text-accent bg-accent-soft border-accent/30",
    Icon: Loader2,
  },
  verifying: {
    label: "Verifying",
    className: "text-accent bg-accent-soft border-accent/30",
    Icon: Loader2,
  },
  verified_boundary: {
    label: "Verified boundary",
    className: "text-success bg-success/10 border-success/30",
    Icon: CheckCircle2,
  },
  inconclusive: {
    label: "Inconclusive",
    className: "text-warn bg-warn/10 border-warn/30",
    Icon: AlertCircle,
  },
  invalid_range: {
    label: "Invalid range",
    className: "text-danger bg-danger/10 border-danger/30",
    Icon: XCircle,
  },
  inconsistent_reproduction: {
    label: "Inconsistent",
    className: "text-danger bg-danger/10 border-danger/30",
    Icon: XCircle,
  },
  unsupported_history: {
    label: "Unsupported history",
    className: "text-warn bg-warn/10 border-warn/30",
    Icon: AlertCircle,
  },
  budget_exhausted: {
    label: "Budget exhausted",
    className: "text-warn bg-warn/10 border-warn/30",
    Icon: AlertCircle,
  },
  proposing: {
    label: "Proposing",
    className: "text-accent bg-accent-soft border-accent/30",
    Icon: Loader2,
  },
  baseline: {
    label: "Baseline",
    className: "text-accent bg-accent-soft border-accent/30",
    Icon: Loader2,
  },
  deciding: {
    label: "Deciding",
    className: "text-accent bg-accent-soft border-accent/30",
    Icon: Loader2,
  },
  verified_fix: {
    label: "Verified fix",
    className: "text-success bg-success/10 border-success/30",
    Icon: CheckCircle2,
  },
  partial_verification: {
    label: "Partial verification",
    className: "text-warn bg-warn/10 border-warn/30",
    Icon: AlertCircle,
  },
  verification_failed: {
    label: "Verification failed",
    className: "text-danger bg-danger/10 border-danger/30",
    Icon: XCircle,
  },
  baseline_invalid: {
    label: "Baseline invalid",
    className: "text-danger bg-danger/10 border-danger/30",
    Icon: XCircle,
  },
  accepted: {
    label: "Accepted",
    className: "text-success bg-success/10 border-success/30",
    Icon: CheckCircle2,
  },
  rejected: {
    label: "Rejected",
    className: "text-danger bg-danger/10 border-danger/30",
    Icon: XCircle,
  },
  preview: {
    label: "Preview",
    className: "text-mute bg-elevated border-line",
    Icon: Clock3,
  },
  stale_base: {
    label: "Stale base",
    className: "text-warn bg-warn/10 border-warn/30",
    Icon: AlertCircle,
  },
  revalidating: {
    label: "Revalidating",
    className: "text-accent bg-accent-soft border-accent/30",
    Icon: Loader2,
  },
  publishing: {
    label: "Publishing",
    className: "text-accent bg-accent-soft border-accent/30",
    Icon: Loader2,
  },
  published: {
    label: "Published",
    className: "text-success bg-success/10 border-success/30",
    Icon: CheckCircle2,
  },
  conflict: {
    label: "Conflict",
    className: "text-danger bg-danger/10 border-danger/30",
    Icon: XCircle,
  },
};

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const meta = MAP[status] || {
    label: status,
    className: "text-mute bg-elevated border-line",
    Icon: AlertCircle,
  };
  const Icon = meta.Icon;
  const spin = [
    "indexing",
    "retrieving",
    "fetching",
    "scanning",
    "chunking",
    "embedding",
    "saving",
    "preparing",
    "running",
    "validating",
    "searching",
    "verifying",
    "proposing",
    "baseline",
    "retrieving",
    "deciding",
    "preparing",
    "revalidating",
    "publishing",
  ].includes(status);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-control border px-2 py-0.5 text-meta font-medium",
        meta.className,
      )}
    >
      <Icon className={cn("h-3.5 w-3.5", spin && "animate-spin")} aria-hidden />
      <span>{label || meta.label}</span>
    </span>
  );
}
