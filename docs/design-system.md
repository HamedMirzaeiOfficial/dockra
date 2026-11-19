# Design system

**English** · [فارسی](fa/design-system.md)

The Dockra dashboard aims for a calm, premium developer-tool feel: dark first, an excellent light mode, a restrained indigo accent, dense but readable information, and no decoration that does not carry meaning.

## Brand

- **Name:** Dockra — **Tagline:** *From code to cloud.*
- **Mark:** a rounded square with an indigo-to-blue gradient holding a "D" whose counter contains an upward chevron (code going up). Source: [`assets/logo.svg`](assets/logo.svg), React: `web/components/logo.tsx` (`LogoMark`, `Wordmark`).
- **Voice:** short, concrete, sentence case. Say what happened and what to do next ("Saved, but could not be applied to the container: …"). No exclamation marks, no blame.

## Tokens

All colors are CSS variables holding `R G B` triplets in `web/app/globals.css`, exposed to Tailwind in `web/tailwind.config.js` as `rgb(var(--token) / <alpha-value>)`. Opacity modifiers therefore work everywhere (`bg-danger/10`, `ring-fg/10`). **Components never use raw palette colors** (`zinc-300`, `#fff`); they use these roles:

| Token | Use | Dark | Light |
| --- | --- | --- | --- |
| `bg` | page background | `#090a0e` | `#f6f7fa` |
| `surface` | sidebars, top bar | `#0f1016` | `#ffffff` |
| `card` | cards, dialogs | `#13141b` | `#ffffff` |
| `raised` | hover/selected backgrounds, footers | `#1a1b24` | `#f1f2f7` |
| `line` | borders, dividers | `#242633` | `#e2e4ed` |
| `field` | inputs | `#0c0d12` | `#ffffff` |
| `fg` | primary text | `#ecedf3` | `#11131e` |
| `fg-2` | secondary text, values | `#c0c3d1` | `#373c52` |
| `muted` | labels, hints | `#8a8fa5` | `#5e637a` |
| `faint` | placeholders, disabled | `#60657a` | `#9196aa` |
| `accent` | fills: primary buttons, active indicators | `#6366f1` | `#4f46e5` |
| `brand` | accent-colored **text** and icons | `#a5a8ff` | `#4338ca` |
| `ink` | text on `accent` | `#ffffff` | `#ffffff` |
| `success` / `warning` / `danger` / `info` | status | emerald / amber / rose / blue | darker variants for contrast |

`accent` and `brand` are separate on purpose: a fill that holds white text and a text color readable on the background cannot be the same indigo in a dark theme.

Elevation uses two shadows, also tokens: `shadow-card` (cards) and `shadow-pop` (dialogs, drawers, toasts). `shadow-glow` is reserved for the primary call to action.

## Typography and spacing

- **Font:** Inter (variable, self-hosted from `@fontsource-variable/inter` through `next/font/local` so builds never contact a font CDN), falling back to the system UI font. Monospace: the platform's UI monospace (SF Mono, Cascadia, JetBrains Mono, Menlo, Consolas).
- **Scale:** page title `text-2xl font-semibold tracking-tight`, section title `text-[15px] font-semibold`, body `text-sm`, metadata `text-xs`, badges `text-[11px]`.
- **Numbers** use `tabular-nums` wherever they update live.
- **Spacing:** a 4 px grid; cards use `p-5`, rows `px-5 py-3.5`, sections are separated by `gap-6` / `mb-8`.
- **Radii:** controls `rounded-lg` (8 px), cards `rounded-xl` (12 px), auth card `rounded-2xl`, pills `rounded-full`.

## Layout

- **Sidebar** (desktop, 240 px): wordmark, organization, grouped navigation (Overview · Resources · Platform) with live counts, theme switch, language switch, user and sign-out. On screens below `lg` it becomes a drawer behind a top bar (Escape and backdrop close it).
- **Detail pages** (application, database) add a second column with section navigation; on small screens it becomes a horizontal scroller. The active section is kept in the URL hash so links and reloads land on the same tab.
- Content is capped at `max-w-6xl`.

## Components (`web/components/ui.tsx`)

| Component | Notes |
| --- | --- |
| `Button`, `ButtonLink` | tones `primary`, `ghost`, `soft`, `danger`; sizes `sm`, `md`; optional icon |
| `Card`, `CardHeader`, `FormSection` | `FormSection` is the standard settings block: description on the left, fields on the right, actions in a footer |
| `Field`, `inputClass`, `selectClass` | label, control and hint; focus ring comes from the token |
| `StatusBadge` | maps API statuses to `live` / `busy` / `bad` / `idle`; busy pulses |
| `Stat`, `Meter` | KPI tile and percentage bar (`role="meter"`); bars turn warning at 65 % and danger at 85 % |
| `Empty` | dotted backdrop, icon, title, one sentence, optional action |
| `Tabs`, `Choice`, `Toggle` | `tablist`/`tab`, `radiogroup`/`radio`, `switch` roles with `aria-*` state |
| `Alert` | danger / warning / info |
| `CopyText`, `Code` | copy-to-clipboard value; inline monospace token |
| `Loader`, `Skeleton`, `Dots` (`loader.tsx`) | skeletons for first paint, dots for in-button progress |
| `notify`, `confirmAction` (`notify.tsx`) | toasts and a confirm dialog; destructive confirms focus *Cancel* |

## States

- **Loading:** skeletons that match the final layout; cached data is shown instantly on back-navigation and refreshed in the background.
- **Empty:** explain what the list is for and offer the next action ("Create your first application").
- **Error:** the API's `detail` is shown verbatim; network failures say so and suggest retrying.
- **Live data** refreshes at a stated interval ("Refreshes every 5 seconds").

## Accessibility

- Visible `:focus-visible` outline from the accent token on every interactive element; a "Skip to content" link.
- Icons are `aria-hidden`; icon-only buttons carry `aria-label`.
- Contrast: body text ≥ 7:1, secondary text ≥ 4.5:1 in both themes; status is never conveyed by color alone (badges carry a label).
- `prefers-reduced-motion` disables entrance animations and replaces bouncing dots with a fade.

## Languages and right-to-left

The panel ships in English and Persian. `lib/i18n-script.ts` is an inline `<head>` script, like the theme script, that sets `<html lang dir>` from the saved choice or the browser language before first paint. `components/lang.tsx` holds `<LangGate>`, which renders the UI only once the language is known (so there is no hydration mismatch and no flash of English), and the `LangSwitch` in the sidebar and on the sign-in page.

- Layout uses logical properties only (`ms-`/`me-`, `ps-`/`pe-`, `start-`/`end-`, `text-start`/`text-end`), so the whole UI mirrors in Persian.
- Directional icons mirror with `rtl:` variants: back/forward chevrons, the switch thumb, the select arrow and the toast progress bar.
- Technical content stays left-to-right inside a right-to-left page: code, hostnames, paths, env keys, logs, terminals and charts carry `dir="ltr"`.
- Persian text uses **Vazirmatn** (self-hosted, OFL). It is listed after Inter in the font stack, so Latin text keeps Inter and Persian characters fall back to Vazirmatn per glyph.
- Numbers use Persian digits and dates use the Solar Hijri calendar through `Intl` (`fa-IR`).

## Themes

`lib/theme-script.ts` contains a tiny inline script placed in `<head>` that applies the stored choice (`system`, `dark`, `light`) before first paint, so there is no flash. `lib/theme.ts` exposes `useTheme()` for the sidebar switch and follows OS changes while the choice is `system`. Terminals and log viewers stay dark in both themes on purpose.
