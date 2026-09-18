# Yamtrack - Entorno de Desarrollo Local

Este documento describe cómo levantar Yamtrack para desarrollo local. El entorno utiliza **Docker Compose** para PostgreSQL y Redis, y **uv** para ejecutar el código Python (Django y Celery) directamente en el host. Esto permite **hot-reload** del servidor Django y un ciclo de desarrollo muy rápido, manteniéndolo totalmente aislado de la instancia de producción.

## 1. Configuración inicial

Asegúrate de que los puertos `8000`, `5433` (Postgres dev) y `6380` (Redis dev) estén libres.

Crea el archivo `.env` en la raíz del proyecto (`/home/sigterm/projects/yamtrack/.env`). Django lo cargará automáticamente:

```ini
SECRET=dev-secret-key-1234
URLS=http://localhost:8000
DB_HOST=127.0.0.1
DB_PORT=5433
DB_NAME=yamtrack
DB_USER=yamtrack
DB_PASSWORD=yamtrack
REDIS_URL=redis://127.0.0.1:6380
TMDB_API=<PON_AQUI_LA_API_KEY_DE_TMDB>
ENV_DEBUG=True
REGISTRATION=True
```
*(Nota: Sustituye `<PON_AQUI_LA_API_KEY_DE_TMDB>` por el valor real que puedes consultar en `/home/sigterm/homelab/services/yamtrack/.env`)*.

## 2. Levantar servicios base (DB & Redis)

El proyecto incluye un `docker-compose.dev.yml` para los servicios de datos:

```bash
cd /home/sigterm/projects/yamtrack
docker compose -f docker-compose.dev.yml up -d
```
*Se levantarán los contenedores `yamtrack-dev-db` y `yamtrack-dev-redis`, sin colisionar con producción.*

## 3. Instalar dependencias e inicializar BD

Aprovechando `uv`, sincronizamos el entorno virtual y corremos las migraciones:

```bash
# 1. Instalar dependencias
uv sync

# 2. Correr migraciones
uv run python src/manage.py migrate

# 3. Crear superusuario para desarrollo
uv run python src/manage.py createsuperuser --username admin --email admin@example.com
```

## 4. Levantar la aplicación

Yamtrack necesita el servidor web y el worker de Celery (para tareas en background como actualizar metadatos). Necesitarás dos terminales.

**Terminal 1 (Servidor Django - con hot-reload):**
```bash
uv run python src/manage.py runserver 0.0.0.0:8000
```
La aplicación estará disponible en [http://localhost:8000](http://localhost:8000).

**Terminal 2 (Celery Worker):**
```bash
uv run celery --workdir src -A config worker --beat --loglevel DEBUG --without-mingle --without-gossip
```

## 5. Lanzar los tests

La suite de tests usa la base de datos local temporal. Para ejecutarla completa (igual que en CI):

```bash
uv sync --group test
uv run playwright install
uv run coverage run src/manage.py test app users integrations lists events --parallel --noinput
```

## 6. Parar y Destruir el entorno

Para detener temporalmente los servicios de Docker:
```bash
docker compose -f docker-compose.dev.yml stop
```

**Para destruir y recrear desde cero (borrar base de datos local de dev):**
```bash
docker compose -f docker-compose.dev.yml down -v
```
Esto borrará los volúmenes dev y permitirá empezar de nuevo desde el paso 2.