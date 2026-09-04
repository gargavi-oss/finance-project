/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          900: "#0a0a0a",
          800: "#111111",
          700: "#171717",
          600: "#1f1f1f",
          500: "#2a2a2a",
          400: "#3a3a3a",
          300: "#525252",
          200: "#a3a3a3",
          100: "#d4d4d4",
          50: "#f5f5f5",
        },
        brand: {
          DEFAULT: "#c5341c",
          hover: "#d54a2f",
          soft: "#3a1a14",
        },
        accent: {
          amber: "#e07a3c",
          yellow: "#e3b34a",
        },
        ok: "#2e9c5a",
        warn: "#e3b34a",
        danger: "#e2533c",
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(197,52,28,0.35), 0 6px 24px rgba(197,52,28,0.18)",
      },
    },
  },
  plugins: [],
};