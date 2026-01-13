import os
import httpx
import logging

DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "password")
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "postgres")

LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()

IS_DEV = os.getenv("IS_DEV", "true").lower() == "true"

RANCHER_HOST = os.getenv("RANCHER_HOST", "172.17.0.1")
RANCHER_USER = "admin" if IS_DEV else ""

def get_db_url() -> str:
    """
    Generate database connection URL.
    """
    return f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

async def get_user_id(request) -> str:
    if RANCHER_USER:
        return RANCHER_USER

    rancher_host = request.headers.get("Host", RANCHER_HOST)
    rancher_token = request.cookies.get("R_SESS")

    api_url = f"https://{rancher_host}/v3/users?me=true"

    try:
        async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
            resp = await client.get(api_url, headers={
                "Cookie": f"R_SESS={rancher_token}",
            })
            payload = resp.json() 
            
            user_id = payload["data"][0]["id"]
            
            if user_id:
                logging.info("user API returned: %s - userId %s", resp.status_code, user_id)

                return user_id
    except Exception as e:
        logging.error("user API call failed: %s", e)

    return None