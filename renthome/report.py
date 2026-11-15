"""Genera REPORT.md, docs/index.html (GitHub Pages) y el texto de aviso."""
from __future__ import annotations

import html
import json
from pathlib import Path

from .filters import effective_price

TERM_LABEL = {"long": "Anual", "mixed": "Anual o temporada", "unknown": "Sección anual"}


def _sorted_matches(store) -> list[dict]:
    items = [r for r in store.listings.values() if r.get("matches") and r.get("active")]
    items.sort(key=lambda r: (-r.get("score", 0), effective_price(r) or 99999))
    return items


def _price(r) -> str:
    p = effective_price(r)
    return f"{p:,} €/mes".replace(",", ".") if p else "Consultar precio"


def _beds(r) -> str:
    b = r.get("bedrooms")
    if b is None:
        return "¿? hab."
    if b == 0:
        return "Estudio"
    return f"{b} hab."


def _md_row(r) -> str:
    t = r.get("title", "").replace("|", "/")[:90]
    loc = r.get("location") or "Ibiza"
    notes = "; ".join(r.get("notes", []))
    return f"| [{t}]({r['url']}) | {_price(r)} | {_beds(r)} | {loc} | {r['source']} | {r.get('first_matched') or r.get('first_seen')} | {notes} |"


def markdown(store, criteria, summary) -> str:
    items = _sorted_matches(store)
    new_ids = set(summary.get("new", []))
    pref = criteria.get("preferred_bedrooms", 2)
    lines = [
        "# 🏠 RentHome Ibiza – alquiler anual en inmobiliarias locales",
        "",
        f"Última revisión: **{summary.get('date')}** · Criterios: alquiler anual, ≤ {criteria['max_price']} €/mes, "
        f"preferiblemente {pref} habitaciones · {len(items)} anuncios activos cumplen los criterios.",
        "",
    ]
    def table(rows):
        out = ["| Inmueble | Precio | Hab. | Zona | Inmobiliaria | Visto desde | Notas |",
               "|---|---|---|---|---|---|---|"]
        out += [_md_row(r) for r in rows]
        return out

    new = [r for r in items if r["id"] in new_ids]
    lines += [f"## 🆕 Nuevos en esta revisión ({len(new)})", ""]
    lines += table(new) if new else ["_No hay anuncios nuevos esta semana._"]
    lines.append("")
    groups = [
        (f"## ⭐ {pref} habitaciones", [r for r in items if r.get("bedrooms") == pref]),
        ("## Otras opciones", [r for r in items if r.get("bedrooms") not in (pref, None)]),
        ("## Habitaciones sin confirmar", [r for r in items if r.get("bedrooms") is None]),
    ]
    for h, rows in groups:
        if rows:
            lines += [f"{h} ({len(rows)})", ""] + table(rows) + [""]
    drops = [store.listings[i] for i in summary.get("price_drops", []) if i in store.listings]
    if drops:
        lines += ["## 📉 Bajadas de precio", ""] + table(drops) + [""]
    gone = [store.listings[i] for i in summary.get("gone", []) if i in store.listings]
    if gone:
        lines += [f"## Ya no aparecen ({len(gone)})", ""] + [f"- ~~{r.get('title')}~~ ({r['source']})" for r in gone] + [""]
    errs = summary.get("errors") or []
    if errs:
        lines += ["## ⚠️ Avisos del rastreador", ""] + [f"- {e}" for e in errs] + [""]
    lines += ["---", "Generado automáticamente por RentHome Ibiza. Confirma siempre con la inmobiliaria el tipo de contrato y la disponibilidad."]
    return "\n".join(lines)


def notification(store, criteria, summary) -> str:
    """Cuerpo del aviso (issue de GitHub / email) con solo lo nuevo."""
    new = [store.listings[i] for i in summary.get("new", []) if i in store.listings]
    drops = [store.listings[i] for i in summary.get("price_drops", []) if i in store.listings]
    new.sort(key=lambda r: (-r.get("score", 0), effective_price(r) or 99999))
    lines = [f"Revisión del {summary.get('date')}: **{len(new)} anuncio(s) nuevo(s)** que cumplen los criterios "
             f"(anual, ≤ {criteria['max_price']} €/mes).", ""]
    for r in new:
        lines.append(f"- **{_price(r)} · {_beds(r)} · {r.get('location') or 'Ibiza'}** – [{r.get('title')}]({r['url']}) _({r['source']})_")
        if r.get("notes"):
            lines.append(f"  - {'; '.join(r['notes'])}")
    if drops:
        lines += ["", "Bajadas de precio:"]
        lines += [f"- {_price(r)} – [{r.get('title')}]({r['url']}) _({r['source']})_" for r in drops]
    lines += ["", "Listado completo: ver `REPORT.md` o la página del informe."]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
CSS = """
:root{--bg:#f6f4ef;--card:#fff;--ink:#1d1b18;--muted:#6b665e;--line:#e4dfd5;--accent:#0f6e6e;--accent-ink:#fff;--new:#c2410c;--warn:#a16207;--chip:#efebe3}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#151412;--card:#1e1c19;--ink:#efece6;--muted:#a39d93;--line:#34302a;--accent:#3fb6b0;--accent-ink:#0b1514;--new:#fb923c;--warn:#facc15;--chip:#2a2722}}
:root[data-theme="dark"]{--bg:#151412;--card:#1e1c19;--ink:#efece6;--muted:#a39d93;--line:#34302a;--accent:#3fb6b0;--accent-ink:#0b1514;--new:#fb923c;--warn:#facc15;--chip:#2a2722}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:26px;margin:0 0 4px}.sub{color:var(--muted);margin:0 0 20px}
.stats{display:flex;gap:12px;flex-wrap:wrap;margin:0 0 20px}.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.stat b{display:block;font-size:22px}.stat span{color:var(--muted);font-size:13px}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 18px;align-items:center}
.bar button,.bar select{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:6px 12px;font:inherit;cursor:pointer}
.bar button[aria-pressed="true"]{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;display:flex;flex-direction:column}
.card img{width:100%;height:170px;object-fit:cover;background:var(--chip)}
.noimg{height:8px;background:var(--accent)}
.body{padding:12px 14px;display:flex;flex-direction:column;gap:6px;flex:1}
.price{font-size:20px;font-weight:700}.title{font-weight:600;line-height:1.3}
.title a{color:inherit;text-decoration:none}.title a:hover{text-decoration:underline}
.chips{display:flex;gap:6px;flex-wrap:wrap}.chip{background:var(--chip);border-radius:999px;padding:2px 9px;font-size:12.5px}
.chip.new{background:var(--new);color:#fff}.chip.pref{background:var(--accent);color:var(--accent-ink)}
.note{color:var(--warn);font-size:13px}.meta{color:var(--muted);font-size:12.5px;margin-top:auto}
.snip{color:var(--muted);font-size:13px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
h2{font-size:18px;margin:28px 0 10px}.empty{color:var(--muted)}
details{margin-top:28px;color:var(--muted)}footer{margin-top:40px;color:var(--muted);font-size:13px}
"""

JS = """
const state={beds:'all',onlyNew:false,src:'all'};
function apply(){document.querySelectorAll('.card').forEach(c=>{let ok=true;
 if(state.beds==='pref'&&c.dataset.pref!=='1')ok=false;
 if(state.onlyNew&&c.dataset.new!=='1')ok=false;
 if(state.src!=='all'&&c.dataset.src!==state.src)ok=false;
 c.style.display=ok?'':'none';});
 document.getElementById('count').textContent=[...document.querySelectorAll('.card')].filter(c=>c.style.display!=='none').length;}
document.querySelectorAll('[data-beds]').forEach(b=>b.onclick=()=>{state.beds=b.dataset.beds;document.querySelectorAll('[data-beds]').forEach(x=>x.setAttribute('aria-pressed',x===b));apply();});
document.getElementById('onlynew').onclick=e=>{state.onlyNew=!state.onlyNew;e.target.setAttribute('aria-pressed',state.onlyNew);apply();};
document.getElementById('src').onchange=e=>{state.src=e.target.value;apply();};
apply();
"""


def _card(r, new_ids, pref) -> str:
    e = html.escape
    is_new = r["id"] in new_ids
    chips = []
    if is_new:
        chips.append('<span class="chip new">NUEVO</span>')
    chips.append(f'<span class="chip{" pref" if r.get("bedrooms") == pref else ""}">{e(_beds(r))}</span>')
    chips.append(f'<span class="chip">{e(r.get("location") or "Ibiza")}</span>')
    chips.append(f'<span class="chip">{e(TERM_LABEL.get(r.get("term"), "Anual"))}</span>')
    img = f'<img loading="lazy" src="{e(r["image"])}" alt="">' if r.get("image") else '<div class="noimg"></div>'
    notes = "".join(f'<div class="note">{e(n)}</div>' for n in r.get("notes", []))
    return f"""<article class="card" data-pref="{1 if r.get('bedrooms') == pref else 0}" data-new="{1 if is_new else 0}" data-src="{e(r['source'])}">
{img}<div class="body"><div class="price">{e(_price(r))}</div>
<div class="title"><a href="{e(r['url'])}" target="_blank" rel="noopener">{e(r.get('title') or r['url'])}</a></div>
<div class="chips">{''.join(chips)}</div>{notes}
<div class="snip">{e(r.get('snippet', ''))}</div>
<div class="meta">{e(r['source'])} · visto desde {e(r.get('first_matched') or r.get('first_seen') or '')}</div></div></article>"""


def html_report(store, criteria, sources, summary) -> str:
    e = html.escape
    items = _sorted_matches(store)
    new_ids = set(summary.get("new", []))
    pref = criteria.get("preferred_bedrooms", 2)
    n_pref = sum(1 for r in items if r.get("bedrooms") == pref)
    srcs = sorted({r["source"] for r in items})
    opts = "".join(f'<option value="{e(s)}">{e(s)}</option>' for s in srcs)
    cards = "\n".join(_card(r, new_ids, pref) for r in items) or '<p class="empty">Ningún anuncio cumple ahora mismo los criterios.</p>'
    agencies = "".join(f'<li><a href="{e(s.get("website", ""))}" target="_blank" rel="noopener">{e(s["name"])}</a> · {e(s.get("town", ""))}</li>' for s in sources)
    errs = "".join(f"<li>{e(x)}</li>" for x in summary.get("errors") or [])
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RentHome Ibiza</title><style>{CSS}</style></head><body><div class="wrap">
<h1>🏠 RentHome Ibiza</h1>
<p class="sub">Alquiler anual en inmobiliarias locales · ≤ {criteria['max_price']} €/mes · preferible {pref} habitaciones · revisado el {e(str(summary.get('date')))}</p>
<div class="stats"><div class="stat"><b>{len(items)}</b><span>cumplen criterios</span></div>
<div class="stat"><b>{n_pref}</b><span>con {pref} habitaciones</span></div>
<div class="stat"><b>{len(new_ids)}</b><span>nuevos esta semana</span></div>
<div class="stat"><b>{len(summary.get('sources_ok', []))}/{summary.get('sources_total', len(sources))}</b><span>webs revisadas</span></div></div>
<div class="bar"><button data-beds="all" aria-pressed="true">Todos</button><button data-beds="pref" aria-pressed="false">Solo {pref} hab.</button>
<button id="onlynew" aria-pressed="false">Solo nuevos</button><select id="src" aria-label="Inmobiliaria"><option value="all">Todas las inmobiliarias</option>{opts}</select>
<span class="sub" style="margin:0">· <b id="count">{len(items)}</b> visibles</span></div>
<div class="grid">{cards}</div>
<details><summary>Inmobiliarias consultadas ({len(sources)})</summary><ul>{agencies}</ul></details>
{f'<details open><summary>Avisos del rastreador</summary><ul>{errs}</ul></details>' if errs else ''}
<footer>Generado automáticamente cada semana. Los datos se extraen de las webs de cada inmobiliaria: confirma siempre precio, tipo de contrato y disponibilidad antes de visitar.</footer>
</div><script>{JS}</script></body></html>"""


def write_reports(store, criteria, sources, summary, root: Path):
    (root / "REPORT.md").write_text(markdown(store, criteria, summary), encoding="utf-8")
    docs = root / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "index.html").write_text(html_report(store, criteria, sources, summary), encoding="utf-8")
    (root / "data" / "notification.md").write_text(notification(store, criteria, summary), encoding="utf-8")
    (root / "data" / "matches.json").write_text(
        json.dumps(_sorted_matches(store), ensure_ascii=False, indent=1), encoding="utf-8")
