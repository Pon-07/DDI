# AEGIS Rx - Clinical Safety and Drug-Drug Interaction Resolution Platform

AEGIS Rx is an offline medication safety and clinical decision-support platform providing deterministic drug-drug interaction (DDI) detection, transparent alternative ranking, cryptographic audit trails, and clinical role-based dashboards.

---

## 1. Primary Database: PostgreSQL & Neon Serverless

AEGIS Rx uses **PostgreSQL** as its primary production and cloud database (with native support for **Neon Serverless PostgreSQL** via connection pooling and auto-reconnect). SQLite remains available as an optional local development and test fallback.

### Database Architecture & Key Features
- **Connection URL Handling:** Reads `DATABASE_URL` (or `AEGIS_DATABASE_URL`). Automatically normalizes legacy `postgres://` and `postgresql://` prefixes to dialect-supported drivers (`postgresql+psycopg2://` or `postgresql+psycopg://`) while preserving query parameters (`?sslmode=require&channel_binding=require`).
- **Serverless Resilience & Pooling:** Configured with `pool_pre_ping=True` and `pool_recycle=300` to transparently handle Neon compute auto-suspend / re-activation without dropped connections.
- **Dialect Parity:** Cross-platform partial indexes (`postgresql_where` and `sqlite_where` for open substitution requests), sequence auto-increment management (`SERIAL` / `pg_get_serial_sequence`), and dialect-aware schema inspection.
- **Cryptographic Audit Ledger:** The SHA-256 tamper-evident audit ledger (`agents/audit/ledger.py`) operates natively on PostgreSQL with JSONB payloads while retaining 100% cryptographic parity and SQLite fallback.

---

## 2. Quickstart & Configuration

### Prerequisites & Dependencies
Install the required dependencies including PostgreSQL drivers and Alembic:
```bash
pip install -r requirements.txt
```

### Environment Configuration
Copy the configuration template:
```bash
cp .env.example .env
```

Configure your `DATABASE_URL` in `.env`:
```env
# Neon Serverless PostgreSQL (Production Target):
DATABASE_URL=postgresql+psycopg2://[user]:[password]@[endpoint].neon.tech/[dbname]?sslmode=require&channel_binding=require

# Local PostgreSQL:
# DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/aegis_rx

# SQLite Fallback:
# DATABASE_URL=sqlite:///./aegis_rx.db
```

---

## 3. Database Migrations (Alembic)

The project uses **Alembic** for tracked, repeatable schema migrations across all 16 clinical, auth, and audit tables.

### Run Migrations to Latest Schema:
```bash
alembic upgrade head
```

### Check Current Migration Version:
```bash
alembic current
```

### Stamp Existing Database at Head:
```bash
alembic stamp head
```

---

## 4. SQLite-to-PostgreSQL Data Migration & Validation Tool

A dedicated, idempotent migration tool (`scripts/migrate_sqlite_to_postgres.py`) safely transfers existing records from SQLite (`aegis_rx.db`) into PostgreSQL in strict foreign-key dependency order, resets sequence generators, and validates integrity:

### Perform Migration & Full Integrity Check:
```bash
python scripts/migrate_sqlite_to_postgres.py --sqlite-path aegis_rx.db --pg-url "postgresql+psycopg2://[user]:[pass]@[host]/[db]?sslmode=require"
```

### Inspect Source Data (Dry-Run):
```bash
python scripts/migrate_sqlite_to_postgres.py --dry-run
```

### Validate Parity Only (Without Modifying Data):
```bash
python scripts/migrate_sqlite_to_postgres.py --validate-only
```

### Rollback Guidance:
- The migration executes inside an atomic PostgreSQL transaction. Any database exception triggers an automatic rollback with no partial state committed.
- The source SQLite file is opened strictly in read-only mode (`mode=ro`) and is never modified.
- To reset a PostgreSQL schema to blank:
  ```sql
  DROP SCHEMA public CASCADE;
  CREATE SCHEMA public;
  ```
  Then re-apply migrations: `alembic upgrade head`.

---

## 5. Running the Application

Start the FastAPI application with Uvicorn:
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
- API Documentation (Swagger UI): `http://localhost:8000/docs`
- Health & DB Diagnostic: `http://localhost:8000/health/db`

---

## 6. Running the Test Suite

Run the full pytest suite against PostgreSQL or Neon:
```bash
# Against PostgreSQL / Neon:
DATABASE_URL="postgresql+psycopg2://[user]:[pass]@[host]/[db]?sslmode=require" python -m pytest tests/test_postgresql_neon_migration.py tests/test_audit_ledger.py tests/test_alternative_ranker.py tests/ml/test_severity_advisory.py -v

# Run entire suite:
python -m pytest -q
```

---

## 7. Alternative Ranker

`agents/resolution/alternatives.py` proposes replacement drugs for a flagged drug, ranked by a transparent weighted score (interaction risk 0.60, ATC-class closeness 0.25, dosing-evidence availability 0.15; weights and penalties live in `RANKER_CONFIG`). There is no LLM and no trained model in the decision. It is optional: `SafetyResolutionEngine(alternative_ranker=AlternativeRanker())`; by default nothing changes. Results appear as `result.alternatives` and every item carries `requires_cosign=True`.

- **Risk gate:** full ranked alternatives only for HIGH risk (finding severity critical/contraindicated/major/review_required/high, or a DDInter known Major for the flagged pair). MODERATE gives a monitoring action plus an optional secondary list of at most 3. LOW or undetermined gives no alternatives and says why. Duplicate therapy returns a "consolidate duplicate therapy" action instead of substitutes.
- **Candidates:** same ATC level-4 class (level 3 is added only if fewer than 3 level-4 candidates are free of a known Major; level 4 always ranks first). Active drugs, the flagged drug, identical RxCUI/ingredient and combination products are excluded. Each candidate is checked against every other active drug.
- **Order:** known Major last (and flagged); then level 4 before 3; then known Minor, known Moderate, unknown, ML-raised caution; then score; then name.
- **ML advisory:** for pairs DDInter does not label, the existing severity advisor's estimate is displayed with probabilities, confidence band and regime. It can only raise caution (a non-low-confidence Major lowers rank and flags pharmacist review). Minor/Moderate estimates never improve rank or make a pair "safe"; low-confidence estimates are shown as unreliable and change nothing. If the artifact is missing the ranker works without it.
- **Coverage:** `python scripts/alternative_ranker_coverage.py` regenerates `reports/alternative_ranker/coverage.md` (aggregate counts over the DDInter Major pairs; no drug names or ATC codes).

**Limits:** ATC class is not clinical equivalence (route, form, indication, potency and patient suitability are not checked). A pair absent from DDInter is unknown, not safe. The dosing component only says whether label evidence exists (keyword flags from the openFDA clean data); it never proposes or derives a dose. The ranker has not been clinically validated, and a pharmacist must review every suggestion. It reads the clean CSVs at runtime and does not copy ATC data anywhere else.
