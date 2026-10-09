import type { Config } from "tailwindcss";

export default {
  darkMode: "class",
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        canvas: "var(--bt-bg)",
        sidebar: "var(--bt-sidebar)",
        panel: "var(--bt-panel)",
        elevated: "var(--bt-elevated)",
        line: "var(--bt-border)",
        ink: "var(--bt-text)",
        mute: "var(--bt-mute)",
        accent: "var(--bt-accent)",
        "accent-soft": "var(--bt-accent-soft)",
        danger: "var(--bt-danger)",
        success: "var(--bt-success)",
        warn: "var(--bt-warn)",
      },
      fontFamily: {
        sans: ["var(--font-geist-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-geist-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      borderRadius: {
        panel: "10px",
        control: "7px",
      },
      boxShadow: {
        panel: "0 1px 0 rgba(255,255,255,0.03), 0 8px 24px rgba(0,0,0,0.22)",
        soft: "0 1px 2px rgba(0,0,0,0.18)",
      },
      transitionDuration: {
        ui: "150ms",
      },
      fontSize: {
        heading: ["28px", { lineHeight: "1.25", fontWeight: "600" }],
        body: ["15px", { lineHeight: "1.5" }],
        meta: ["12.5px", { lineHeight: "1.4" }],
      },
    },
  },
  plugins: [],
} satisfies Config;
