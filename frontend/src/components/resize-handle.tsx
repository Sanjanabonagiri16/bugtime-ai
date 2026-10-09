"use client";

import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

export function ResizeHandle({
  direction,
  onDrag,
  className,
}: {
  direction: "horizontal" | "vertical";
  onDrag: (delta: number) => void;
  className?: string;
}) {
  const dragging = useRef(false);
  const last = useRef(0);

  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      if (!dragging.current) return;
      const pos = direction === "horizontal" ? e.clientX : e.clientY;
      const delta = pos - last.current;
      last.current = pos;
      onDrag(delta);
    };
    const onUp = () => {
      dragging.current = false;
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [direction, onDrag]);

  return (
    <div
      role="separator"
      aria-orientation={direction === "horizontal" ? "vertical" : "horizontal"}
      tabIndex={0}
      className={cn(
        "shrink-0 bg-transparent hover:bg-accent/20 focus-visible:bg-accent/30 bt-transition",
        direction === "horizontal" ? "w-1.5 cursor-col-resize" : "h-1.5 cursor-row-resize",
        className,
      )}
      onPointerDown={(e) => {
        dragging.current = true;
        last.current = direction === "horizontal" ? e.clientX : e.clientY;
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 24 : 8;
        if (direction === "horizontal") {
          if (e.key === "ArrowLeft") onDrag(-step);
          if (e.key === "ArrowRight") onDrag(step);
        } else {
          if (e.key === "ArrowUp") onDrag(-step);
          if (e.key === "ArrowDown") onDrag(step);
        }
      }}
    />
  );
}
