# MARÉ — final quality record

## Product and review scope

Open `index.html` directly in a modern browser. HTML, CSS, JavaScript and all original SVG drawings are inside that file: no build, packages, external fonts/images, account or internet connection are needed. This remains an honestly labelled coffee-roaster concept and useful local planner, not a live shop.

The follow-up review inspected every previously delivered source/document and test module, parsed the audit/result JSON, read the actual exported coffee note and process logs, and checked PNG dimensions against the rendering code. `REVIEW.md` lists **exactly twelve new deficiencies**, completed fixes and verification evidence. The previous review/result are retained as `.verification/baseline-review.md` and `baseline-results.json`; `.verification/initial-audit.json` is the still-earlier historical baseline. These are history, not current pass reports.

## Current skill-bar counts

Counts come from the initial rendered page: pour-over selected, other recipes hidden, only the first FAQ open, empty planner. Normally visible offscreen content counts; script/style, SVG lettering, visually hidden text, no-script content, print-only settings and initially hidden content do not. Paragraph-only counts do not rely on navigation or control text.

| Measure | 360px | 768px | 1280px |
|---|---:|---:|---:|
| Meaningful main sections | 11 | 11 | 11 |
| Ordinary visible text words | 2,412 | 2,412 | 2,421 |
| **Visible paragraph prose words** | **1,602** | **1,602** | **1,602** |
| Substantial visible original SVGs | 4 | 4 | 4 |
| Horizontal overflow | 0 | 0 | 0 |
| Active buttons below 44 × 44px | 0 | 0 | 0 |
| Hero headline lines | 3 | 3 | 2 |

The eleven sections are hero, buying rhythm, coffee shelf, sourcing, subscriptions, brew notebook, coastal story, freshness, service clarity, FAQ and planner. Six coffee cards, three subscription tiers, four accessible brew tabs and six native FAQ disclosures remain present. All four drawings have accessible titles/descriptions; they contain 27, 19, 22 and 22 SVG shape elements, including multi-segment hand-drawn paths. The hero's sea-glass bag, cobalt tiles and roasted-orange sun remain the signature artifact.

## Executed verification

**132 browser checks passed** in Google Chrome 154 using Node 23.11.0 on Darwin. `.verification/results.json` records each assertion and evidence with the SHA-256 of the tested `index.html`. `.verification/run-status.json` is the authoritative status of the entire latest runner invocation. `.verification/latest-audit.json` is a non-destructive supplementary measurement pass. The final inventory requires those results to match the current source and a completed successful run.

Covered behaviours and outputs:

- Sticky navigation, anchor clearance, real pointer opening, keyboard focus, Escape, Tab exit, mobile destination focus and desktop resize reset.
- Short-screen menus at **360 × 260** and **768 × 300**, including internal scrolling to the last link. An extra **320px reflow** check passes.
- Readable 12px utility-type roles across the target widths; the decorative wordmark subline and illustration lettering are deliberate exceptions. Tablet pricing uses two-column editorial rows rather than cramped three-up cards.
- All brew tabs, a single selected panel, roving tabindex, real Left/Right/Home/End events, wraparound and Tab into the active panel. The new planner recipe selector synchronises with tabs without changing grind.
- All six FAQs, plus keyboard Enter activation.
- Repeated monthly/annual changes for all three subscription prices, payment totals, equivalent-price suffixes and annual savings. Monthly equivalents in annual mode are ₹675 / ₹1,260 / ₹2,340; annual prepayments ₹8,100 / ₹15,120 / ₹28,080; annual savings ₹900 / ₹1,680 / ₹3,120. Monthly mode resets savings to ₹0. Existing planned subscriptions update too.
- Every add/select button; ₹3,300 for one of each coffee; quantities, subtotal, weight/cup estimate, zero removal, focus repair and a twenty-bag-per-profile limit. All card accessible names begin with the visible action; plan changes have one atomic live announcement channel, not a duplicate toast announcement.
- Profile and grind controls, replacing/removing subscriptions, clear and **undo clear**, invalidation of stale undo after a new selection, and disabled empty-save controls.
- An actual UTF-8 file at `.verification/downloads/mare-coffee-plan.txt`: ₹2,820 single-bag subtotal, 1250g, French press grind, Araku subscription, ₹15,120 annual payment and ₹1,680 saving. The recipe has labelled quantities and numbered steps 1–4. The inline copy matches the file exactly.
- Theme persistence, native control colour-scheme alignment, invalid saved values, live system preference changes, explicit override, synchronisation from a second real tab, and blocked storage.
- Direct `file://` operation. With JavaScript disabled: a read-only notice, all four recipes, native FAQs and no visible inert buttons.
- Print emulation from dark mode, white paper/dark ink, selected settings, all four recipes, temporary FAQ expansion and restoration, and a real `.verification/print.pdf`.
- Unique IDs, valid anchor targets, no uncaught JavaScript errors and no external page requests. An invalid DevTools command rejects rather than hanging.

## Contrast, focus and visual self-review

The six primary identity colours are unchanged: pale paper, dark ink, cobalt, sea-glass, roasted orange and sand. Adapted dark surfaces preserve the original drawings. Utility sizing and focus are now named CSS tokens, and individual CSS rules are readable rather than one enormous line.

The computed text audit composites transparent backgrounds and excludes disabled controls, hidden content and SVG art. Initial-page light and dark tests pass at all target widths: minimum sampled ratios **4.50:1** and **4.99:1**, respectively, with 254–258 text samples per state. Print text passes with a minimum **5.39:1**. Large text uses a 3:1 threshold; ordinary text uses 4.5:1. Focus tests independently exercise the two billing buttons and all three tier buttons: all outlines are solid, at least 3px, and at least 3:1 against their surrounding surface. Forced-colour rules preserve selected states without relying on fills alone.

Original sourcing safeguards remain: no invented estates, certifications, scores, endorsements, addresses, shipping promises or impact numbers. Monsooning is identified as a process, not a farm. Indicative prices exclude uncalculated shipping/taxes and are never described as an amount due. Brew notes retain hot-water, pressure and refrigerated-storage cautions.

## Captured outputs

The normal test run now produces, rather than relying on separate manual capture:

- `light-360.png`, `light-768.png`, `light-1280.png` — initial page at the three target widths, height 900px.
- `dark-360.png`, `dark-768.png`, `dark-1280.png` — equivalent dark-mode captures.
- `planner-360.png` — populated mobile planner, 360 × 900.
- `menu-360.png` and `menu-768.png` — short-screen menu states, 360 × 260 and 768 × 300.
- `full-1280.png` — entire current desktop page, 1280 × 9209.
- `print.pdf` — actual browser-generated A4 PDF, not a screenshot pretending to be a document.
- `downloads/mare-coffee-plan.txt` — actual saved user note.

All are in `.verification/`. `inventory.json` reads back every delivered source, document, test and evidence file, validates text/JSON/PNG/PDF formats and records sizes and SHA-256 hashes. Its own file is excluded from the hash list to avoid recursion. No personal browser profile is used or retained.

## Reproduce with one command

From this directory, with Node 22+ and a locally installed Chrome:

```sh
node tests/run.mjs
```

On a different Chrome installation, point `CHROME_PATH` to its executable:

```sh
CHROME_PATH="/path/to/chrome" node tests/run.mjs
```

No package installation or Python server is needed. The runner starts a standard-library HTTP server on a free loopback port, creates a fresh Chrome profile inside `.verification/`, discovers a free DevTools port, executes `verify.mjs`, then `audit.mjs`, cleans up the processes/profile and checks the file inventory. Each CDP client owns its own tab. Connection/command and child-process timeouts fail with explicit status. Progress output identifies the last completed check. Existing browser results are cleared at run start; a failed run cannot be confused with stale passing JSON. Final source/document consistency can also be rechecked with `node tests/inventory.mjs`.

Other executed commands: `node --check tests/verify.mjs`, `node --check tests/run.mjs`, source/PNG/JSON/UTF-8 read-back inspections, and the full runner. During this follow-up, two runner attempts were interrupted/terminated, including one after all browser checks had passed but before diagnostics completed. They were treated as failed overall runs, not quietly called successful; a failed status was retained in `interrupted-run.json`. Progress diagnostics were added before rerunning. No root cause for the unusually long host pauses is asserted.

## Limitations and intentional boundaries

- Execution was verified in local Chromium, not Safari, Firefox, physical phones or an audible screen reader. Native semantics, keyboard operations and focus were tested; this is not a complete assistive-technology certification.
- Screenshots were generated and structurally inspected. There was no separate human visual approval or image-viewing tool in this review. Automated geometry, computed styles and source inspection are the basis for the visual findings.
- Contrast checks do not model raster antialiasing, arbitrary CSS filters, every SVG stroke or transient animation frames. Print output is a real PDF with tested print-state styles; physical paper pagination was not manually inspected.
- Clipboard permission is not needed: selectable inline text backs up the verified Chrome download. Other browsers may prompt before saving.
- Coffee selections intentionally reset on refresh; only the display preference is persisted. Undo is local to the current unchanged plan. There is no backend, purchase, shipment or subscription activation to test.
