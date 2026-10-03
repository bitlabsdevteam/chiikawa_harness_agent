# Viper

A self-contained, indigo-and-mango canvas snake game. Open **index.html** in a current browser. No build step, downloads, fonts, accounts or network connection are needed.

## Play

- Choose **Start game**; the board does not move beforehand.
- Steer with **arrow keys**, **WASD**, or the four touch buttons.
- Eat a mango for one point and one extra tail square. Every five foods increases the pace.
- Avoid walls and your own body. There is no edge wrapping.
- Only one valid turn is accepted per movement step. Reversals and extra turns in the same step are ignored.
- Use **Pause**, **P**, or **Space** while the board is focused. A focused button keeps its normal Space behavior. **Resume** continues the round.
- **Restart** discards the round and returns to Ready. **Play again** starts a new round after a collision or full board.
- Leaving the window or tab pauses automatically. Returning never starts movement for you.

Your best score is saved in this browser using `viper.best.v1` in localStorage. If storage is blocked or full, the cabinet says so and keeps a best for the current visit. Saving rules for directly opened files vary by browser. The in-page field guide covers strategy, exact rules, accessibility and storage in detail.

## Optional local hosting

From this directory, using Python's standard library:

```sh
python3 -m http.server 8000 --bind 127.0.0.1
```

Visit `http://127.0.0.1:8000`. A hosted origin and a directly opened file have separate high scores. Stop the server with Ctrl+C.

## Run the checks

Node 22+ is recommended; verification here used Node 23.11.0.

```sh
node tests.mjs
node browser-tests.mjs
```

The first command extracts and tests the actual engine inside index.html. The second uses Node's built-in HTTP, WebSocket and process APIs to drive an already-installed Chromium-family browser. It finds Brave/Chrome on macOS or Chromium on Linux; set `BROWSER` to another executable if needed. It installs nothing. It uses an isolated temporary profile inside this directory, removes that profile afterward, and writes screenshots and results to `verification/`.

Some browser checks replace only the animation scheduler with a manually advanced clock to make boundary cases deterministic. Separate checks exercise the real requestAnimationFrame loop. The suite covers keyboard/touch input, focus, storage exceptions, visibility events, responsive bounds and high-density canvas resizing.

## Files

- `index.html` — the entire product, including original SVGs, styles and game logic.
- `DESIGN.md` — pre-build design plan and first critique.
- `REVIEW.md` — twelve second-review findings, implemented fixes and evidence.
- `QUALITY.md` — acceptance counts, test results and limitations.
- `tests.mjs`, `browser-tests.mjs` — dependency-free executable tests.
- `verification/` — generated evidence, not needed to play.

Viper exposes textual game state, named keyboard-operable controls and event announcements. It remains a visual real-time game, not a complete nonvisual mode. See QUALITY.md for the precise verification scope.
