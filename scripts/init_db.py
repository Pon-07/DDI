import sys
from pathlib import Path

# Ensure project root is in sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from database.database import Base, engine
from models import Event, Finding, Lab, Medication, Order, Patient  # noqa: F401


def init_db() -> None:
    """Initialize the SQLite database schema by creating all defined tables."""
    print("Initializing AEGIS Rx SQLite database...")
    Base.metadata.create_all(bind=engine)
    table_names = list(Base.metadata.tables.keys())
    print("Database tables created successfully:")
    for table_name in sorted(table_names):
        print(f"  - {table_name}")


if __name__ == "__main__":
    init_db()
