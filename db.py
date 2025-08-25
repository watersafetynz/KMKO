# db.py
import os
from urllib.parse import quote_plus
from sqlalchemy import create_engine

# Prefer full DB_URL if defined
DB_URL = os.getenv("DB_URL")

if not DB_URL:
    # Build fallback ODBC string manually
    driver = "{ODBC Driver 18 for SQL Server}"
    server = os.getenv("DB_SERVER", "heimatau.database.windows.net")
    db     = os.getenv("DB_NAME", "WSFL")
    user   = os.getenv("DB_USER")
    pwd    = os.getenv("DB_PASS")

    odbc_str = (
        f"DRIVER={driver};"
        f"SERVER={server};"
        f"DATABASE={db};"
        f"UID={user};"
        f"PWD={pwd};"
        "Encrypt=Yes;TrustServerCertificate=No;"
        "Connection Timeout=30;"
    )

    DB_URL = "mssql+pyodbc:///?odbc_connect=" + quote_plus(odbc_str)

# Create a single global engine
_engine = create_engine(DB_URL, pool_pre_ping=True, pool_recycle=1800)

def get_engine():
    return _engine
