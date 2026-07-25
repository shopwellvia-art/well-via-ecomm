"""Load-test DB bootstrap: build the schema straight from the ORM models
(bypassing alembic — the throwaway DB has no divergence to worry about), then
seed RBAC/settings/email-templates and a small product catalog to browse."""
import logging
import runpy
import sys

# The script is mounted at /loadtest but the backend package lives in /app.
sys.path.insert(0, "/app")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bootstrap_lt")

# app.db.base imports every model, so Base.metadata knows all tables.
from app.db.base import Base
from app.db.session import engine, SessionLocal

log.info("creating schema from ORM models ...")
Base.metadata.create_all(engine)

from app.services.rbac_seed import seed_rbac
from app.services.settings_seed import seed_settings
from app.services.email_templates.seed import seed_email_templates

db = SessionLocal()
try:
    seed_rbac(db)
    seed_settings(db)
    seed_email_templates(db)
    db.commit()
finally:
    db.close()
log.info("core seeds done")

# Product catalog + demo accounts via the repo's own seed script.
sys.argv = ["seed.py"]
runpy.run_path("/app/scripts/seed.py", run_name="__main__")
print("BOOTSTRAP_LT_DONE")
