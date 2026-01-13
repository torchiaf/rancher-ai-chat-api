import logging
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from .config import get_db_url
from .db_utils import extract_message_row

async def get_conn():
    """
    Connect to the database and return the connection.
    """
    db_url = get_db_url()
    conn = await AsyncConnection.connect(db_url)
    conn.row_factory = dict_row

    return conn

async def setup_database() -> None:
    """
    Connect to database and create custom tables if they do not exist.
    """
    conn = await get_conn()
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS r_chat_metadata (
                   chat_id VARCHAR(255) NOT NULL, 
                   user_id VARCHAR(255) NOT NULL, 
                   metadata JSONB, 
                   PRIMARY KEY (chat_id, user_id) 
                )
                """
            )
            logging.info("Created/verified r_chat_metadata table")        
        await conn.commit()
    finally:
        await conn.close()

async def get_db_chats(user_id: str, request_args: dict) -> list[dict]:
    conn = await get_conn()
    rows = []
    try:
        async with conn.cursor() as cur:
            sql = (
                "SELECT * FROM ("
                "  SELECT DISTINCT ON (c.thread_id) "
                "  c.thread_id as \"chatId\", "
                "  (c.metadata->>'user_id')::text as \"userId\", "
                "  'Chat ' || to_char((c.checkpoint->>'ts')::timestamp, 'YYYY-MM-DD HH24:MI:SS') as \"name\", "
                "  (c.checkpoint->>'ts') as \"createdAt\" "
                "  FROM checkpoints c "
                "  WHERE (c.metadata->>'user_id')::text=%s "
                "  ORDER BY c.thread_id, (c.checkpoint->>'ts')::timestamp DESC "
                ") as chats "
                "ORDER BY chats.\"createdAt\" DESC "
            )
            params = (user_id,)
            await cur.execute(sql, params)

            rows = await cur.fetchall()
            logging.info("Fetched %d chats for user_id: %s", len(rows), user_id)
    except Exception as e:
        logging.error("get_db_chats failed: %s", e)
        raise e
    finally:
        await conn.close()
        
    return rows

async def delete_db_chat(chat_id: str, user_id: str) -> None:
    conn = await get_conn()
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT (metadata->>'user_id')::text as user_id "
                "FROM checkpoints WHERE thread_id=%s",
                (chat_id,)
            )
            row = await cur.fetchone()

            if not row or row["user_id"] != user_id:
                raise ValueError("Chat not found or access denied")
            
            # Delete from checkpoint_blobs
            sql_blobs = (
                "DELETE FROM checkpoint_blobs "
                "WHERE thread_id IN ("
                "    SELECT thread_id FROM checkpoints "
                "    WHERE (metadata->>'user_id')::text=%s "
                "    AND thread_id=%s "
                ") "
            )
            params = (user_id, chat_id)

            await cur.execute(sql_blobs, params)

            res = cur.rowcount
            logging.debug("Deleted %d checkpoint blobs for user_id, chat_id: %s, %s", res, user_id, chat_id)
            
            # Delete from checkpoint_writes
            sql_writes = (
                "DELETE FROM checkpoint_writes "
                "WHERE thread_id IN ("
                "    SELECT thread_id FROM checkpoints "
                "    WHERE (metadata->>'user_id')::text=%s "
                "    AND thread_id=%s "
                ") "
            )
            await cur.execute(sql_writes, params)

            res = cur.rowcount
            logging.debug("Deleted %d checkpoint writes for user_id, chat_id: %s, %s", res, user_id, chat_id)
        
            # Delete from checkpoints
            sql_checkpoints = (
                "DELETE FROM checkpoints "
                "WHERE (metadata->>'user_id')::text=%s "
                "AND thread_id=%s "
            )
            await cur.execute(sql_checkpoints, params)

            res = cur.rowcount
            logging.debug("Deleted %d checkpoints for user_id, chat_id: %s, %s", res, user_id, chat_id)
        
        await conn.commit()

        logging.info("Deleted chat_id: %s for user_id: %s", chat_id, user_id)
    except Exception as e:
        logging.error("delete_db_chat failed: %s", e)
        raise e
    finally:
        await conn.close()

async def delete_db_chats(user_id: str) -> None:
    conn = await get_conn()
    try:
        async with conn.cursor() as cur:            
            # Delete from checkpoint_blobs
            sql_blobs = (
                "DELETE FROM checkpoint_blobs "
                "WHERE thread_id IN ("
                "    SELECT thread_id FROM checkpoints "
                "    WHERE (metadata->>'user_id')::text=%s "
                ") "
            )
            await cur.execute(sql_blobs, (user_id,))

            res = cur.rowcount
            logging.debug("Deleted %d checkpoint blobs for user_id: %s", res, user_id)
            
            # Delete from checkpoint_writes
            sql_writes = (
                "DELETE FROM checkpoint_writes "
                "WHERE thread_id IN ("
                "    SELECT thread_id FROM checkpoints "
                "    WHERE (metadata->>'user_id')::text=%s "
                ") "
            )
            await cur.execute(sql_writes, (user_id,))

            res = cur.rowcount
            logging.debug("Deleted %d checkpoint writes for user_id: %s", res, user_id)
            
            # Delete from checkpoints
            sql_checkpoints = (
                "DELETE FROM checkpoints "
                "WHERE (metadata->>'user_id')::text=%s "
            )
            await cur.execute(sql_checkpoints, (user_id,))

            res = cur.rowcount
            logging.debug("Deleted %d checkpoints for user_id: %s", res, user_id)
        
        logging.info("Deleted all threads for user_id: %s", user_id)
        await conn.commit()

    except Exception as e:
        logging.error("delete_db_chats failed: %s", e)
        raise e
    finally:
        await conn.close()
 
async def get_db_messages(chat_id: str, user_id: str, request_args: dict) -> list[dict]:
    conn = await get_conn()
    rows = []
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT (metadata->>'user_id')::text as user_id "
                "FROM checkpoints WHERE thread_id=%s",
                (chat_id,)
            )
            row = await cur.fetchone()

            if not row or row["user_id"] != user_id:
                raise ValueError("Chat not found or access denied")
            
            sql = (
                """
                SELECT c.metadata->>'request_id' AS request_id
                FROM checkpoint_writes cw
                JOIN checkpoints c ON cw.checkpoint_id = c.checkpoint_id
                WHERE c.thread_id = %s
                AND (c.metadata->>'user_id')::text = %s
                AND c.metadata->>'request_id' IS NOT NULL
                GROUP BY c.metadata->>'request_id'
                ORDER BY MIN(c.checkpoint_id) ASC;
                """
            )
            params = (chat_id, user_id)
            
            await cur.execute(sql, params)
            request_ids = await cur.fetchall()
            
            logging.debug("Found %d request_ids for chat_id: %s, user_id: %s", len(request_ids), chat_id, user_id)

            for row in request_ids:
                sql = (
                    """
                    SELECT DISTINCT
                        c.thread_id,
                        c.checkpoint_id,
                        (c.metadata->>'request_id') as request_id,
                        c.checkpoint,
                        c.metadata
                    FROM checkpoint_writes cw
                    JOIN checkpoints c ON cw.checkpoint_id = c.checkpoint_id
                    WHERE c.thread_id = %s
                    AND (c.metadata->>'user_id')::text = %s
                    AND c.metadata->>'request_id' = %s
                    ORDER BY c.checkpoint_id 
                    """
                )
                
                params = (chat_id, user_id, row['request_id'])
                
                await cur.execute(sql, params)
                rows_checkpoints = await cur.fetchall()
            
                user_row_msg = ""
                llm_row_msg = ""
                mcp_row_msg = ""
                context_row = None
                tags_row = None
                created_at_row = None

                for row in rows_checkpoints:                
                    checkpoint_id = str(row['checkpoint_id'])
                    request_id = row['request_id']
                    checkpoint_raw = row['checkpoint']
                    metadata_raw = row['metadata']
                    
                    logging.debug("Processing new checkpoint: %s, request_id=%s", checkpoint_id, request_id)
                    
                    context_val, tags_list, user_msg, mcp_msg, llm_msg = await extract_message_row(
                        cur,
                        checkpoint_id, 
                        checkpoint_raw,
                        metadata_raw
                    )
                    logging.debug(f"Extracted message: context_val={context_val}, tags_list={tags_list}, user_msg={user_msg}, mcp_msg={mcp_msg}, llm_msg={llm_msg}")

                    if not mcp_msg and not llm_msg and not user_msg:
                        logging.warning("No user or agent message content found for checkpoint_id: %s", checkpoint_id)
                        continue

                    if user_msg and user_msg != "":
                        user_row_msg = user_msg
                        
                    if mcp_msg and mcp_msg != "":
                        mcp_row_msg = mcp_msg
                        
                    if llm_msg and llm_msg != "":
                        llm_row_msg = llm_msg
                        
                    if context_val and context_val != "":
                        context_row = context_val
                        
                    if tags_list and tags_list != []:
                        tags_row = tags_list
                        
                    if not created_at_row:
                        created_at_row = checkpoint_raw.get('ts')
                
                # TODO: complete tag filtering here, for now we filter out 'welcome' and 'confirmation' user tags only
                # Example:
                #   tags:include, tags:exclude, tags:include:user, tags:exclude:user, tags:include:agent, tags:exclude:agent
                
                if 'welcome' in (tags_row or []):
                    logging.debug("Excluding message row due to 'welcome' tag for request_id: %s", row['request_id'])
                    continue

                if user_row_msg and user_row_msg != "":
                    if 'confirmation' in (tags_row or []):
                        logging.debug("Excluding user message row due to 'confirmation' tag for request_id: %s", row['request_id'])
                    else:
                        user_row = {
                            "chatId": chat_id,
                            "requestId": row['request_id'],
                            "role": "user",
                            "message": user_row_msg,
                            "context": context_row,
                            "tags": tags_row,
                            "createdAt": created_at_row
                        }
                        rows.append(user_row)
                        logging.debug("Appended user message row %s", user_row)

                if llm_row_msg or mcp_row_msg:
                    agent_row = {
                        "chatId": chat_id,
                        "requestId": row['request_id'],
                        "role": "agent",
                        "message": mcp_row_msg + llm_row_msg,
                        "context": None,
                        "tags": None,
                        "createdAt": created_at_row
                    }
                    rows.append(agent_row)
                    logging.debug("Appended agent message row %s", agent_row)

            logging.info("Fetched %d messages for chat_id: %s, user_id: %s", len(rows), chat_id, user_id)
    except Exception as e:
        logging.error("get_db_messages failed: %s", e)
        raise e
    finally:
        await conn.close()
        
    return rows
        
