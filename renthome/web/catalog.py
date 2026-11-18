"""Listas cerradas que usan los formularios (clave → texto)."""
from __future__ import annotations

AREAS: dict[str, str] = {
    "ibiza": "Ibiza",
    "formentera": "Formentera",
    "mallorca": "Mallorca",
    "menorca": "Menorca",
    "barcelona": "Barcelona ciudad",
    "barcelona_area": "Área metropolitana de Barcelona",
}

PROPERTY_TYPES: dict[str, str] = {
    "piso": "Piso / apartamento",
    "atico": "Ático",
    "duplex": "Dúplex",
    "casa": "Casa / chalet",
    "adosado": "Adosado",
    "estudio": "Estudio",
}

FURNISHED: dict[str, str] = {"any": "Me da igual", "yes": "Amueblado", "no": "Sin amueblar"}

# servicios que se pueden pedir cerca del piso (claves = categorías de area_sources, salvo «transporte»,
# que agrupa bus, tren/metro/tranvía y ferri)
SERVICES: dict[str, str] = {
    "super": "Supermercado", "farmacia": "Farmacia", "salud": "Centro de salud o clínica",
    "educacion": "Colegio o guardería", "transporte": "Transporte público", "playas": "Playa",
    "parques": "Parque", "restaurantes": "Restaurantes y cafés", "deporte": "Gimnasio o polideportivo",
    "ocio": "Cine, teatro o biblioteca",
}
SERVICE_SHORT: dict[str, str] = {
    "super": "súper", "farmacia": "farmacia", "salud": "centro de salud", "educacion": "colegio",
    "transporte": "transporte público", "playas": "playa", "parques": "parque", "restaurantes": "restaurantes",
    "deporte": "gimnasio", "ocio": "ocio",
}

ID_TYPES: dict[str, str] = {"dni": "DNI", "nie": "NIE", "pasaporte": "Pasaporte"}

EMPLOYMENT: dict[str, str] = {
    "indefinido": "Cuenta ajena · contrato indefinido",
    "temporal": "Cuenta ajena · contrato temporal",
    "fijo_discontinuo": "Fijo discontinuo",
    "autonomo": "Autónomo/a",
    "funcionario": "Funcionario/a",
    "pensionista": "Pensionista",
    "estudiante": "Estudiante",
    "desempleado": "En búsqueda de empleo",
}

# Documentación habitual para un alquiler de larga duración en España
DOCUMENT_KINDS: dict[str, str] = {
    "identidad": "DNI, NIE o pasaporte",
    "contrato": "Contrato de trabajo",
    "nominas": "Últimas nóminas",
    "renta": "Declaración de la renta (IRPF)",
    "vida_laboral": "Informe de vida laboral",
    "autonomo": "Autónomos: modelos 130 / 303 o recibo de cuota",
    "solvencia": "Justificante bancario o de ahorros",
    "referencias": "Referencias de un arrendador anterior",
    "aval": "Documentación del avalista",
    "otros": "Otros",
}
