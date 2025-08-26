# db.py
import os
from sqlalchemy import create_engine
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

DB_URL = os.getenv("DB_URL")
if not DB_URL:
    raise RuntimeError("DB_URL is not set in environment")

# Create the SQLAlchemy engine
_engine = create_engine(DB_URL, pool_pre_ping=True, pool_recycle=1800)

def get_engine():
    return _engine
