# Chiikawa — A little world of happy

A standalone, unofficial fan website for Chiikawa, Hachiware, and Usagi. This is a character-themed website, not a landing page for the repository’s coding-agent harness.

## Preview

From the repository root:

```sh
python3 -m http.server 8080 --bind 127.0.0.1 --directory products/chiikawa-world
```

Open **http://127.0.0.1:8080**. Stop the server with Ctrl+C.

You can also open `index.html` directly. Use the local server for predictable saved-progress behavior: browsers differ in how they handle local storage on `file:` URLs.

## Features

- Responsive cream-and-pastel design, with self-contained SVG character and scene illustrations.
- Three character profiles using native modal dialogs, keyboard support, and focus restoration.
- A three-item daily-joy checklist, saved only in the current browser. Progress resets on a new local calendar day when the page loads, becomes visible, or a task changes.
- Random encouragement with no consecutive duplicate reminders.
- Collapsible mobile navigation, a skip link, visible focus indicators, and reduced-motion support.
- Readable content and navigation without JavaScript. The checklist remains usable without browser storage; an explanatory note appears when saving is unavailable.
- No external fonts, asset requests, analytics, dependencies, build step, or backend.

## Files

- `index.html`: page content and original fan-made SVG artwork.
- `style.css`: responsive styling.
- `app.js`: progressive enhancement and local checklist storage.
- `favicon.svg`: local site icon.
- `browser-tests.mjs`: dependency-free Chromium integration checks.

## Verification

Requires Node.js 22+ and an installed Chromium-family browser. The runner discovers common Chrome/Brave/Chromium locations, or accepts an explicit executable:

```sh
node products/chiikawa-world/browser-tests.mjs
# If automatic browser discovery fails:
BROWSER=/path/to/chromium node products/chiikawa-world/browser-tests.mjs
```

The test runner starts its own loopback server and fresh browser profile, then removes both on completion. It does not use your personal browser profile. Outputs are written under `verification/`.

The 18 browser checks cover local navigation and SVG references, no external asset requests, layout at 320/360/760/768/1024/1440 pixels, all character dialogs, inert modal backgrounds, keyboard controls and focus restoration, persistence, old-day reset, corrupt/unknown/duplicate storage values, unavailable storage, nonrepeating reminders, mobile navigation, reduced motion, no-JavaScript fallback, and uncaught JavaScript errors.

`verification/results.json` records the latest passing run. `home-360.png` and `home-1440.png` are browser screenshots for manual review; automated geometry checks are not a substitute for a visual or assistive-technology audit. Safari and Firefox have not been tested.

## Attribution and scope

Chiikawa and its characters belong to Nagano. This is an unofficial, unaffiliated fan-made site with original illustrative recreations, not official artwork or merchandise. No deployment or publication is included.
