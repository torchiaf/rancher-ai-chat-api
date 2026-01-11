import os

DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "password")
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "postgres")

IS_DEV = os.getenv("IS_DEV", "true").lower() == "true"

RANCHER_HOST = os.getenv("RANCHER_HOST", "172.17.0.1")
RANCHER_USER = "admin" if IS_DEV else ""

def get_db_url() -> str:
    """
    Generate database connection URL.
    """
    return f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"