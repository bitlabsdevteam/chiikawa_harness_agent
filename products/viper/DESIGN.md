# Viper — design plan

## Direction, before implementation
An indigo-and-mango countertop arcade cabinet, surrounded by an illustrated field guide. The game is the product, not a thumbnail. A player should see the board, score and an unambiguous Start action before reading instructions.

- **Six color tokens:** ink `#23204d`, cabinet `#302967`, mango `#ffc45b`, paper `#fff8e8`, muted `#68647c`, line `#d8d0c2`. Pale ink and mango tints are derived for surfaces; no competing accent colors.
- **Type roles:** system sans for instructions and controls; heavy condensed-feeling system sans with tight tracking for the oversized headline; Georgia italic for the word “bite” and chapter moments; system monospace for score, keyboard legends and numbered labels. No font downloads.
- **Wireframe:** cream masthead → indigo hero (short editorial left rail with original viper; large working cabinet at right) → compact rules strip → nine numbered field-guide chapters in alternating editorial layouts → quiet footer. At 768px, cabinet and introduction stack; at 360px all reading columns stack, with full-width cabinet and large touch targets. Reading columns stay around 65 characters wide.
- **Visual signature:** a mango snake makes a square-grid hairpin, with a cream tooth-shaped notch. The same hairpin geometry appears on the cabinet corner, chapter markers and route diagrams. Canvas snake segments are rounded but the path remains unmistakably square-grid.
- **Controls:** explicit Start; tactile Pause and Restart; always-visible D-pad; keyboard focus ring; polite live state changes, separate scores and a canvas text description. No movement until Start, no reverse turns, one direction change per grid step.

## First design-director critique and revision
The initial “dark hero plus feature cards” approach would look like a generic software landing page and push the actual game below the fold. **Revision:** place the live cabinet directly in the hero, use a split editorial heading instead of a centered marketing pitch, avoid repeated rounded cards, and use numbered open chapters separated by thin rules. Illustrated routes must explain real game decisions. A ticket-shaped quick-reference strip and mechanical score windows reinforce an arcade object, rather than decorative gradients. All long copy must teach the actual rule set; no imaginary modes, accounts, power-ups or rewards.

## Follow-up director plan — make the cabinet usable, not merely visible
Keep the six primary colors and the indigo/mango hairpin signature. Make the narrow-screen intro a compact headline-and-viper pairing instead of a tall poster; preserve the full introduction underneath. Start/Resume should frame the complete cabinet in the viewport. A pause should reveal the route, not place a dark card over it. Add a numbered, open chapter index and an honest, proportionate speed chart with a five-bite progress ticket. Use shared surface, focus, radius and spacing tokens for the cabinet and drawing palette.

**Critique and revision:** adding more arcade ornament would not fix the usability problems. The memorable detail will instead do real work: five small mango-colored notches count down to the next pace. Keep the chapter index typographic, not a grid of promotional cards. Do not shrink the board to accommodate oversized introductory copy. Test the full control deck in view, not only document overflow.

## Implementation acceptance
At least nine semantic sections, over 1,400 initially visible instructional words, four original inline SVGs (including a hero artifact), 44px minimum controls, focus and reduced-motion support. Verify the engine independently and the assembled page at 360, 768 and 1280px. Review a second time in REVIEW.md, implement twelve concrete fixes and record verification, counts and any remaining limitations in QUALITY.md.
