# 🏠 RentHome Ibiza

Buscador automático de **pisos y casas en alquiler anual (larga duración)** en Ibiza.
**No usa portales masivos** (Idealista, Fotocasa, etc.): recorre directamente las webs de las
**inmobiliarias locales** de la isla.

- Presupuesto máximo: **2.000 €/mes**
- Preferencia: **2 habitaciones** (se muestran primero; también salen 1 y 3+ como alternativas)
- Descarta alquileres de temporada (verano, invierno, "septiembre a junio", "no anual"…), anuncios
  alquilados/reservados (también las etiquetas "Alquilado" de los listados), ventas, parkings,
  locales, oficinas, terrenos, estudios sin dormitorio y anuncios fuera de la isla
- **Cada lunes** se ejecuta solo (GitHub Actions), guarda el historial y **avisa de los anuncios nuevos**

📄 **Listado actual:** [`REPORT.md`](REPORT.md) · página visual: [`docs/index.html`](docs/index.html)

## Cómo funciona

```
config.yaml ──► rastreador ──► fichas de cada inmueble ──► filtros ──► historial ──► informes + aviso
                 (13 webs)      (precio, hab., zona,        (≤2000 €,     data/        REPORT.md
                                 anual/temporada, estado)    anual…)      listings.json docs/index.html
                                                                                      issue / email
```

1. Para cada inmobiliaria de `config.yaml` abre sus páginas de alquiler anual (y la paginación).
2. Entra en cada ficha y extrae precio, habitaciones, zona, si es anual o de temporada y si ya está
   alquilado. Funciona en español e inglés y entiende formatos como `1.800 €/mes`, `€1,800 Monthly`,
   `2 dormitorios`, `Bedrooms | 2`, `Estudio`…
3. Aplica los criterios y ordena: 2 habitaciones primero, luego por precio.
4. Compara con `data/listings.json` para saber qué es **nuevo**, qué **bajó de precio** y qué
   **ya no aparece**.
5. Genera `REPORT.md`, `docs/index.html` y, si hay novedades, abre un *issue* en GitHub
   (GitHub te lo envía por email) y opcionalmente manda un email directo.

## Inmobiliarias consultadas

| Inmobiliaria | Zona | Web |
|---|---|---|
| Living in Ibiza | Santa Eulària | living-in-ibiza.com |
| Inmonline Ibiza | Sant Josep | inmonlineibiza.com |
| Love to Live Ibiza | Eivissa | lovetoliveibiza.com |
| Ibiza Services | Eivissa | ibiza-services.com |
| Rentals Ibiza | Santa Eulària | rentalsibiza.co.uk |
| Ibiza Domo | Sant Antoni | ibizadomo.com |
| Ibiza Vende | Eivissa | ibizavende.com |
| Long Term Rental Ibiza | Ibiza | longtermrentalibiza.com |
| Summum Ibiza | Eivissa | summumibiza.com |
| Mondialité Ibiza | Eivissa | mondialiteibiza.com |
| Villa Contact | Santa Eulària | villacontact.com |
| Roxire | Sant Antoni | roxire.com (web con JavaScript, usa navegador) |

Para **añadir otra inmobiliaria**, copia un bloque en `config.yaml` e indica la página de listado
(`start_urls`) y cómo son las URLs de sus fichas (`detail_pattern`).

## Puesta en marcha

### Ejecución automática semanal (recomendado)

El workflow `.github/workflows/weekly.yml` se ejecuta **todos los lunes a las 06:47 UTC**.
Una vez el código esté en la rama principal del repositorio:

1. **Settings → Actions → General → Workflow permissions**: marca *Read and write permissions*.
2. Para lanzarlo a mano: pestaña **Actions → "Búsqueda semanal de pisos" → Run workflow**.
3. Avisos: cuando hay anuncios nuevos se crea un *issue*. Asegúrate de estar en *Watching*
   del repositorio (o en Settings → Notifications) para recibirlo por email.
4. (Opcional) Página web del informe: **Settings → Pages → Deploy from branch → `main` / `docs`**.
5. (Opcional) Email directo con Gmail: crea una
   [contraseña de aplicación](https://myaccount.google.com/apppasswords) y añade en
   **Settings → Secrets and variables → Actions** los secretos
   `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER=tu@gmail.com`,
   `SMTP_PASSWORD=<contraseña de aplicación>`, `MAIL_TO=tu@gmail.com`.

### En tu ordenador

```bash
pip install -r requirements.txt
python -m playwright install chromium      # solo necesario para webs con JavaScript (Roxire)
# (si ya tienes un Chromium instalado: export CHROMIUM_PATH=/ruta/a/chrome)
python -m renthome                          # búsqueda completa (~10-20 min)
python -m renthome --only "Living in Ibiza" # una sola inmobiliaria
python -m renthome --report-only            # re-aplicar criterios de config.yaml y regenerar informes (sin rastrear)
python -m pytest tests                      # tests del extractor
```

## Ajustar criterios

Todo está en `config.yaml`:

```yaml
criteria:
  max_price: 2000
  preferred_bedrooms: 2
  min_bedrooms: 1      # pon 2 para ver SOLO pisos de 2 o más habitaciones
```

## Avisos

- La información se extrae automáticamente de cada web: **confirma siempre con la inmobiliaria**
  el precio, que el contrato sea anual (vivienda habitual) y la disponibilidad.
- Los anuncios marcados con ⚠️ mencionan a la vez alquiler anual y de temporada.
- Si una inmobiliaria cambia su web, el informe mostrará un aviso "no se encontraron fichas";
  basta con actualizar sus `start_urls` / `detail_pattern`.
- El rastreador va despacio a propósito (1 petición por segundo y web) y se identifica como
  `RentHomeIbiza`. Si una web bloquea el acceso (HTTP 403), se respeta y aparece en los avisos.
