<<<<<<< HEAD
from fastapi import APIRouter, Request, HTTPException, Response, Depends
from fastapi.responses import StreamingResponse
from typing import Union

from database import check_login, add_conversation, get_user_sessions, get_session_with_messages, create_chat_session, delete_chat_session, get_chat_session
from database.schema import User
from backend.schema import ChatRequest, SessionResponse, SessionMessagesResponse, PreChatResponse
from backend.llm import ModelManager

from .utils import NO_CACHE_HEADERS, get_timestamp, CommonResponse
=======
from fastapi import APIRouter, Request, HTTPException

from database import check_login, get_user_sessions, get_session_with_messages, create_chat_session, delete_chat_session, get_chat_session
from backend.schema import ChatRequest, SessionResponse, SessionMessagesResponse, PreChatResponse
from backend.llm import ModelManager

from .utils import CommonResponse

>>>>>>> origin/final

router = APIRouter()

# Dependency để check user role (không cho admin truy cập chat)
async def require_user_role(request: Request) -> User:
    user = await check_login(request)
    if user.role == "admin":
        raise HTTPException(
            status_code=403, 
            detail="Admin accounts cannot access chat features. Please use a regular user account."
        )
    return user
    
@router.post("/chat", name="chat")
<<<<<<< HEAD
async def chat(request: Request, data: ChatRequest, user: User = Depends(require_user_role)) -> PreChatResponse:
=======
async def chat(request: Request, data: ChatRequest) -> PreChatResponse:
    """
    Chat route.\n
    This would send a `PreChatResponse` first, which contain `WebSource`, `RagSource`, ...\n
    Then send access answer through `result_url` field.\n
    Result would be stored with a call from worker (See `worker_router`).\n
    Would need server to call `pre_inference` in worker, so we could prevent direct `pre_inference` request from unauthozied user, create new `ChatSession`,  provide chat history, ...
    """
    user = await check_login(request)
>>>>>>> origin/final
    session_id = data.session_id
    if session_id is None:
        session_id = await create_chat_session(user.id)
        if session_id is None:
            raise HTTPException(status_code=500, detail=f"Failed to create new chat session")
<<<<<<< HEAD
    user_timestamp = get_timestamp()
    async def finish_call(bot_answer: str, web_sources: list = None):
        if model_output != None:
            bot_timestamp = get_timestamp()
            # Cập nhật web_sources từ inference result
            if web_sources:
                model_output["web_sources"] = web_sources
            await add_conversation(
                user_id=user.id,
                session_id=session_id,
                user_text=data.text,
                bot_text=bot_answer,
                model_id=model_output["model_id"],
                web_sources=model_output["web_sources"],
                rag_sources=[],  # Luôn empty
                params=data.params,
                user_timestamp=user_timestamp,
                bot_timestamp=bot_timestamp, # Does not prevent incorrect order
                user_extra_data={},
                bot_extra_data=model_output["extra_data"]
            )
            try:
                from ..cache.history_cache import append_user_and_bot, Msg
                await append_user_and_bot(
                    session_id,
                    user_msg=Msg(role="user", text=data.text, timestamp=user_timestamp),
                    bot_msg=Msg(role="bot", text=bot_answer, timestamp=bot_timestamp)
                )
            except Exception:
                pass
    model_output = await ModelManager.pre_inference(data.text, data.model_id, data.params, finish_call, session_id)
    if model_output == None:
        raise HTTPException(status_code=503, detail="Server is not approved or Admin blocked this server")
    
    # Simplified response - sources đã clean
    response: PreChatResponse = {
        "stream_id": model_output["stream_id"],
        "session_id": session_id,
        "role": "bot",
        "web_sources": model_output["web_sources"],
        "rag_sources": [],  # Luôn empty
        "extra_data": model_output["extra_data"]
=======
    model_output = await ModelManager.pre_inference(session_id, user.id, data.text, data.params)
    if model_output == None:
        raise HTTPException(status_code=500, detail="Failed to inference model")    
    response: PreChatResponse = {
        "session_id": session_id,
        "role": "bot",
        "web_sources": model_output["web_sources"],
        "rag_sources": model_output["rag_sources"],
        "extra_data": model_output["extra_data"],
        "result_url": model_output["result_url"]
>>>>>>> origin/final
    }    
    return response

@router.get("/sessions")
<<<<<<< HEAD
async def sessions(request: Request, user: User = Depends(require_user_role)) -> list[SessionResponse]:
=======
async def sessions(request: Request) -> list[SessionResponse]:
    """Get list of `ChatSession`"""
    user = await check_login(request)
>>>>>>> origin/final
    sessions = await get_user_sessions(user.id)
    return [session.to_dict() for session in sessions] #type:ignore

@router.get("/session/{session_id}/messages")
<<<<<<< HEAD
async def session_messages(request: Request, session_id: str, user: User = Depends(require_user_role)) -> SessionMessagesResponse:
    chat_session = await get_session_with_messages(session_id)
    if chat_session and chat_session.user_id == user.id:
        # Simplified - sources đã clean
        messages = [msg.to_dict() for msg in chat_session.messages]
=======
async def session_messages(request: Request, session_id: str) -> SessionMessagesResponse:
    """Get all `ChatMessage` inside a `ChatSession`"""
    user = await check_login(request)
    chat_session = await get_session_with_messages(session_id)
    if chat_session and chat_session.user_id == user.id:
        # Fix web_sources in messages for backward compatibility
        messages = [msg.to_dict()for msg in chat_session.messages]
>>>>>>> origin/final
        result: SessionMessagesResponse = {
            "session": chat_session.to_dict(), #type:ignore
            "messages": messages
        }
        return result
    raise HTTPException(status_code=404, detail=f"Not found session with id: {session_id}")

@router.delete("/session/{session_id}")
<<<<<<< HEAD
async def delete_session(request: Request, session_id: str, user: User = Depends(require_user_role)):
=======
async def delete_session(request: Request, session_id: str):
    """Delete a `ChatSession`"""
    user = await check_login(request)
>>>>>>> origin/final
    chat_session = await get_chat_session(session_id)
    if chat_session and chat_session.user_id == user.id:
        chat_session = await delete_chat_session(chat_session)
        return CommonResponse(200, True, "Ok")
    else:
        raise HTTPException(status_code=404, detail=f"Not found session with id: {session_id}")