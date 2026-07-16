"""Self-contained HTML deck and evidence-supported content derivatives."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "asset"


DECK_CSS = """
:root{color-scheme:light;--paper:#f6f2e9;--surface:#fffefa;--ink:#17201e;--muted:#66716d;--indigo:#3e4c83;--copper:#a15c38;--green:#2f6b57;--rule:#d8d4c9;font-family:'Source Sans 3','Segoe UI',sans-serif}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink)}
a{color:inherit}.skip{position:fixed;left:.75rem;top:.75rem;transform:translateY(-180%);background:var(--ink);color:var(--surface);padding:.75rem 1rem;z-index:9}.skip:focus{transform:none}
.deck-nav{position:fixed;inset:0 0 auto 0;display:flex;align-items:center;gap:.65rem;padding:.6rem 1rem;background:rgba(246,242,233,.97);border-bottom:1px solid var(--rule);z-index:5}.deck-nav strong{margin-right:auto;letter-spacing:.08em;font-size:.75rem}.deck-nav a{display:inline-flex;min-width:2.5rem;min-height:2.5rem;align-items:center;justify-content:center;text-decoration:none;border:1px solid transparent;border-radius:50%}.deck-nav a:hover,.deck-nav a:focus-visible{border-color:var(--indigo);outline:3px solid rgba(62,76,131,.2);outline-offset:2px}
main{scroll-snap-type:y mandatory}.slide{min-height:100vh;padding:7rem max(5vw,2rem) 4rem;display:grid;grid-template-columns:minmax(0,1.2fr) minmax(18rem,.8fr);gap:clamp(2rem,6vw,7rem);align-items:center;scroll-snap-align:start;border-bottom:1px solid var(--rule)}
.slide:nth-child(even){background:var(--surface)}.eyebrow{font-size:.78rem;font-weight:750;letter-spacing:.16em;color:var(--copper);text-transform:uppercase}.slide h1{font-family:'Fraunces',Georgia,serif;font-size:clamp(2.8rem,6.5vw,6.2rem);line-height:.95;max-width:14ch;margin:.7rem 0 1.5rem;letter-spacing:-.035em}.slide__body{font-size:clamp(1.05rem,1.55vw,1.4rem);line-height:1.55;max-width:54ch;color:var(--muted)}
.signal-list{margin:0;padding:0;list-style:none;border-top:1px solid var(--rule)}.signal-list li{padding:1.3rem 0;border-bottom:1px solid var(--rule)}.signal-list strong{display:block;color:var(--indigo);font-size:.78rem;letter-spacing:.09em;text-transform:uppercase;margin-bottom:.35rem}.signal-list span{font-size:1.05rem;line-height:1.45}.folio{position:absolute;right:max(5vw,2rem);bottom:2rem;color:var(--muted);font-variant-numeric:tabular-nums;font-size:.8rem}.slide{position:relative}
.deck-footer{padding:2rem max(5vw,2rem);font-size:.8rem;color:var(--muted);display:flex;gap:2rem;justify-content:space-between}
@media(max-width:760px){.deck-nav a:nth-of-type(n+7){display:none}.slide{grid-template-columns:1fr;padding-top:6rem;align-content:center}.slide h1{font-size:clamp(2.4rem,13vw,4.5rem)}.signal-list{margin-top:1rem}.deck-footer{display:block}.deck-footer span{display:block;margin:.4rem 0}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}main{scroll-snap-type:none}}
@media print{.deck-nav,.skip{display:none}.slide{width:297mm;height:210mm;min-height:0;padding:22mm;page-break-after:always;break-after:page}.slide h1{font-size:36pt}.deck-footer{display:none}}
"""


def build_html_deck(data: dict[str, Any], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    slides = data.get("deck", [])
    nav = "".join(
        f'<a href="#slide-{index}" aria-label="Go to slide {index}">{index}</a>'
        for index in range(1, len(slides) + 1)
    )
    rendered: list[str] = []
    for index, slide in enumerate(slides, start=1):
        points = "".join(
            f'<li><strong>{_e(point["label"])}</strong><span>{_e(point["text"])}</span></li>'
            for point in slide.get("points", [])
        )
        rendered.append(
            f"""<section class="slide" id="slide-{index}" aria-labelledby="slide-{index}-title">
<div><p class="eyebrow">{_e(slide.get('eyebrow', 'Executive review'))}</p><h1 id="slide-{index}-title">{_e(slide['title'])}</h1><p class="slide__body">{_e(slide['body'])}</p></div>
<ul class="signal-list" aria-label="Supporting evidence">{points}</ul><span class="folio">{index:02d} / {len(slides):02d}</span></section>"""
        )
    run = data["run"]
    document = f"""<!doctype html>
<html lang="en-AU"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>Kakawa Chocolates · Enterprise SEO Executive Review</title><style>{DECK_CSS}</style></head>
<body><a class="skip" href="#slide-1">Skip to presentation</a><nav class="deck-nav" aria-label="Slide navigation"><strong>TRAFFIC RADIUS · KAKAWA</strong>{nav}</nav><main>{''.join(rendered)}</main>
<footer class="deck-footer"><span>Evidence as of {_e(run['evidence_as_of'])} · Run {_e(run['id'])}</span><span>Self-contained HTML · no external assets or machine paths</span></footer></body></html>"""
    output.write_text(document, encoding="utf-8", newline="\n")
    return output


CONTENT_CSS = """
:root{--paper:#f6f2e9;--surface:#fffefa;--ink:#17201e;--muted:#66716d;--indigo:#3e4c83;--copper:#a15c38;--rule:#d8d4c9;font-family:'Source Sans 3','Segoe UI',sans-serif}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);line-height:1.65}main{max-width:75rem;margin:auto;padding:clamp(2rem,6vw,6rem)}header{display:grid;grid-template-columns:1fr minmax(16rem,.45fr);gap:3rem;padding-bottom:3rem;border-bottom:1px solid var(--rule)}h1,h2{font-family:'Fraunces',Georgia,serif;line-height:1.05}h1{font-size:clamp(2.8rem,7vw,5.6rem);margin:.5rem 0 1rem;letter-spacing:-.04em}h2{font-size:2rem;margin-top:3rem}.eyebrow{color:var(--copper);font-size:.78rem;letter-spacing:.15em;text-transform:uppercase;font-weight:700}.lede{font-size:1.2rem;color:var(--muted);max-width:55ch}.meta{margin:0;padding:1rem 0;border-top:1px solid var(--rule)}.meta div{padding:.75rem 0;border-bottom:1px solid var(--rule)}.meta dt{font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.1em}.meta dd{margin:.2rem 0}.article{max-width:48rem;padding-top:2rem}.ledger{margin-top:4rem;padding-top:2rem;border-top:1px solid var(--rule)}table{width:100%;border-collapse:collapse;background:var(--surface);font-size:.88rem}th,td{text-align:left;vertical-align:top;padding:.8rem;border:1px solid var(--rule)}th{background:var(--indigo);color:var(--surface)}@media(max-width:700px){header{grid-template-columns:1fr}table{display:block;overflow-x:auto}}@media print{body{background:white}main{padding:15mm}header{break-after:page}}
"""


def _render_blocks(blocks: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for block in blocks:
        kind = block["type"]
        if kind == "heading":
            level = min(max(int(block.get("level", 2)), 2), 3)
            parts.append(f"<h{level}>{_e(block['text'])}</h{level}>")
        elif kind == "list":
            parts.append("<ul>" + "".join(f"<li>{_e(item)}</li>" for item in block["items"]) + "</ul>")
        else:
            parts.append(f"<p>{_e(block['text'])}</p>")
    return "".join(parts)


def build_content_html(data: dict[str, Any], asset: dict[str, Any], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = "".join(
        f"<tr><td>{_e(claim['claim'])}</td><td>{_e(', '.join(claim['evidence_ids']))}</td><td>{claim['confidence']:.0%}</td><td>{_e(claim['validation'])}</td></tr>"
        for claim in asset.get("claims", [])
    )
    source_ids = {value for claim in asset.get("claims", []) for value in claim["evidence_ids"]}
    sources = "".join(
        f"<tr><td>{_e(source['id'])}</td><td>{_e(source['label'])}</td><td>{_e(source['captured_at'])}</td><td>{_e(source['scope'])}</td></tr>"
        for source in data.get("sources", [])
        if source["id"] in source_ids
    )
    document = f"""<!doctype html><html lang="en-AU"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(asset['title'])}</title><style>{CONTENT_CSS}</style></head><body><main><header><div><p class="eyebrow">Traffic Radius · {_e(asset['asset_type'])}</p><h1>{_e(asset['headline'])}</h1><p class="lede">{_e(asset['summary'])}</p></div><dl class="meta"><div><dt>Target</dt><dd>{_e(asset['target_url'])}</dd></div><div><dt>Audience</dt><dd>{_e(asset['audience'])}</dd></div><div><dt>Intent</dt><dd>{_e(asset['intent'])}</dd></div><div><dt>Approval</dt><dd>{_e(asset['approval_state'])}</dd></div></dl></header><article class="article">{_render_blocks(asset.get('body', []))}</article><section class="ledger"><h2>Claim ledger</h2><table><thead><tr><th scope="col">Claim</th><th scope="col">Evidence</th><th scope="col">Confidence</th><th scope="col">Validation</th></tr></thead><tbody>{rows}</tbody></table><h2>Source ledger</h2><table><thead><tr><th scope="col">ID</th><th scope="col">Source</th><th scope="col">Captured</th><th scope="col">Scope</th></tr></thead><tbody>{sources}</tbody></table></section></main></body></html>"""
    output.write_text(document, encoding="utf-8", newline="\n")
    return output


def build_content_markdown(data: dict[str, Any], asset: dict[str, Any], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"title: {json.dumps(asset['title'], ensure_ascii=False)}",
        f"target_url: {json.dumps(asset['target_url'], ensure_ascii=False)}",
        f"intent: {json.dumps(asset['intent'], ensure_ascii=False)}",
        f"approval_state: {json.dumps(asset['approval_state'], ensure_ascii=False)}",
        f"evidence_as_of: {json.dumps(data['run']['evidence_as_of'], ensure_ascii=False)}",
        "---",
        "",
        f"# {asset['headline']}",
        "",
        asset["summary"],
        "",
    ]
    for block in asset.get("body", []):
        if block["type"] == "heading":
            lines.extend([f"{'#' * min(max(int(block.get('level', 2)), 2), 3)} {block['text']}", ""])
        elif block["type"] == "list":
            lines.extend([f"- {item}" for item in block["items"]])
            lines.append("")
        else:
            lines.extend([block["text"], ""])
    lines.extend(["## Claim ledger", "", "| Claim | Evidence | Confidence | Validation |", "|---|---|---:|---|"])
    for claim in asset.get("claims", []):
        clean = claim["claim"].replace("|", "\\|")
        lines.append(f"| {clean} | {', '.join(claim['evidence_ids'])} | {claim['confidence']:.0%} | {claim['validation']} |")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return output


def content_filename(asset: dict[str, Any]) -> str:
    return f"{asset['id']}_{_slug(asset['title'])}"

