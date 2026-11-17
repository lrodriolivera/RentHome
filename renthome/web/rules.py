"""Reglas comunes a todos los usuarios para aceptar un anuncio en la base compartida."""
from __future__ import annotations

# No son vivienda (salvo que el título hable de una vivienda "con parking", etc.)
EXCLUDE_TYPES = [
    "parking", "garaje", "plaza de aparcamiento", "trastero", "local comercial", "local-negocio",
    "commercial premises", "oficina", "office", "despacho", "habitación en piso compartido",
    "alquiler de habitación", "room for rent", "tienda", "licencia", "plot", "parcela", "terreno",
    "solar", "nave", "warehouse", "traspaso", "restaurante", "restaurant", "edificio", "hotel",
]
# Lugares fuera de las zonas cubiertas que algunas inmobiliarias mezclan en su cartera
FAR_PLACES = ["tarbena", "alicante", "valencia", "madrid", "denia", "andorra", "malaga", "marbella"]
# Sin precio publicado + estas palabras = casi seguro muy por encima de un alquiler normal
LUXURY_WORDS = ["villa", "mansion", "finca", "lujo", "luxury", "exclusiv", "six senses", "estate",
                "casa de campo", "payesa", "masia"]
UNKNOWN_PRICE_MAX_BEDROOMS = 3

# Criterios deliberadamente amplios: aquí solo se decide si es un alquiler de vivienda de larga
# duración disponible. Presupuesto y habitaciones se aplican después, por usuario.
BASE_CRITERIA = {
    "max_price": 10**7,
    "preferred_bedrooms": 2,
    "min_bedrooms": 0,
    "max_bedrooms": None,
    "include_unknown_price": True,
    "include_unknown_bedrooms": True,
    "exclude_types": EXCLUDE_TYPES,
    "exclude_places": FAR_PLACES,
    "unknown_price_max_bedrooms": UNKNOWN_PRICE_MAX_BEDROOMS,
    "luxury_words": LUXURY_WORDS,
}
