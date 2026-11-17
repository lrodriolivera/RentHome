"""Extracción genérica de datos de una ficha de inmueble.

Las webs de inmobiliarias usan plantillas muy distintas, así que en lugar de
selectores frágiles por web usamos heurísticas en español/inglés sobre el
texto de la ficha, empezando a leer desde el título del anuncio y cortando en
los bloques de "propiedades similares". Cada fuente puede afinar con
selectores CSS opcionales en config.yaml.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Optional

from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# utilidades de texto
# ---------------------------------------------------------------------------

def norm(s: str) -> str:
    """minúsculas sin acentos, espacios colapsados"""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


# Marcadores a partir de los cuales la página habla de OTROS inmuebles
CUT_MARKERS = [
    "propiedades similares", "inmuebles similares", "similar properties",
    "related properties", "propiedades relacionadas", "tambien te puede interesar",
    "te puede interesar", "you may also like", "compare properties",
    "otras propiedades", "other properties", "mas propiedades", "inmuebles relacionados",
    "featured properties", "propiedades destacadas", "ultimas propiedades", "latest properties",
    "recently viewed", "vistos recientemente", "opciones similares", "anuncios similares",
    "similar listings", "more properties", "otros inmuebles", "ver mas inmuebles",
]

# El precio "no disponible" en estas webs significa que ya está alquilado
PRICE_UNAVAILABLE_RE = re.compile(
    r"(precio|price)\s*:?\s*\|?\s*(no disponible|not available|alquilado|rented)", re.I)


# ---------------------------------------------------------------------------
# precio
# ---------------------------------------------------------------------------
_NUM = r"(\d{1,3}(?:[.,  ]\d{3})+|\d{3,7})(?:[.,]\d{1,2})?"
PRICE_RE = re.compile(
    rf"(?:€|eur\b|euros?\b)\s*{_NUM}|{_NUM}\s*(?:\|\s*)?(?:€|eur\b|euros?\b)", re.I
)
MONTHLY_AFTER = re.compile(r"^\s*(?:€|eur|euros?)?\s*(?:/|al|por|per|a|\|)?\s*(?:mes|month|mensual|monthly|pm\b|p\.m\.|mo\b)", re.I)
PRICE_LABEL_BEFORE = re.compile(r"(alquiler mensual|precio|price|rent|renta|alquiler|mensualidad)[^\d€]{0,25}$", re.I)
EXCLUDE_BEFORE = re.compile(
    r"(fianza|deposito|deposit|garantia|guarantee|parking|garaje|plaza|comunidad|gastos|"
    r"honorarios|comision|fee|agency|agencia|iva|suministros|luz|agua|electricidad|"
    r"winter|invierno|summer|verano|min(?:imo|imum)? price|max(?:imo|imum)? price|precio m[ai]x|precio min)[^\d€]{0,50}$",
    re.I,
)
ANNUAL_PRICE_RE = re.compile(
    rf"(?:annual|anual|anualmente|yearly|year[- ]round|all year|todo el ano|largo plazo|long[- ]term|larga duracion)"
    rf"[^€\d]{{0,45}}(?:(?:€|eur)\s*{_NUM}|{_NUM}\s*(?:€|eur|euros))",
    re.I,
)


def _to_int(num: str) -> Optional[int]:
    digits = re.sub(r"[^\d]", "", num)
    return int(digits) if digits else None


def _amounts(text: str):
    for m in PRICE_RE.finditer(text):
        raw = m.group(1) or m.group(2)
        val = _to_int(raw)
        if val is None:
            continue
        yield m.start(), m.end(), val


def find_price(text: str) -> tuple[Optional[int], Optional[int]]:
    """Devuelve (precio_mensual, precio_anual_mencionado).

    Prioridad: importes marcados como mensuales o con etiqueta "precio",
    ignorando fianzas, parking, gastos y precios de temporada.
    """
    t = norm(text)
    candidates = []
    for start, end, val in _amounts(t):
        before = re.sub(r"(sin|without|excl\.?|excluding|\+)\s*(gastos|suministros|utilities|bills)", " ",
                        t[max(0, start - 45):start])
        after = t[end:end + 25]
        if EXCLUDE_BEFORE.search(before):
            continue
        score = 0
        if MONTHLY_AFTER.search(after):
            score += 2
        if PRICE_LABEL_BEFORE.search(before):
            score += 1
        candidates.append((score, start, val))
    price = None
    if candidates:
        best = max(c[0] for c in candidates)
        # el primer importe con la mejor puntuación
        price = next(c[2] for c in candidates if c[0] == best)

    annual = None
    m = ANNUAL_PRICE_RE.search(t)
    if m:
        annual = _to_int(m.group(1) or m.group(2))
        if annual and not (300 <= annual <= 50000):
            annual = None
    return price, annual


# ---------------------------------------------------------------------------
# habitaciones
# ---------------------------------------------------------------------------
_WORDNUM = {
    "un": 1, "una": 1, "uno": 1, "one": 1, "single": 1,
    "dos": 2, "two": 2, "double": None,
    "tres": 3, "three": 3, "cuatro": 4, "four": 4, "cinco": 5, "five": 5,
    "seis": 6, "six": 6,
}
_BED = r"(?:dormitorios?|habitaciones?|hab\.?|dorm\.?|bedrooms?|beds?|bed\b|br\b|chambres?|schlafzimmer|rooms?\b)"
BED_NUM_BEFORE = re.compile(rf"(?<![\d.,])\b(\d{{1,2}})\s*(?:\|\s*)?[- ]?\s*{_BED}", re.I)
BED_WORD_BEFORE = re.compile(rf"\b({'|'.join(k for k in _WORDNUM)})[- ]{_BED}", re.I)
BED_LABEL_AFTER = re.compile(
    r"(?:cantidad (?:de )?dormitorios|cantidad (?:de )?habitaciones|n[ºo°]? (?:de )?(?:dormitorios|habitaciones)|"
    r"dormitorios|habitaciones|bedrooms|beds)\s*[:|]?\s*(\d{1,2})\b",
    re.I,
)
STUDIO_RE = re.compile(r"\b(estudio|studio|loft)\b", re.I)


def find_bedrooms(title: str, text: str) -> Optional[int]:
    nt = norm(title)
    for rx in (BED_NUM_BEFORE, BED_WORD_BEFORE):
        m = rx.search(nt)
        if m:
            v = m.group(1)
            n = int(v) if v.isdigit() else _WORDNUM.get(v.lower())
            if n is not None and 0 <= n <= 20:
                return n
    if STUDIO_RE.search(nt):
        return 0
    t = norm(text)
    hits = []
    for rx in (BED_LABEL_AFTER, BED_NUM_BEFORE, BED_WORD_BEFORE):
        m = rx.search(t)
        if m:
            v = m.group(1)
            n = int(v) if v.isdigit() else _WORDNUM.get(v.lower())
            if n is not None and 0 <= n <= 20:
                hits.append((m.start(), n))
    if hits:
        return min(hits)[1]
    if STUDIO_RE.search(t[:300]):
        return 0
    return None


# ---------------------------------------------------------------------------
# tipo de alquiler (anual vs temporada)
# ---------------------------------------------------------------------------
POSITIVE = [
    "alquiler anual", "anual", "anualmente", "annual", "annually", "yearly", "year round", "year-round",
    "todo el ano", "larga duracion", "larga temporada", "largo plazo", "long term", "long-term",
    "longterm", "long stay", "contrato de vivienda habitual", "vivienda habitual", "permanent home",
    "residencia habitual", "lloguer anual",
]
STRONG_NEGATIVE = [
    "no anual", "no se alquila anual", "not an annual", "not annual", "no es anual",
    "solo invierno", "only winter", "solo verano", "only summer", "only for the season",
    "solo temporada", "no larga duracion",
]
NEGATIVE = [
    "temporada", "temporal", "seasonal", "season", "invierno", "winter", "verano", "summer",
    "corto plazo", "short term", "short-term", "meses sueltos", "por meses", "vacacional",
    "holiday", "vacation", "turistico", "tourist", "septiembre a junio", "september to june",
    "sept-june", "septiembre-junio", "de octubre a", "from october to", "hasta mayo", "until may",
    "months rental only", "meses de alquiler", "por semanas", "weekly", "nightly",
]


def _has(t: str, words) -> list[str]:
    found = []
    for w in words:
        if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", t):
            found.append(w)
    return found


def classify_term(title: str, body_start: str) -> tuple[str, list[str]]:
    """'long' | 'seasonal' | 'mixed' | 'unknown' y las palabras clave encontradas."""
    out = []
    for zone in (norm(title), norm(body_start)):
        strong = _has(zone, STRONG_NEGATIVE)
        if strong:
            return "seasonal", strong
        pos = _has(zone, POSITIVE)
        # "larga temporada" contiene "temporada": quitar frases positivas antes
        z2 = zone
        for p in pos:
            z2 = z2.replace(p, " ")
        neg = _has(z2, NEGATIVE)
        if pos and not neg:
            return "long", pos
        if neg and not pos:
            return "seasonal", neg
        if pos and neg:
            out = pos + neg
            return "mixed", out
    return "unknown", out


# ---------------------------------------------------------------------------
# estado (alquilado/reservado) y operación (venta/alquiler)
# ---------------------------------------------------------------------------
RENTED_RE = re.compile(
    r"\b(alquilad[oa]|reservad[oa]|rented(?!\s+(?:only|to|for|out|furnished|unfurnished|as))|"
    r"let agreed|under offer|not available|no disponible|ya no disponible|vermietet|reserviert|"
    r"under reservation|en reserva)\b",
    re.I,
)
SALE_RE = re.compile(r"\b(en venta|for sale|se vende|venta|precio de venta|sale price)\b", re.I)


# ---------------------------------------------------------------------------
# localización
# ---------------------------------------------------------------------------
PLACES = [
    # (canónico, variantes normalizadas)
    ("Santa Eulària", ["santa eularia", "santa eulalia", "sta eulalia", "sta. eulalia", "santa eulària"]),
    ("Sant Antoni", ["sant antoni", "san antonio", "san antoni"]),
    ("Sant Josep", ["sant josep", "san jose", "san josep"]),
    ("Sant Joan", ["sant joan", "san juan"]),
    ("Sant Carles", ["sant carles", "san carlos"]),
    ("Santa Gertrudis", ["santa gertrudis"]),
    ("Sant Rafel", ["sant rafel", "san rafael"]),
    ("Sant Jordi", ["sant jordi", "san jorge"]),
    ("Sant Llorenç", ["sant llorenc", "san lorenzo"]),
    ("Sant Miquel", ["sant miquel", "san miguel"]),
    ("Sant Agustí", ["sant agusti", "san agustin"]),
    ("Sant Mateu", ["sant mateu", "san mateo"]),
    ("Santa Agnès", ["santa agnes", "santa ines"]),
    ("Jesús", ["jesus"]),
    ("Puig d'en Valls", ["puig d'en valls", "puig den valls", "puig d en valls"]),
    ("Talamanca", ["talamanca"]),
    ("Marina Botafoch", ["botafoch", "botafoc", "las boas"]),
    ("Playa d'en Bossa", ["playa d'en bossa", "platja d'en bossa", "playa den bossa", "bossa"]),
    ("Figueretes", ["figueretas", "figueretes"]),
    ("Dalt Vila", ["dalt vila"]),
    ("Can Misses", ["can misses"]),
    ("Es Vivé", ["es vive"]),
    ("Ses Figueres", ["ses figueres"]),
    ("Es Canar", ["es cana", "es canar"]),
    ("Cala Llonga", ["cala llonga"]),
    ("Cala Pada", ["cala pada"]),
    ("Siesta", ["siesta"]),
    ("Roca Llisa", ["roca llisa"]),
    ("Cala de Bou", ["cala de bou", "port des torrent"]),
    ("Cala Vadella", ["cala vadella"]),
    ("Cala Tarida", ["cala tarida"]),
    ("Es Cubells", ["es cubells", "cubells"]),
    ("Cala Gració", ["cala gracio", "cala gracion"]),
    ("Portinatx", ["portinatx"]),
    ("Sant Vicent", ["sant vicent", "san vicente"]),
    ("Buscastell", ["buscastell"]),
    ("Es Figueral", ["es figueral"]),
    ("Cala Conta", ["cala conta", "cala comte"]),
    ("Eivissa", ["eivissa", "ibiza town", "ibiza ciudad", "ibiza centro", "ciudad de ibiza", "vara de rey", "ses dos illes", "illa plana", "marina ibiza", "la marina"]),
]


def find_location(title: str, text: str, url: str) -> Optional[str]:
    for zone in (norm(title), norm(url.replace("-", " ")), norm(text[:1500])):
        best = None
        for canon, variants in PLACES:
            for v in variants:
                m = re.search(rf"(?<![a-z]){re.escape(v)}(?![a-z])", zone)
                if m and (best is None or m.start() < best[0]):
                    best = (m.start(), canon)
        if best:
            return best[1]
    return None


# ---------------------------------------------------------------------------
# resultado
# ---------------------------------------------------------------------------
@dataclass
class Listing:
    url: str
    source: str
    title: str = ""
    price: Optional[int] = None
    annual_price: Optional[int] = None
    bedrooms: Optional[int] = None
    location: Optional[str] = None
    term: str = "unknown"
    term_keywords: list = field(default_factory=list)
    status: str = "available"
    operation: str = "rent"
    image: Optional[str] = None
    snippet: str = ""

    def to_dict(self):
        return asdict(self)


def _clean_soup(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header",
                     "form", "select", "option", "iframe", "button"]):
        tag.decompose()
    return soup


def _meta(soup: BeautifulSoup, prop: str) -> Optional[str]:
    t = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
    return t.get("content").strip() if t and t.get("content") else None


def _jsonld(html: str) -> dict:
    """Busca datos de inmueble en JSON-LD (si la web lo publica)."""
    out = {}
    soup = BeautifulSoup(html, "lxml")
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for it in items:
            if not isinstance(it, dict):
                continue
            offers = it.get("offers")
            if isinstance(offers, dict) and offers.get("price") and "price" not in out:
                try:
                    out["price"] = int(float(str(offers["price"]).replace(",", "")))
                except ValueError:
                    pass
            for k in ("numberOfBedrooms", "numberOfRooms"):
                if it.get(k) and "bedrooms" not in out:
                    try:
                        out["bedrooms"] = int(float(str(it[k])))
                    except ValueError:
                        pass
    return out


def _clean_title(t: str) -> str:
    t = re.sub(r"\s+", " ", t or "").strip()
    # quitar sufijos " | Agencia" / " - Agencia"
    t = re.split(r"\s+[|–-]\s+(?=[^|–-]*$)", t)[0] if len(t) > 40 and re.search(r"\s[|–-]\s", t) else t
    return t.strip(" |-–")


def extract(html: str, url: str, source: str, selectors: Optional[dict] = None) -> Listing:
    return extract_with_text(html, url, source, selectors)[0]


def extract_with_text(html: str, url: str, source: str, selectors: Optional[dict] = None) -> tuple[Listing, str]:
    """Como `extract`, y además devuelve el texto normalizado de la ficha (sin el bloque de similares)."""
    selectors = selectors or {}
    raw_soup = BeautifulSoup(html, "lxml")
    og_title = _meta(raw_soup, "og:title")
    image = _meta(raw_soup, "og:image")
    jl = _jsonld(html)

    soup = _clean_soup(html)

    # ---- título
    title = ""
    if selectors.get("title"):
        el = soup.select_one(selectors["title"])
        title = el.get_text(" ", strip=True) if el else ""
    if not title:
        h1 = soup.find("h1")
        h1t = h1.get_text(" ", strip=True) if h1 else ""
        # algunos h1 son genéricos ("Inmueble", "Ficha")
        if h1t and len(h1t) > 12:
            title = h1t
    if not title and og_title:
        title = og_title
    if not title:
        h2 = soup.find("h2")
        title = h2.get_text(" ", strip=True) if h2 else ""
    if not title and raw_soup.title:
        title = raw_soup.title.get_text(" ", strip=True)
    title = _clean_title(title)

    # ---- texto principal
    body_el = soup.select_one(selectors["body"]) if selectors.get("body") else None
    text = (body_el or soup.body or soup).get_text(" | ", strip=True)
    ntext = norm(text)
    # empezar a leer en el título del anuncio
    nt = norm(title)[:40]
    start = ntext.find(nt) if nt else -1
    if start > 0:
        # algunas webs ponen el precio justo antes del título
        text_main = ntext[max(0, start - 250):]
    else:
        text_main = ntext
    # cortar en bloques de "similares"
    cut = len(text_main)
    for mk in CUT_MARKERS:
        i = text_main.find(mk, 200)
        if i != -1:
            cut = min(cut, i)
    text_main = text_main[:cut]

    # ---- precio
    price = annual = None
    if selectors.get("price"):
        el = soup.select_one(selectors["price"])
        if el:
            price, annual = find_price(el.get_text(" ", strip=True))
    if price is None:
        price, annual2 = find_price(text_main[:6000])
        annual = annual or annual2
    if price is None and jl.get("price"):
        price = jl["price"]

    # ---- habitaciones
    beds = None
    if selectors.get("bedrooms"):
        el = soup.select_one(selectors["bedrooms"])
        if el:
            m = re.search(r"\d+", el.get_text())
            beds = int(m.group()) if m else None
    if beds is None:
        beds = find_bedrooms(title, text_main[:6000])
    if beds is None and jl.get("bedrooms") is not None:
        beds = jl["bedrooms"]

    # ---- tipo de alquiler
    term, kws = classify_term(title + " " + norm(url.split("/")[-1].replace("-", " ")), text_main[:1500])

    # ---- estado
    status = "available"
    status_zone = norm(title) + " " + text_main[:400]
    m = RENTED_RE.search(status_zone)
    if PRICE_UNAVAILABLE_RE.search(text_main[:3000]):
        status = "rented"
    elif m:
        w = m.group(1).lower()
        status = "reserved" if w.startswith(("reserv", "under", "en reserva")) else "rented"

    # ---- operación
    operation = "rent"
    if price and price > 30000:
        operation = "sale"
    elif SALE_RE.search(norm(title)) and not re.search(r"alquiler|rent|lloguer", norm(title)):
        operation = "sale"

    location = find_location(title, text_main, url)
    snippet = text_main[len(nt):len(nt) + 280].strip(" |") if nt else text_main[:280]

    return Listing(
        url=url, source=source, title=title[:200], price=price, annual_price=annual,
        bedrooms=beds, location=location, term=term, term_keywords=kws,
        status=status, operation=operation, image=image, snippet=snippet,
    ), text_main
