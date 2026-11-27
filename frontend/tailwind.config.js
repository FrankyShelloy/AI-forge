/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#fff1f2",
          100: "#ffe4e6",
          500: "#e11d48",
          600: "#c8102e",
          700: "#a10d25",
        },
      },
    },
  },
  plugins: [],
};
