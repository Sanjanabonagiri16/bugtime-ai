"use client";

import Editor from "@monaco-editor/react";
import { useTheme } from "next-themes";

export function CodeViewer({
  path,
  content,
  language,
  highlightStart,
  highlightEnd,
  height = "420px",
  showPath = true,
}: {
  path: string;
  content: string;
  language?: string | null;
  highlightStart?: number;
  highlightEnd?: number;
  height?: string;
  showPath?: boolean;
}) {
  const { resolvedTheme } = useTheme();
  const lang =
    language ||
    (path.endsWith(".py")
      ? "python"
      : path.endsWith(".md")
        ? "markdown"
        : path.endsWith(".json")
          ? "json"
          : path.endsWith(".yml") || path.endsWith(".yaml")
            ? "yaml"
            : "plaintext");

  return (
    <div className="overflow-hidden rounded-panel border border-line bg-panel">
      {showPath ? (
        <div className="flex flex-wrap items-center gap-2 border-b border-line bg-elevated px-3 py-2 font-mono text-meta text-mute">
          <span className="min-w-0 truncate text-ink">{path}</span>
          {highlightStart ? (
            <span className="shrink-0">
              L{highlightStart}
              {highlightEnd && highlightEnd !== highlightStart ? `–L${highlightEnd}` : ""}
            </span>
          ) : null}
        </div>
      ) : null}
      <Editor
        height={height}
        theme={resolvedTheme === "light" ? "light" : "vs-dark"}
        language={lang}
        value={content}
        options={{
          readOnly: true,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          fontSize: 13,
          fontFamily: "var(--font-geist-mono), ui-monospace, monospace",
          lineNumbers: "on",
          wordWrap: "on",
          automaticLayout: true,
        }}
        onMount={(editor, monaco) => {
          if (highlightStart) {
            const end = highlightEnd || highlightStart;
            editor.deltaDecorations(
              [],
              [
                {
                  range: new monaco.Range(highlightStart, 1, end, 1),
                  options: {
                    isWholeLine: true,
                    className: "monaco-bt-highlight",
                    linesDecorationsClassName: "monaco-bt-gutter",
                  },
                },
              ],
            );
            editor.revealLineInCenter(highlightStart);
          }
        }}
      />
    </div>
  );
}
