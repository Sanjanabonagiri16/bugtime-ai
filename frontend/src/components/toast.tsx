"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

type ToastItem = {
  id: string;
  message: string;
  tone?: "info" | "success" | "danger";
};

type ToastCtx = {
  push: (message: string, tone?: ToastItem["tone"]) => void;
};

const Ctx = createContext<ToastCtx>({ push: () => undefined });

export function useToast() {
  return useContext(Ctx);
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const push = useCallback((message: string, tone: ToastItem["tone"] = "info") => {
    const id = crypto.randomUUID();
    setItems((prev) => [...prev, { id, message, tone }]);
    window.setTimeout(() => {
      setItems((prev) => prev.filter((t) => t.id !== id));
    }, 3200);
  }, []);

  const value = useMemo(() => ({ push }), [push]);

  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[80] flex w-[min(360px,calc(100vw-2rem))] flex-col gap-2">
        {items.map((t) => (
          <div
            key={t.id}
            className={cn(
              "pointer-events-auto flex items-start gap-2 rounded-control border bg-elevated px-3 py-2 text-sm shadow-panel bt-transition",
              t.tone === "success" && "border-success/40",
              t.tone === "danger" && "border-danger/40",
              t.tone === "info" && "border-line",
            )}
            role="status"
          >
            <p className="flex-1 text-ink">{t.message}</p>
            <button
              type="button"
              className="text-mute hover:text-ink"
              aria-label="Dismiss"
              onClick={() => setItems((prev) => prev.filter((x) => x.id !== t.id))}
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
