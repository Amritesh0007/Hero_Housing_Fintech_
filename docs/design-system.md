# Design system — ValueGraph web app

Source of truth: the `:root` tokens in `src/valuegraph/web/app.css`. If a value isn't a token, it doesn't belong in the UI.

## Direction
A land-survey board: navy drafting rail, cool paper, teal for evidence, marigold reserved for the estimate. One orchestrated motion moment — the valuation reveal — and quiet everywhere else.

## Tokens

| Group | Token | Value | Use |
|---|---|---|---|
| Colour | `--navy` | `#0E2A47` | Navigation rail, primary buttons, headline value |
| | `--paper` / `--surface` | `#F2F5F7` / `#FFFFFF` | Page / panels |
| | `--ink` / `--ink-2` | `#13212E` / `#4B5B6A` | Body / secondary text |
| | `--line` | `#D6DEE5` | Borders, axis |
| | `--teal` / `--teal-soft` | `#0B7A75` / `#D7EEEC` | Comparable listings, value range, focus ring |
| | `--gold` | `#E0A21B` | The estimate marker only (and keyboard focus outline) |
| | `--up` / `--down` | `#1F7A4D` / `#B3392B` | Factors that raise / lower value |
| Reliability | `--g-high`, `--g-medium`, `--g-low`, `--g-none` (+ `-bg`) | text on tint | Grade pills |
| Type | `--display` | Bricolage Grotesque 700 | Page titles, the value, stats |
| | `--body` | Instrument Sans 400–600, tabular figures | Everything else |
| Shape | `--r-lg` / `--r-sm` | 14px / 8px | Panels / controls; pills fully rounded |
| Motion | `--ease` | `cubic-bezier(.2,.8,.2,1)` | All transitions |

All text/background pairs meet WCAG AA. Grade is always text, never colour alone.

## Motion
| Moment | What moves | Timing |
|---|---|---|
| Valuation reveal | value counts up → range rail draws → comparables drop onto the ₹/sqft strip → estimate pin rises and pulses → waterfall bars grow in order | ~2.2s total, staggered 70–120ms |
| Sign-in | contour lines drift | 40s loop, very low contrast |
| Responses to actions | nav indicator grows, review card slides out, toast rises | 150–350ms |

`prefers-reduced-motion: reduce` turns every animation off and shows final states.

## Components
| Component | Where | Rules |
|---|---|---|
| Value headline | report | Never a bare number: range, ₹/sqft with area basis, and "trained on asking prices" badge always shown |
| Grade pill | report, queue, history | Dot + full words ("Low reliability") |
| Survey strip | report | One ₹/sqft axis on the user's area basis; comps scaled from super built-up |
| Waterfall | report | Starts at "typical flat this size", ends at the estimate in gold; rupee amounts with sign |
| Comparables table | report | Header says asking prices; similarity shown as % |
| Fact tiles | report | LTV on the low estimate, guideline deviation, similar-listing count |

## Do / Don't
| Do | Don't |
|---|---|
| Say "asking price" wherever listing prices appear | Call model output "market value" or "accurate" |
| Build DOM with `h()` so data is always text | Put API data into `innerHTML` |
| Add new colours as tokens first | Hard-code hex in components |
