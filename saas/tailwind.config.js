/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          900: "#043434",
          700: "#0A4A46",
        },
        mint: {
          500: "#83F1B1",
          600: "#4FD494",
        },
        ink: {
          DEFAULT: "#0E2622",
          subtle: "#4A5F5A",
          muted: "#829691",
        },
        success: "#0F8A4D",
        coral: "#DC6E41",
        amber: "#C7911E",
        "price-high": "#B4462F",
        sector: "var(--sector-surface)",
      },
      fontFamily: {
        ui: ["Huwiya", "Noto Sans Arabic", "Geeza Pro", "Tahoma", "sans-serif"],
        mark: ["Futura", "Century Gothic", "Avenir Next", "sans-serif"],
      },
      boxShadow: {
        card: "0 10px 30px rgba(4, 52, 52, 0.06)",
        lift: "0 16px 40px rgba(4, 52, 52, 0.12)",
      },
      borderRadius: {
        card: "22px",
      },
    },
  },
  plugins: [],
};
