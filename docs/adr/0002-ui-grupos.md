# ADR 0002: Rediseño de la UI de grupos y descarte

- **Estado:** aceptado como alcance de producto
- **Fecha:** 2026-09-23
- **Decisor:** producto

## Contexto

La UI actual de grupos pone la gestión antes que el uso: el detalle (`group_detail.html`) muestra la caja de invitar y la lista de miembros por encima de los medios, y no permite añadir medios al grupo (el servicio `add_item_to_group` existe pero ninguna vista lo llama). No hay pestañas, ni buscador dentro del grupo, ni ruleta; el registro de consumo se resuelve con radios binarios «Just me / Whole group» en lugar de selección de participantes; y la comparativa de valoraciones y las estadísticas de géneros viven en páginas separadas. El rediseño ordena la pantalla por prioridad de uso y fija las reglas del descarte («No me interesa»).

## Decisión

1. La pantalla del grupo se ordena por prioridad de uso: (1) añadir medios, (2) decidir qué ver, (3) actualizar el estado, (4) gestionar el grupo.
2. El diseño es genérico para N personas, no específico de pareja: contadores tipo «X/Y».
3. El grupo tiene cinco pestañas: **Pendientes** (con buscador para añadir arriba y botón de ruleta) · **Viendo** · **Vistos** · **Estadísticas** (comparativa de valoraciones y géneros) · **Ajustes** (miembros, invitaciones, salir). Pendientes es la pestaña por defecto. Las tres primeras se basan en el estado propio del grupo, no en la suma de registros personales.
4. El estilo es la rejilla de pósters del resto de la app, con indicador de qué miembros lo han visto.
5. Añadir usa el buscador dentro del grupo (reutiliza la búsqueda existente) más un botón «añadir a grupo» en la ficha del medio y en la búsqueda general.
6. Ruleta y recomendador no son exclusivos de grupo: existen en ámbito individual y de grupo.
7. El estado y el progreso son propios del grupo, según la spec de CONTEXT.md: el grupo tiene estado y progreso contextuales; al avanzar actualiza los registros personales sin reducir progreso ni reabrir completados.
8. Quitar un medio del grupo lo puede hacer cualquier miembro; solo lo quita del grupo, los registros personales se conservan.
9. El **descarte** («No me interesa») se marca desde la ruleta y el recomendador; excluye el medio de futuras tiradas y recomendaciones; es reversible. Existe en ámbito individual y de grupo:
   - En grupo basta con que un miembro descarte para que desaparezca para todo el grupo; cualquier miembro puede recuperarlo.
   - Individual y grupal son independientes: descartar para ti no lo descarta en tus grupos, y viceversa.
   - Recuperación: sección «Descartados» (en recomendaciones para lo individual, en el grupo para lo grupal) con botón «recuperar», más «deshacer» inmediato tras descartar.

## Consecuencias

Hay que construir las cinco pestañas con el estado propio del grupo como fuente, el buscador dentro del grupo y el botón «añadir a grupo» en ficha y búsqueda, la ruleta de grupo sobre pendientes, la comparativa y los géneros integrados en la pestaña de estadísticas, y el flujo de descarte con «deshacer» y sección «Descartados» en ambos ámbitos. La gestión de miembros e invitaciones se repliega a la pestaña de Ajustes.

## Alternativas descartadas

- **Diseño específico de pareja:** se descarta por el acuerdo 2; el diseño genérico con contadores «X/Y» cubre la pareja sin casos especiales.
- **Tabla compacta en lugar de rejilla de pósters:** se descarta por el acuerdo 4; la rejilla mantiene la coherencia visual con el resto de la app.
- **Ruleta solo de grupo:** se descarta por el acuerdo 6; la ruleta existe en ambos ámbitos.
- **Descarte por unanimidad en grupo:** se descarta por el acuerdo 9; basta un miembro para descartar y cualquiera puede recuperar.
- **Descarte individual que se propaga al grupo:** se descarta por el acuerdo 9; los ámbitos son independientes.
