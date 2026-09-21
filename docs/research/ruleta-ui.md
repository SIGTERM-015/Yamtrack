# E7.3 — Interfaz de la ruleta (diseño)

- **Tarea Vikunja:** [46] E7.3 - Interfaz de la ruleta (Épica 7)
- **Rama:** `spike/e73-ruleta-ui` (desde `dev`)
- **Estado:** propuesta, CERO código productivo.
- **Depende de:** [44] E7.1 (persistir géneros) y [45] E7.2 (motor de selección aleatoria con filtros).
- **Épica padre:** [43] ÉPICA 7 - Elegir qué ver: selección aleatoria filtrada.

## 1. Objetivo y alcance

Definir la pantalla que resuelve *"¿y qué vemos hoy?"*: filtros + botón que
muestra un título al azar de forma vistosa, con opción de volver a tirar.

Alcance de este documento: layout, filtros, estados, acciones y criterios de
aceptación de UI. Fuera de alcance: el motor de selección (E7.2), la
persistencia de géneros (E7.1), endpoints y queries.

## 2. Descripción literal de la tarea

> Pantalla con los filtros y un botón que muestre el resultado de forma vistosa,
> con opción de volver a tirar.
>
> **Deseable:** poder marcarlo directamente como *watching* desde ahí.
>
> **Hecho cuando:** funciona sobre una lista individual y sobre una de grupo.

## 3. Contexto UI actual (solo lectura, rama `dev`)

- Vistas de lista: `src/lists/views.py` → `lists`, `list_detail`
  (`src/templates/lists/list_detail.html`), componentes reutilizables en
  `src/templates/lists/components/` y `src/templates/app/components/`
  (`media_card.html`, `media_grid_items.html`).
- Vista de media: `src/app/views.py` → `media_list` (`/<username>/<media_type>`),
  `src/templates/app/media_list.html`.
- Acción "marcar como viendo": endpoint existente `media_save`
  (`src/app/urls.py:53`, `views.media_save`) que crea/actualiza la fila `Media`
  con `status = Status.IN_PROGRESS` ("In progress", `src/app/models.py:775`).
  No hace falta endpoint nuevo para el deseable.
- **Listas de grupo:** en `dev` **no existe** la app `groups` (vive en
  `feat/groups` / `spike/e81-recomendaciones`: `src/groups/views.py`
  `group_detail`, `src/templates/groups/group_detail.html`, pool =
  `get_group_progress(group)`). La rama de trabajo E7.3 sale de `dev`, así que
  la variante de grupo se diseña aquí pero **se implementa cuando `groups`
  esté mergeado**.

## 4. Definición funcional

Dos modos, misma pantalla, distinto *pool* y distinto scope de filtros:

| Modo | Origen | Pool |
|---|---|---|
| **Individual** | lista propia (`list_detail` o `media_list`) | items del usuario |
| **Grupo** | `group_detail` | items presentes en el progreso del grupo (unión de miembros) |

El modo se elige por contexto (desde dónde se abre la ruleta), no por un
selector dentro de la pantalla. La pantalla recibe el pool ya acotado por el
backend (E7.2) y solo presenta filtros + resultado.

## 5. Filtros

Requisito de épica: filtrar por **tipo de medio** y **género** (este último
depende de E7.1).

1. **Tipo de medio** (obligatorio, un valor). `select` con los tipos presentes
   en el pool. Default: `movie` si existe, si no el primer tipo disponible.
   Valores: `tv, movie, anime, manga, game, book, comic, boardgame`,
   `season`/`episode` excluidos (no se eligen por azar).
2. **Géneros** (opcional, multi-selección). Chips de los géneros disponibles
   para el tipo elegido. Sin selección = "cualquier género". Con ≥1 seleccionado
   = intersección (el título debe tener al menos uno; ver semántica abierta §11).
3. **Modo grupo** (solo si el contexto es grupo), radio:
   - **"Para el grupo"** — el pool son items que **no tiene completados nadie**.
   - **"Para mí (dentro del grupo)"** — el pool son items que **yo no he visto**,
     aunque otro miembro sí.

Los filtros se aplican al pulsar *Tirar*. Cambiar un filtro con un resultado ya
visible limpia el resultado y vuelve a estado `idle` (evita mostrar un título
que ya no coincide).

## 6. Estados de la pantalla

Máquina de estados (una sola pantalla, sin recarga completa):

```
idle ──tirar──▶ spinning ──ok──▶ result ──tirar──▶ spinning
  ▲                 │                │
  │              sin match           │ vaciar/ cambiar filtro
  └──── empty ◀────┘                └──────────▶ idle
                 error (red/500) → toast + idle
```

| Estado | Qué se ve |
|---|---|
| `idle` | Filtros activos + botón grande **"Tirar"**. Zona de resultado con placeholder ("aún no has tirado"). |
| `spinning` | Animación breve (~1–1.5 s) sobre la zona de resultado; botón deshabilitado. Puramente cosmético. |
| `result` | Card del título a lo grande (imagen + título + año/tipo + géneros) + acciones **Volver a tirar** y **Marcar como viendo**. |
| `empty` | Mensaje "Ningún título coincide con estos filtros" + sugerencia de quitar género + botón para resetear filtros. |
| `error` | Toast "No se pudo tirar, inténtalo de nuevo"; se vuelve a `idle` conservando filtros. |

Casos límite de UI:
- Pool vacío de entrada (lista sin items): estado `empty` con CTA a buscar/añadir.
- Un solo título en el pool: `result` muestra ese título con normalidad;
  "volver a tirar" no cambia nada (backend devuelve lo mismo). Se acepta.
- Un solo título tras filtrar: igual que el anterior.

## 7. Wireframe textual

### Desktop (≥768 px)

```
┌───────────────────────────────────────────────────────────────┐
│  🎰  ¿Qué vemos hoy?                     [ Individual | Grupo ] │  ← toggle solo en modo grupo
├───────────────────────────────────────────────────────────────┤
│  Tipo:  [ Película ▾ ]      Género: [Acción][Sci-Fi][Drama]…   │  ← chips multi
│  (modo grupo)  ( ) Para el grupo   (•) Para mí                 │
│                                                                │
│                ┌─────────────────────────────┐                 │
│                │                             │                 │
│                │     [ póster grande ]       │                 │  ← placeholder en idle
│                │      TÍTULO (2021)          │                 │     card en result
│                │      movie · Acción, Sci-Fi │                 │     spinner en spinning
│                │                             │                 │
│                └─────────────────────────────┘                 │
│                                                                │
│              ┌──────────────┐   ┌────────────────────┐         │
│              │ 🎲  TIRAR    │   │ ▶ Marcar como viendo│         │  ← 2º botón solo en result
│              └──────────────┘   └────────────────────┘         │
└───────────────────────────────────────────────────────────────┘
```

### Móvil (<768 px)

```
┌─────────────────────────┐
│ 🎰 ¿Qué vemos hoy?      │
│ Tipo  [ Película ▾ ]    │
│ Género [Acción][Sci-Fi] │  ← chips con scroll horizontal
│ ( ) Grupo  (•) Para mí  │
│                         │
│   ┌─────────────────┐   │
│   │  [ póster ]     │   │
│   │  TÍTULO (2021)  │   │
│   └─────────────────┘   │
│  [ 🎲  TIRAR ]          │  ← ancho completo
│  [ ▶ Marcar viendo ]    │  ← solo en result
└─────────────────────────┘
```

Convenciones visuales: reutilizar `media_card.html` y la paleta dark existente
(`#2a2f35` fondo de card, `#4f46e5` acento); el "vistoso" se consigue con
póster grande + animación de entrada, sin dependencias nuevas.

## 8. Acciones y feedback

- **Tirar / Volver a tirar:** misma acción; si ya hay resultado visible se
  etiqueta "Volver a tirar". Re-llama al backend con los filtros actuales.
- **Marcar como viendo:** `POST media_save` con `status = IN_PROGRESS` para el
  item mostrado. Al éxito → toast "Añadido a En progreso"; el título **sigue
  visible** y el botón pasa a estado deshabilitado/"Ya en progreso". No
  redirige (el usuario puede seguir tirando).
- **Resetear filtros:** visible solo en `empty`; limpia géneros y modo grupo.

## 9. Criterios de aceptación UI

1. Existe una pantalla de ruleta accesible desde `list_detail` (lista
   individual) y desde `group_detail` (lista de grupo).
2. La pantalla muestra, al menos, el filtro de **tipo de medio** y el de
   **género**; el tipo de medio es obligatorio.
3. Al pulsar **Tirar** se llama al motor E7.2 con los filtros activos y se
   muestra **un** título resultado, con póster, título, tipo y géneros.
4. **Volver a tirar** produce un nuevo resultado sin salir de la pantalla.
5. Con filtros que no casan con ningún título se muestra el estado `empty` con
   mensaje accionable; **nunca** un error crudo ni un resultado vacío.
6. En modo grupo existe el selector *"Para el grupo" / "Para mí"* y cada modo
   usa el pool correspondiente (§4).
7. Desde el resultado se puede **marcar como viendo** en un clic
   (`media_save`, `IN_PROGRESS`), con confirmación visible y sin recargar la
   página.
8. Cambiar un filtro tras un resultado limpia ese resultado (no se muestra un
   título que ya no cumple los filtros).
9. Funciona en móvil (≥360 px) y desktop (≥1280 px) sin scroll horizontal.
10. Sin JS, el formulario sigue funcionando como POST normal (degradación
    progresiva): filtros + tirar devuelven la página con el resultado; la
    animación `spinning` es una mejora opcional.

## 10. Contrato con el backend (E7.2)

La UI espera del motor, como mínimo:

- **Entrada:** `pool` (lista | grupo), `media_type`, `genres[]`, `group_scope`
  (`group` | `me`, solo modo grupo).
- **Salida:** un item (`item_pk`/`media_id` + `source` + `media_type`) o
  vacío/`None` cuando no hay match. Recomendado: que el motor devuelva también
  los géneros del item para pintarlos sin fetch extra.

La UI **no** conoce las reglas de azar ni de exclusión; solo presenta.

## 11. Decisiones abiertas (a resolver en E7.2, no bloquean E7.3)

- **Semántica de multi-género:** ¿intersección ("tiene TODOS") o unión ("tiene
  alguno")? La UI se adapta a cualquiera; documentar en la etiqueta del filtro.
- **Exclusión de "ya visto":** ¿la ruleta excluye lo completado? En modo
  *"Para el grupo"* ya se asume; en individual queda a criterio de E7.2.
- **`spinning`:** animación puramente cliente vs. latencia real del backend.

## 12. Relación con otras tareas

- **E8.1 (algoritmo de recomendación):** la ruleta es **aleatoria y filtrada**,
  no rankeada. No usa el perfil de gustos. Sí puede reutilizar, más adelante,
  el criterio de explicabilidad ("porque te gusta Sci-Fi") si se quiere mostrar
  *por qué* salió el título, pero no es requisito de E7.3.
- **E8.4 (UI de recomendaciones):** pantalla distinta (lista rankeada). No
  fusionar con la ruleta.
- **Spike Stremio (`docs/research/stremio-spike.md`, E10.1):** no relevante
  para esta UI (trata integración del reproductor, no selección).

## 13. Fuera de alcance

Código productivo, endpoints, migraciones, tests, decisiones de azar, y la
implementación de la app `groups` (pendiente de merge a `dev`).
