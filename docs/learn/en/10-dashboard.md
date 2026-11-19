# Chapter 10 — The dashboard: design, tokens and themes

**English** · [فارسی](../10-dashboard.md)

## Goal of this chapter

See that a "professional" UI is a **system** more than colors and a font: tokens, a few base components, loading, empty and error states, and accessibility. And see how the whole look of the panel changed without rewriting the pages.

## The problem: scattered colors

The panel's first version had colors directly in its classes: `text-zinc-300`, `bg-white/5`, `bg-[#1b1b1c]`, `text-rose-200`. About 400 places. The result:

- A light theme was practically impossible (`text-zinc-300` is unreadable on white).
- Changing the main color meant search and replace across dozens of files.
- "Red" was `rose-200` in one place and `rose-300` in another.

## The solution: semantic tokens

Instead of "which color?", ask "what **role** does this color have?". `web/app/globals.css`:

```css
:root, :root[data-theme="dark"] { --fg: 236 237 243; --muted: 138 143 165; --danger: 244 114 128; ... }
:root[data-theme="light"]        { --fg: 17 19 30;    --muted: 94 99 122;   --danger: 200 44 64;   ... }
```

And `web/tailwind.config.js`:

```js
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`;
colors: { fg: token("fg"), muted: token("muted"), danger: token("danger"), ... }
```

Why `236 237 243` and not `#ecedf3`? So that Tailwind can add opacity: `bg-danger/10` becomes `rgb(var(--danger) / 0.1)`. With hex that was impossible.

Then a small script (`tokens.py`, used during development) mapped every raw class to a token: `zinc-300 → fg-2`, `rose-* → danger`, `white/5 → fg/5`. The pages were barely touched, and the whole panel got two themes in one go. That is the power of the right layer of abstraction.

## Two colors for one "indigo"

`accent` and `brand` are both indigo, but separate. Why? The primary button has an indigo background with white text; the shade that is readable behind white text (darker) is not readable as **text** on the panel's dark background. So `accent` is for fills and `brand` (lighter in the dark theme, darker in the light theme) is for text and icons. Large design systems call this an "on-color" or a "foreground/background pair".

## A theme without a flash

If you apply the theme in a React `useEffect`, the page first renders with the default theme and then switches (FOUC). The fix: a tiny script **inside `<head>`** runs before any rendering (`web/lib/theme-script.ts`), reads the saved choice or the OS preference, and sets `data-theme` on `<html>`.

A real bug while building this: at first the `THEME_SCRIPT` constant and the `useTheme` hook lived in one file. `layout.tsx` is a **Server Component**, and importing a file that uses `useState` into it broke the build. The fix: separate the constant (no React) in `theme-script.ts` from the client hook in `theme.ts`. The lesson: in the App Router, design the server/client boundary at the **file** level.

## A few components, everywhere

`web/components/ui.tsx` has about fifteen components (`Button`, `Card`, `FormSection`, `StatusBadge`, `Empty`, …). The rule: pages do not create colors, they compose components. Because `StatusBadge` maps every API status to four tones (`live`, `busy`, `bad`, `idle`), "building" looks exactly the same in the app list, the app detail and the history.

## States matter more than the happy path

Users spend most of their time in "wait", "nothing here yet" and "something failed":

- **Loading**: a skeleton shaped like the final content, not a spinner in the middle of the page. `lib/api.ts` has an in-memory cache so going back to the previous page is instant and refreshes in the background.
- **Empty**: `Empty` explains what this list is for and suggests the next step.
- **Error**: the API's own `detail` message is shown verbatim, so the API must give understandable messages ("Saved, but could not be applied to the container: …").
- **Dangerous actions**: `confirmAction` focuses the "Cancel" button so an accidental Enter deletes nothing.

## Accessibility from the start

- Every interactive element has `:focus-visible` (from the accent token).
- `Tabs` has the `tablist/tab` roles, `Choice` `radiogroup/radio`, `Toggle` `switch` and `Meter` `meter`.
- Icons are `aria-hidden`; icon-only buttons have an `aria-label`.
- Status is never conveyed by color alone (badges carry text).
- `prefers-reduced-motion` turns animations off.
- A "Skip to content" link for keyboard users.

## From right-to-left to left-to-right (and back)

The first version was Persian and RTL. Converting it took more than changing `dir`: every physical class (`right-3`, `pr-9`, `text-right`) and every directional icon (the "back" and "open details" chevrons) had to be checked one by one. The lesson for the next project: use **logical** classes (`ps-`, `pe-`, `start-`, `end-`) from the start so both directions work for free. Dockra later did exactly that when it became bilingual again; chapter 13 tells that story.

## Exercises

1. Add a new `--highlight` token and change `StatusBadge` so the `deploying` status uses it. Try it in both themes.
2. Compute the contrast ratio of `--muted` on `--bg` in both themes with a tool (WebAIM, for example). What is the WCAG AA minimum for normal text?
3. Follow the `system` mode in `useTheme`: what happens if the user changes the OS theme in the middle of a session? Which line handles it?
4. Run `node e2e/screenshots.cjs` (chapter 12) and add a console error on purpose. What does the script do?
