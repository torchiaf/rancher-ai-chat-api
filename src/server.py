import os
import uuid
import time
import httpx
import json
import logging
import asyncio
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from flask import Flask, jsonify, request, abort

from .config import RANCHER_HOST, RANCHER_USER, get_db_url

app = Flask(__name__)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

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

@app.route("/chats", methods=["GET"])
async def list_chats():
    user_id = await get_user_id(request)

    if not user_id:
        abort(400, "user_id not found")

    # Request query parameters handling
    include_tags = request.args.getlist("include-tag")
    exclude_tags = request.args.getlist("exclude-tag")

    db_url = get_db_url()
    conn = await AsyncConnection.connect(db_url)
    conn.row_factory = dict_row
    try:
        async with conn.cursor() as cur:
            sql = (
                "SELECT "
                "s.chat_id as \"chatId\", "
                "s.active as \"active\", "
                "s.name as \"name\", "
                "EXTRACT(EPOCH FROM s.created_at)::int as \"createdAt\""
                "FROM r_chats s "
                "WHERE s.user_id=%s "
                "AND s.name IS NOT NULL "
                "AND s.name <> '' "
                "AND EXISTS ("
                "  SELECT 1 FROM r_messages m "
                "  WHERE m.chat_id = s.chat_id"
            )
            params = [user_id]
            if include_tags:
                for tag in include_tags:
                    sql += " AND m.tags::text LIKE %s"
                    params.append(f"%{tag}%")
            if exclude_tags:
                for tag in exclude_tags:
                    sql += " AND (m.tags IS NULL OR m.tags::text NOT LIKE %s)"
                    params.append(f"%{tag}%")
            sql += ") ORDER BY s.created_at DESC"
            await cur.execute(sql, params)
            rows = await cur.fetchall()
            return jsonify(rows)
    finally:
        await conn.close()

@app.route("/chats", methods=["POST"])
async def create_chat():
    user_id = await get_user_id(request)
    
    if not user_id:
        abort(400, "user_id not found")
        
    data = request.get_json(force=True, silent=True)

    if not data or not data.get("name"):
        abort(400, "chat name is required")

    db_url = get_db_url()
    conn = await AsyncConnection.connect(db_url)
    conn.row_factory = dict_row
    try:
        async with conn.cursor() as cur:
            chat_id = str(uuid.uuid4())
            now = int(time.time())
            await cur.execute(
                "INSERT INTO r_chats "
                "(chat_id, user_id, active, name, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING chat_id as \"chatId\", user_id as \"userId\", active as \"active\", name as \"name\", EXTRACT(EPOCH FROM created_at)::int as \"createdAt\", EXTRACT(EPOCH FROM updated_at)::int as \"updatedAt\"",
                (chat_id, user_id, data.get("active", True), data["name"], now, now),
            )
            row = await cur.fetchone()
        await conn.commit()
        return jsonify(row), 201
    finally:
        await conn.close()
        
@app.route("/chats", methods=["DELETE"])
async def delete_chats():
    user_id = await get_user_id(request)
    
    if not user_id:
        abort(400, "user_id not found")

    db_url = get_db_url()
    conn = await AsyncConnection.connect(db_url)
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM r_messages "
                "WHERE chat_id IN ("
                "  SELECT chat_id FROM r_chats WHERE user_id=%s"
                ")",
                (user_id,)
            )

            await cur.execute(
                "DELETE FROM r_chats "
                "WHERE user_id=%s",
                (user_id,)
            )

        await conn.commit()
        return "", 204
    finally:
        await conn.close()

@app.route("/chats/<chat_id>", methods=["DELETE"])
async def delete_chat(chat_id):
    user_id = await get_user_id(request)
    
    if not user_id:
        abort(400, "user_id not found")

    if not chat_id:
        abort(400, "chat_id is required")

    db_url = get_db_url()
    conn = await AsyncConnection.connect(db_url)
    try:
        async with conn.cursor() as cur:
            await cur.execute("SELECT user_id FROM r_chats WHERE chat_id=%s", (chat_id,))
            row = await cur.fetchone()

            if not row or row[0] != user_id:
                abort(404)
            
            await cur.execute(
                "DELETE FROM r_messages "
                "WHERE chat_id=%s",
                (chat_id,)
            )
            
            await cur.execute(
                "DELETE FROM r_chats "
                "WHERE chat_id=%s",
                (chat_id,)
            )

        await conn.commit()
        return "", 204
    finally:
        await conn.close()

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

    db_url = get_db_url()
    conn = await AsyncConnection.connect(db_url)
    conn.row_factory = dict_row

    try:
        async with conn.cursor() as cur:
            # Build SQL that returns 2 rows per message: one user, one agent
            sql = (
                "SELECT "
                "    \"chatId\", "
                "    \"requestId\", "
                "    \"role\", "
                "    \"message\", "
                "    \"context\", "
                "    \"tags\", "
                "    \"createdAt\" "
                "FROM ("
                "    SELECT "
                "        chat_id as \"chatId\", "
                "        request_id as \"requestId\", "
                "        'user' as \"role\", "
                "        user_message as \"message\", "
                "        context as \"context\", "
                "        tags as \"tags\", "
                "        EXTRACT(EPOCH FROM created_at)::int as \"createdAt\" "
                "    FROM r_messages "
                "    WHERE chat_id = %s "
                "    UNION ALL "
                "    SELECT "
                "        chat_id as \"chatId\", "
                "        request_id as \"requestId\", "
                "        'agent' as \"role\", "
                "        COALESCE(mcp_responses, '') || COALESCE(llm_response, '') as \"message\", "
                "        context as \"context\", "
                "        tags as \"tags\", "
                "        EXTRACT(EPOCH FROM created_at)::int as \"createdAt\" "
                "    FROM r_messages "
                "    WHERE chat_id = %s "
                ") AS merged "
                "WHERE EXISTS (SELECT 1 FROM r_chats WHERE chat_id = %s AND user_id = %s) "
            )
            
            params = [chat_id, chat_id, chat_id, user_id]
            
            # Add tag filters
            if include_tags:
                for tag in include_tags:
                    sql += "AND tags @> ARRAY[%s] "
                    params.append(tag)
            
            if exclude_tags:
                for tag in exclude_tags:
                    sql += "AND (tags IS NULL OR NOT (tags @> ARRAY[%s])) "
                    params.append(tag)
            
            # Order by request_id and role (user first in each pair)
            sql += "ORDER BY \"requestId\" ASC, \"role\" = 'user' DESC"
            
            await cur.execute(sql, params)
            rows = await cur.fetchall()
            return jsonify(rows)
    finally:
        await conn.close()

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
    logging.getLogger().setLevel(LOG_LEVEL)

    app.run(host="0.0.0.0", port=5000)