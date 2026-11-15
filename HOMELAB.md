# RentHome Ibiza en el homelab (Mac Studio + Tailscale + Cloudflare Tunnel)

Esta guía monta la **aplicación completa** en el Mac Studio: búsqueda semanal, historial,
web con botón **"Buscar ahora"** y avisos al móvil. Tardarás unos 15 minutos.

```
Mac Studio (Docker)
└── renthome  ── busca cada lunes, guarda data/ y sirve la web en :8088
        │
        ├── Tailscale:  http://mac-studio-ibiza:8088   (solo tus dispositivos)
        └── Cloudflare Tunnel: https://pisos.tudominio.com  (Internet)
```

## 1. Requisitos

- **Docker Desktop** u **OrbStack** instalado y abierto en el Mac.
- `git` (viene con las herramientas de Xcode: `xcode-select --install`).

## 2. Descargar y configurar

En la Terminal del Mac:

```bash
mkdir -p ~/homelab && cd ~/homelab
git clone -b ccr-4507a6bb-yrqzxg https://github.com/lrodriolivera/RentHome.git renthome
cd renthome
cp .env.example .env
open -e .env        # edita: RENTHOME_SCHEDULE, PUBLIC_URL, NTFY_URL…
```

## 3. Arrancar

```bash
docker compose up -d --build
```

La primera vez tarda unos minutos (descarga Python y Chromium). Comprueba que funciona:

- En el Mac: <http://localhost:8088>
- Desde el móvil con Tailscale activo: <http://mac-studio-ibiza:8088>
  (o `http://100.109.70.110:8088`)

Pulsa **Buscar ahora** para lanzar la primera búsqueda desde el homelab (unos 15 min).
Para ver lo que hace: `docker compose logs -f` o el archivo `data/last_crawl.log`.

## 4. Publicarlo con tu túnel de Cloudflare

### Si gestionas el túnel desde el panel (Zero Trust)
1. <https://one.dash.cloudflare.com> → **Networks → Tunnels** → tu túnel → **Edit**.
2. Pestaña **Public Hostname** → **Add a public hostname**:
   - Subdomain: `pisos` · Domain: tu dominio
   - Service: **HTTP** · URL: `localhost:8088`
3. Guarda. En un minuto estará en `https://pisos.tudominio.com`.

### Si usas un `config.yml` de cloudflared
Añade esta regla **antes** de la regla final `http_status:404`:

```yaml
ingress:
  - hostname: pisos.tudominio.com
    service: http://localhost:8088
  # … tus otras reglas …
  - service: http_status:404
```

y crea el DNS: `cloudflared tunnel route dns <nombre-del-túnel> pisos.tudominio.com`,
luego reinicia cloudflared.

### Si aún no tienes cloudflared en el Mac
Crea un túnel en el panel, copia su token en `.env` (`CLOUDFLARE_TUNNEL_TOKEN=…`), configura
el Public Hostname con Service `http://renthome:8080` y arranca:
`docker compose --profile tunnel up -d`.

## 5. Proteger la web (recomendado)

Como tiene el botón **Buscar ahora**, mejor que solo entres tú:
Zero Trust → **Access → Applications → Add → Self-hosted** → dominio `pisos.tudominio.com`
→ política **Allow** con **Emails** = tu email. Cloudflare te pedirá un código por email
la primera vez. (El botón tiene además un límite de una búsqueda cada 30 minutos.)

## 6. Avisos al móvil (ntfy)

1. Instala la app **ntfy** (Android / iOS).
2. Suscríbete al tema que pusiste en `NTFY_URL` (p. ej. `pisos-ibiza-cambia-esto-x7k2`).
3. Cuando una búsqueda encuentre pisos nuevos te llegará una notificación; al pulsarla se
   abre `PUBLIC_URL`.

## Mantenimiento

| Acción | Comando |
|---|---|
| Ver logs | `docker compose logs -f` |
| Cambiar criterios | edita `config.yaml` y `docker compose restart` |
| Actualizar la app | `git pull && docker compose up -d --build` |
| Parar | `docker compose down` |

El historial vive en `~/homelab/renthome/data` y sobrevive a reinicios y actualizaciones.

> Si mantienes también la búsqueda en GitHub Actions, cada una lleva su propio historial.
> Puedes desactivar la de GitHub en *Actions → Búsqueda semanal de pisos → ⋯ → Disable workflow*.
