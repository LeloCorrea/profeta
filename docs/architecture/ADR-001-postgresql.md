# ADR-001: Migração SQLite → PostgreSQL

**Status:** Planejado (não executar antes de 1.000 usuários ativos)

## Contexto

O sistema usa SQLite com WAL mode. Funciona bem para até ~500 usuários simultâneos.
Limitações que justificarão a migração:
- SQLite não suporta conexões concorrentes de múltiplos processos (API + bot + job)
- Sem suporte a replicação ou read replicas
- Sem LISTEN/NOTIFY para eventos assíncronos

## Decisão

Migrar para PostgreSQL via Alembic quando atingir ~500 usuários ativos ou múltiplas VPS.

## Plano de execução

### 1. Preparação (sem downtime)
- Instalar `asyncpg` e `psycopg2-binary`
- Adicionar `POSTGRES_URL` ao `.env` e `config.py`
- `DATABASE_URL` permanece SQLite em dev; PostgreSQL em prod via env var
- Nenhuma mudança de código nos services (SQLAlchemy abstrai o dialeto)

### 2. Migração de schema
```bash
pip install alembic
alembic init migrations
# Configurar env.py para usar app.db.Base.metadata
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

### 3. Migração de dados
```bash
# Exportar SQLite
sqlite3 data/profeta.db .dump > dump.sql

# Adaptar e importar no PostgreSQL
# Usar pgloader para migração automatizada:
pgloader sqlite:///data/profeta.db postgresql://...
```

### 4. Mudanças de código obrigatórias
- `func.random()` → continua igual (PostgreSQL usa `RANDOM()`, SQLAlchemy traduz)
- `PRAGMA journal_mode=WAL` → remover listener em `db.py` (condicionado a "sqlite" já)
- Remover `aiosqlite` de requirements.txt, adicionar `asyncpg`

### 5. Connection pooling
```python
engine = create_async_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)
```

## Pontos de integração afetados

| Arquivo | Mudança |
|---------|---------|
| `app/db.py` | Engine URL + pool config |
| `requirements.txt` | asyncpg + psycopg2-binary |
| `.env` | DATABASE_URL aponta para PostgreSQL |
| `deploy/systemd/*.service` | Sem mudança |

## Rollback

Manter backup SQLite por 30 dias pós-migração. Rollback = trocar DATABASE_URL de volta.
