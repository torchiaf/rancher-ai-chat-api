import os
import pymysql
import uuid
import time

from flask import Flask, jsonify, request, abort

app = Flask(__name__)

MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_ROOT_PASSWORD", os.getenv("MYSQL_PASSWORD", "rancher-ai"))
MYSQL_DB = os.getenv("MYSQL_DATABASE", os.getenv("MYSQL_DB", "rancher-ai"))

def get_conn():
    return pymysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD, database=MYSQL_DB)

@app.route("/sessions", methods=["GET"])
def list_sessions():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, session_id, user_id, active, created_at "
                "FROM sessions "
                "ORDER BY created_at DESC"
            )
            rows = cur.fetchall()
        return jsonify(rows)
    finally:
        conn.close()
        
@app.route("/sessions", methods=["POST"])
def create_session():
    data = request.get_json(force=True, silent=True)

    if not data or "user_id" not in data:
        abort(400, "user_id is required")
    
    user_id = data["user_id"]

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
def delete_session(session_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE id=%s", (session_id,))
            conn.commit()
            if cur.rowcount == 0:
                abort(404)
        return "", 204
    finally:
        conn.close()

@app.route("/messages")
def list_messages():
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT session_id, request_id, role, message, created_at "
            "FROM messages "
            "ORDER BY created_at DESC"
        )
        rows = cur.fetchall()
    conn.close()
    return jsonify(rows)

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)