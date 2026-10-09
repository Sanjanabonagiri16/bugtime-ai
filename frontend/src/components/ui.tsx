import { cn } from "@/lib/utils";
import {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

export function Button({
  className,
  variant = "primary",
  size = "md",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  size?: "sm" | "md";
}) {
  const variants = {
    primary:
      "bg-accent text-[#042f2e] hover:brightness-110 disabled:bg-elevated disabled:text-mute dark:text-[#042f2e]",
    secondary:
      "bg-elevated text-ink border border-line hover:border-accent/40 hover:bg-panel",
    danger: "bg-danger/15 text-danger border border-danger/30 hover:bg-danger/25",
    ghost: "bg-transparent text-mute hover:bg-elevated hover:text-ink",
  };
  const sizes = {
    sm: "h-8 px-2.5 text-meta",
    md: "h-9 px-3.5 text-sm",
  };
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-control font-medium bt-transition disabled:cursor-not-allowed disabled:opacity-50",
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    />
  );
}

export function Input({
  className,
  ...props
}: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "w-full rounded-control border border-line bg-canvas px-3 py-2 text-sm text-ink placeholder:text-mute/70 outline-none bt-transition focus:border-accent/50",
        className,
      )}
      {...props}
    />
  );
}

export function Textarea({
  className,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "w-full rounded-control border border-line bg-canvas px-3 py-2 font-mono text-sm text-ink placeholder:text-mute/70 outline-none bt-transition focus:border-accent/50",
        className,
      )}
      {...props}
    />
  );
}

export function Label({
  children,
  htmlFor,
  hint,
}: {
  children: React.ReactNode;
  htmlFor?: string;
  hint?: string;
}) {
  return (
    <div className="mb-1.5">
      <label htmlFor={htmlFor} className="block text-sm font-medium text-ink">
        {children}
      </label>
      {hint ? <p className="mt-0.5 text-meta text-mute">{hint}</p> : null}
    </div>
  );
}

export function Card({
  children,
  className,
  elevated = false,
}: {
  children: React.ReactNode;
  className?: string;
  elevated?: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded-panel border border-line p-4 shadow-soft",
        elevated ? "bg-elevated" : "bg-panel",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  eyebrow?: string;
}) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        {eyebrow ? (
          <div className="mb-1 text-meta uppercase tracking-[0.08em] text-mute">{eyebrow}</div>
        ) : null}
        <h1 className="text-heading tracking-tight text-ink">{title}</h1>
        {description ? <p className="mt-1 max-w-2xl text-sm text-mute">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <Card className="border-dashed px-6 py-10 text-center">
      <h2 className="text-lg font-semibold text-ink">{title}</h2>
      <p className="mx-auto mt-2 max-w-md text-sm text-mute">{description}</p>
      {action ? <div className="mt-5 flex justify-center gap-2">{action}</div> : null}
    </Card>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn("animate-pulse rounded-control bg-elevated", className)}
      aria-hidden
    />
  );
}

export function InlineError({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="rounded-panel border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p>{message}</p>
        {onRetry ? (
          <Button type="button" size="sm" variant="secondary" onClick={onRetry}>
            Retry
          </Button>
        ) : null}
      </div>
    </div>
  );
}

export function Hint({ children }: { children: React.ReactNode }) {
  return <p className="text-meta text-mute">{children}</p>;
}
