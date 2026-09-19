# E8.1 — Algoritmo de recomendación (diseño)

- **Tarea Vikunja:** [48] E8.1 - Definir el algoritmo de recomendación (Épica 8)
- **Rama:** `spike/e81-recomendaciones` (desde `dev`)
- **Estado:** propuesta, CERO código productivo.
- **Depende de:** [44] E7.1 — persistir géneros por título (aún no en `Item`).

## 1. Objetivo y alcance

Definir *cómo* se recomiendan títulos a un usuario (y a un grupo) a partir de su
historial y sus notas, antes de implementar. Este documento fija el algoritmo,
su justificación, los casos límite y cómo se valida.

Fuera de alcance: UI, endpoints, migraciones, rendimiento de queries.

## 2. Estado actual (solo lectura)

Datos disponibles hoy en `src/app/models.py`:

- `Item`: `media_id`, `source` (`tmdb`, `mal`, `mangaupdates`, `igdb`,
  `openlibrary`, `hardcover`, `comicvine`, `bgg`, `manual`), `media_type`
  (`tv`, `season`, `episode`, `movie`, `anime`, `manga`, `game`, `book`,
  `comic`, `boardgame`), `title`, `image`. **No hay campo `genres` todavía**
  (lo añade E7.1).
- `Media` (abstracta; `BasicMedia`, `TV`, `Season`, `Manga`, `Anime`, `Movie`,
  `Game`, `Book`, `Comic`, `BoardGame`): `item` FK, `user` FK,
  `score DecimalField(null=True, 0–10, 1 decimal)`, `progress`,
  `status` (`Completed`, `In progress`, `Planning`, `Paused`, `Dropped`),
  `start_date`, `end_date`, `notes TextField` (privado).
- Programas: secciones de recomendaciones ya existen como
  `enrich_items_with_user_data(..., section_name="recommendations")` y el flag
  `User.hide_completed_recommendations` / `User.hide_zero_rating`. Hoy la
  sección "recommendations" son los *related items* que devuelve la API del
  proveedor (p. ej. `tmdb.py` → `response["recommendations"]`), sin ningún
  modelo de gustos.
- Grupos (`src/groups/models.py`): `Group` con `members` M2M vía
  `GroupMembership`. Un grupo comparte items, pero no hay señal de gusto
  agregada.

Cada proveedor (TMDB, MAL, IGDB, ComicVine, MangaUpdates) ya expone un endpoint
de *recommendations*, por lo que existe un pool de candidatos por fuente.

## 3. Opciones evaluadas

| Opción | A favor | En contra |
|---|---|---|
| **A. Afinidad de géneros ponderada por nota** | No requiere API extra; funciona con cualquier fuente con géneros; resultados explicables ("te gustan los *thriller*"); soporta grupos de forma natural | Necesita E7.1 (géneros persistidos); cold start serio; menos "descubrimiento" (sesgo a lo popular del género) |
| **B. *Recommendations* de la API (TMDB y equivalentes)** | Ya existe; 0 coste de cómputo; variedad dentro de un título semilla | Solo cubre las fuentes que lo ofrecen; no usa las notas del usuario salvo para elegir la semilla; poco explicable; "lo que se vio junto" ≠ "lo que me gusta" |
| **C. Híbrido** | Pool de candidatos de B + ranking por gusto de A; aprovecha ambos; degrada con gracia si falta un lado | Un poco más de complejidad de scoring |

**Decisión: C (híbrido).** El pool de candidatos lo aportan los endpoints de
*recommendations* de cada proveedor, sembrado por los títulos mejor valorados
del usuario; el orden final lo decide un score de afinidad de géneros derivado
de sus notas. Justificación: B sola ignora el historial (el valor de la épica),
A sola tiene cold start y techo de descubrimiento; juntas se compensan y el
resultado es explicable. Si un título no tiene géneros o no tiene semilla
válida, se cae a B (o a popularidad) sin romper.

## 4. Señal de gusto (un usuario)

### 4.1 Normalización de la nota

`score ∈ [0,10]` o `None`. Se centra en neutro 5 para que sirva de gusto
positivo/negativo:

```
r_i = (score_i - 5) / 5      # rango [-1, +1], 0 = neutro
```

Nota `0` se trata como rechazo fuerte (coherente con `hide_zero_rating`), no
como "sin nota".

### 4.2 Peso por estado

El estado matiza la nota. Un `Dropped` es desinterés aunque la nota sea media;
un `Completed` con nota alta es señal fuerte.

```
w(status):
  Completed     ->  1.0
  Dropped       -> -1.0   (se ignora score; abandono = negativo)
  Paused        ->  0.3
  In progress   ->  0.2
  Planning      ->  0.0   (interés, sin opinión; no contamina el perfil)
```

Contribución de un título al perfil: `c_i = w(status_i) * r_i`. Si
`score is None`, el título no aporta señal de gusto (solo cuenta para
"ya visto").

### 4.3 Vector de afinidad por género

Por **media_type** (no global: los géneros de película y de videojuego no son
comparables). Con `G_i` = géneros del título i:

```
afinidad[g] = Σ_{i : g ∈ G_i} c_i  /  (n_g + K)
```

donde `n_g` = número de títulos valorados con género `g` y `K` = constante de
suavizado (propuesta **K = 5**) para que un género con una sola nota media no
domine. Es un encogimiento hacia 0 (neutralidad); a más datos, más confianza.

## 5. Cold start — cuántas valoraciones hacen falta

Umbral propuesto (**a validar con datos**, ver §9):

- `N_min = 10` títulos con nota por usuario **en ese media_type** para que su
  vector de afinidad se considere fiable.
- `N_min_seed = 3` títulos con `score ≥ 7` (o `n_g` suficiente) para sembrar
  candidatos vía API.

Comportamiento según madurez:

1. **0 valoraciones** → sin recomendaciones personalizadas; mostrar
   popularidad / tendencias del proveedor.
2. **1 .. N_min-1** → usar la señal disponible pero con suavizado fuerte
   (`K` alto); mezclar con pool de proveedor por títulos `Planning` de alto
   interés.
3. **≥ N_min** → perfil completo (fórmula §4), ranking híbrido (§6).

## 6. Ranking de candidatos

Para cada candidato `t` del pool (§7):

```
score_final(t) = α * relevancia_proveedor(t)
               + β * afinidad_genero(t)
               + γ * calidad(t)
               - δ * penalizaciones(t)

afinidad_genero(t) = media( afinidad[g] : g ∈ G_t )
```

Parámetros iniciales (a calibrar): **α = 0.3, β = 0.5, γ = 0.2, δ libre**.

- `relevancia_proveedor`: normalizada a [0,1] desde el endpoint de
  *recommendations* (o 1.0 si viene de semilla directa). Solo aplica a la
  fuente que lo ofrece; si no, 0 y el peso se redistribuye.
- `calidad`: nota media de la comunidad normalizada a [0,1] (escalas distintas
  por proveedor → normalizar cada una).
- `penalizaciones(t)`: ya visto (§7), mismo género ya saturado
  (diversificación), `t` ya descartado por el usuario, etc.

**Explicabilidad:** junto al título se puede mostrar el género dominante que
disparó el score ("porque te gusta *Sci-Fi*"), requisito implícito para que la
recomendación sea de fiar.

## 7. Exclusión de "ya visto"

Un título se excluye del pool si el usuario tiene fila `Media` con estado
`Completed`, `In progress` o `Dropped`. `Planning` se excluye también de la
sección de recomendaciones (ya está en su lista de pendientes).

Clave de identidad de un título: `(source, media_id, media_type)`; para
`season`, incluir además `season_number` (`episode` normalmente no se
recomienda). Los items `manual` no tienen id de proveedor: se pueden excluir
por `(title, media_type)` normalizado, como fallback conservador.

## 8. Recomendaciones de grupo

Dos modos explícitos, no mezclados:

- **"Para el grupo" (least-misery):** la puntuación del candidato es
  `min_m( score_final_m(t) )` sobre los miembros. Evita recomendar algo que un
  miembro odia; favorece lo que todos toleran. Si la afinidad de **cualquier**
  miembro cae por debajo de un umbral negativo (`afinidad_genero < -0.3`),
  el candidato se descarta.
- **"Para mí (dentro del grupo)":** ranking individual del §6, filtrado a
  títulos que el resto del grupo no haya visto.

Agregación del vector de grupo (opción simple y explicable): **media ponderada
por actividad**, con peso `min(n_valoraciones_m, 50)`, para no dejar que un
miembro que puntúa todo lo domine. Alternativa a evaluar: intersección de
top-géneros de cada miembro.

Un grupo de 1 miembro degenera al modo individual. Un grupo sin miembros con
datos → fallback a popularidad.

## 9. Casos límite

- **Notas privadas:** `notes` NUNCA se usa como señal (privacidad por diseño,
  ver E9.4). Solo `score` + `status`.
- **Géneros ausentes** (antes de E7.1 o en items manuales): candidato cae a
  `relevancia_proveedor`/`calidad`; el usuario, a fallback §5.
- **`score = None` masivo** (usuarios que solo marcan "visto"): perfil débil →
  tratar `Completed` sin nota como positivo leve (`+0.2` fijo, no `r_i`).
- **Escalas de comunidad distintas** (MAL 0–10, IGDB 0–100, TMDB 0–10):
  normalizar por proveedor antes de `calidad`.
- **Duplicados entre fuentes**: desambiguar por `(media_title, media_type)`
  aproximado; documentar como limitación conocida.
- **Tipos sin endpoint de recomendaciones** (p. ej. `boardgame`): pool = solo
  afinidad de géneros / popularidad del proveedor.
- **Sesgo de popularidad**: géneros minoritarios nunca puntúan alto →
  diversificación obligatoria (§8 y penalización δ).
- **Recálculo**: el perfil se puede cachear por usuario+media_type e
  invalidar al guardar `Media` (signal). Frecuencia propuesta: on-demand con
  caché, no cron.
- **Volumen**: limitar a top-K (p. ej. K=20) por sección; el resto no se
  materializa.

## 10. Métricas y validación

**Offline (sin tocar producción):**

- *Recall@k / nDCG@k* sobre un *hold-out* temporal: ocultar los últimos ~20%
  de títulos con nota `≥ 7` del usuario y medir si el algoritmo los
  recuperaría.
- *Purity* de géneros y *coverage* (¿recomienda algo fuera del top-10 de
  popularidad?).
- *Diversity* (genres distintos en el top-K) para detectar colapso.

**Online (cuando exista UI):**

- CTR de la sección, % de recomendaciones añadidas a la biblioteca, y nota
  posterior dada a lo recomendado.
- Tasa de "ocultar"/descartar como señal negativa.
- Guardrail técnico: latencia p95 y *cache hit ratio*.

**Umbral de éxito de la propuesta:** batir al baseline "recommendations de la
API sin personalizar" en nDCG@10 sobre el hold-out, o al menos no empeorar
CTR en A/B. Si con <N_min valoraciones no bate al baseline, activar fallback
§5 (no mostrar personalizado).

## 11. Pasos de validación (criterio de "hecho" de E8.1)

1. Dataset de ejemplo: exportar pares (usuario anónimo, títulos, `score`,
   `status`, géneros) de una instancia de desarrollo.
2. Implementar el scoring descrito como función pura en un *spike* aparte
   (no código productivo) y cubrirlo con tests de casos límite (§9).
3. Script offline de evaluación (nDCG@10, recall@20, purity) contra el baseline
   del proveedor; registrar resultados.
4. Calibrar `K`, `N_min`, `α/β/γ` con esos datos; documentar la elección.
5. Revisar con datos reales que el modo grupo (§8) no recomienda títulos que
   algún miembro haya descartado.
6. Cerrar con A/B en la sección real una vez implementada la épica.

## 12. Decisiones abiertas

- ¿Recomendación intra-media_type o cross-tipo (un juego porque te gustan los
  libros de ese género)? Propuesta: intra-tipo.
- ¿`Planning` cuenta como positivo leve o se ignora del todo en el perfil?
- ¿El modo grupo es vista por defecto o requiere elegir integrantes?
- ¿Top-K fijo o adaptativo por tamaño de historial?
