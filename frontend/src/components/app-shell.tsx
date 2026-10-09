"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ChevronLeft,
  ChevronRight,
  FlaskConical,
  FolderGit2,
  History,
  LayoutDashboard,
  Menu,
  Settings,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { cn, formatDate } from "@/lib/utils";
import { BugTimeMark } from "@/components/bugtime-mark";
import { api } from "@/lib/api";
import type { HealthResponse, InvestigationSummary } from "@/lib/types";
import { getSidebarCollapsed, setSidebarCollapsed } from "@/lib/prefs";
import { useTheme } from "next-themes";
import { Button } from "@/components/ui";

const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/repositories", label: "Repositories", icon: FolderGit2 },
  { href: "/investigations", label: "History", icon: History },
  { href: "/evaluations", label: "Evaluations", icon: FlaskConical },
  { href: "/settings", label: "Settings", icon: Settings },
];

function crumbs(pathname: string): { label: string; href?: string }[] {
  if (pathname === "/") return [{ label: "Investigations" }];
  if (pathname.startsWith("/repositories/new")) {
    return [
      { label: "Repositories", href: "/repositories" },
      { label: "Add repository" },
    ];
  }
  if (pathname.startsWith("/repositories/")) {
    return [
      { label: "Repositories", href: "/repositories" },
      { label: "Repository" },
    ];
  }
  if (pathname === "/repositories") return [{ label: "Repositories" }];
  if (pathname.startsWith("/investigations/new")) {
    return [
      { label: "Investigations", href: "/" },
      { label: "New investigation" },
    ];
  }
  if (pathname.startsWith("/investigations/")) {
    return [
      { label: "Investigations", href: "/" },
      { label: "Workspace" },
    ];
  }
  if (pathname === "/investigations") return [{ label: "History" }];
  if (pathname === "/settings") return [{ label: "Settings" }];
  if (pathname === "/evaluations" || pathname.startsWith("/evaluations/")) {
    return [{ label: "Evaluations" }];
  }
  return [{ label: "BugTime" }];
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { theme, setTheme } = useTheme();
  const [collapsed, setCollapsed] = useState(true);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [recent, setRecent] = useState<InvestigationSummary[]>([]);
  const [health, setHealth] = useState<HealthResponse | null>(null);

  useEffect(() => {
    setCollapsed(getSidebarCollapsed());
  }, []);

  useEffect(() => {
    api
      .listInvestigations({ page: 1 })
      .then((r) => setRecent(r.items.slice(0, 5)))
      .catch(() => undefined);
    api
      .health()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, [pathname]);

  const toggleCollapsed = () => {
    setCollapsed((v) => {
      const next = !v;
      setSidebarCollapsed(next);
      return next;
    });
  };

  const NavLinks = (
    <nav className="flex flex-1 flex-col gap-1 p-2">
      {NAV.map((item) => {
        const active =
          item.href === "/"
            ? pathname === "/"
            : item.href === "/investigations"
              ? pathname === "/investigations" || pathname === "/investigations/"
              : pathname.startsWith(item.href);
        const Icon = item.icon;
        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={() => setMobileOpen(false)}
            className={cn(
              "relative flex min-h-11 items-center gap-2.5 rounded-control px-2.5 py-2 text-sm bt-transition",
              active
                ? "bg-accent-soft text-ink"
                : "text-mute hover:bg-elevated hover:text-ink",
              collapsed && "justify-center px-2",
            )}
            title={collapsed ? item.label : undefined}
          >
            {active ? (
              <span className="absolute bottom-1.5 left-0 top-1.5 w-[3px] rounded-r bg-accent" />
            ) : null}
            <Icon className="h-4 w-4 shrink-0" />
            {!collapsed ? <span>{item.label}</span> : null}
          </Link>
        );
      })}

      {!collapsed ? (
        <div className="mt-4 px-2">
          <div className="mb-2 text-meta uppercase tracking-[0.08em] text-mute">
            Recent investigations
          </div>
          <ul className="space-y-1">
            {recent.map((inv) => (
              <li key={inv.id}>
                <Link
                  href={`/investigations/${inv.id}`}
                  onClick={() => setMobileOpen(false)}
                  className="block rounded-control px-2 py-1.5 hover:bg-elevated"
                >
                  <div className="truncate text-sm text-ink">{inv.title}</div>
                  <div className="truncate text-meta text-mute">
                    {inv.repository_name} · {formatDate(inv.created_at)}
                  </div>
                </Link>
              </li>
            ))}
            {recent.length === 0 ? (
              <li className="px-2 py-1 text-meta text-mute">No investigations yet</li>
            ) : null}
          </ul>
        </div>
      ) : null}
    </nav>
  );

  const healthOk = health?.status === "ok";
  const trail = crumbs(pathname);
  const isDashboard = pathname === "/";

  return (
    <div className="min-h-screen bg-canvas text-ink">
      <div className="flex min-h-screen">
        <aside
          className={cn(
            "sticky top-0 hidden h-screen shrink-0 flex-col border-r border-line bg-sidebar md:flex bt-transition",
            collapsed ? "w-16" : "w-[220px]",
          )}
        >
          <div className="flex h-14 items-center gap-2 border-b border-line px-2.5">
            <Link href="/" className="flex min-w-0 items-center gap-2" title="BugTime AI">
              <BugTimeMark size={28} />
              {!collapsed ? (
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold">BugTime AI</div>
                  <div className="truncate text-meta text-mute">Trace the bug</div>
                </div>
              ) : null}
            </Link>
            <button
              type="button"
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              className="ml-auto rounded-control border border-line p-1.5 text-mute hover:text-ink"
              onClick={toggleCollapsed}
            >
              {collapsed ? (
                <ChevronRight className="h-3.5 w-3.5" />
              ) : (
                <ChevronLeft className="h-3.5 w-3.5" />
              )}
            </button>
          </div>
          {NavLinks}
          <div className="border-t border-line p-2.5 text-meta text-mute">
            <div
              className={cn("flex items-center gap-2", collapsed && "justify-center")}
              title={
                health
                  ? health.services.map((s) => `${s.name}: ${s.status}`).join(", ")
                  : "Health unavailable"
              }
            >
              <span
                className={cn(
                  "h-2 w-2 shrink-0 rounded-full",
                  healthOk ? "bg-success" : "bg-danger",
                )}
              />
              {!collapsed ? (
                <span>Services {healthOk ? "healthy" : health ? "degraded" : "unknown"}</span>
              ) : null}
            </div>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-40 flex h-14 items-center justify-between gap-3 border-b border-line bg-canvas/95 px-4 backdrop-blur md:px-6">
            <div className="flex min-w-0 items-center gap-3">
              <button
                type="button"
                className="flex h-9 w-9 items-center justify-center rounded-control border border-line md:hidden"
                aria-label={mobileOpen ? "Close navigation" : "Open navigation"}
                onClick={() => setMobileOpen((v) => !v)}
              >
                {mobileOpen ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
              </button>
              {!isDashboard ? (
                <nav aria-label="Breadcrumb" className="min-w-0 truncate text-sm text-mute">
                  {trail.map((c, i) => (
                    <span key={`${c.label}-${i}`}>
                      {i > 0 ? <span className="mx-1.5 text-line">/</span> : null}
                      {c.href ? (
                        <Link href={c.href} className="hover:text-ink">
                          {c.label}
                        </Link>
                      ) : (
                        <span className="text-ink">{c.label}</span>
                      )}
                    </span>
                  ))}
                </nav>
              ) : (
                <span className="text-sm text-mute">Investigation center</span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <div
                className="hidden items-center gap-1.5 rounded-control border border-line px-2 py-1 text-meta text-mute sm:flex"
                title={
                  health
                    ? health.services.map((s) => `${s.name}: ${s.status}`).join(", ")
                    : "Health unavailable"
                }
              >
                <span
                  className={cn(
                    "h-1.5 w-1.5 rounded-full",
                    healthOk ? "bg-success" : "bg-danger",
                  )}
                />
                {healthOk ? "Healthy" : health ? "Degraded" : "Offline"}
              </div>
              <Button
                type="button"
                size="sm"
                variant="secondary"
                onClick={() => setTheme(theme === "light" ? "dark" : "light")}
              >
                {theme === "light" ? "Dark" : "Light"}
              </Button>
            </div>
          </header>

          {mobileOpen ? (
            <div className="border-b border-line bg-sidebar md:hidden">
              <div className="flex h-14 items-center gap-2 border-b border-line px-4">
                <BugTimeMark size={24} />
                <span className="font-semibold">BugTime AI</span>
              </div>
              <div className="[&_nav]:flex-none">{NavLinks}</div>
            </div>
          ) : null}

          <main
            className={cn(
              "flex-1",
              isDashboard ? "px-4 py-4 md:px-6 md:py-5" : "px-4 py-5 md:px-6 md:py-6",
            )}
          >
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}
