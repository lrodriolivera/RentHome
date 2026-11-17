# RentHome — plataforma multiusuario

Buscador privado de **alquiler de larga duración** en **Baleares y Barcelona** que lee directamente
las webs de **inmobiliarias locales** (nunca portales como Idealista o Fotocasa).

## Qué hace

| Para quien busca piso | Para el administrador |
|---|---|
| Cuenta solo por invitación | Invitar, desactivar y eliminar usuarios |
| Perfil de inquilino con lo que piden las inmobiliarias | Alta, edición y pausa de inmobiliarias |
| Documentos cifrados (DNI, nóminas, contrato…) | Rastreo manual y estado de cada web |
| Búsqueda principal + búsquedas guardadas con avisos | Candidatas descubiertas cada semana, para aprobar |
| Botón «Buscar ahora», guardar y descartar pisos | Registro de actividad |
| IA local: resumen del anuncio, requisitos, alertas, encaje y borrador de mensaje | |
| Descargar o borrar todos sus datos (RGPD) | |

## Cómo funciona

```
inmobiliarias (BD) ─► cola de rastreo ─► anuncios compartidos (BD) ─► filtro por usuario ─► resultados
        ▲                  │  diario + «Buscar ahora»       │
        │                  ▼                                ▼
 descubrimiento      solo abre fichas nuevas         IA local (Qwen): contrato, precio anual,
 semanal (IA)        o no revisadas en 3 días        requisitos, alertas, encaje, mensaje
```

- **Rastreo compartido e incremental**: cada inmobiliaria se lee una vez para todos. «Buscar ahora»
  solo vuelve a leer las que llevan más de 3 horas sin revisarse; el resto se actualiza solo cada día.
- **Reglas + IA**: las reglas descartan venta, temporada, locales y anuncios ya alquilados; la IA
  resuelve los casos dudosos y puede rescatar un anuncio solo si el único problema era no saber si es anual.
- **Descubrimiento**: la IA propone inmobiliarias por zona; cada web se visita, se deduce cómo
  rastrearla y se prueba de verdad. Solo entra si el administrador la aprueba.

## Estructura

```
renthome/            rastreador original (parse, crawl, fetch, filters) — lo reutiliza la plataforma
renthome/web/
  app.py             aplicación FastAPI, middleware y cabeceras
  models.py          usuarios, perfil, documentos, inmobiliarias, anuncios, búsquedas…
  routes_*.py        pantallas (auth, perfil, documentos, búsqueda, IA, privacidad, admin)
  crawl_service.py   rastreo incremental hacia la base de datos
  worker.py          cola de trabajos, planificador diario y semanal
  matching.py        qué anuncios encajan con cada usuario
  ai*.py             cliente del modelo local, lectura de anuncios, asistente del inquilino
  discovery.py       descubrimiento y análisis de inmobiliarias nuevas
  geo.py features.py zona/localidad y características del anuncio
  crypto.py security.py limits.py   cifrado, contraseñas, límites
  agencies_seed.yaml inmobiliarias iniciales
tests/               pytest (se ejecutan dentro del contenedor)
documentacion/       este documento y RGPD.md
```

## Puesta en marcha (Docker)

```bash
cp .env.plataforma.example .env.plataforma     # y rellénalo (clave, URL pública, responsable)
docker compose -f docker-compose.plataforma.yml up -d --build
docker exec -it renthome-next python -m renthome.web.cli crear-admin tu@email.com "Tu nombre"
```

- Web: `http://127.0.0.1:8089` · en la red Docker `homelab`: `http://renthome-next:8080`
- Datos (base de datos y documentos cifrados): carpeta `data-web/`. **Haz copia de seguridad de esa
  carpeta y de `RENTHOME_SECRET_KEY`**: sin la clave, los documentos no se pueden leer.

## Tests

```bash
docker run --rm -v "$PWD/renthome:/app/renthome:ro" -v "$PWD/tests:/app/tests:ro" \
  renthome-plataforma python -m pytest -q tests -p no:cacheprovider
```

## Probar una inmobiliaria a mano

```bash
docker run --rm -v "$PWD/renthome:/app/renthome:ro" -v "$PWD/tools:/app/tools:ro" -v "$PWD/mis-pruebas:/work" \
  renthome-plataforma python tools/probe_agency.py /work/candidatas.yaml --details 3
```

## Límites conocidos

- Los datos se extraen automáticamente: siempre hay que confirmar precio, contrato y disponibilidad con la inmobiliaria.
- Algunas webs bloquean el acceso automático; se respeta y aparecen como «fallo» en el panel.
- El descubrimiento parte de sugerencias de la IA (puede proponer webs que no existen: se descartan al verificarlas).
- Los imprescindibles (parking, ascensor, terraza) suben arriba los anuncios que los mencionan; no descartan
  los que no dicen nada, porque muchas fichas no lo indican.
- El cifrado protege los archivos en disco; quien administra el servidor custodia la clave.
