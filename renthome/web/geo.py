"""Detección de zona (isla / Barcelona) y localidad a partir del texto de un anuncio."""
from __future__ import annotations

import re

from ..parse import PLACES as IBIZA_PLACES
from ..parse import norm

# zona → [(nombre a mostrar, [variantes normalizadas])]
_PLACES: dict[str, list[tuple[str, list[str]]]] = {
    "ibiza": [(name, variants) for name, variants in IBIZA_PLACES],
    "formentera": [
        ("Sant Francesc", ["sant francesc", "san francisco javier"]), ("Es Pujols", ["es pujols"]),
        ("La Savina", ["la savina", "la sabina"]), ("Sant Ferran", ["sant ferran", "san fernando de ses roques"]),
        ("Es Caló", ["es calo"]), ("La Mola", ["la mola", "el pilar de la mola"]),
        ("Formentera", ["formentera"]),
    ],
    "mallorca": [
        ("Palma", ["palma de mallorca", "palma", "santa catalina", "son armadams", "el terreno", "portixol",
                   "es molinar", "son espanyolet", "bonanova", "genova", "son vida", "son rapinya", "pere garau"]),
        ("Calvià", ["calvia", "santa ponsa", "santa ponca", "palmanova", "magaluf", "portals nous", "bendinat",
                    "paguera", "peguera", "el toro", "son ferrer", "costa de la calma", "illetas", "illetes",
                    "cas catala"]),
        ("Llucmajor", ["llucmajor", "s'arenal", "el arenal", "son veri", "cala blava", "puig de ros",
                       "sa torre", "bahia grande", "las palmeras"]),
        ("Marratxí", ["marratxi", "pont d'inca", "sa cabaneta", "portol", "es figueral de marratxi"]),
        ("Inca", ["inca"]), ("Manacor", ["manacor", "porto cristo", "cala millor", "s'illot"]),
        ("Alcúdia", ["alcudia", "port d'alcudia", "puerto de alcudia"]),
        ("Pollença", ["pollenca", "pollensa", "port de pollenca", "puerto pollensa", "cala sant vicenc"]),
        ("Sóller", ["soller", "port de soller", "fornalutx", "deia"]),
        ("Andratx", ["andratx", "port d'andratx", "puerto andratx", "camp de mar", "sant elm"]),
        ("Santanyí", ["santanyi", "cala d'or", "cala figuera", "portopetro"]),
        ("Felanitx", ["felanitx", "portocolom"]), ("Campos", ["campos", "sa rapita", "colonia de sant jordi"]),
        ("Binissalem", ["binissalem", "santa maria del cami", "consell", "alaro", "bunyola", "esporles",
                        "valldemossa"]),
        ("Capdepera", ["capdepera", "cala ratjada", "cala rajada", "arta", "canyamel"]),
        ("Sa Pobla", ["sa pobla", "muro", "can picafort", "santa margalida"]),
        ("Mallorca", ["mallorca"]),
    ],
    "menorca": [
        ("Maó", ["mao", "mahon", "es castell", "sant climent", "llucmacanes"]),
        ("Ciutadella", ["ciutadella", "ciudadela", "cala en blanes", "cala'n bosch", "cala en bosc", "cala morell"]),
        ("Alaior", ["alaior", "alayor", "cala en porter", "son bou"]), ("Es Mercadal", ["es mercadal", "fornells"]),
        ("Ferreries", ["ferreries", "ferrerias", "cala galdana"]), ("Sant Lluís", ["sant lluis", "san luis", "binibeca", "binibequer", "punta prima", "s'algar"]),
        ("Es Migjorn", ["es migjorn", "sant tomas"]), ("Menorca", ["menorca"]),
    ],
    "barcelona": [
        ("Eixample", ["eixample", "ensanche", "sant antoni", "sagrada familia", "fort pienc", "dreta de l'eixample",
                      "esquerra de l'eixample"]),
        ("Gràcia", ["gracia", "vila de gracia", "vallcarca", "el coll", "la salut", "camp d'en grassot"]),
        ("Ciutat Vella", ["ciutat vella", "el gotic", "gotico", "barri gotic", "el born", "el raval", "raval",
                          "barceloneta", "sant pere", "la ribera"]),
        ("Sants-Montjuïc", ["sants montjuic", "sants", "montjuic", "poble sec", "hostafrancs", "la bordeta",
                            "la marina del port", "font de la guatlla"]),
        ("Les Corts", ["les corts", "pedralbes", "la maternitat"]),
        ("Sarrià-Sant Gervasi", ["sarria sant gervasi", "sarria", "sant gervasi", "galvany", "el putxet", "bonanova",
                                 "tres torres", "vallvidrera"]),
        ("Horta-Guinardó", ["horta guinardo", "horta", "guinardo", "el carmel", "vall d'hebron", "la teixonera"]),
        ("Nou Barris", ["nou barris", "vilapicina", "prosperitat", "la guineueta", "roquetes", "verdun", "porta"]),
        ("Sant Andreu", ["sant andreu", "la sagrera", "navas", "bon pastor", "trinitat vella"]),
        ("Sant Martí", ["sant marti", "poblenou", "el clot", "diagonal mar", "vila olimpica", "la verneda",
                        "camp de l'arpa", "provencals", "glories"]),
        ("Barcelona", ["barcelona"]),
    ],
    "barcelona_area": [
        ("L'Hospitalet", ["l'hospitalet", "hospitalet de llobregat", "hospitalet"]),
        ("Badalona", ["badalona"]), ("Santa Coloma", ["santa coloma de gramenet", "santa coloma"]),
        ("Sant Cugat", ["sant cugat", "san cugat", "valldoreix", "mira sol"]),
        ("Cornellà", ["cornella"]), ("Esplugues", ["esplugues"]), ("Sant Just", ["sant just desvern"]),
        ("Sant Boi", ["sant boi"]), ("El Prat", ["el prat de llobregat"]), ("Viladecans", ["viladecans"]),
        ("Gavà", ["gava"]), ("Castelldefels", ["castelldefels"]), ("Sitges", ["sitges"]),
        ("Sabadell", ["sabadell"]), ("Terrassa", ["terrassa", "tarrasa"]), ("Cerdanyola", ["cerdanyola"]),
        ("Rubí", ["rubi"]), ("Mollet", ["mollet del valles"]), ("Granollers", ["granollers"]),
        ("Mataró", ["mataro"]), ("Sant Adrià", ["sant adria"]), ("Montgat", ["montgat", "el masnou", "premia"]),
        ("Sant Feliu", ["sant feliu de llobregat"]), ("Molins de Rei", ["molins de rei"]),
    ],
}

# El nombre genérico de la zona ("Mallorca", "Barcelona") solo se usa si no hay nada más concreto
_GENERIC = {"formentera", "mallorca", "menorca", "barcelona", "eivissa"}
_COMPILED: list[tuple[str, str, bool, re.Pattern[str]]] = [
    (area, name, variant in _GENERIC, re.compile(rf"(?<![a-z]){re.escape(variant)}(?![a-z])"))
    for area, places in _PLACES.items() for name, variants in places for variant in variants
]


def _best(zone: str, areas: set[str] | None) -> tuple[str, str] | None:
    best: tuple[int, int, str, str] | None = None
    for area, name, generic, pattern in _COMPILED:
        if areas is not None and area not in areas:
            continue
        match = pattern.search(zone)
        if match is None:
            continue
        candidate = (1 if generic else 0, match.start(), area, name)
        if best is None or candidate < best:
            best = candidate
    return (best[2], best[3]) if best else None


def detect(texts: list[str], agency_areas: list[str]) -> tuple[str, str]:
    """Devuelve (zona, localidad). Se prueban los textos en orden (título, URL, descripción).

    Primero se buscan localidades de las zonas donde trabaja la inmobiliaria (hay nombres
    repetidos: «Sant Antoni» es un pueblo de Ibiza y un barrio de Barcelona). Si no aparece
    ninguna, se mira el resto: algunas agencias anuncian algún piso fuera de su zona.
    """
    zones = [norm(text).replace("-", " ").replace("_", " ") for text in texts]
    for scope in (set(agency_areas), None):
        for zone in zones:
            found = _best(zone, scope)
            if found is not None:
                return found
    return (agency_areas[0] if agency_areas else ""), ""
