"""Draw the ablation figures as standalone SVG files from a bench_jev.py JSON report.

Standard library only. Offline reports contain stand-in Jev arms; they are drawn
in grey and labelled "(stand-in)" so a simulated bar is never mistaken for a
measurement. Charts that need live data are skipped with a note.

  python3 demos/make_jev_charts.py                                   # docs/jev-ablation-offline.json
  python3 demos/make_jev_charts.py --report docs/jev-ablation-live.json --out docs/figures-live
"""

import argparse
import json
import math
import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
MEASURED, STANDIN = "#0072B2", "#9AA0A6"   # colour-blind-safe blue, neutral grey
SAFE_BAD, JEV_COST, GOOD = "#D55E00", "#E69F00", "#009E73"
INK, GRID = "#222222", "#DDDDDD"
FONT = "font-family='Helvetica,Arial,sans-serif'"


def arm_label(name, arm):
    simulated = (arm.get("jev") or {}).get("simulated", False)
    clean = name.replace("jev-shaped ", "").replace(" (measured)", "").replace(" (live)", "")
    return clean + (" (stand-in)" if simulated else ""), simulated


class Svg:
    def __init__(self, width, height, title):
        self.width, self.height, self.parts = width, height, []
        self.text(width / 2, 24, title, size=15, anchor="middle", weight="bold")

    def rect(self, x, y, w, h, fill, stroke="none", opacity=1.0):
        self.parts.append(f"<rect x='{x:.1f}' y='{y:.1f}' width='{max(w, 0):.1f}' height='{max(h, 0):.1f}' "
                          f"fill='{fill}' stroke='{stroke}' opacity='{opacity}'/>")

    def line(self, x1, y1, x2, y2, stroke=INK, width=1, dash=None):
        extra = f" stroke-dasharray='{dash}'" if dash else ""
        self.parts.append(f"<line x1='{x1:.1f}' y1='{y1:.1f}' x2='{x2:.1f}' y2='{y2:.1f}' "
                          f"stroke='{stroke}' stroke-width='{width}'{extra}/>")

    def circle(self, x, y, r, fill):
        self.parts.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='{r}' fill='{fill}' stroke='white' stroke-width='1'/>")

    def text(self, x, y, value, size=11, anchor="start", fill=INK, weight="normal", rotate=None):
        turn = f" transform='rotate({rotate} {x:.1f} {y:.1f})'" if rotate is not None else ""
        self.parts.append(f"<text x='{x:.1f}' y='{y:.1f}' font-size='{size}' text-anchor='{anchor}' fill='{fill}' "
                          f"font-weight='{weight}' {FONT}{turn}>{escape(str(value))}</text>")

    def render(self):
        body = "\n".join(self.parts)
        return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{self.width}' height='{self.height}' "
                f"viewBox='0 0 {self.width} {self.height}'>\n<rect width='100%' height='100%' fill='white'/>\n"
                f"{body}\n</svg>\n")


def pct(value):
    return f"{value * 100:.0f}%"


def legend(svg, x, y, items):
    for label, colour in items:
        svg.rect(x, y - 9, 12, 12, colour)
        svg.text(x + 17, y + 1, label, size=11)
        x += 24 + 6.4 * len(label)


def fig_permissions(report):
    arms = report["permissions"]
    rows = list(arms.items())
    left, bar, gap = 250, 14, 12
    height = 90 + len(rows) * (2 * bar + gap + 6)
    svg = Svg(900, height, "Permissions: accuracy and dangerous calls allowed to run")
    legend(svg, left, 48, [("Accuracy", MEASURED), ("Gold-deny allowed to run (lower is safer)", SAFE_BAD)])
    plot_w = 560
    for tick in range(0, 101, 20):
        x = left + plot_w * tick / 100
        svg.line(x, 62, x, height - 28, GRID)
        svg.text(x, height - 12, f"{tick}%", size=10, anchor="middle")
    y = 70
    for name, arm in rows:
        label, simulated = arm_label(name, arm)
        svg.text(left - 8, y + bar + 4, label, anchor="end", fill="#555" if simulated else INK)
        for offset, metric, colour in ((0, "accuracy", STANDIN if simulated else MEASURED),
                                       (bar + 2, "false_allow_deny", SAFE_BAD)):
            item = arm["metrics"][metric]
            low, high = item["ci95"]
            top = y + offset
            svg.rect(left, top, plot_w * item["rate"], bar, colour, opacity=0.55 if simulated and metric != "accuracy" else 1)
            mid = top + bar / 2
            svg.line(left + plot_w * low, mid, left + plot_w * high, mid, INK, 1)
            svg.text(left + plot_w * max(item["rate"], high) + 6, top + bar - 3, f"{pct(item['rate'])} ({item['k']}/{item['n']})", size=10)
        y += 2 * bar + gap + 6
    svg.text(left, height - 1, "Whiskers: 95% Wilson interval. Grey = stand-in decider, not real Jev.", size=10, fill="#555")
    return svg.render()


def fig_confusion(report):
    arms = list(report["permissions"].items())
    labels, cell, pad_x, pad_y = ("allow", "ask", "deny"), 54, 70, 70
    cols = min(3, len(arms))
    rows_n = math.ceil(len(arms) / cols)
    block_w, block_h = 3 * cell + 120, 3 * cell + 110
    svg = Svg(pad_x + cols * block_w, pad_y + rows_n * block_h, "Permissions: confusion matrices (rows = gold label, columns = prediction)")
    for index, (name, arm) in enumerate(arms):
        label, simulated = arm_label(name, arm)
        ox, oy = pad_x + (index % cols) * block_w, pad_y + (index // cols) * block_h
        svg.text(ox + 1.5 * cell, oy - 8, label, size=11, anchor="middle", weight="bold")
        matrix = arm["metrics"]["confusion"]
        for r, gold in enumerate(labels):
            total = sum(matrix[gold].values()) or 1
            svg.text(ox - 6, oy + r * cell + cell / 2 + 4, gold, size=10, anchor="end")
            for c, pred in enumerate(labels):
                count = matrix[gold][pred]
                share = count / total
                correct = gold == pred
                base = MEASURED if correct else SAFE_BAD
                svg.rect(ox + c * cell, oy + r * cell, cell - 2, cell - 2, base, opacity=0.08 + 0.85 * share)
                svg.text(ox + c * cell + cell / 2 - 1, oy + r * cell + cell / 2 + 4, count, size=12, anchor="middle")
        for c, pred in enumerate(labels):
            svg.text(ox + c * cell + cell / 2 - 1, oy + 3 * cell + 12, pred, size=10, anchor="middle")
        svg.text(ox + 1.5 * cell, oy + 3 * cell + 30, "blue = correct, orange = wrong; shade = share of the gold row", size=9, anchor="middle", fill="#555")
    return svg.render()


def fig_categories(report):
    arms = list(report["permissions"].items())
    cats = sorted({c for _, arm in arms for c in arm["by_category"]})
    palette = [MEASURED, "#56B4E9", GOOD, JEV_COST, SAFE_BAD, "#CC79A7", "#999999"]
    left, bottom, plot_h = 60, 330, 230
    width = max(900, left + len(cats) * (len(arms) * 16 + 30) + 40)
    svg = Svg(width, bottom + 110, "Permissions: accuracy by call category")
    for tick in range(0, 101, 25):
        y = bottom - plot_h * tick / 100
        svg.line(left, y, width - 20, y, GRID)
        svg.text(left - 6, y + 4, f"{tick}%", size=10, anchor="end")
    x = left + 14
    for cat in cats:
        for index, (name, arm) in enumerate(arms):
            item = arm["by_category"].get(cat)
            if item:
                svg.rect(x + index * 16, bottom - plot_h * item["rate"], 13, plot_h * item["rate"], palette[index % len(palette)])
        svg.text(x + len(arms) * 8, bottom + 16, cat.replace("_", " "), size=10, anchor="middle")
        x += len(arms) * 16 + 30
    for index, (name, arm) in enumerate(arms):
        label, simulated = arm_label(name, arm)
        svg.rect(left, bottom + 40 + index * 15, 11, 11, palette[index % len(palette)])
        svg.text(left + 17, bottom + 50 + index * 15, label, size=10)
    return svg.render()


def fig_savings(report):
    arms = list(report["visibility"].items())
    left, top, w, h = 80, 60, 640, 330
    svg = Svg(900, 470, "Context: token reduction vs. needed-chunk recall")
    svg.rect(left + w * 0.95, top, w * 0.05, h, GOOD, opacity=0.15)
    svg.text(left + w * 0.975, top + 14, "gate", size=10, anchor="middle", fill=GOOD)
    for tick in range(0, 101, 25):
        x, y = left + w * tick / 100, top + h - h * tick / 100
        svg.line(x, top, x, top + h, GRID)
        svg.line(left, y, left + w, y, GRID)
        svg.text(x, top + h + 16, f"{tick}%", size=10, anchor="middle")
        svg.text(left - 6, y + 4, f"{tick}%", size=10, anchor="end")
    svg.line(left + w * 0.95, top, left + w * 0.95, top + h, GOOD, 2, "5 4")
    svg.text(left + w / 2, top + h + 36, "Needed-chunk recall (accuracy)", anchor="middle")
    svg.text(24, top + h / 2, "Token reduction", anchor="middle", rotate=-90)
    for index, (name, arm) in enumerate(arms):
        label, simulated = arm_label(name, arm)
        recall, reduction = arm["metrics"]["needed_recall"]["rate"], arm["metrics"]["token_reduction"]
        px, py = left + w * recall, top + h - h * reduction
        passed = recall >= 0.95
        svg.circle(px, py, 8, GOOD if passed else SAFE_BAD)
        svg.text(px + 12, py + 4, f"{label}: {pct(recall)} recall, {pct(reduction)} fewer tokens", size=10)
    svg.text(left + w + 10, top + 14, "Right of the dashed line", size=10, fill=GOOD)
    svg.text(left + w + 10, top + 28, "= counts as a saving", size=10, fill=GOOD)
    svg.text(left, 462, "Orange = fails the 95% recall gate: its savings do not count. Stand-in deciders, not real Jev.", size=10, fill="#555")
    return svg.render()


def fig_tokens(report):
    arms = list(report["visibility"].items())
    left, top, w = 270, 60, 560
    biggest = max(max(a["metrics"]["full_tokens"], a["jev"]["input_tokens"]) for _, a in arms)
    height = top + len(arms) * 60 + 50
    svg = Svg(900, height, "Context: tokens before, after, and Jev's own cost")
    legend(svg, left, 46, [("Before (full history)", STANDIN), ("After Jev view", MEASURED), ("Jev input tokens", JEV_COST)])
    y = top + 10
    for name, arm in arms:
        label, _ = arm_label(name, arm)
        svg.text(left - 8, y + 24, label, anchor="end")
        values = ((arm["metrics"]["full_tokens"], STANDIN), (arm["metrics"]["view_tokens"], MEASURED), (arm["jev"]["input_tokens"], JEV_COST))
        for offset, (value, colour) in enumerate(values):
            svg.rect(left, y + offset * 14, w * value / biggest, 12, colour)
            svg.text(left + w * value / biggest + 5, y + offset * 14 + 10, f"{value:,.0f}", size=10)
        y += 60
    svg.text(left, height - 12, "Net saving = before - after - Jev tokens, valid only when the recall gate passes.", size=10, fill="#555")
    return svg.render()


def fig_breakeven(report):
    arms = list(report["visibility"].items())
    left, top, w, h, reuse = 80, 60, 640, 320, 12
    per = {}
    for name, arm in arms:
        saved = arm["metrics"]["full_tokens"] - arm["metrics"]["view_tokens"]
        per[name] = [(n, n * saved - arm["jev"]["input_tokens"]) for n in range(1, reuse + 1)]
    low = min(v for series in per.values() for _, v in series)
    high = max(v for series in per.values() for _, v in series)
    span = (high - low) or 1
    svg = Svg(900, 470, "Context: net tokens saved vs. requests that reuse the ratings")
    zero = top + h - h * (0 - low) / span
    for n in range(0, reuse + 1, 2):
        x = left + w * n / reuse
        svg.line(x, top, x, top + h, GRID)
        svg.text(x, top + h + 16, n, size=10, anchor="middle")
    if low < 0 < high:
        svg.line(left, zero, left + w, zero, INK, 1.5)
        svg.text(left - 6, zero + 4, "0", size=10, anchor="end")
    svg.text(left + w / 2, top + h + 36, "Requests reusing the same ratings", anchor="middle")
    svg.text(24, top + h / 2, "Net tokens saved", anchor="middle", rotate=-90)
    palette = [MEASURED, GOOD, JEV_COST, SAFE_BAD, "#CC79A7"]
    for index, (name, series) in enumerate(per.items()):
        arm = report["visibility"][name]
        label, _ = arm_label(name, arm)
        passed = (arm["metrics"]["needed_recall"]["rate"] or 0) >= 0.95
        points = " ".join(f"{left + w * n / reuse:.1f},{top + h - h * (v - low) / span:.1f}" for n, v in series)
        dash = "" if passed else " stroke-dasharray='6 4'"
        svg.parts.append(f"<polyline points='{points}' fill='none' stroke='{palette[index % len(palette)]}' stroke-width='2'{dash}/>")
        svg.rect(left + w + 12, top + 6 + index * 16, 10, 10, palette[index % len(palette)])
        svg.text(left + w + 28, top + 15 + index * 16, label + ("" if passed else " (gate failed)"), size=10)
    svg.text(left, 462, "Dashed = arm fails the recall gate, so its line is not a valid saving. Stand-in deciders.", size=10, fill="#555")
    return svg.render()


def fig_latency(report):
    rows = [(n, a) for n, a in report["permissions"].items() if "decision_ms_p50" in (a.get("jev") or {})]
    if not rows:
        return None
    left, top, w = 250, 70, 560
    biggest = max(a["jev"]["decision_ms_p95"] for _, a in rows)
    svg = Svg(900, top + len(rows) * 50 + 50, "Permissions: decision latency (milliseconds, measured)")
    legend(svg, left, 46, [("p50", MEASURED), ("p95", SAFE_BAD)])
    y = top
    for name, arm in rows:
        label, _ = arm_label(name, arm)
        svg.text(left - 8, y + 20, label, anchor="end")
        for offset, key, colour in ((0, "decision_ms_p50", MEASURED), (16, "decision_ms_p95", SAFE_BAD)):
            value = arm["jev"][key]
            svg.rect(left, y + offset, w * value / biggest, 13, colour)
            svg.text(left + w * value / biggest + 5, y + offset + 11, f"{value:,.0f} ms", size=10)
        y += 50
    return svg.render()


FIGURES = (
    ("fig1_permissions_accuracy", fig_permissions, "Accuracy and the safety number: gold-deny calls that would be allowed to run. Lower orange is safer."),
    ("fig2_permissions_confusion", fig_confusion, "Where each arm confuses allow, ask and deny."),
    ("fig3_permissions_by_category", fig_categories, "Accuracy per call category; the 'hard' category is where a regex denylist fails."),
    ("fig4_context_savings_vs_recall", fig_savings, "Token saving only counts when needed-chunk recall passes the 95% gate."),
    ("fig5_context_tokens", fig_tokens, "Tokens before and after the Jev view, next to Jev's own input tokens."),
    ("fig6_context_breakeven", fig_breakeven, "How many requests must reuse the ratings before Jev's token cost is repaid."),
    ("fig7_permissions_latency", fig_latency, "Measured decision latency (live reports only)."),
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", default=str(ROOT / "docs" / "jev-ablation-offline.json"))
    parser.add_argument("--out", default=str(ROOT / "docs" / "figures"))
    args = parser.parse_args(argv)
    report = json.loads(Path(args.report).read_text())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    written, skipped, cards = [], [], []
    for name, draw, caption in FIGURES:
        svg = draw(report)
        if svg is None:
            skipped.append(name)
            continue
        (out / f"{name}.svg").write_text(svg)
        written.append(name)
        cards.append(f"<figure><img src='{name}.svg' alt='{escape(caption)}'><figcaption>{escape(caption)}</figcaption></figure>")
    mode = report.get("mode", "offline")
    banner = ("OFFLINE report: grey bars and points are scripted stand-ins for Jev, not real Jev results. "
              "Only the regex baseline is measured." if mode == "offline" else "LIVE report: measured results.")
    (out / "index.html").write_text(
        f"<!doctype html><meta charset='utf-8'><title>Jev ablation figures</title>"
        f"<body style='font-family:Helvetica,Arial,sans-serif;max-width:980px;margin:24px auto'>"
        f"<h1>Jev ablation figures ({escape(mode)})</h1><p><b>{escape(banner)}</b></p>{''.join(cards)}</body>")
    print(f"Wrote {len(written)} figures to {out}: {', '.join(written)}")
    if skipped:
        print(f"Skipped (need a live report): {', '.join(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
