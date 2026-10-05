/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#10110f",
        panel: "#171814",
        line: "#2c2d27",
        paper: "#f4f1e8",
        mute: "#9a958a",
        accent: "#d6ff4a",
        danger: "#ff5d45",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "sans-serif"],
        serif: ["var(--font-serif)", "serif"],
      },
    },
  },
  plugins: [],
};
