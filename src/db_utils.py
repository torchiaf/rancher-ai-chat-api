import json
import logging
import msgpack
import pickle
from datetime import datetime
    
async def extract_message_row(
    cur,
    checkpoint_id: str,
    checkpoint_raw,
    metadata_raw,
) -> tuple[ str | None, list[str] | None, str | None, str | None, str | None]:
    """
    Fetch messages from checkpoint_writes (msgpack+pickle) and sync to r_messages table.
    checkpoint_raw and metadata_raw are passed from the loop query.
    """
    
    logging.debug(f"[extract_message_row] Processing checkpoint_id={checkpoint_id}")
    
    try:
        checkpoint_data = checkpoint_raw if isinstance(checkpoint_raw, dict) else json.loads(checkpoint_raw)
        metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else (metadata_raw or {})
        step = metadata.get('step', '?')
        logging.debug(f"[extract_message_row] Loaded checkpoint: checkpoint_id={checkpoint_id}, step={step}")
    except Exception as e:
        logging.error(f"Failed to deserialize checkpoint: {e}")
        return None, None, None, None, None
    
    channel_values = checkpoint_data.get("channel_values", {})
    prompt = channel_values.get("prompt", "")
    logging.debug(f"[extract_message_row] Channel values keys: {list(channel_values.keys())}")

    logging.debug(f"[extract_message_row] Querying checkpoint_writes for checkpoint_id={checkpoint_id}")
    await cur.execute("""
        SELECT cw.channel, cw.blob
        FROM checkpoint_writes cw
        WHERE cw.checkpoint_id = %s
        ORDER BY cw.channel
    """, (checkpoint_id,))
    
    all_rows = await cur.fetchall()
    logging.debug(f"[extract_message_row] Found {len(all_rows)} checkpoint_writes rows for checkpoint_id={checkpoint_id}")
    
    messages_blob = None
    tags_blob = None
    context_blob = None
    mcp_blob = None
    
    for row in all_rows:
        channel = row['channel']
        blob = row['blob']
        logging.debug(f"[extract_message_row] Processing channel='{channel}', blob_size={len(blob) if blob else 0} bytes")
        
        if blob:
            try:
                import msgpack
                deserialized = msgpack.unpackb(blob, raw=False)
                logging.debug(f"[extract_message_row] Deserialized '{channel}': type={type(deserialized).__name__}, content_preview={str(deserialized)[:200]}")
            except Exception as e:
                logging.error(f"[extract_message_row] Failed to deserialize '{channel}': {e}")

        if channel == 'messages':
            messages_blob = blob
        elif channel == 'tags':
            tags_blob = blob
        elif channel == 'context':
            context_blob = blob
        elif channel == 'mcp_responses':
            mcp_blob = blob
    
    logging.debug(f"[extract_message_row] Blob assignment: messages={messages_blob is not None}, tags={tags_blob is not None}, context={context_blob is not None}, mcp={mcp_blob is not None}")

    user_msg, llm_msg = extract_messages(messages_blob)

    tags = extract_tags(tags_blob)
    
    context = extract_context(context_blob)
    
    mcp_responses_list = extract_mcp_responses(mcp_blob)
    
    logging.debug(f"[extract_message_row] Prompt from checkpoint: {len(str(prompt))} chars")
    
    # Fallback to prompt if no user message found
    if not user_msg and prompt:
        user_msg = prompt
        logging.debug(f"[extract_message_row] No user_msg extracted, using prompt fallback: {len(user_msg)} chars")
    
    # Convert data to final string formats
    logging.debug(f"[extract_message_row] Converting data to database formats...")
    context_str, tags_list, mcp_res = convert_data_to_strings(
        user_msg, llm_msg, mcp_responses_list, context, tags
    )
    
    logging.debug(f"[extract_message_row] FINAL RESULT: step={step}, user_len={len(user_msg)}, llm_len={len(llm_msg)}, tags={tags_list}, context_len={len(context_str)}, mcp_len={len(mcp_res)}")

    # Convert empty strings to None so COALESCE treats them as NULL in the database
    context_val = context_str if context_str else None
    user_val = user_msg if user_msg else None
    llm_val = llm_msg if llm_msg else None
    mcp_val = mcp_res if mcp_res else None

    return context_val, tags_list, user_val, mcp_val, llm_val

def extract_messages(messages_blob):
    """
    Extract and deserialize messages from checkpoint_writes blob.
    Handles multiple formats:
    - New format: Plain dicts with role/content
    - Old format: ExtType wrapping with type/human
    - Pickled bytes
    
    Returns: (user_msg, llm_msg) tuple
    """
    user_msg = ""
    llm_msg = ""
    
    if not messages_blob:
        logging.debug("[extract_messages] No messages_blob provided")
        return user_msg, llm_msg
    
    try:
        msgpack_data = msgpack.unpackb(messages_blob, raw=False)
        logging.debug(f"[extract_messages] Unpacked msgpack: {type(msgpack_data)}")
        
        messages = []
        
        # Handle different message formats from msgpack
        if isinstance(msgpack_data, list):
            for item in msgpack_data:
                if isinstance(item, dict):
                    # New format: already a dict with role/content
                    messages.append(item)
                    logging.debug(f"[extract_messages] Found dict message: {item.get('role', 'unknown')}")
                elif hasattr(item, 'data'):
                    # Old format: ExtType wrapping pickled data
                    ext_data = item.data
                    logging.debug(f"[extract_messages] Found ExtType, unpacking {len(ext_data)} bytes")
                    try:
                        inner_data = msgpack.unpackb(ext_data, raw=False)
                        if isinstance(inner_data, list):
                            messages.extend(inner_data)
                        elif isinstance(inner_data, bytes):
                            unpickled = pickle.loads(inner_data)
                            if isinstance(unpickled, list):
                                messages.extend(unpickled)
                            else:
                                messages.append(unpickled)
                    except Exception as e:
                        logging.error(f"[extract_messages] Failed to unpack ExtType: {e}")
                elif isinstance(item, (str, bytes)):
                    # Legacy: Pickled bytes
                    try:
                        if isinstance(item, str):
                            item_bytes = item.encode('latin-1')
                        else:
                            item_bytes = item
                        unpickled = pickle.loads(item_bytes)
                        if isinstance(unpickled, list):
                            messages.extend(unpickled)
                        else:
                            messages.append(unpickled)
                    except Exception as e:
                        logging.error(f"[extract_messages] Failed to unpickle: {e}")
        
        logging.debug(f"[extract_messages] Processed {len(messages)} messages")
        
        # Extract user and AI messages - support both old and new formats
        for msg in messages:
            if isinstance(msg, dict):
                # Support both formats: type/human and role/user
                msg_type = msg.get("type") or msg.get("role")
                content = msg.get("content", "")
                
                if msg_type in ("human", "user") and not user_msg:
                    user_msg = content
                    logging.debug(f"[extract_messages] Found user message: {len(content)} chars")
                elif msg_type in ("ai", "assistant") and not llm_msg:
                    llm_msg = content
                    logging.debug(f"[extract_messages] Found AI message: {len(content)} chars")
    
    except Exception as e:
        logging.error(f"[extract_messages] Failed to deserialize messages: {e}", exc_info=True)
    
    return user_msg, llm_msg

def extract_tags(tags_blob):
    """Extract and deserialize tags from blob."""
    if not tags_blob:
        return []
    
    tags_data = deserialize_msgpack_blob(tags_blob)
    if isinstance(tags_data, list):
        logging.debug(f"[extract_tags] Extracted tags: {tags_data}")
        return tags_data
    else:
        logging.warning(f"[extract_tags] tags_data is not a list: {type(tags_data)}")
        return []

def extract_context(context_blob):
    """Extract and deserialize context from blob."""
    if not context_blob:
        return {}
    
    context_data = deserialize_msgpack_blob(context_blob)
    if isinstance(context_data, dict):
        logging.debug(f"[extract_context] Extracted context keys: {list(context_data.keys())}")
        return context_data
    else:
        logging.warning(f"[extract_context] context_data is not a dict: {type(context_data)}")
        return {}

def extract_mcp_responses(mcp_blob):
    """Extract and deserialize mcp_responses from blob."""
    if not mcp_blob:
        return []
    
    mcp_data = deserialize_msgpack_blob(mcp_blob)
    if isinstance(mcp_data, list):
        logging.debug(f"[extract_mcp_responses] Extracted {len(mcp_data)} items")
        return mcp_data
    else:
        logging.warning(f"[extract_mcp_responses] mcp_data is not a list: {type(mcp_data)}")
        return []

def convert_data_to_strings(user_msg, llm_msg, mcp_responses_list, context, tags):
    """
    Convert extracted data to final string formats for database storage.
    
    Returns: (context_str, tags_list, mcp_res) tuple
    """
    # Convert mcp_responses to string
    if isinstance(mcp_responses_list, list):
        mcp_res = " ".join(str(m) for m in mcp_responses_list) if mcp_responses_list else ""
    else:
        mcp_res = str(mcp_responses_list) if mcp_responses_list else ""
    
    # Convert context to JSON string
    if isinstance(context, dict):
        context_str = json.dumps(context) if context else ""
    else:
        context_str = str(context) if context else ""
    
    # tags should be a list for the PostgreSQL array column
    if not isinstance(tags, list):
        tags_list = [str(tags)] if tags else None
    else:
        tags_list = tags if tags else None
    
    return context_str, tags_list, mcp_res

def deserialize_msgpack_blob(blob):
    """Deserialize a msgpack blob, handling errors gracefully."""
    if not blob:
        logging.debug("[deserialize_msgpack_blob] blob is None/empty")
        return None
    try:
        data = msgpack.unpackb(blob, raw=False)
        logging.debug(f"[deserialize_msgpack_blob] Successfully unpacked: {type(data)}")
        return data
    except Exception as e:
        logging.error(f"[deserialize_msgpack_blob] Failed to deserialize: {e}")
        return None
    
def parse_timestamp(ts_str: str | None) -> datetime | str | None:
    """
    Parse timestamp string to datetime object.
    Handles ISO 8601 format with Z suffix.
    """
    if not ts_str:
        return None
    
    try:
        return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return ts_str