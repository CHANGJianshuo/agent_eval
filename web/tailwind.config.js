/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Linear / Vercel 风格灰阶
        border: 'hsl(0 0% 90%)',
        background: 'hsl(0 0% 100%)',
        foreground: 'hsl(240 10% 4%)',
        muted: { DEFAULT: 'hsl(240 5% 96%)', foreground: 'hsl(240 4% 46%)' },
        primary: { DEFAULT: 'hsl(240 6% 10%)', foreground: 'hsl(0 0% 100%)' },
        accent: { DEFAULT: 'hsl(240 5% 96%)', foreground: 'hsl(240 6% 10%)' },
        destructive: { DEFAULT: 'hsl(0 84% 60%)', foreground: 'hsl(0 0% 100%)' },
        success: { DEFAULT: 'hsl(142 71% 45%)', foreground: 'hsl(0 0% 100%)' },
        warning: { DEFAULT: 'hsl(38 92% 50%)', foreground: 'hsl(0 0% 100%)' },
      },
      borderRadius: {
        lg: '8px',
        md: '6px',
        sm: '4px',
      },
      fontFamily: {
        sans: [
          'Inter', 'ui-sans-serif', 'system-ui', '-apple-system',
          'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC',
          'sans-serif',
        ],
        mono: [
          'JetBrains Mono', 'Menlo', 'Monaco', 'Consolas', 'monospace',
        ],
      },
    },
  },
  plugins: [],
}
