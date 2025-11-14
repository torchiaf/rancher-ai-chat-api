import os
import pymysql
import uuid
import time
import httpx
import logging

from flask import Flask, jsonify, request, abort

app = Flask(__name__)

# configure logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_ROOT_PASSWORD", os.getenv("MYSQL_PASSWORD", "rancher-ai"))
MYSQL_DB = os.getenv("MYSQL_DATABASE", os.getenv("MYSQL_DB", "rancher-ai"))

def get_conn():
    return pymysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD, database=MYSQL_DB)

async def get_user_id(request) -> str:
    rancher_token = request.cookies.get("R_SESS")

    try:
        async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
            resp = await client.get("https://172.17.0.1/v3/users?me=true", headers={
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

@app.route("/sessions", methods=["GET"])
async def list_sessions():
    user_id = await get_user_id(request)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, session_id, user_id, active, created_at "
                "FROM sessions "
                "WHERE user_id=%s "
                "ORDER BY created_at DESC",
                (user_id,),
            )
            rows = cur.fetchall()
        return jsonify(rows)
    finally:
        conn.close()
        
@app.route("/sessions", methods=["POST"])
async def create_session():
    user_id = await get_user_id(request)
        
    data = request.get_json(force=True, silent=True)

    if not data or not data["chat_name"] or not user_id:
        abort(400, "user_id and chat_name are required")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sessions "
                "(session_id, user_id, active, created_at) "
                "VALUES (%s, %s, %s, %s)",
                (str(uuid.uuid4()), user_id, True, int(time.time())),
            )
            conn.commit()

            new_id = cur.lastrowid
            cur.execute(
                "SELECT id, session_id, user_id, active, created_at "
                "FROM sessions "
                "WHERE id=%s",
                new_id
            )

            row = cur.fetchone()
        return jsonify(row), 201
    finally:
        conn.close()
        
@app.route("/sessions/<int:session_id>", methods=["DELETE"])
async def delete_session(session_id):
    user_id = await get_user_id(request)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM sessions WHERE id=%s", (session_id,))
            row = cur.fetchone()
            
            if not row or row[0] != user_id:
                abort(404)
            
            cur.execute(
                "DELETE FROM sessions "
                "WHERE id=%s ",
                (session_id,)
            )
            conn.commit()
            if cur.rowcount == 0:
                abort(404)
        return "", 204
    finally:
        conn.close()

@app.route("/messages")
async def list_messages():
    user_id = await get_user_id(request)

    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT session_id, request_id, role, message, created_at "
            "FROM messages "
            "WHERE user_id=%s "
            "ORDER BY created_at DESC",
            (user_id,),
        )
        rows = cur.fetchall()
    conn.close()
    return jsonify(rows)

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
    logging.getLogger().setLevel(LOG_LEVEL)

    app.run(host="0.0.0.0", port=5000)