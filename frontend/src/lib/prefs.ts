"use client";

const SIDEBAR_KEY = "bugtime.sidebarCollapsed";
const AUTOSCROLL_KEY = "bugtime.terminalAutoScroll";

export function getSidebarCollapsed(): boolean {
  if (typeof window === "undefined") return true;
  const v = localStorage.getItem(SIDEBAR_KEY);
  // Default to compact rail when unset.
  return v === null ? true : v === "1";
}

export function setSidebarCollapsed(value: boolean) {
  localStorage.setItem(SIDEBAR_KEY, value ? "1" : "0");
}

export function getTerminalAutoScroll(): boolean {
  if (typeof window === "undefined") return true;
  const v = localStorage.getItem(AUTOSCROLL_KEY);
  return v === null ? true : v === "1";
}

export function setTerminalAutoScroll(value: boolean) {
  localStorage.setItem(AUTOSCROLL_KEY, value ? "1" : "0");
}
