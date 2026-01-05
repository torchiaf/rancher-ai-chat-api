import os
import pymysql
import uuid
import time
import httpx
import json
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
RANCHER_HOST = os.getenv("RANCHER_HOST", "https://172.17.0.1")

def get_conn():
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        cursorclass=pymysql.cursors.DictCursor
    )

async def get_user_id(request) -> str:
    rancher_host = request.headers.get("Host", RANCHER_HOST)
    rancher_token = request.cookies.get("R_SESS")

    api_url = f"{rancher_host}/v3/users?me=true"

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

@app.route("/chats", methods=["GET"])
async def list_chats():
    user_id = await get_user_id(request)

    if not user_id:
        abort(400, "user_id not found")

    # Request query parameters handling
    include_tags = request.args.getlist("include-tag")
    exclude_tags = request.args.getlist("exclude-tag")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            sql = (
                "SELECT s.chat_id as chatId, s.id as id, s.active as active, s.name as name, s.created_at as createdAt "
                "FROM chats s "
                "WHERE s.user_id=%s "
                "AND EXISTS ("
                "  SELECT 1 FROM messages m "
                "  WHERE m.chat_id = s.chat_id AND m.role = 'user' "
            )
            params = [user_id]
            tag_conditions = []
            if include_tags:
                tag_in = ' OR '.join(["JSON_CONTAINS(m.tags, %s)" for _ in include_tags])
                tag_conditions.append(f"({tag_in})")
                params.extend([json.dumps(tag) for tag in include_tags])
            if exclude_tags:
                tag_out = ' OR '.join(["JSON_CONTAINS(m.tags, %s)" for _ in exclude_tags])
                tag_conditions.append(f"NOT ({tag_out})")
                params.extend([json.dumps(tag) for tag in exclude_tags])
            if tag_conditions:
                sql += " AND (" + " AND ".join(tag_conditions) + ")"
            sql += ") ORDER BY s.created_at DESC"
            cur.execute(sql, params)
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
                (str(uuid.uuid4()), user_id, data["active"], data["name"], int(time.time())),
            )
            conn.commit()

            new_id = cur.lastrowid
            cur.execute(
                "SELECT id, chat_id, user_id, active, name, created_at "
                "FROM chats "
                "WHERE id=%s",
                (new_id,)
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

            if not row or row.get("user_id") != user_id:
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
    
    # Request query parameters handling
    include_tags = request.args.getlist("include-tag")
    exclude_tags = request.args.getlist("exclude-tag")

    res = []
    conn = get_conn()

    try:
        with conn.cursor() as cur:
            cur.execute("SET SESSION group_concat_max_len = 1000000")
            # Build SQL for tag filtering
            user_where = "m.chat_id=%s AND c.user_id = %s AND m.role = 'user'"
            agent_where = "m.chat_id=%s AND c.user_id = %s AND m.role IN ('llm', 'mcp')"
            params = []
            # user part
            params.append(chat_id)
            params.append(user_id)
            tag_conditions_user = []
            tag_conditions_agent = []
            if include_tags:
                tag_in = ' OR '.join(["JSON_CONTAINS(m.tags, %s)" for _ in include_tags])
                tag_conditions_user.append(f"({tag_in})")
                tag_conditions_agent.append(f"({tag_in})")
                params.extend([json.dumps(tag) for tag in include_tags])
            if exclude_tags:
                tag_out = ' OR '.join(["JSON_CONTAINS(m.tags, %s)" for _ in exclude_tags])                
                tag_conditions_user.append(f"NOT ({tag_out})")
                tag_conditions_agent.append(f"NOT ({tag_out})")
                params.extend([json.dumps(tag) for tag in exclude_tags])
            if tag_conditions_user:
                user_where += " AND (" + " AND ".join(tag_conditions_user) + ")"
            if tag_conditions_agent:
                agent_where += " AND (" + " AND ".join(tag_conditions_agent) + ")"
                # agent part
                params.append(chat_id)
                params.append(user_id)
                if include_tags:
                    params.extend([json.dumps(tag) for tag in include_tags])
                if exclude_tags:
                    params.extend([json.dumps(tag) for tag in exclude_tags])
            sql = (
                "SELECT chatId, requestId, role, message, context, tags, createdAt FROM ("
                "  SELECT c.chat_id AS chatId, "
                "         m.request_id AS requestId, "
                "         m.role, "
                "         m.message, "
                "         m.context, "
                "         m.tags, "
                "         m.created_at AS createdAt "
                "  FROM messages m "
                "  JOIN chats c ON c.chat_id = m.chat_id "
                "  WHERE " + user_where +
                "  UNION ALL "
                "  SELECT c.chat_id AS chatId, "
                "         m.request_id AS requestId, "
                "         'agent' AS role, "
                "         GROUP_CONCAT(m.message ORDER BY m.created_at SEPARATOR '\n') AS message, "
                "         '' AS context, "
                "         MAX(CASE WHEN m.role = 'llm' THEN m.tags ELSE NULL END) AS tags, "
                "         MIN(m.created_at) AS createdAt "
                "  FROM messages m "
                "  JOIN chats c ON c.chat_id = m.chat_id "
                "  WHERE " + agent_where +
                "  GROUP BY c.chat_id, m.request_id "
                ") AS merged "
                "ORDER BY createdAt DESC"
            )
            cur.execute(sql, params)
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