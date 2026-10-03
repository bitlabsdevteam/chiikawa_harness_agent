# MARÉ — Coffee, Goa

Open **index.html** directly in a modern browser. It is the complete offline-capable product: no build, packages, external fonts or images.

## Make a useful coffee note

Browse six Indian coffee profiles and add 250g bags. Compare three subscription sizes with monthly or annual billing; full annual prepayments and savings stay explicit. Choose a grind, a subscription profile when relevant, and the recipe to include in your saved note. Recipe and grind are independent choices.

Adjust quantities with plus/minus. **Save my plan** downloads a plain-text note with prices and numbered brewing instructions. **Read or copy this plan** provides the same text if downloads are blocked. **Undo clear** recovers an accidentally cleared plan until you change another coffee selection. Browser printing uses a paper-friendly layout, includes the four recipes and temporarily opens the FAQs.

The four brew tabs support Left/Right, Home/End and Tab into the recipe. On mobile, Menu opens the navigation; Escape closes it and restores focus. The menu scrolls in short viewports. Light/dark mode follows your system until you choose explicitly, then remembers that choice when local storage is available.

Prices and sourcing profiles are clearly proposed. No order, stock reservation, payment or recurring charge is created. Refreshing clears the coffee plan; save your note before leaving. No personal details are requested.

## Verify the delivery

With locally installed Chrome and Node 22+ (test tools only):

```sh
node tests/run.mjs
```

The standard-library runner starts and stops its own local server and isolated browser, uses free ports and removes its temporary browser profile. Set `CHROME_PATH` if Chrome is not at the default macOS location. No package installation is required.

- `DESIGN.md`: original plan and follow-up visual direction.
- `REVIEW.md`: twelve new concrete deficiencies, completed fixes and evidence.
- `QUALITY.md`: current counts, 132 browser checks, instructions and honest limits.
- `.verification/`: current screenshots, actual PDF/text exports, source-fingerprinted results and an artifact inventory. Historical baselines are explicitly labelled; `run-status.json` identifies whether the latest complete run passed.
