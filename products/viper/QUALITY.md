# Viper — quality and verification

## Delivered product

`index.html` is the complete runnable product. It contains all CSS, original inline SVG illustrations, canvas rendering, deterministic game rules, requestAnimationFrame scheduling, event handling and storage logic. No runtime imports, external assets, package installs or build step. Start is always explicit. No Python source files were created.

## Self-review counts

Measured from the rendered DOM, not source-code tokens:

| Requirement | 360px | 768px | 1280px |
| --- | ---: | ---: | ---: |
| Semantic main sections | 10 | 10 | 10 |
| Displayed original inline SVG illustrations | 4 | 4 | 4 |
| Visible text words excluding SVG, script, style, noscript, hidden controls and screen-reader-only text | 2,332 | 2,352 | 2,352 |
| Conservative prose words in visible paragraphs, list items and figure captions | **1,999** | **2,016** | **2,016** |
| Canvas CSS square | 273px | 440px | 440px |
| Backing square at tested DPR | 546px (2×) | 880px (2×) | 440px (1×) |
| Horizontal document overflow | none | none | none |

The section count is the playable hero plus nine distinct field-guide chapters. The four original illustrations are the hero viper/miniature grid artifact, a turn sequence, a pace chart and a route/exit diagram. All remain visible on narrow screens. Prose counts exclude heading and table padding; even the strict mobile count exceeds 1,400 by 599 words. Content is expanded on initial load, not hidden inside accordions.

Six working interaction groups: explicit Start/replay; keyboard steering; touch steering; Pause/Resume; Restart-to-ready; and in-page guide/cabinet navigation. Persistent best-score updates are additionally exercised, including recovery from storage exceptions.

## Design and accessibility checks

- Six subject-specific color tokens, spacing/radius/type roles, responsive editorial layouts and a square-hairpin viper signature. System fonts only.
- The first critique in DESIGN.md replaced generic feature cards with the actual cabinet and numbered open chapters.
- The second critique in REVIEW.md contains **exactly twelve numbered deficiencies**. All twelve fixes are implemented and tied to concrete evidence.
- Visible game buttons meet **44 × 44px** minimums at each tested width. Score labels and storage notices stay at least 11px; cabinet foot labels stay at least 10px.
- Overlay heading contrast is **8.01:1** in the worst tested compositing case, above the 4.5:1 text threshold.
- Native buttons, named direction controls, a focusable labeled canvas, a named cabinet region, skip link, explicit status text and one deliberate game announcer.
- Movement does not continually rewrite score text nodes or announce every square. Start, food, pause, resume and completion convey useful state; head and food coordinates supplement the visual board.
- Pausing from the board moves focus to the visible Resume action. Starting/resuming returns focus to the board. The touch pad does not steal focus on every tap.
- Reduced-motion media emulation confirms normal rather than smooth page scrolling. No flashing, sound, shaking or decorative motion.
- Browser screenshots record ready layouts at all required sizes, a paused state, a final desktop view and the complete desktop page. Geometry, computed styles, accessible names and content counts were checked programmatically; a human screenshot or screen-reader audit was not performed.

## Executed verification

Environment: Darwin, Node **23.11.0**, installed Brave in headless Chromium mode. All tools use standard-library APIs; no packages were added.

```sh
node tests.mjs
node browser-tests.mjs
```

**18 engine tests passed**, extracting the engine from the actual delivered HTML:

- Ready-state immobility; exact grid movement; food growth and scoring.
- All 79 reachable five-food pace transitions, plus eating the fifth actual food.
- All reverse directions; invalid input; same-direction and reverse inputs do not consume a valid turn; double-turn locking with no delayed extra command.
- Pause/reset/replay transitions, all four walls, body collision and entering a vacating tail square.
- Empty-square food placement, last-square completion and 100 seeded random sessions checking occupancy, length and bounds.

**21 browser checks passed**:

- Actual requestAnimationFrame idle/start/freeze behavior.
- Responsive bounds, content counts, illustration visibility, touch targets, density and text sizes at 360/768/1280.
- Overlay contrast; arrow/WASD input; scoped shortcuts; live-text stability; coordinate announcements; visible pause focus and a full first interval on resume.
- Five real food updates, pace change and immediate best persistence.
- A 490ms delay makes only one move; a 501ms stall pauses.
- Visibility and blur event handling, pending-turn clearing and no automatic return-to-play.
- Paused-state preservation through high-density resizing.
- Actual CDP touch events and native Enter activation of a direction button.
- Wall ending, replay, Restart-to-ready, storage persistence across reload, malformed stored data and throwing read/write operations.
- Reduced motion, focus indicator and accessibility-tree names.
- No JavaScript exceptions and no external page requests.
- Direct `file:` opening, actual food collection, save and reload, confirming the standalone artifact works in the tested browser.

Evidence: `verification/results.json`, `verification/engine-tests.txt`, `verification/browser-tests.txt`, and six PNGs. Browser tests create and remove their own workspace-local profile. Test development also corrected the engine-extraction delimiter, scrollbar-width assumptions, DPR-only resizing, and native touch/Enter test setup before the final passing run.

## Limitations and honest scope

- Verified in Chromium/Brave only. Safari, Firefox, physical phone touch, actual OS tab/minimize behavior and assistive-technology speech were not manually tested. Visibility loss and storage failures are injected browser events/implementations; touch is browser-protocol emulation, not a physical device.
- Modern canvas APIs, CSS color mixing, focus-visible and system fonts are assumed. There is a canvas-unavailable message and a no-JavaScript notice; legacy browsers are not a target.
- This is still a visual spatial game. Accessible controls, status and coordinates are not a full nonvisual play mode.
- At very high paces, the display's refresh rate limits visible grid updates. At most one step occurs in a frame; missed time is not replayed as an invisible burst. Slow devices can feel slower than the target interval.
- Scores are browser-local, not synchronized across tabs/devices. Private browsing, blocked storage, cleared site data or a different file/origin can lose or separate records. Active rounds are intentionally not persisted.
- Restart deliberately discards the current run without a confirmation dialog, then waits at Ready. There is no undo, swipe steering, multiplayer, sound or online leaderboard; the page does not claim otherwise.
