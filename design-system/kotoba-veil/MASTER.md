# Design System Master File

> **LOGIC:** When building a specific page, first check `design-system/pages/[page-name].md`.
> If that file exists, its rules **override** this Master file.
> If not, strictly follow the rules below.

---

**Project:** Kotoba Veil
**Generated:** 2026-08-01 07:59:45
**Category:** Micro SaaS

---

## Global Rules

### Color Palette

| Role | Light | Dark | CSS Variable |
|------|-------|------|--------------|
| Primary | `#0369A1` | `#5EA3C9` | `--color-primary` |
| Secondary | `#0EA5E9` | `#6EB0CF` | `--color-secondary` |
| CTA/Accent | `#22C55E` | `#66CC8C` | `--color-cta` |
| Background | `#F0F9FF` | `#111418` | `--color-background` |
| Surface | `#F8FCFF` | `#1A2029` | `--color-surface` |
| Text | `#0C4A6E` | `#E6E8EB` | `--color-text` |
| Border | `#CCD5DD` | `#3C3E42` | `--color-border` |

**Color Notes:** Security blue + protected green

### Theme Tokens (Light / Dark)

```css
:root {
  color-scheme: light;
  --color-primary: #0369A1;
  --color-secondary: #0EA5E9;
  --color-cta: #22C55E;
  --color-background: #F0F9FF;
  --color-surface: #F8FCFF;
  --color-text: #0C4A6E;
  --color-border: #CCD5DD;
  --color-on-primary: #FFFFFF;
  --color-on-cta: #FFFFFF;
  --color-primary-ring: rgba(3, 105, 161, 0.24);
}

[data-theme="dark"],
.dark {
  color-scheme: dark;
  --color-primary: #5EA3C9;
  --color-secondary: #6EB0CF;
  --color-cta: #66CC8C;
  --color-background: #111418;
  --color-surface: #1A2029;
  --color-text: #E6E8EB;
  --color-border: #3C3E42;
  --color-on-primary: #FFFFFF;
  --color-on-cta: #0F1115;
  --color-primary-ring: rgba(94, 163, 201, 0.28);
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --color-primary: #5EA3C9;
    --color-secondary: #6EB0CF;
    --color-cta: #66CC8C;
    --color-background: #111418;
    --color-surface: #1A2029;
    --color-text: #E6E8EB;
    --color-border: #3C3E42;
    --color-on-primary: #FFFFFF;
    --color-on-cta: #0F1115;
    --color-primary-ring: rgba(94, 163, 201, 0.28);
  }
}
```

### Typography

- **Heading Font:** Inter
- **Body Font:** Inter
- **Mood:** minimal, clean, swiss, functional, neutral, professional
- **Google Fonts:** [Inter + Inter](https://fonts.google.com/share?selection.family=Inter:wght@300;400;500;600;700)

**CSS Import:**
```css
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
```

### Spacing Variables

| Token | Value | Usage |
|-------|-------|-------|
| `--space-xs` | `4px` / `0.25rem` | Tight gaps |
| `--space-sm` | `8px` / `0.5rem` | Icon gaps, inline spacing |
| `--space-md` | `16px` / `1rem` | Standard padding |
| `--space-lg` | `24px` / `1.5rem` | Section padding |
| `--space-xl` | `32px` / `2rem` | Large gaps |
| `--space-2xl` | `48px` / `3rem` | Section margins |
| `--space-3xl` | `64px` / `4rem` | Hero padding |

### Shadow Depths

| Level | Value | Usage |
|-------|-------|-------|
| `--shadow-sm` | `0 1px 2px rgba(0,0,0,0.05)` | Subtle lift |
| `--shadow-md` | `0 4px 6px rgba(0,0,0,0.1)` | Cards, buttons |
| `--shadow-lg` | `0 10px 15px rgba(0,0,0,0.1)` | Modals, dropdowns |
| `--shadow-xl` | `0 20px 25px rgba(0,0,0,0.15)` | Hero images, featured cards |

---

## Component Specs

### Foundation

```css
body {
  background: var(--color-background);
  color: var(--color-text);
}

.surface {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
}
```

### Buttons

```css
/* Primary Button */
.btn-primary {
  background: var(--color-cta);
  color: var(--color-on-cta);
  border: 1px solid transparent;
  padding: 12px 24px;
  border-radius: 8px;
  font-weight: 600;
  transition: background-color 200ms ease, transform 200ms ease, opacity 200ms ease;
  cursor: pointer;
}

.btn-primary:hover {
  opacity: 0.9;
  transform: translateY(-1px);
}

/* Secondary Button */
.btn-secondary {
  background: transparent;
  color: var(--color-primary);
  border: 2px solid var(--color-primary);
  padding: 12px 24px;
  border-radius: 8px;
  font-weight: 600;
  transition: color 200ms ease, border-color 200ms ease, background-color 200ms ease;
  cursor: pointer;
}

.btn-secondary:hover {
  background: var(--color-primary);
  color: var(--color-on-primary);
}
```

### Cards

```css
.card {
  background: var(--color-surface);
  color: var(--color-text);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 24px;
  box-shadow: var(--shadow-md);
  transition: border-color 200ms ease, box-shadow 200ms ease, transform 200ms ease;
  cursor: pointer;
}

.card:hover {
  border-color: var(--color-primary);
  box-shadow: var(--shadow-lg);
  transform: translateY(-2px);
}
```

### Inputs

```css
.input {
  background: var(--color-surface);
  color: var(--color-text);
  padding: 12px 16px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  font-size: 16px;
  transition: border-color 200ms ease, box-shadow 200ms ease;
}

.input::placeholder {
  color: var(--color-secondary);
}

.input:focus {
  border-color: var(--color-primary);
  outline: none;
  box-shadow: 0 0 0 3px var(--color-primary-ring);
}
```

### Modals

```css
.modal-overlay {
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(4px);
}

.modal {
  background: var(--color-surface);
  color: var(--color-text);
  border: 1px solid var(--color-border);
  border-radius: 16px;
  padding: 32px;
  box-shadow: var(--shadow-xl);
  max-width: 500px;
  width: 90%;
}
```

---

## Style Guidelines

**Style:** Vibrant & Block-based

**Keywords:** Bold, energetic, playful, block layout, geometric shapes, high color contrast, duotone, modern, energetic

**Best For:** Startups, creative agencies, gaming, social media, youth-focused, entertainment, consumer

**Key Effects:** Large sections (48px+ gaps), animated patterns, bold hover (color shift), scroll-snap, large type (32px+), 200-300ms

### Page Pattern

**Pattern Name:** Minimal Single Column

- **Conversion Strategy:** Single CTA focus. Large typography. Lots of whitespace. No nav clutter. Mobile-first.
- **CTA Placement:** Center, large CTA button
- **Section Order:** 1. Hero headline, 2. Short description, 3. Benefit bullets (3 max), 4. CTA, 5. Footer

---

## Anti-Patterns (Do NOT Use)

- ❌ Complex onboarding flow
- ❌ Cluttered layout

### Additional Forbidden Patterns

- ❌ **Emojis as icons** — Use SVG icons (Heroicons, Lucide, Simple Icons)
- ❌ **Missing cursor:pointer** — All clickable elements must have cursor:pointer
- ❌ **Layout-shifting hovers** — Avoid scale transforms that shift layout
- ❌ **Low contrast text** — Maintain 4.5:1 minimum contrast ratio
- ❌ **Instant state changes** — Always use transitions (150-300ms)
- ❌ **Invisible focus states** — Focus states must be visible for a11y

---

## Pre-Delivery Checklist

Before delivering any UI code, verify:

- [ ] No emojis used as icons (use SVG instead)
- [ ] All icons from consistent icon set (Heroicons/Lucide)
- [ ] `cursor-pointer` on all clickable elements
- [ ] Hover states with smooth transitions (150-300ms)
- [ ] Light mode: text contrast 4.5:1 minimum
- [ ] Dark mode: token switch verified (`.dark`, `[data-theme="dark"]`, `prefers-color-scheme`)
- [ ] Focus states visible for keyboard navigation
- [ ] `prefers-reduced-motion` respected
- [ ] Responsive: 375px, 768px, 1024px, 1440px
- [ ] No content hidden behind fixed navbars
- [ ] No horizontal scroll on mobile
