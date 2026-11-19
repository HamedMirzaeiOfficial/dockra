/** @type {import('tailwindcss').Config} */

// Every color is a CSS variable holding an "R G B" triplet (see app/globals.css), so opacity modifiers
// such as bg-danger/10 keep working and the whole panel switches theme by swapping one set of variables.
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`;

module.exports = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: token("bg"),
        surface: token("surface"),
        card: token("card"),
        raised: token("raised"),
        line: token("line"),
        fg: token("fg"),
        "fg-2": token("fg-2"),
        muted: token("muted"),
        faint: token("faint"),
        field: token("field"),
        accent: token("accent"),
        brand: token("brand"),
        ink: token("ink"),
        success: token("success"),
        warning: token("warning"),
        danger: token("danger"),
        info: token("info"),
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["ui-monospace", "SF Mono", "JetBrains Mono", "Cascadia Code", "Menlo", "Consolas", "monospace"],
      },
      borderRadius: {
        DEFAULT: "6px",
      },
      boxShadow: {
        card: "var(--shadow-card)",
        pop: "var(--shadow-pop)",
        glow: "0 8px 24px -12px rgb(var(--accent) / 0.65)",
      },
    },
  },
  plugins: [],
};
