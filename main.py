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

@app.route("/chats", methods=["GET"])
async def list_chats():
    user_id = await get_user_id(request)

    if not user_id:
        abort(400, "user_id not found")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Fetch chats that have at least one user message
            cur.execute(
                "SELECT s.id, s.chat_id, s.user_id, s.active, s.name, s.created_at "
                "FROM chats s "
                "WHERE s.user_id=%s "
                "AND EXISTS ( "
                " SELECT 1 FROM messages m WHERE m.chat_id = s.chat_id AND m.role = 'user' "
                ") "
                "ORDER BY s.created_at DESC",
                (user_id,),
            )
            rows = cur.fetchall()
        return jsonify(rows)
    finally:
        conn.close()

@app.route("/chats", methods=["POST"])
async def create_chat():
    user_id = await get_user_id(request)
    
    if not user_id:
        abort(400, "user_id not found")
        
    data = request.get_json(force=True, silent=True)

    if not data or not data["name"]:
        abort(400, "chat name is required")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO chats "
                "(chat_id, user_id, active, name, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (str(uuid.uuid4()), user_id, True, data["name"], int(time.time())),
            )
            conn.commit()

            new_id = cur.lastrowid
            cur.execute(
                "SELECT id, chat_id, user_id, active, name, created_at "
                "FROM chats "
                "WHERE id=%s",
                new_id
            )

            row = cur.fetchone()
        return jsonify(row), 201
    finally:
        conn.close()

@app.route("/chats/<chat_id>", methods=["DELETE"])
async def delete_chat(chat_id):
    user_id = await get_user_id(request)
    
    if not user_id:
        abort(400, "user_id not found")

    if not chat_id:
        abort(400, "chat_id is required")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM chats WHERE chat_id=%s", (chat_id,))
            row = cur.fetchone()
            
            if not row or row[0] != user_id:
                abort(404)
            
            cur.execute(
                "DELETE FROM chats "
                "WHERE chat_id=%s ",
                (chat_id,)
            )
            
            if cur.rowcount == 0:
                abort(404)

            # Delete associated messages
            cur.execute(
                "DELETE FROM messages "
                "WHERE chat_id=%s ",
                (chat_id,)
            )

            conn.commit()
        return "", 204
    finally:
        conn.close()

@app.route("/chats/<chat_id>/messages", methods=["GET"])
async def list_messages(chat_id):
    user_id = await get_user_id(request)
    
    if not user_id:
        abort(400, "user_id not found")

    if not chat_id:
        abort(400, "chat_id is required")

    res = []
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT m.chat_id, m.request_id, m.role, m.message, m.created_at "
                "FROM messages m "
                "WHERE m.chat_id=%s "
                "AND EXISTS ( "
                "   SELECT 1 FROM chats c WHERE c.chat_id = m.chat_id AND c.user_id = %s "
                ") "
                "ORDER BY m.created_at DESC",
                (chat_id, user_id),
            )
            rows = cur.fetchall()
            
            res = jsonify(rows)
    finally:
        conn.close()

    return res

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
    logging.getLogger().setLevel(LOG_LEVEL)

    app.run(host="0.0.0.0", port=5000)