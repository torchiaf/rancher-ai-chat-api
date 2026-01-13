import logging
from flask import Flask, request, abort

from .config import LOG_LEVEL, get_user_id
from .db import get_db_chats, delete_db_chats, delete_db_chat, get_db_messages

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger(__name__)
    
app = Flask(__name__)

@app.route("/chats", methods=["GET"])
async def list_chats():
    user_id = await get_user_id(request)

    if not user_id:
        abort(400, "user_id not found")
    
    chats = []
    
    try:
        chat_rows = await get_db_chats(user_id, request.args)

        for row in chat_rows:
            messages = await get_db_messages(row["id"], user_id, request.args)
            if messages and len(messages) > 0:
                chats.append(row)
    except Exception as e:
      logging.error("Error fetching chats: %s", e)
      abort(500, "Internal server error")
    
    return chats, 200
        
@app.route("/chats", methods=["DELETE"])
async def delete_chats():
    user_id = await get_user_id(request)
    
    if not user_id:
        abort(400, "user_id not found")
        
    try:
        await delete_db_chats(user_id)
    except Exception as e:
        logging.error("Error deleting all chats: %s", e)
        abort(500, "Internal server error")

    return "", 204

@app.route("/chats/<chat_id>", methods=["DELETE"])
async def delete_chat(chat_id):
    user_id = await get_user_id(request)
    
    if not user_id:
        abort(400, "user_id not found")

    if not chat_id:
        abort(400, "chat_id is required")

    try:
        await delete_db_chat(chat_id, user_id)
    except ValueError as ve:
        logging.warning("Attempt to delete non-existent or unauthorized chat %s by user %s", chat_id, user_id)
        abort(404, str(ve))
    except Exception as e:
        logging.error("Error deleting chat %s: %s", chat_id, e)
        abort(500, "Internal server error")
        
    return "", 204

@app.route("/chats/<chat_id>/messages", methods=["GET"])
async def list_messages(chat_id):
    user_id = await get_user_id(request)
    
    if not user_id:
        abort(400, "user_id not found")

    if not chat_id:
        abort(400, "chat_id is required")

    messages = []
    
    try:
        messages = await get_db_messages(chat_id, user_id, request.args)
        
    except ValueError as ve:
        logging.warning("Attempt to delete non-existent or unauthorized chat %s by user %s", chat_id, user_id)
        abort(404, str(ve))
    except Exception as e:
        logging.error("Error fetching messages for chat %s: %s", chat_id, e)
        abort(500, "Internal server error")

    return messages, 200

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)