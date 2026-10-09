import { cn } from "@/lib/utils";

export function BugTimeMark({
  className,
  size = 28,
}: {
  className?: string;
  size?: number;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={cn("shrink-0", className)}
      aria-hidden
    >
      <rect x="1" y="1" width="30" height="30" rx="8" fill="var(--bt-elevated)" stroke="var(--bt-border)" />
      {/* Timeline spine */}
      <path
        d="M8 7v18"
        stroke="var(--bt-mute)"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      <circle cx="8" cy="10" r="2" fill="var(--bt-accent)" />
      <circle cx="8" cy="16" r="2" fill="var(--bt-mute)" />
      <circle cx="8" cy="22" r="2" fill="var(--bt-mute)" />
      {/* Magnifying glass */}
      <circle
        cx="19.5"
        cy="15.5"
        r="5.2"
        stroke="var(--bt-accent)"
        strokeWidth="1.8"
      />
      <path
        d="M23.2 19.2L27 23"
        stroke="var(--bt-accent)"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}
