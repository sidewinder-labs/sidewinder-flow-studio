/** @type {import('tailwindcss').Config} */
import plugin from 'tailwindcss/plugin'
import defaults from 'tailwindcss/colors'

/* Themes. Every colour is a CSS variable, so one class works in both themes:
 * <html data-theme="light"> swaps the values (src/hooks/useTheme.ts sets it).
 *
 * - The app's own tokens (canvas, surface, accent, muted, …) get a value per
 *   theme below.
 * - Tailwind's grey and hue scales flip in light mode, 100 ↔ 900, 400 ↔ 600:
 *   text-slate-100 is dark text on a light page, bg-red-900/40 a pale red,
 *   text-emerald-400 a green that reads on white. Shade 500 stays.
 */

const SHADES = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950]
const FLIP = Object.fromEntries(SHADES.map((s, i) => [s, SHADES[SHADES.length - 1 - i]]))
const HUES = ['slate', 'gray', 'zinc', 'neutral', 'stone', 'red', 'orange', 'amber', 'yellow', 'lime',
  'green', 'emerald', 'teal', 'cyan', 'sky', 'blue', 'indigo', 'violet', 'purple', 'fuchsia', 'pink', 'rose']

// The app's tokens: [dark, light]. Light is the "Daylight" palette.
const TOKENS = {
  'canvas-bg':      ['#090c14', '#f3f6fa'],
  'canvas-grid':    ['#11172a', '#97a6bc'],
  'canvas-border':  ['#1e2540', '#d5dde8'],
  'surface':        ['#111827', '#ffffff'],
  'surface-raised': ['#1a2235', '#eaeff6'],
  'surface-overlay':['#212d45', '#dde4ee'],
  'accent':         ['#38bdf8', '#0369a1'],
  'accent-dim':     ['#0ea5e9', '#075985'],
  'muted':          ['#7a8aa1', '#556275'],
  'subtle':         ['#2d3a52', '#8592a6'],
  'node-trigger':          ['#0d2a3a', '#e0f2fe'],
  'node-triggerBorder':    ['#0ea5e9', '#0369a1'],
  'node-processor':        ['#0d1f1a', '#dcfce7'],
  'node-processorBorder':  ['#34d399', '#047857'],
  'node-stop':             ['#2a1a0d', '#ffedd5'],
  'node-stopBorder':       ['#fb923c', '#c2410c'],
  'node-tableOut':         ['#1a1a2e', '#e0e7ff'],
  'node-tableOutBorder':   ['#818cf8', '#4338ca'],
  'node-chartOut':         ['#1f0d2a', '#fae8ff'],
  'node-chartOutBorder':   ['#e879f9', '#a21caf'],
  'status-ok':      ['#34d399', '#047857'],
  'status-error':   ['#f87171', '#b91c1c'],
  'status-running': ['#fbbf24', '#b45309'],
  'status-stopped': ['#fb923c', '#c2410c'],
}

const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)).join(' ')
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`

const scales = Object.fromEntries(HUES.map((h) => [h, Object.fromEntries(SHADES.map((s) => [s, v(`c-${h}-${s}`)]))]))
const token = (name) => v(`t-${name}`)

const themeVars = plugin(({ addBase }) => {
  const dark = { 'color-scheme': 'dark' }, light = { 'color-scheme': 'light' }
  for (const h of HUES) for (const s of SHADES) {
    dark[`--c-${h}-${s}`] = rgb(defaults[h][s])
    light[`--c-${h}-${s}`] = rgb(defaults[h][FLIP[s]])
  }
  for (const [name, [d, l]] of Object.entries(TOKENS)) {
    dark[`--t-${name}`] = rgb(d)
    light[`--t-${name}`] = rgb(l)
  }
  addBase({ ':root': dark, ':root[data-theme="light"]': light })
})

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    colors: {
      transparent: 'transparent', current: 'currentColor', inherit: 'inherit',
      white: '#ffffff', black: '#000000',
      ...scales,
    },
    extend: {
      colors: {
        canvas:  { bg: token('canvas-bg'), grid: token('canvas-grid'), border: token('canvas-border') },
        surface: { DEFAULT: token('surface'), raised: token('surface-raised'), overlay: token('surface-overlay') },
        accent:  { DEFAULT: token('accent'), dim: token('accent-dim'), glow: 'rgb(var(--t-accent) / 0.13)' },
        node: {
          trigger: token('node-trigger'),     triggerBorder: token('node-triggerBorder'),
          processor: token('node-processor'), processorBorder: token('node-processorBorder'),
          stop: token('node-stop'),           stopBorder: token('node-stopBorder'),
          tableOut: token('node-tableOut'),   tableOutBorder: token('node-tableOutBorder'),
          chartOut: token('node-chartOut'),   chartOutBorder: token('node-chartOutBorder'),
        },
        status: {
          ok: token('status-ok'), error: token('status-error'),
          running: token('status-running'), stopped: token('status-stopped'),
        },
        muted:  token('muted'),
        subtle: token('subtle'),
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      animation: {
        'pulse-row': 'pulseRow 0.6s ease-out',
        'flow-dot':  'flowDot 1.2s linear infinite',
      },
      keyframes: {
        pulseRow: {
          '0%':   { backgroundColor: 'rgb(var(--t-accent) / 0.3)' },
          '100%': { backgroundColor: 'transparent' },
        },
        flowDot: {
          '0%':   { strokeDashoffset: '100' },
          '100%': { strokeDashoffset: '0' },
        },
      },
    },
  },
  plugins: [themeVars],
}
