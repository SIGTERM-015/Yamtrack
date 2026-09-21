# Spike E10.1 — Integración con Stremio

- **Tarea:** Vikunja #60 · E10.1 (ÉPICA 10 — Sincronización automática con Stremio)
- **Tipo:** investigación, sin código productivo.
- **Fecha:** 2026-09-19
- **Precede a:** #61 E10.2 — Endpoint receptor de eventos de reproducción.

## Resumen ejecutivo

Stremio **no ofrece forma limpia de notificar reproducción a un servicio externo**. El
protocolo de addons solo sirve para *meter* datos en Stremio (catálogos, metadata, streams,
subtítulos); no existe resource de `progress`/`playback` ni webhooks. La petición de la
comunidad para añadirlos sigue abierta.

La vía viable y de menor coste es **Trakt como intermediario**: Stremio trae scrobbling
nativo de Trakt y Yamtrack ya tiene importador de Trakt con OAuth y tarea periódica. Esto
cubre el caso de uso principal (TV del salón / Android TV) sin desarrollo de addon.

**Recomendación:** opción C (Trakt intermediario) como vía principal; marcado manual rápido
como fallback. Descartar por ahora addon propio (no puede recibir eventos) y polling directo
de la API cloud de Stremio (no oficial y frágil).

## Caso de uso y restricción de plataforma

El objetivo es la **TV del salón**, el entorno más limitado: no se puede asumir instalar
addons, sidecar services ni clientes alternativos. Cualquier diseño debe funcionar en la app
oficial de Android TV / Google TV tal cual, configurada a lo sumo desde el móvil o la web.

| Plataforma | Scrobbling Trakt nativo | Notas |
|---|---|---|
| Web (`web.stremio.com`) | Sí | Configurable desde ajustes de cuenta. |
| Desktop | Sí | Configurable desde ajustes de cuenta. |
| Android móvil | Sí | — |
| **Android TV / Google TV** | **Sí (v1.5.6+)** | **Hay que configurarlo antes en Android móvil, Desktop o el dashboard web.** La TV no permite autenticar Trakt por sí sola. |
| iOS / tvOS (Stremio Lite) | **No** (a nov-2025) | Issue abierta en stremio-features; no aplica a nuestro caso (Android TV). |

Conclusión de plataforma: Android TV es viable **solo si** Trakt se autentica primero en otro
dispositivo. Es un paso único de onboarding, asumible.

## Opciones investigadas

### A. Addon de Stremio que reciba eventos de reproducción — NO VIABLE

El protocolo de addons define manifests con resources `catalog`, `meta`, `stream`,
`subtitles` (y `addon_catalog`). **No existe un resource de progreso/reproducción y Stremio no
envía eventos de playback a los addons.**

Evidencia:
- Feature request `Stremio/stremio-features#1278` ("Track Progress via new Addon Resource or
  Webhooks") sigue **abierta**. La propia discusión resume: *"addons are to get data INTO
  stremio, and webhooks are for getting data OUT of stremio"* — es decir, hoy no existe el
  canal de salida.
- `Stremio/stremio-addon-sdk#212` pregunta por leer/escribir el estado "watched" vía API de
  addon: la respuesta es que no está soportado.

**Workaround conocido:** algunos addons infieren reproducción a partir de las peticiones de
subtítulos (el usuario reporta que usa *"the subtitle workaround, with all its drawbacks"*).
Es un hack frágil, dependiente del comportamiento del reproductor, y **no se recomienda** como
base de una épica.

### B. API cloud de Stremio (`api.strem.io`, datastore) — VIABLE PERO NO OFICIAL

Las apps de Stremio sincronizan biblioteca y "Continuar viendo" contra el backend
`api.strem.io` (`datastoreGet` / `datastorePut`, autenticación de cuenta). Un cliente externo
podría autenticarse como el usuario y **hacer polling** del estado de biblioteca/progreso,
incluido el detalle por episodio. Existe cliente de referencia (`Stremio/stremio-api-client`)
y wrappers de terceros.

Pros:
- Refleja la actividad de la TV directamente, sin depender de Trakt.
- Incluye progreso por episodio (no solo "visto").

Contras (por eso no es la recomendación principal):
- **No documentado como API pública**, sin contrato ni garantía de estabilidad. Puede cambiar
  sin aviso.
- Requiere guardar credenciales de la cuenta Stremio del usuario; superficie de seguridad
  mayor que un token OAuth de Trakt.
- La semántica de "visto"/progreso hay que invertirla desde estructuras internas (fragilidad).
- Alternativa de terceros relevante: **StremThru**, que se apoya en la funcionalidad de
  biblioteca de Stremio y funciona incluso con reproductores externos.

Recomendación: **no** construir sobre esto ahora; dejarlo documentado como plan B si Trakt
resulta demasiado poco fiable.

### C. Trakt como intermediario — RECOMENDADO

Stremio incorpora **scrobbling nativo de Trakt** (Ajustes de cuenta → Integrations →
*Authenticate* junto a "Trakt Scrobbling"). Al activarlo instala el "Trakt Integration addon".
Stremio envía el historial de reproducción a Trakt; Yamtrack lo consume.

Yamtrack ya tiene todo el lado Trakt:
- `src/integrations/imports/trakt.py`: importador OAuth con refresco de token y soporte de
  export; procesa *watch history* (`/history`) mapeando a los modelos de Yamtrack.
- Tarea periódica registrada vía `django_celery_beat.PeriodicTask` (`"Import from Trakt"`),
  con rotación de refresh token ya resuelta (`update_refresh_token`).
- `Sources`/`MediaTypes` (`src/app/models.py`) y `Status` cubren película/serie/temporada/
  episodio y el estado "visto" que Trakt reporta.

Pros:
- Cero desarrollo de addon; reutiliza importador y scheduler existentes.
- OAuth, no credenciales de usuario.
- Funciona en Android TV (previa configuración en otro dispositivo).
- Trakt es además un formato de intercambio ya soportado por el proyecto.

Contras:
- Dependencia de terceros encadenada: Stremio → Trakt → Yamtrack.
- La propia guía de referencia avisa: *"Trakt scrobbling can occasionally be unreliable for
  some users"*.
- Latencia: la sincronización es por polling (tarea periódica), no en tiempo real.

### D. Marcado manual rápido — FALLBACK

No es "automático", pero es barato y siempre funciona en cualquier plataforma/versión. Debe
mantenerse como red de seguridad y como fallback si A/B/C fallan en el parque real del grupo.

## Comparativa

| Criterio | A. Addon propio | B. API cloud Stremio | C. Trakt intermediario | D. Manual |
|---|---|---|---|---|
| ¿Recibe reproducción? | No (imposible hoy) | Sí (polling) | Sí (vía Trakt) | Sí (humano) |
| Funciona en Android TV | N/A | Probable | Sí (config. previa) | Sí |
| Oficial / estable | N/A | No | Sí (APIs públicas) | N/A |
| Desarrollo necesario | Alto e inútil | Medio-alto | Bajo (reutiliza importador) | Nulo |
| Credenciales sensibles | — | Cuenta Stremio | OAuth Trakt | — |
| Tiempo real | — | Casi | No (periódico) | No |
| Riesgo principal | No existe el evento | Ruptura sin aviso | Fiabilidad de Trakt | Fricción usuario |
| Veredicto | Descartada | Plan B | **Recomendada** | Fallback |

## Encaje con los modelos actuales

- **Metadatos / IDs:** el historial de Trakt trae IDs resueltos por Trakt; el importador ya
  sabe mapearlos a `Item` (`source`, `media_id`, `media_type`, `season_number`,
  `episode_number`). No hay que tocar `Sources`: no se añade una fuente "stremio".
- **Webhooks existentes** (`src/integrations/webhooks/`): `BaseWebhookProcessor` con
  `process_payload` / `_is_supported_event` / `_extract_external_ids` / `_process_media`, y
  autenticación por `User.token` con vistas `csrf_exempt` (`jellyfin_webhook`, `plex_webhook`,
  `emby_webhook`). Este patrón **solo será necesario si en el futuro aparece una fuente de
  eventos real**; con la vía Trakt no se requiere un receptor nuevo.
- **Configuración por usuario:** el modelo `users.models.User` ya centraliza flags de
  integración (`plex_usernames`, `jellyfin_mark_played_enabled`, …). Un ajuste de
  "sincronizar con Trakt" encaja en el mismo sitio.

## Propuesta

1. **E10.2 se reenfoca**: no es "receptor de webhooks de Stremio" (no existe tal webhook) sino
   **job de sincronización periódica desde Trakt**, reutilizando `TraktImporter` y el patrón de
   `PeriodicTask` ya presente. Documentar el onboarding: activar Trakt Scrobbling en Stremio
   (una vez, desde móvil/desktop/web) y conectar Trakt en Yamtrack.
2. **No construir addon de Stremio** mientras `stremio-features#1278` siga abierta.
3. **Mantener el marcado manual rápido** como fallback y para plataformas no cubiertas.
4. **Plan B documentado:** si la fiabilidad de Trakt es insuficiente en uso real, evaluar
   polling de `api.strem.io` (`datastoreGet`) asumiendo su naturaleza no oficial.

## Preguntas abiertas (verificar antes de implementar)

- Fiabilidad real del scrobbling Trakt en Android TV dentro del grupo (probar en el parque
  real, no en laboratorio).
- ¿La app de Android TV exige configuración previa de Trakt en otro dispositivo en todas las
  versiones desplegadas? (Tech Update #19 indica que sí desde v1.5.6.)
- ¿Qué granularidad de "visto" reporta Trakt desde Stremio (película/episodio vs temporada)?
- ¿Aceptable que la sincronización sea por polling en lugar de en tiempo real?

## Fuentes

- Stremio Addon SDK / protocolo: <https://www.stremio.com/addon-sdk>
- Feature request (abierta) webhooks/progress: <https://github.com/Stremio/stremio-features/issues/1278>
- "Read/write stremio watched status": <https://github.com/Stremio/stremio-addon-sdk/issues/212>
- Guía de Trakt en Stremio (scrobbling nativo): <https://guides.viren070.me/stremio/extras/trakt>
- Stremio Tech Update #19 — Trakt scrobbling y VLC en Android TV: <https://blog.stremio.com/stremio-tech-update-19-trakt-scrobbling-and-vlc-on-android-tv>
- Trakt scrobbling ausente en iOS/tvOS: <https://github.com/Stremio/stremio-features/issues/1476>
- Cliente API Stremio: <https://github.com/Stremio/stremio-api-client>
- Código Yamtrack relevante: `src/app/models.py`, `src/integrations/imports/trakt.py`,
  `src/integrations/webhooks/`, `src/integrations/views.py`, `src/users/models.py`.
