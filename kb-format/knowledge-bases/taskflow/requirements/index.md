# Requirements

Source file: `requirements.txt`

Pinned pip requirements for a small FastAPI web API using PostgreSQL via SQLAlchemy, Alembic and psycopg, with JWT auth through python-jose and passlib. It separates core runtime packages from development tooling (pytest, ruff, mypy) and pins httpx as the HTTP client.

| Module | Pages | Summary |
| --- | --- | --- |
| [Web API Dependencies](modules/web-api-dependencies/01-web-api-dependencies.md) | pages not recorded | Header comment identifying the file as the dependency list for a FastAPI-based web API. |
| [Install:  pip install -r requirements.txt](modules/install-pip-install-r-requirements-txt/01-install-pip-install-r-requirements-txt.md) | pages not recorded | Install instruction comment: all packages are installed with pip install -r requirements.txt. |
| [Core Packages](modules/core-packages/01-core-packages.md) | pages not recorded | Runtime dependencies: FastAPI, Uvicorn, Pydantic and pydantic-settings, SQLAlchemy with Alembic and psycopg, python-jose, passlib and python-multipart. |
| [Development Packages](modules/development-packages/01-development-packages.md) | pages not recorded | Test and lint tooling: pytest, pytest-asyncio, pytest-cov, ruff and mypy. |
| [HTTP client (httpx)](modules/http-client-httpx/01-http-client-httpx.md) | pages not recorded | httpx 0.28.1, used for outbound HTTP calls and as the FastAPI TestClient transport. |

## Sections

**Web API Dependencies**
- [Web API Dependencies](modules/web-api-dependencies/01-web-api-dependencies.md) — pages not recorded

**Install:  pip install -r requirements.txt**
- [Install:  pip install -r requirements.txt](modules/install-pip-install-r-requirements-txt/01-install-pip-install-r-requirements-txt.md) — pages not recorded

**Core Packages**
- [Core Packages](modules/core-packages/01-core-packages.md) — pages not recorded

**Development Packages**
- [Development Packages](modules/development-packages/01-development-packages.md) — pages not recorded

**HTTP client (httpx)**
- [HTTP client (httpx)](modules/http-client-httpx/01-http-client-httpx.md) — pages not recorded
