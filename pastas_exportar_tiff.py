"""O que falta exportar em TIFF (para virar master JXL) — varredura + checklist.

    py pastas_exportar_tiff.py            # varre e abre a página
    py pastas_exportar_tiff.py --no-web   # só grava o .md

Atalho: presets_LOCAL\\EXPORT - FALTA TIFF.bat. Só biblioteca padrão.

Varre as pastas de ano em ROOT (toda pasta de quatro dígitos a partir de
YEARS_FROM: um ano novo entra assim que a pasta dele existe), acha toda pasta
*EXPORT* (sem distinguir maiúsculas) e compara os TIFF dela com os JPG
exportados (por foto: o nome sem perfil/versão/câmera). Cada linha da página é uma pasta de câmera (a que
contém os *EXPORT*) com algo faltando, nestas categorias:

  1. tem _EXPORT, sem TIFF dentro
  2. tem _EXPORT com TIFF, mas algum JPG não tem o TIFF
  3. sem _EXPORT, só outro *EXPORT*, sem TIFF dentro
  4. sem _EXPORT, só outro *EXPORT*, com TIFF dentro (e algum JPG sem TIFF)
  5. tem _EXPORT e também outro *EXPORT*
  6. sem TIFF, mas todas as fotos já têm TIFF em outra pasta do mesmo evento
  7. só JPG, em _EXPORT\\JPG_only: sem TIFF possível, aceito (fica na lista,
     sem checkbox; um JPG do evento que também está num JPG_only não conta
     mais como faltando)

Clicar abre a pasta no Explorer; o check fica salvo em
Logs\\pastas_exportar_tiff.state.json (vale entre varreduras). Cada varredura
grava Logs\\<AAMMDD>_pastas_para_exportar_tiff.md. Ctrl+C no terminal encerra.
"""
import datetime
import html
import json
import os
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------------------------------------------------------------- settings
ROOT = "G:\\"
# the year folders scanned: every ROOT\<four digits> from this year on that
# exists, so a new year needs no edit here (a fixed list stopped at 2026)
YEARS_FROM = 2024
MAIN_EXPORT = "_EXPORT"
# fotos que não têm mais como virar TIFF (edição do C1 perdida): ficam só em
# JPG nesta subpasta do _EXPORT (ou de outro *EXPORT*), soltas ou em
# subpastas. Vão para a seção 7 (aceito) e cobrem o mesmo JPG em qualquer
# pasta do evento.
JPG_ONLY_FOLDER = "JPG_only"
# subpastas de um *EXPORT* que NÃO entram na comparação: cópias do toolkit,
# feitas a partir do próprio TIFF/master (nunca dizem se falta TIFF)
IGNORE_JPG_FOLDERS = ("JPG_mobile", "16B_JXL", "sRGB_JXL", "JXL")
# pastas-irmãs com este prefixo são UM export (uma pasta por receita)
EXPORT_FAMILIES = ("C1_Export_",)
# pastas onde nunca procurar *EXPORT*
SKIP_DIRS = ("CaptureOne", "NKSC_PARAM", "tiff_zip")
# palavras do nome que não identificam a foto (perfil, versão, câmera)
NOISE_WORD = re.compile(
    r"^(prophoto.*|adobe ?rgb.*|srgb.*|display ?p3.*|p3|rec2020.*|elle.*|"
    r"v\d+|web|check)$", re.I)
# genéricas (tem espaço; parece câmera, E4500/D810): só cortam quando o que já
# ficou tem um dígito, senão cortariam o próprio número da foto
# ("DSC_1234 1" -> "dsc"; "IMG_E1234" -> "img")
NOISE_GENERIC = re.compile(r"^(.* .*|[a-z]{1,2}\d{3,4})$", re.I)
PORT = 8765
SHOW_NAMES = 15          # quantos nomes mostrar por diferença
LOG_DIR = "Logs"         # ao lado deste script; env PHOTO_ORIGIN_TAG_LOG_DIR
# -------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
DUP_COUNTER = re.compile(r"^\d+ \(\d+\)$")
STATE_NAME = "pastas_exportar_tiff.state.json"
TIFF_EXT = (".tif", ".tiff")
JPG_EXT = (".jpg", ".jpeg")

CATEGORIES = {
    1: "Tem _EXPORT, sem TIFF dentro",
    2: "Tem _EXPORT com TIFF, mas algum JPG não tem o TIFF",
    3: "Sem _EXPORT — só outro *EXPORT*, sem TIFF",
    4: "Sem _EXPORT — só outro *EXPORT*, com TIFF (e algum JPG sem TIFF)",
    5: "Tem _EXPORT e também outro *EXPORT*",
    6: "Sem TIFF, mas as fotos já têm TIFF em outra pasta do evento",
    7: f"Só JPG, sem TIFF possível ({JPG_ONLY_FOLDER}) — aceito, não tem jeito",
}
ACCEPTED_CAT = 7


class RootNotFound(Exception):
    """No year folder (YEARS_FROM or later) in ROOT (drive not connected?)."""


def year_dirs():
    """The year folders in ROOT, oldest first: four-digit names from
    YEARS_FROM on."""
    try:
        names = os.listdir(ROOT)
    except OSError:
        return []
    return sorted(n for n in names
                  if re.fullmatch(r"\d{4}", n) and int(n) >= YEARS_FROM
                  and os.path.isdir(os.path.join(ROOT, n)))


_years = []             # the year folders of the last scan


def scope():
    """'G:\\2024–2026': what the page and the list cover."""
    if not _years:
        return ROOT
    return f"{ROOT}{_years[0]}" + (f"–{_years[-1]}" if len(_years) > 1 else "")


def photo_key(filename):
    """Radical do nome: tudo antes do primeiro perfil/versão/WEB/CHECK/câmera.
    DSC_3000_v1_CHECK_1 -> dsc_3000; DSC_0566_NXstudio_AdobeRGB_v1 ->
    dsc_0566_nxstudio. O 1º pedaço é sempre o nome (pode ter espaço:
    "P1010076 (2)", "WhatsApp Image ..."). Um pedaço "genérico" (com espaço,
    ou com cara de câmera) só corta depois de o nome já ter um dígito."""
    first, *rest = os.path.splitext(filename)[0].lower().lstrip("_").split("_")
    keep = [first]
    for t in rest:
        if NOISE_WORD.match(t):
            break
        # "0002 (2)" is the frame number + Windows' duplicate counter, part of
        # the name -- the "has a space" noise rule would cut every
        # DSC_xxxx (n) down to "dsc" and let one TIFF cover them all. For the
        # same reason a generic token never cuts while the name so far has no
        # digit: "DSC_1234 1" and "IMG_E1234" would collapse to "dsc"/"img"
        if (NOISE_GENERIC.match(t) and not DUP_COUNTER.match(t)
                and any(c.isdigit() for c in "".join(keep))):
            break
        keep.append(t)
    return "_".join(t for t in keep if t)


def is_export(name):
    return "export" in name.lower()


def scan_export(path):
    """TIFF keys + JPG keys per type folder of one *EXPORT* folder."""
    ignore = {n.lower() for n in IGNORE_JPG_FOLDERS}
    tiffs, jpgs = set(), {}
    for dirpath, dirs, files in os.walk(path):
        rel = os.path.relpath(dirpath, path)
        top = rel.split(os.sep)[0]
        if top.lower() in ignore:
            dirs[:] = []
            continue
        for f in files:
            low = f.lower()
            if low.endswith(TIFF_EXT):
                tiffs.add(photo_key(f))
            elif low.endswith(JPG_EXT):
                kind = os.path.basename(path).lower() if rel == "." else top.lower()
                jpgs.setdefault(kind, set()).add(photo_key(f))
    return tiffs, jpgs


def find_exports(year_dir, progress):
    skip = {n.lower() for n in SKIP_DIRS}
    found = []
    for dirpath, dirs, _files in os.walk(year_dir):
        progress(dirpath)
        keep = []
        for d in dirs:
            if is_export(d):
                found.append(os.path.join(dirpath, d))
            elif d.lower() not in skip and not d.startswith("."):
                keep.append(d)
        dirs[:] = keep
    return found


def scan():
    global _years
    t0, last = time.time(), [0.0]
    count = [0]

    def progress(_p):
        count[0] += 1
        if time.time() - last[0] > 2:
            last[0] = time.time()
            print(f"\r  varrendo... {count[0]} pastas, {time.time() - t0:.0f} s", end="", flush=True)

    years = year_dirs()
    if not years:
        # an empty scan would say "0 folders missing something" and overwrite
        # the day's list: a disk that is not there is an error, not a clean bill
        raise RootNotFound(f"no year folder ({YEARS_FROM} or later) in {ROOT} "
                           f"(is the drive connected?)")
    exports = []
    for y in years:
        exports += find_exports(os.path.join(ROOT, y), progress)
    _years = years
    print(f"\r  {count[0]} pastas, {len(exports)} *EXPORT*; lendo cada uma...", flush=True)

    # Folders of one export FAMILY (Capture One writes one folder per recipe:
    # C1_Export_1_TIFF, C1_Export_AdobeRGB, ...) are compared as one export.
    groups = {}
    for ex in exports:
        parent, name = os.path.split(ex)
        fam = next((f for f in EXPORT_FAMILIES if name.lower().startswith(f.lower())), None)
        groups.setdefault((parent, fam) if fam else (ex, None), []).append(ex)

    jpg_only = JPG_ONLY_FOLDER.lower()
    info = {}
    event_tiffs = {}
    event_jpg_only = {}     # JPG keys accepted as JPG-only (in a JPG_only)
    for i, ((anchor, fam), members) in enumerate(groups.items(), 1):
        if time.time() - last[0] > 2:
            last[0] = time.time()
            print(f"\r  {i}/{len(groups)} *EXPORT* lidas", end="", flush=True)
        tiffs, jpgs = set(), {}
        for ex in sorted(members, key=str.lower):
            t, j = scan_export(ex)
            tiffs |= t
            for kind, keys in j.items():
                jpgs.setdefault(kind, set()).update(keys)
        key = os.path.join(anchor, fam + "*") if fam else anchor
        rel = os.path.relpath(key, ROOT)
        event = os.sep.join(rel.split(os.sep)[:2])
        accepted = jpgs.pop(jpg_only, set())
        event_tiffs.setdefault(event, set()).update(tiffs)
        event_jpg_only.setdefault(event, set()).update(accepted)
        info[key] = {"rel": rel, "event": event, "tiffs": tiffs, "jpgs": jpgs,
                     "accepted": accepted,
                     "members": sorted(members, key=str.lower)}

    by_parent = {}
    for ex, d in info.items():
        all_jpg = set().union(*d["jpgs"].values()) if d["jpgs"] else set()
        have_tiff = event_tiffs[d["event"]]
        covered = have_tiff | event_jpg_only[d["event"]]
        d["photos"] = len(all_jpg)
        d["problem"] = None
        d["lines"] = []
        # JPGs with no TIFF anywhere in the event, accepted as JPG-only
        d["jpg_only_n"] = len(d["accepted"] | (all_jpg - have_tiff))
        if not d["tiffs"] and all_jpg:
            if all_jpg <= have_tiff:
                d["problem"] = "sibling"
            elif all_jpg <= covered:
                d["problem"] = "accepted"
            else:
                d["problem"] = "notiff"
        elif d["tiffs"]:
            # TIFF without its JPG is fine; only a JPG without its TIFF counts,
            # and a TIFF anywhere in the event covers it (re-export, _OLD), as
            # does the same JPG in a JPG_only (no TIFF possible, accepted)
            for kind, keys in sorted(d["jpgs"].items()):
                extra_j = sorted(keys - covered)
                if extra_j:
                    d["problem"] = "mismatch"
                    d["lines"].append(f"`{kind}`: {len(keys)} fotos; "
                                      f"**{len(extra_j)} sem TIFF**: {_names(extra_j)}")
        if d["problem"] is None and d["jpg_only_n"]:
            d["problem"] = "accepted"
        by_parent.setdefault(os.path.dirname(ex), []).append(ex)

    rows = []
    for parent, exs in by_parent.items():
        ds = [info[e] for e in exs]
        if not any(d["problem"] for d in ds):
            continue
        main = [d for e, d in zip(exs, ds) if os.path.basename(e).lower() == MAIN_EXPORT.lower()]
        other = len(main) < len(ds)
        if all(d["problem"] in (None, "accepted") for d in ds):
            cat = ACCEPTED_CAT
        elif all(d["problem"] in (None, "sibling", "accepted") for d in ds):
            cat = 6
        elif main and other:
            cat = 5
        elif main:
            cat = 2 if main[0]["tiffs"] else 1
        else:
            cat = 4 if any(d["tiffs"] for d in ds) else 3
        rows.append({"key": os.path.relpath(parent, ROOT), "path": parent, "cat": cat,
                     "exports": sorted(({"path": e, **info[e]} for e in exs),
                                       key=lambda d: d["rel"].lower())})
    rows.sort(key=lambda r: r["key"].lower())
    print(f"\r  pronto em {time.time() - t0:.0f} s: {len(rows)} pastas com algo faltando"
          + " " * 20, flush=True)
    return rows


def _names(keys):
    more = f" … (+{len(keys) - SHOW_NAMES})" if len(keys) > SHOW_NAMES else ""
    return ", ".join(keys[:SHOW_NAMES]) + more


def export_status(d):
    if d["problem"] == "accepted":
        only = f"{d['jpg_only_n']} fotos só em JPG ({JPG_ONLY_FOLDER}, aceito)"
        if d["tiffs"]:
            return f"TIFF: {len(d['tiffs'])} fotos — ok; {only}"
        return f"sem TIFF — {only}"
    if d["problem"] == "notiff":
        return f"sem TIFF — {d['photos']} fotos"
    if d["problem"] == "sibling":
        return f"sem TIFF — {d['photos']} fotos, todas com TIFF em outra pasta do evento"
    if d["problem"] == "mismatch":
        return f"TIFF: {len(d['tiffs'])} fotos — falta TIFF de algum JPG"
    if d["tiffs"]:
        return f"TIFF: {len(d['tiffs'])} fotos — ok"
    return "sem TIFF nem JPG"


def write_md(rows):
    today = datetime.date.today()
    out = out_dir() / f"{today:%y%m%d}_pastas_para_exportar_tiff.md"
    lines = [f"# Pastas *EXPORT* em {scope()} (varredura de {today:%Y-%m-%d})", ""]
    for cat, title in CATEGORIES.items():
        mine = [r for r in rows if r["cat"] == cat]
        lines += [f"## {cat}. {title} ({len(mine)})", ""]
        for r in mine:
            lines.append(f"### `{r['key']}`")
            for d in r["exports"]:
                names = ", ".join(f"`{os.path.basename(m)}`" for m in d["members"])
                lines.append(f"- {names} — {export_status(d)}")
                lines += [f"  - {ln}" for ln in d["lines"] if d["problem"]]
            lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"  lista gravada em {out}")
    return out


# ------------------------------------------------------------------- web page
_rows = []
_allowed = set()
_scan_lock = threading.Lock()
_state_lock = threading.Lock()      # save_state: one writer at a time


def rescan():
    global _rows
    with _scan_lock:
        _rows = scan()
        write_md(_rows)


def out_dir():
    """Logs\\ next to this script (gitignored), or PHOTO_ORIGIN_TAG_LOG_DIR."""
    env = os.environ.get("PHOTO_ORIGIN_TAG_LOG_DIR")
    d = Path(env) if env else HERE / LOG_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_path():
    return out_dir() / STATE_NAME


def load_state():
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(data):
    """Write the checks atomically, one writer at a time: two requests at once
    shared the .tmp and one of them failed (PermissionError on Windows)."""
    state = state_path()
    tmp = state.with_suffix(".tmp")
    with _state_lock:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, state)


def inline(text):
    t = html.escape(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", t)


def link(path, text, cls="p"):
    _allowed.add(os.path.normcase(path))
    return (f'<a class="{cls}" href="#" data-open="{html.escape(path)}" '
            f'title="{html.escape(path)}">{html.escape(text)}</a>')


PAGE = r"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Exportar TIFF</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--fg:#1d1d1f;--mut:#6b6b70;--line:#e3e1dc;--acc:#2f6fdf;--ok:#2e8b57;--warn:#b5482e;--chip:#eeece7}
@media (prefers-color-scheme:dark){:root{--bg:#18181a;--card:#222225;--fg:#ececee;--mut:#9a9aa0;--line:#333338;--acc:#7aa7ff;--ok:#5fc48c;--warn:#ef8a6f;--chip:#2d2d31}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 "Segoe UI","Yu Gothic UI",system-ui,sans-serif}
header{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line);padding:12px 16px}
header h1{margin:0;font-size:18px}
.bar{display:flex;gap:16px;align-items:center;margin-top:8px;flex-wrap:wrap;color:var(--mut);font-size:13px}
.prog{flex:1;min-width:160px;height:6px;background:var(--line);border-radius:3px;overflow:hidden}
.prog i{display:block;height:100%;background:var(--ok);transition:width .2s}
button{font:inherit;font-size:13px;padding:3px 12px;border-radius:6px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}
nav{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
nav a{font-size:12.5px;padding:2px 10px;border-radius:10px;background:var(--chip);color:var(--fg);text-decoration:none}
main{max-width:1000px;margin:0 auto;padding:8px 16px 48px}
section{scroll-margin-top:130px}
h2{font-size:16px;margin:28px 0 8px;display:flex;justify-content:space-between;gap:8px}
h2 span{color:var(--mut);font-weight:400;font-size:13px;white-space:nowrap}
ul{list-style:none;margin:0;padding:0;background:var(--card);border:1px solid var(--line);border-radius:8px}
li{display:flex;gap:10px;padding:9px 12px;border-top:1px solid var(--line);align-items:flex-start;transition:opacity .2s}
li:first-child{border-top:0}
li>input{margin-top:4px;width:17px;height:17px;flex:none;cursor:pointer;accent-color:var(--ok)}
.body{min-width:0;flex:1}
a.p{color:var(--acc);text-decoration:none;word-break:break-all;cursor:pointer}
a.p:hover{text-decoration:underline}
.parent{font-weight:600}
.ex{margin:3px 0 0 14px;font-size:13.5px}
.ex.fine a.p{color:var(--mut)}
.tag{font-size:12px;padding:0 7px;border-radius:8px;margin-left:6px;background:var(--chip);color:var(--mut)}
.tag.bad{color:var(--warn)}
.tag.good{color:var(--ok)}
.note{color:var(--mut);font-size:12.5px;margin-left:14px;word-break:break-word}
code{font-family:Consolas,monospace;font-size:12.5px}
li.done{opacity:.45}
li.done a.p,li.done .note{text-decoration:line-through}
body.hide li.done{display:none}
.empty{color:var(--mut);font-size:13px;padding:10px 12px}
section.accepted{margin-top:40px;padding-top:8px;border-top:2px dashed var(--line)}
section.accepted ul{background:transparent;border-style:dashed}
li.acc .mark{flex:none;width:17px;text-align:center;color:var(--ok);margin-top:1px}
#toast{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:var(--fg);color:var(--bg);padding:6px 14px;border-radius:6px;font-size:13px;opacity:0;transition:opacity .2s;pointer-events:none}
</style></head><body>
<header><h1>O que falta exportar em TIFF — __SCOPE__</h1>
<div class="bar"><span id="count"></span><div class="prog"><i id="pbar"></i></div>
<label><input type="checkbox" id="hide"> esconder feitos</label>
<button id="rescan">Varrer de novo</button></div>
<nav>__NAV__</nav></header>
<main>__BODY__</main>
<div id="toast"></div>
<script>
let state = __STATE__;
const toast = (msg, ms) => { const t = document.getElementById('toast'); t.textContent = msg;
  t.style.opacity = 1; clearTimeout(t._h); t._h = setTimeout(() => t.style.opacity = 0, ms || 1600); };
function refresh() {
  const all = document.querySelectorAll('li[data-key]'); let done = 0;
  all.forEach(li => { const on = !!state[li.dataset.key]; li.classList.toggle('done', on);
    li.querySelector('input').checked = on; if (on) done++; });
  document.getElementById('count').textContent = done + ' / ' + all.length + ' feitas';
  document.getElementById('pbar').style.width = (all.length ? 100 * done / all.length : 0) + '%';
  document.querySelectorAll('section:not(.accepted)').forEach(sec => {
    const items = sec.querySelectorAll('li[data-key]'); const d = [...items].filter(li => state[li.dataset.key]).length;
    sec.querySelector('h2 span').textContent = d + ' / ' + items.length; });
}
// one save at a time, the newest state last: two quick clicks could otherwise
// arrive in the wrong order and the older state would be the one kept
let saving = false, again = false;
async function save() {
  if (saving) { again = true; return; }
  saving = true;
  try {
    do { again = false;
      const r = await fetch('/state', {method: 'POST', body: JSON.stringify(state)});
      if (!r.ok) throw 0; } while (again);
  } catch (e) { toast('Não salvou — o script ainda está rodando?'); }
  finally { saving = false; }
}
document.addEventListener('change', e => {
  if (e.target.id === 'hide') { document.body.classList.toggle('hide', e.target.checked);
    try { localStorage.setItem('hide', e.target.checked ? '1' : ''); } catch (_) {} return; }
  const li = e.target.closest('li[data-key]'); if (!li) return;
  if (e.target.checked) state[li.dataset.key] = new Date().toISOString(); else delete state[li.dataset.key];
  refresh(); save();
});
document.addEventListener('click', async e => {
  if (e.target.id === 'rescan') { toast('Varrendo o disco…', 600000); e.target.disabled = true;
    try { const r = await fetch('/rescan', {method: 'POST'});
      if (r.ok) { location.reload(); }
      else { toast('Disco não encontrado'); e.target.disabled = false; } }
    catch (_) { toast('O script não está rodando'); e.target.disabled = false; } return; }
  const a = e.target.closest('a.p'); if (!a) return; e.preventDefault();
  try { const r = await fetch('/open', {method: 'POST', body: JSON.stringify({path: a.dataset.open})});
    toast(r.ok ? 'Abrindo no Explorer…' : 'Pasta não encontrada'); }
  catch (_) { toast('O script não está rodando'); }
});
try { if (localStorage.getItem('hide')) { document.getElementById('hide').checked = true; document.body.classList.add('hide'); } } catch (_) {}
refresh();
</script></body></html>"""


def render(rows, state):
    _allowed.clear()
    nav, parts = [], []
    for cat, title in CATEGORIES.items():
        mine = [r for r in rows if r["cat"] == cat]
        nav.append(f'<a href="#c{cat}">{cat}. {len(mine)}</a>')
        accepted = cat == ACCEPTED_CAT
        if accepted:
            parts.append(f'<section id="c{cat}" class="accepted"><h2>{cat}. {html.escape(title)} '
                         f'({len(mine)})<span>sem checkbox: não tem o que fazer</span></h2><ul>')
        else:
            parts.append(f'<section id="c{cat}"><h2>{cat}. {html.escape(title)} '
                         f'({len(mine)})<span></span></h2><ul>')
        if not mine:
            parts.append('<div class="empty">nenhuma</div>')
        for r in mine:
            sub = []
            for d in r["exports"]:
                cls = {"notiff": "bad", "mismatch": "bad", None: "good",
                       "accepted": "good"}.get(d["problem"], "")
                notes = "".join(f'<div class="note">{inline(n)}</div>'
                                for n in d["lines"]) if d["problem"] else ""
                names = " · ".join(link(m, os.path.basename(m)) for m in d["members"])
                sub.append(f'<div class="ex{"" if d["problem"] else " fine"}">'
                           f'{names}'
                           f'<span class="tag {cls}">{html.escape(export_status(d))}</span>{notes}</div>')
            if accepted:
                parts.append(f'<li class="acc"><span class="mark">✓</span>'
                             f'<div class="body">{link(r["path"], r["key"], "p parent")}'
                             f'{"".join(sub)}</div></li>')
                continue
            parts.append(f'<li data-key="{html.escape(r["key"])}"><input type="checkbox">'
                         f'<div class="body">{link(r["path"], r["key"], "p parent")}'
                         f'{"".join(sub)}</div></li>')
        parts.append("</ul></section>")
    return (PAGE.replace("__SCOPE__", html.escape(scope())).replace("__NAV__", "".join(nav))
                .replace("__BODY__", "\n".join(parts))
                .replace("__STATE__", json.dumps(state).replace("</", "<\\/")))


LOCAL_HOSTS = ("127.0.0.1", "localhost")


def request_allowed(host, origin):
    """Only this machine's own page is served or may POST: Host must be
    127.0.0.1:PORT or localhost:PORT (a page that rebinds its own name to
    127.0.0.1 sends its name), and an Origin, if the browser sent one, must be
    http:// + one of those (a page from another site gets 403)."""
    ok = {f"{h}:{PORT}" for h in LOCAL_HOSTS}
    if host is None or host.lower() not in ok:
        return False
    if origin is None:
        return True
    return origin.lower() in {f"http://{h}" for h in ok}


class Server(ThreadingHTTPServer):
    # HTTPServer sets this to True; on Windows that lets a second instance
    # open the same port and the page then shows the data of either one
    allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8") or "null")

    def do_GET(self):
        # the page lists the folder names: never to another site (DNS rebinding)
        if not request_allowed(self.headers.get("Host"), self.headers.get("Origin")):
            return self._send(403)
        if self.path != "/":
            return self._send(404)
        with _scan_lock:
            body = render(_rows, load_state()).encode("utf-8")
        self._send(200, body, "text/html; charset=utf-8")

    def do_POST(self):
        if not request_allowed(self.headers.get("Host"), self.headers.get("Origin")):
            return self._send(403)
        try:
            self._post()
        except ValueError:       # bad Content-Length / not UTF-8 / not JSON
            self._send(400)

    def _post(self):
        if self.path == "/state":
            data = self._json()
            if not isinstance(data, dict):
                return self._send(400)
            save_state(data)
            return self._send(204)
        if self.path == "/rescan":
            try:
                rescan()
            except RootNotFound:
                return self._send(503)       # the previous rows stay on the page
            return self._send(204)
        if self.path == "/open":
            body = self._json()
            if not isinstance(body, dict):
                return self._send(400)
            wanted = body.get("path") or ""
            # only folders shown on the page, never an arbitrary path
            if os.path.normcase(wanted) in _allowed and os.path.isdir(wanted):
                os.startfile(wanted)
                return self._send(204)
            return self._send(404)
        self._send(404)


def main():
    web = "--no-web" not in sys.argv
    server = None
    if web:
        # the port first: a second instance must fail here, not after a scan
        try:
            server = Server(("127.0.0.1", PORT), Handler)
        except OSError as e:
            print(f"ERROR: port {PORT} in use (is the checklist already open?) [{e}]",
                  file=sys.stderr)
            return 2
    try:
        try:
            rescan()
        except RootNotFound as e:
            print(f"\nERROR: {e}", file=sys.stderr)
            return 2
        if not web:
            return 0
        url = f"http://127.0.0.1:{PORT}/"
        print(f"Checklist em {url}  (Ctrl+C encerra)")
        threading.Timer(0.5, webbrowser.open, (url,)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    finally:
        if server:
            server.server_close()


if __name__ == "__main__":
    sys.exit(main())
