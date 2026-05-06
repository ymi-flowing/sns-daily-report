# SNS Design System & Style Guide

## Brand Colors

### Primary Palette
| Color | Hex | Usage |
|-------|-----|-------|
| **Deep Blue** | `#1e40af` | Headers, primary actions, branding |
| **Bright Blue** | `#3b82f6` | Links, accents, secondary actions |
| **Emerald** | `#10b981` | Success states, positive metrics, occupied, renovated |
| **Amber** | `#f59e0b` | Warnings, NTV status, partial renovated |
| **Red** | `#ef4444` | Errors, evictions, critical alerts |
| **Pink** | `#db2777` | Off the market units |
| **Slate** | `#64748b` | Neutral text, borders, backgrounds, classic units |

### Status Colors
| Status | Light Mode | Dark Mode |
|--------|------------|-----------|
| Occupied | `#10b981` | `#34d399` |
| Vacant | `#3b82f6` | `#60a5fa` |
| Vacant Leased | `#8b5cf6` | `#a78bfa` |
| NTV | `#f59e0b` | `#fbbf24` |
| NTV Leased | `#f97316` | `#fb923c` |
| Under Eviction | `#ef4444` | `#f87171` |
| Off the Market | `#db2777` | `#f472b6` |

### Renovation Status Colors
| Status | Light Mode | Dark Mode |
|--------|------------|-----------|
| Renovated | `#10b981` | `#34d399` |
| Partial Renovated | `#f59e0b` | `#fbbf24` |
| Classic | `#64748b` | `#94a3b8` |

---

## Typography

### Font Stack
```css
font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
```

### Sizes & Weights
| Element | Size | Weight | Color (Light) | Color (Dark) |
|---------|------|--------|---------------|--------------|
| Page Title | `text-2xl` (1.5rem) | Bold (700) | `slate-900` | `white` |
| Section Title | `text-lg` (1.125rem) | Bold (700) | `slate-800` | `white` |
| KPI Value | `text-3xl` (1.875rem) | Bold (700) | Status color | Status color |
| Label | `text-sm` (0.875rem) | Medium (500) | `slate-500` | `slate-400` |
| Body | `text-base` (1rem) | Regular (400) | `slate-600` | `slate-300` |
| Small/Caption | `text-xs` (0.75rem) | Medium (500) | `slate-500` | `slate-400` |

---

## Components

### Cards
```html
<div class="bg-white dark:bg-slate-800 p-6 rounded-2xl shadow-sm border border-slate-200 dark:border-slate-700">
  <!-- Content -->
</div>
```

**Properties:**
- Background: `white` / `slate-800`
- Padding: `p-6` (1.5rem)
- Border Radius: `rounded-2xl` (1rem)
- Shadow: `shadow-sm`
- Border: `1px solid` `slate-200` / `slate-700`

### KPI Cards
```html
<div class="kpi-card bg-white dark:bg-slate-800 p-5 rounded-2xl shadow-sm border border-slate-200 dark:border-slate-700">
  <h3 class="text-sm font-medium text-slate-500 dark:text-slate-400">Label</h3>
  <p class="text-3xl font-bold text-emerald-600 dark:text-emerald-400 mt-1">Value</p>
</div>
```

**Hover Effect:**
```css
.kpi-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1);
}
```

### Dark Mode Toggle Button
```html
<button id="theme-toggle" class="p-3 rounded-2xl bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-600 transition-all outline-none focus:ring-2 focus:ring-blue-400 shadow-sm border-2 border-slate-200 dark:border-slate-600">
  <!-- Moon icon (dark mode) -->
  <svg class="w-5 h-5 hidden" fill="currentColor" viewBox="0 0 20 20">
    <path d="M17.293 13.293A8 8 0 016.707 2.707a8.001 8.001 0 1010.586 10.586z"></path>
  </svg>
  <!-- Sun icon (light mode) -->
  <svg class="w-5 h-5 hidden" fill="currentColor" viewBox="0 0 20 20">
    <path d="M10 2a1 1 0 011 1v1a1 1 0 11-2 0V3a1 1 0 011-1zm4 8a4 4 0 11-8 0 4 4 0 018 0zm-.464 4.95l.707.707a1 1 0 001.414-1.414l-.707-.707a1 1 0 00-1.414 1.414zm2.12-10.607a1 1 0 010 1.414l-.706.707a1 1 0 11-1.414-1.414l.707-.707a1 1 0 011.414 0zM17 11a1 1 0 100-2h-1a1 1 0 100 2h1zm-7 4a1 1 0 011 1v1a1 1 0 11-2 0v-1a1 1 0 011-1zM5.05 6.464A1 1 0 106.465 5.05l-.708-.707a1 1 0 00-1.414 1.414l.707.707zm1.414 8.486l-.707.707a1 1 0 01-1.414-1.414l.707-.707a1 1 0 011.414 1.414zM4 11a1 1 0 100-2H3a1 1 0 000 2h1z" fill-rule="evenodd" clip-rule="evenodd"></path>
  </svg>
</button>
```

### Buttons
```html
<!-- Primary -->
<button class="px-5 py-3 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-2xl transition-colors">
  Action
</button>

<!-- Secondary -->
<button class="px-5 py-3 bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 font-medium rounded-2xl hover:bg-slate-200 dark:hover:bg-slate-600 transition-colors">
  Action
</button>
```

### Select Dropdowns
```html
<select class="sns-select block w-64 rounded-2xl border-2 border-slate-200 dark:border-slate-600 py-3 pl-5 pr-12 bg-white dark:bg-slate-700 text-slate-900 dark:text-white appearance-none font-medium cursor-pointer">
  <option>Option</option>
</select>
```

### Status Badges
```html
<!-- Success -->
<span class="text-xs font-semibold text-emerald-700 dark:text-emerald-300 bg-emerald-100 dark:bg-emerald-900/50 px-2.5 py-1 rounded-full">86.7%</span>

<!-- Warning -->
<span class="text-xs font-semibold text-amber-700 dark:text-amber-300 bg-amber-100 dark:bg-amber-900/50 px-2.5 py-1 rounded-full">NTV</span>

<!-- Danger -->
<span class="text-xs font-semibold text-red-700 dark:text-red-300 bg-red-100 dark:bg-red-900/50 px-2.5 py-1 rounded-full">Eviction</span>
```

### Tooltips
```css
.tip {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  margin-left: 4px;
  font-size: 9px;
  font-weight: 600;
  background: #e2e8f0;
  color: #64748b;
  border-radius: 50%;
  cursor: help;
}

.dark .tip {
  background: #475569;
  color: #94a3b8;
}

/* Tooltip appears on RIGHT side of icon */
.tip:hover::after {
  content: attr(data-tip);
  position: absolute;
  left: calc(100% + 6px);
  top: 50%;
  transform: translateY(-50%);
  padding: 6px 10px;
  background: white;
  color: #334155;
  font-size: 11px;
  font-weight: 500;
  white-space: nowrap;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  border: 1px solid #e2e8f0;
  z-index: 100;
}

.dark .tip:hover::after {
  background: #1e293b;
  color: #e2e8f0;
  border-color: #475569;
}
```

### Financial Cell Tooltips (Top Center)
```css
/* Top-center tooltip for financial cells */
.fin-cell {
  position: relative;
  cursor: default;
}
.fin-cell[data-count]:hover::after {
  content: attr(data-count);
  position: absolute;
  bottom: calc(100% + 4px);
  left: 50%;
  transform: translateX(-50%);
  padding: 4px 8px;
  background: white;
  color: #334155;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  border: 1px solid #e2e8f0;
  z-index: 100;
}
.dark .fin-cell[data-count]:hover::after {
  background: #1e293b;
  color: #e2e8f0;
  border-color: #475569;
}
```

**Usage:**
```html
<span class="fin-cell" data-count="24">$42,465.38</span>
```

### Progress Bars
```html
<div class="h-3 bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden">
  <div class="progress-bar h-full bg-gradient-to-r from-emerald-500 to-emerald-400 rounded-full" style="width: 86.7%"></div>
</div>
```

### Tables
```html
<table class="min-w-full text-sm">
  <thead class="bg-slate-50 dark:bg-slate-800/50 sticky top-0">
    <tr class="border-b-2 border-slate-200 dark:border-slate-700">
      <th class="px-4 py-3 text-left font-semibold text-slate-600 dark:text-slate-300">Header</th>
    </tr>
  </thead>
  <tbody class="divide-y divide-slate-100 dark:divide-slate-700">
    <tr class="hover:bg-slate-50 dark:hover:bg-slate-700/30 transition-colors">
      <td class="px-4 py-3 text-slate-900 dark:text-white">Content</td>
    </tr>
  </tbody>
</table>
```

### Region Header Colors
| Region | Light Mode | Dark Mode |
|--------|------------|-----------|
| Tampa | `bg-purple-100 text-purple-700` | `bg-purple-900/30 text-purple-300` |
| Orlando | `bg-sky-100 text-sky-700` | `bg-sky-900/30 text-sky-300` |
| Jacksonville | `bg-teal-100 text-teal-700` | `bg-teal-900/30 text-teal-300` |
| Ft. Lauderdale | `bg-rose-100 text-rose-700` | `bg-rose-900/30 text-rose-300` |

---

## Spacing

### Page Layout
- Page padding: `p-6` (1.5rem)
- Max width: `max-w-7xl` (80rem)
- Section gap: `space-y-5` (1.25rem)

### Grid
- 2 columns: `grid grid-cols-2 gap-6`
- 3 columns: `grid grid-cols-3 gap-6`
- 4 columns: `grid grid-cols-4 gap-4`
- 7 columns: `grid grid-cols-7 gap-3` (KPI cards)

### Table Column Widths
- Label/Status column: `w-[120px]`
- Property columns: `w-[65px]`
- Total column: `w-[70px]`

### Financial Summary Table
- Base text size: `text-xs` (titles match other cards)
- Data values: `text-[10px]` (slightly smaller for currency values)
- Table class: `min-w-full text-xs table-fixed`

---

## Dark Mode

### Implementation
```html
<html class="dark">
```

```javascript
// Toggle
document.documentElement.classList.toggle('dark');
localStorage.setItem('color-theme', isDark ? 'dark' : 'light');
```

### Color Mappings
| Light Mode | Dark Mode |
|------------|-----------|
| `bg-white` | `bg-slate-800` |
| `bg-slate-50` | `bg-slate-900` |
| `bg-slate-100` | `bg-slate-700` |
| `text-slate-900` | `text-white` |
| `text-slate-600` | `text-slate-300` |
| `text-slate-500` | `text-slate-400` |
| `border-slate-200` | `border-slate-700` |

---

## Custom Scrollbar
```css
.fancy-scroll {
  scrollbar-width: thin;
  scrollbar-color: #60a5fa #e2e8f0;
}

.dark .fancy-scroll {
  scrollbar-color: #3b82f6 #1e293b;
}

.fancy-scroll::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

.fancy-scroll::-webkit-scrollbar-track {
  background: #e2e8f0;
  border-radius: 10px;
}

.dark .fancy-scroll::-webkit-scrollbar-track {
  background: #1e293b;
}

.fancy-scroll::-webkit-scrollbar-thumb {
  background: linear-gradient(180deg, #60a5fa 0%, #3b82f6 100%);
  border-radius: 10px;
}
```

---

## Transitions
```css
/* Standard transition */
transition-colors duration-200

/* Card hover */
transition: transform 0.2s ease, box-shadow 0.2s ease;

/* Progress bar */
transition: width 0.5s ease-in-out;

/* Toggle button */
transition-all
```

---

## Chart.js Theming
```javascript
const colors = {
  text: isDark ? '#94a3b8' : '#64748b',
  grid: isDark ? '#334155' : '#e2e8f0',
  tooltip: {
    bg: isDark ? '#1e293b' : '#ffffff',
    title: isDark ? '#f1f5f9' : '#1e293b',
    body: isDark ? '#cbd5e1' : '#475569',
    border: isDark ? '#475569' : '#e2e8f0'
  }
};
```

---

## File Structure
```
SNS Daily Report/
  generate_dashboard.py   # Main dashboard generator script
  dashboard_live.html     # Generated dashboard (open in browser)
  README.md               # Project documentation
  SNS_STYLEGUIDE.md       # This file - design system docs
  Cardentials.txt         # API credentials (keep secure)
  archive/                # Old exploration scripts and data
```
