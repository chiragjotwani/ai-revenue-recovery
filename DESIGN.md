---
name: AI Revenue Recovery Console
description: A financial data terminal for monitoring and recovering at-risk revenue, with an AI advisory clearly separated from the deterministic system of record.
colors:
  ink-ground: "#0a0d12"
  panel-raised: "#10141c"
  strip-inset: "#0d1016"
  hairline: "#1f2733"
  hairline-strong: "#2b3648"
  text-primary: "#e7eaf0"
  text-muted: "#9aa4b8"
  text-dim: "#7d879c"
  status-good: "#34d399"
  status-warn: "#fbbf24"
  status-critical: "#f87171"
  signal-cyan: "#22d3ee"
typography:
  display:
    fontFamily: "Space Grotesk, ui-sans-serif, system-ui"
    fontWeight: 600
    letterSpacing: "normal"
  body:
    fontFamily: "Space Grotesk, ui-sans-serif, system-ui"
    fontWeight: 400
  mono:
    fontFamily: "IBM Plex Mono, ui-monospace, monospace"
    fontWeight: 400
rounded:
  none: "0px"
spacing:
  sm: "8px"
  md: "16px"
  lg: "24px"
components:
  status-tag:
    backgroundColor: "{colors.hairline}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.none}"
    padding: "2px 8px"
  panel:
    backgroundColor: "{colors.panel-raised}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.none}"
---

# Design System: AI Revenue Recovery Console

## Overview

**Creative North Star: "The Trading Floor Terminal"**

This is a financial data terminal — Bloomberg/Reuters-class monitoring
software crossed with a mission-control status board — rebuilt for a
platform whose entire pitch is that an AI advises but a deterministic
system of record decides. The console is read in short, repeated glances
under mixed office light by someone triaging financial risk, not browsed
once like a document: it commits to a near-black ink ground, dense
tabular monospace data, and color spent only as status signal, never as
decoration. Nothing here uses rounded pill badges, drop shadows, or
gradient hero sections — those belong to a marketing site, not an
operations console.

The one deliberate departure from a literal terminal pastiche: a single
reserved "signal" color (cyan) marks every place the reasoning model is
present in the UI — separate from the good/warn/critical severity
vocabulary. This is not a decorative fourth accent; it is the visual
encoding of the product's central architectural claim (ADR-003): the AI
is a distinct, watched, bounded participant, never the same thing as
deterministic system state.

**Key Characteristics:**
- Dark-only by commitment, not by default — a monitoring console read in
  short repeated glances, never a document read start to finish.
- Every numeral renders with tabular figures; columns never jitter as
  data updates.
- Status is always dot + text label, never a color-only signal.
- One reserved cyan role marks AI presence specifically, distinct from
  severity color.

## Colors

Color is spent almost entirely as status signal against a near-black
ground; there is no decorative palette beyond it.

### Primary
- **Signal Cyan** (`#22d3ee`): the one non-severity accent. Marks every
  place the reasoning model is present — the "AI Advisory" panel, live/
  in-progress state tags (`diagnosing`), the global ticker's live
  indicator, primary interactive links and focus rings. Never used for a
  plain "important" emphasis unrelated to the AI or liveness.

### Neutral
- **Ink Ground** (`#0a0d12`): page background.
- **Panel Raised** (`#10141c`): panel/card background, one step lighter
  than the page so hairline borders read against both.
- **Strip Inset** (`#0d1016`): the sunken ticker-strip background (header
  live ticker, page-level summary strips).
- **Hairline** (`#1f2733`): default borders between panels, table rows,
  the score-band track.
- **Hairline Strong** (`#2b3648`): scrollbar thumb, hover border state.
- **Text Primary** (`#e7eaf0`): body and data text.
- **Text Muted** (`#9aa4b8`): secondary descriptive text, table cell
  values that aren't the primary datum.
- **Text Dim** (`#7d879c`): labels only (panel titles, table headers,
  ticker labels) — deliberately tuned to ≥4.5:1 against the ink ground;
  the visually "quietest" text token is still full body-text contrast,
  never a lighter gray for decoration.

### Status (reserved; never reused for anything but state)
- **Good** (`#34d399`): low risk, `recovered` case state.
- **Warn** (`#fbbf24`): medium risk, any in-progress case state other
  than `diagnosing`.
- **Critical** (`#f87171`): high risk, `abandoned`/`failed` case state,
  the unreachable-backend banner.

### Named Rules
**The Dot-Plus-Label Rule.** Every status indicator (`StatusTag`) pairs a
solid dot with a visible text label. Color alone never carries a state;
removing color must leave the state legible from the label.

**The Signal-Is-Not-Severity Rule.** Cyan never appears in the
good/warn/critical severity vocabulary and severity colors never appear
where cyan belongs. The two systems answer different questions ("how bad
is this" vs. "is the AI here") and must never be visually interchangeable.

## Typography

**Display/Body Font:** Space Grotesk (with ui-sans-serif, system-ui
fallback)
**Label/Mono Font:** IBM Plex Mono (with ui-monospace, monospace
fallback)

**Character:** A technical grotesk paired with a true monospace — Space
Grotesk carries headings, nav, and prose without leaning on a system
default; IBM Plex Mono is reserved for anything that is actually data or
measurement (amounts, IDs, timestamps, scores, status labels), never used
as a costume for "looking technical."

### Hierarchy
- **Title** (500–600 weight, `text-lg`–`text-2xl`, Space Grotesk): page
  and case titles.
- **Body** (400 weight, `text-sm`, Space Grotesk): descriptive prose
  under section headings.
- **Label** (400–500 weight, `text-[11px]`, IBM Plex Mono, uppercase,
  wide tracking): panel titles, table headers, ticker labels, nav items,
  status tags.
- **Data** (400 weight, IBM Plex Mono, tabular figures): table cell
  values, amounts, timestamps, IDs.

### Named Rules
**The Data-Is-Mono Rule.** Monospace is used exclusively for real data
and measurement (figures, IDs, timestamps, scores) and their labels —
never applied to prose or headings merely to signal "technical."

## Layout

A persistent app shell (`layout.tsx`) wraps every route: a top nav bar
(wordmark + `OVERVIEW` / `RISK` / `RECOVERY` tabs) and, immediately below
it, a full-width live ticker strip pulling `GET /risk/summary` on every
request — the one element present on literally every page, reinforcing
that the console is always live. Page content sits in a single
`max-w-6xl` centered column. Within a page, content is either a
dense-row summary strip (`bg-term-bg-inset`, inline `label value` pairs)
or a `Panel` (see Components) containing a table or a list. There is no
card-grid page structure anywhere in the system — tables and single
bordered panels are the only content containers.

## Elevation & Depth

Flat by rule — no shadows anywhere in the system. Depth is conveyed by
one hairline border (`#1f2733`) and a one-step background lift
(`panel-raised` over `ink-ground`). A pulsing box-shadow glow
(`term-pulse` keyframe) exists solely on live/in-progress indicator dots,
never as ambient decoration on a static element.

### Named Rules
**The Flat-By-Rule Rule.** No `box-shadow` is used for elevation anywhere
in the system. The only shadow in the codebase is the live-pulse glow on
an active status dot, and it is a state signal, not a depth cue.

## Shapes

Square corners everywhere (`rounded: none`) — status tags, panels, and
buttons are all rectangular. This is a deliberate departure from the
rest of the web: a terminal's chrome is drawn from cells and hairlines,
never rounded rectangles, and softening a single corner would contradict
the world.

## Components

### Status Tag
- **Shape:** rectangular, 1px border, `2px 8px` padding.
- **Style:** border + background + text all drawn from the same role
  color at low opacity (background) and full opacity (border/text/dot);
  role is one of good / warn / critical / signal / neutral.
- **Live state:** adds a pulsing dot (`term-pulse`) rather than changing
  color — liveness is a motion signal, not a color signal.

### Score Band
- **Style:** a 64px-wide, 6px-tall hairline-colored track with a solid
  fill bar sized to the 0–1 value, plus the exact numeric value in mono
  text beside it. Used for both Phase 2 risk scores and Phase 4 diagnosis
  confidence — the same instrument reads consistently everywhere a score
  appears in the product.
- **Rule:** never render only a bucket label (low/medium/high) without
  the band; the band is what shows *where in the bucket* a value sits.

### Panel
- **Shape:** rectangular, 1px hairline border, no shadow.
- **Header:** a border-bottomed title bar with an uppercase mono label
  and an optional right-aligned action slot (e.g. the "non-authoritative"
  marker on the AI Advisory panel).
- **Background:** `panel-raised`, one step lighter than the page.

### Buttons / Links
- **Style:** no filled buttons anywhere in the system. A primary action
  is a bordered rectangular tag (`border-term-border`, hover
  `border-term-signal` + `text-term-signal`) or a plain mono text link
  with an arrow glyph (`&rarr;`).
- **Hover / Focus:** border and text shift to signal-cyan on hover;
  `:focus-visible` gets a 2px cyan outline globally (see globals.css),
  never a browser-default blue ring.

### Navigation
- Top bar, mono uppercase tabs. The active route gets a signal-cyan
  border and text (`aria-current="page"`); inactive tabs are muted with a
  hairline hover border. A client component (`NavTabs`, reading
  `usePathname()`) is the one intentional client-side island in an
  otherwise all-server-component app, scoped narrowly to this behavior.

### Global Ticker Strip (signature component)
A full-width, sunken (`strip-inset`) band directly under the nav, present
on every route, showing a pulsing "LIVE" signal-cyan indicator plus
`label value` mono readouts pulled from the live risk summary. This is
the one element that makes the "always-live console" thesis literal
rather than aspirational — no route renders without it.

## Do's and Don'ts

### Do:
- **Do** pair every status color with a visible text label (The
  Dot-Plus-Label Rule).
- **Do** use IBM Plex Mono only for real data/measurement, never for
  prose (The Data-Is-Mono Rule).
- **Do** render scores as a calibrated band, not a flattened bucket
  label.
- **Do** use signal-cyan exclusively for "the AI is/was here," never as
  a general-purpose accent.
- **Do** keep every corner square; a single rounded element breaks the
  terminal world.

### Don't:
- **Don't** add a colored `border-left`/`border-right` accent to a card,
  panel, or list item — refused explicitly during this build (see
  known-issues / build history) in favor of the status-tag/score-band
  vocabulary already carrying that signal.
- **Don't** introduce a card-grid page layout (icon + heading + text).
  Tables and single bordered panels are the only content containers this
  system uses.
- **Don't** add a drop shadow for elevation; use the one-step background
  lift plus hairline border instead.
- **Don't** reuse signal-cyan for a severity meaning, or a severity color
  for an AI-presence meaning.

---

**Not canonized:** nothing observed in this build was excluded as a
craft-floor violation being legitimized — the one gap found while
documenting (missing nav active-state) was fixed during this same pass
rather than written down as accepted behavior.
