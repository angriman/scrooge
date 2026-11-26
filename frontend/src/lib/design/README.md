# The design system

Everything the app looks like is decided here, in three layers. A component names a role,
never a colour, so a theme or a scale changes without touching a component.

| Layer    | File                                    | What it holds                                                                                        |
| -------- | --------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Palette  | `tokens/palette.css`                    | Every colour, by hue and step (`--navy-850`, `--teal-400`). Read only by themes.                     |
| Themes   | `tokens/night.css`, `tokens/alpine.css` | The roles, each given a colour of the palette: surfaces, lines, ink, brand, signals, data, the font. |
| Tailwind | `tokens/theme.css`                      | The roles as Tailwind classes, and the scales every theme shares: type and corners.                  |

`data.ts` says which data colour each series wears (asset types, income groups), so a series
has one colour on every page.

## Roles

- **Surfaces**: `sunken` (wells, tracks), `page`, `panel`, `raised` (menus, tooltips, hover).
- **Lines**: `line`, `line-strong` (inputs).
- **Ink**: `ink`, `ink-secondary`, `ink-muted` (captions, axes).
- **Brand**: `brand`, `brand-hover`, `brand-soft`, `on-brand`, `focus`: what is chosen, links,
  the one main action.
- **Signals**: `gain`, `loss`, `caution`, each with a `-soft` tint.
- **Data**: `data-1` … `data-8`, `data-other`, `chart-grid`, `chart-axis`.

As classes: `bg-panel`, `text-ink-secondary`, `border-line`, `fill-data-3`, `text-gain`.

## Type and corners

Sizes are named by use: `text-caption`, `text-small`, `text-body`, `text-title`,
`text-heading`, `text-figure` (a stat), `text-hero` (the figure a page leads with). Figures are
tabular everywhere.

Corners grow with what they hold: `rounded-mark`, `rounded-control`, `rounded-panel`,
`rounded-lead`.

## Components

`src/lib/ui` holds the components every page is built from, each on a Bits UI primitive where
it needs one (keyboard, focus, positioning), styled with the roles above and nothing else.
Their variants are written with `tailwind-variants`; `cn` (`src/lib/ui/cn.ts`) joins classes
so that one passed in wins over the component's own.

Charts are drawn with LayerChart and take their colours from `data.ts`.

## Themes

`night.css` is the dark theme (default when no `data-theme` is set); `alpine.css` is the light
theme under `[data-theme='alpine']`. By default the app follows `prefers-color-scheme: light`,
and the user can override it under Settings → Appearance (stored in `localStorage`). Adding
another theme means adding a token file giving every role under `[data-theme='<name>']`,
importing it in `src/app.css`, and adding any needed palette steps.
