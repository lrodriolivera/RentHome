# Protección de datos en RentHome (RGPD / LOPDGDD)

Documento interno para el responsable del tratamiento. No es asesoramiento jurídico: si RentHome
deja de ser un servicio privado entre conocidos, conviene que lo revise un profesional.

## 1. Registro de actividades de tratamiento (art. 30)

| | |
|---|---|
| **Responsable** | El que figure en `RENTHOME_CONTROLLER_NAME` / `RENTHOME_CONTROLLER_EMAIL` |
| **Finalidad** | Ayudar a personas invitadas a encontrar vivienda de alquiler de larga duración |
| **Base jurídica** | Consentimiento (art. 6.1.a), recogido al crear la cuenta, con versión y fecha |
| **Interesados** | Usuarios invitados (inquilinos potenciales) |
| **Categorías de datos** | Identificativos y de contacto; número de documento (cifrado); situación laboral e ingresos; composición del hogar; preferencias de vivienda; documentos aportados (cifrados); borradores de mensaje (cifrados); registro de accesos (fecha, acción, IP) |
| **Categorías especiales** | No se solicitan. Se advierte al usuario de que no las incluya en campos libres |
| **Destinatarios** | Ninguno. RentHome no envía datos a las inmobiliarias |
| **Encargados** | Cloudflare, Inc. (túnel y protección del tráfico). Proveedor SMTP si se activan los avisos por email |
| **Transferencias internacionales** | Cloudflare: Marco de Privacidad de Datos UE-EE. UU. y cláusulas contractuales tipo |
| **Plazo de conservación** | Mientras la cuenta esté activa. Cuentas sin acceso en 12 meses: borrado automático. Registro de actividad: 12 meses |
| **Decisiones automatizadas** | No. La IA solo orienta (puntuación de encaje, borradores) y nunca decide ni contacta con terceros |

## 2. Medidas técnicas y organizativas (art. 32)

- **Acceso solo por invitación**; contraseñas con Argon2; bloqueo tras 5 intentos fallidos.
- **Cifrado en reposo** (AES-256-GCM, clave derivada por usuario) de documentos, número de
  identidad y borradores de mensaje. La clave maestra vive en `.env.plataforma`, fuera del repositorio.
- **Cifrado en tránsito**: HTTPS mediante Cloudflare; cookie de sesión `Secure`, `HttpOnly`, `SameSite=Lax`.
- **Aislamiento entre usuarios**: cada persona solo accede a sus datos; el panel de administrador
  no tiene pantallas para ver perfiles ni documentos ajenos.
- **Minimización**: todos los campos del perfil son voluntarios; los anuncios son datos públicos
  compartidos y no contienen datos personales de los usuarios.
- **IA local**: el modelo se ejecuta en el mismo servidor. Nunca recibe el número de documento.
  Se usa a petición del usuario.
- **Trazabilidad**: registro de accesos y cambios sin contenido de los datos.
- **Protecciones web**: CSRF en todos los formularios, cabeceras de seguridad y CSP estricta,
  límite de tamaño de peticiones, validación del tipo real de los archivos.
- **Sin terceros en el navegador**: tipografías autoalojadas, sin analítica ni cookies de terceros.

## 3. Derechos de los interesados

| Derecho | Cómo se ejerce |
|---|---|
| Información | Política de privacidad en `/privacidad`, enlazada al crear la cuenta |
| Acceso y portabilidad | «Mis datos» → Descargar todo (ZIP con JSON y documentos) |
| Rectificación | Edición del perfil y las búsquedas |
| Supresión / retirada del consentimiento | «Mis datos» → Borrar mi cuenta (inmediato). También puede hacerlo el administrador |
| Limitación / oposición | Por email al responsable |

Plazo de respuesta: un mes.

## 4. Si hay una brecha de seguridad (arts. 33 y 34)

1. Contener: desactivar el acceso público (pausar la ruta en Cloudflare) y cambiar `RENTHOME_SECRET_KEY`
   solo si la clave pudo quedar expuesta (ojo: invalida sesiones; los documentos cifrados con la clave
   antigua requieren esa clave para leerse).
2. Evaluar qué datos y qué personas están afectados (registro de actividad en `/admin/registro`).
3. Si hay riesgo para los derechos de las personas: notificar a la AEPD en **72 horas**
   (sede electrónica de la AEPD) y, si el riesgo es alto, avisar a los afectados.
4. Documentar el incidente y las medidas tomadas.

## 5. Tareas pendientes del responsable

- [ ] Rellenar nombre y email de contacto en `.env.plataforma`.
- [ ] Guardar una copia de `RENTHOME_SECRET_KEY` en un gestor de contraseñas.
- [ ] Copias de seguridad de `data-web/` (la base de datos y `uploads/` van juntas; sin la clave no sirven).
- [ ] Si se activan avisos por email: usar un proveedor con contrato de encargado del tratamiento.
- [ ] Revisar este documento si cambia la finalidad o se abre el servicio a más gente.

## 6. Sobre el rastreo de las webs de inmobiliarias

Los anuncios son información pública de empresas. El rastreador se identifica, va despacio
(una petición por segundo y web), abre cada ficha como mucho una vez cada tres días y respeta
los bloqueos (no los intenta saltar). No se guardan datos personales de anunciantes particulares:
solo se rastrean inmobiliarias.
