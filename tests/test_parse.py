from renthome.parse import find_price, find_bedrooms, classify_term, extract
from renthome.filters import evaluate

CRIT = {"max_price": 2000, "preferred_bedrooms": 2, "min_bedrooms": 1,
        "include_unknown_price": True, "include_unknown_bedrooms": True,
        "exclude_types": ["parking", "local comercial", "trastero"]}


def test_price_formats():
    assert find_price("Alquiler mensual (sin gastos): 2.300 € Superficie 90 m²")[0] == 2300
    assert find_price("For Rent, Long term rentals | €1,200 Monthly")[0] == 1200
    assert find_price("Precio: 1.800 € mensuales | Dormitorios 2")[0] == 1800
    assert find_price("Direct available | €1,800 Per Month")[0] == 1800
    assert find_price("2,100 | €/month | Brand new apartment")[0] == 2100


def test_price_ignores_deposit_and_parking():
    p, _ = find_price("Fianza: 3.000 € | Precio 1.500 €/mes | parking 150 €")
    assert p == 1500


def test_annual_price_mentioned():
    _, annual = find_price("WINTER RENTAL at €2,000/month. AVAILABLE FOR ANNUAL RENTAL AT €2,500/month.")
    assert annual == 2500


def test_bedrooms():
    assert find_bedrooms("Elegante piso de 2 dormitorios en Santa Eulalia", "") == 2
    assert find_bedrooms("Apartment Bou Beach", "Bedrooms | 1 | Bathrooms | 1") == 1
    assert find_bedrooms("Piso con terraza", "Cantidad habitaciones: 3") == 3
    assert find_bedrooms("Annual rental studio in Cala Vadella", "") == 0
    assert find_bedrooms("Luminoso piso de dos habitaciones", "") == 2


def test_term():
    assert classify_term("ALQUILER ANUAL – piso en Santa Eulalia", "")[0] == "long"
    assert classify_term("Alquiler larga temporada / contrato anual", "")[0] == "long"
    assert classify_term("Alquiler de temporada 2027 piso primera línea", "")[0] == "seasonal"
    assert classify_term("Not an annual rental - seasonal duplex", "")[0] == "seasonal"
    assert classify_term("Piso en Talamanca", "NO SE ALQUILA ANUAL, SOLO INVIERNO")[0] == "seasonal"
    assert classify_term("Long term seasonal rental modern apartment", "")[0] == "mixed"
    assert classify_term("Piso en Ibiza", "bonito piso")[0] == "unknown"


HTML = """<html><head><meta property="og:title" content="Piso 2 dormitorios | Agencia"></head><body>
<nav>Alquiler temporada 2026</nav>
<h1>ALQUILER ANUAL – PISO DE 2 DORMITORIOS EN SANT ANTONI</h1>
<div>Alquiler mensual: 1.650 € | Fianza 2 meses | Superficie 70 m2</div>
<div>Propiedades similares</div><div>Villa 9.000 € temporada verano</div>
</body></html>"""


def test_extract_and_evaluate():
    d = extract(HTML, "https://x.com/piso", "Test").to_dict()
    assert d["price"] == 1650
    assert d["bedrooms"] == 2
    assert d["term"] == "long"
    assert d["location"] == "Sant Antoni"
    ok, notes, score = evaluate(d, CRIT, long_term_section=False)
    assert ok and score >= 100


def test_evaluate_rejects():
    base = {"title": "Piso", "url": "u", "price": 2500, "bedrooms": 2, "term": "long",
            "status": "available", "operation": "rent"}
    assert not evaluate(base, CRIT, True)[0]
    assert not evaluate({**base, "price": 1500, "status": "rented"}, CRIT, True)[0]
    assert not evaluate({**base, "price": 1500, "term": "seasonal"}, CRIT, True)[0]
    assert not evaluate({**base, "price": 500, "title": "Plaza de parking"}, CRIT, True)[0]
    assert evaluate({**base, "price": 1500, "term": "unknown"}, CRIT, True)[0]
    assert not evaluate({**base, "price": 1500, "term": "unknown"}, CRIT, False)[0]


def test_list_badges():
    import re
    from renthome.crawl import _badges
    html = """
    <div><a href="/p/1">Piso 1</a><div class="badge">Alquilado</div><a href="/p/1">ver</a></div>
    <div><a href="/p/2">Piso 2</a><img><a href="/p/2">ver</a></div>
    <div><a href="/p/3">Piso 3</a><span>Reservado</span><a href="/p/3">ver</a></div>
    """
    b = _badges(html, "https://x.com/", re.compile(r"/p/\d+$"))
    assert b == {"https://x.com/p/1": "rented", "https://x.com/p/3": "reserved"}
