from datetime import datetime, timezone

<<<<<<< HEAD
from core.types import ModelInfo, GenerationParams, ModelPreOutput
from config import GEMINI_MODEL, SYSTEM_INSTRUCTION

from ..cache.history_cache import get_history
from ..search.search_router import search  # Tận dụng hàm search có sẵn
from .utils import load_history_from_db
from .schema import APIJobInfo
from .gemini import GeminiAPIModel
from .utils import generate_id
from .kaggle import KaggleManager
=======
from core import ModelInfo, GenerationParams, ModelPreOutput, ModelOutput, ChatMessage
from database import add_conversation, get_session_with_messages

from .worker import WorkerManager
>>>>>>> origin/final

import uuid

def generate_id() -> str:
    return str(uuid.uuid4())
class ModelManager:
<<<<<<< HEAD
    _gemini_api = GeminiAPIModel()
    _jobs: dict[str, JobInfo] = {} # No timeout implemented yet
            
    @classmethod
    async def pre_inference(cls, question: str, model_id: str, params: GenerationParams, finish_call: Callable[[str], Awaitable], session_id: Optional[str] = None) -> ModelPreOutput | None:
        job_id = generate_id()
        server_kwargs: dict | None = None
        if model_id.startswith("api:"):
            # Get web search params and pre-compute sources for pre_output
            k_pages = params.get("k_pages", 0)
            domain_restrict = params.get("domain_restrict", False)
            
            # Pre-compute web sources to include in response
            try:
                prompt, web_sources = cls._gemini_api.build_prompt(question, k_pages, domain_restrict)
            except Exception as e:
                web_sources = []
            
            conversation_history = []
            if session_id:
                try:
                    msgs = await get_history(session_id, loader=load_history_from_db)
                    for m in msgs:
                        conversation_history.append({"role": "model" if m.role == "bot" else "user", "parts": [{"text": m.text}]})
                except Exception:
                    pass
            conversation_history.append({"role": "user", "parts": [{"text": prompt}]})

            pre_output: ModelPreOutput | None = {
                "stream_id": job_id,
                "model_id": model_id,
                "generation_params": params,
                "web_sources": web_sources,
                "rag_sources": [],
                "extra_data": {}
            }
            domain = ""
            server_kwargs = {
                "model_id": GEMINI_MODEL,
                "text": question,
                "conversation": conversation_history,
                "generation_params": params,
                "session_id": session_id,  # Add session_id,
                "web_sources": web_sources  # Cache web sources to avoid duplicate search
            }
        else:
            # Kaggle models - Check if any servers are blocked before doing search
            from .kaggle import KaggleManager
            available_servers = await KaggleManager.get_available_servers(model_id)
            
            if not available_servers:
                return None
            
            conversation_history = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
            if session_id:
                try:
                    msgs = await get_history(session_id, loader=load_history_from_db)
                    for m in msgs:
                        conversation_history.append({"role": ("bot" if m.role == "bot" else "user"), "content": m.text})
                except Exception:
                    conversation_history = []

            # Xử lý search strategy
            k_pages = params.get("k_pages", 0)
            domain_restrict = params.get("domain_restrict", False)
            
            localdb_sources = None
            web_keywords = None
            if k_pages > 0:  # Chỉ search khi có k_pages
                try:
                    # Lấy search strategy từ router
                    from ..search.search_router import route_search
                    search_strategy = route_search(question)
                    type_search = search_strategy.get("type_search")
                    keywords = search_strategy.get("key_word", [])
                    
                    if type_search == "local_db":
                        # Local DB search tại app/ level
                        from ..search.localdb_search import search_from_local_database
                        localdb_results = search_from_local_database(keywords)
                        if localdb_results:
                            for result in localdb_results:
                                if any("tuyen_sinh" in kw.get("section", "") for kw in keywords) and len(result["text"].split()) <= 50:
                                    web_keywords = [question]
                                    localdb_results = None
                                    break
                                elif any("hoc_phi" in kw.get("section", "") for kw in keywords) and len(result["text"].split()) <= 20:
                                    web_keywords = [question]
                                    localdb_results = None
                                    break
                            localdb_sources = localdb_results
                        else:
                            # Local search fail → fallback to web search keywords
                            web_keywords = [question]
                    
                    elif type_search == "web_search":
                        # Web search sẽ được xử lý bởi kaggle/
                        web_keywords = keywords
                    
                    else:
                        # Fallback
                        web_keywords = [question]
                        
                except Exception as e:
                    # Fallback: pass original question as keyword
                    web_keywords = [question]

            kaggle_preinference = await KaggleManager.pre_inference(job_id, question, model_id, params, conversation_history, localdb_sources, web_keywords)
            if kaggle_preinference is not None:
                domain, pre_output = kaggle_preinference
            else:
                return None
        job_info: JobInfo = {
            "domain": domain,
            "finish_call": finish_call,
            "id": job_id,
            "server_kwargs": server_kwargs
        }
        cls._jobs[job_id] = job_info
        return pre_output
    
    @classmethod
    async def inference(cls, job_id: str) -> AsyncGenerator[str, None]:
        if job_id not in cls._jobs:
            # Check if job_id is undefined/None - likely due to server approval issues
            if job_id == "undefined" or job_id == "null" or not job_id:
                yield "Error: Server is not approved or Admin blocked this server"
            else:
                yield f"Error: Job ID '{job_id}' not found"
            return
            
        job_info = cls._jobs.pop(job_id)
        if job_info["server_kwargs"]:
            kwargs = job_info["server_kwargs"]

            params: GenerationParams = kwargs["generation_params"]
            model_id: str = kwargs["model_id"]
            conversation = kwargs["conversation"]

            api_job_info = APIJobInfo(
                model_id=model_id,
                conversation=conversation,
                sampling_params=params,
                web_sources=kwargs.get("web_sources"),
                session_id=kwargs.get("session_id")
            )

            total = ""
            async for chunk in cls._gemini_api.inference(api_job_info):
                total += chunk
                yield chunk
            
            # Lấy web_sources từ api_job_info sau khi inference
            await job_info["finish_call"](total, api_job_info.get("web_sources", []))
        else:
            total = ""
            job_info_id = job_info["id"]
            async for chunk in KaggleManager.inference(job_info["domain"], job_id):
                total += chunk
                yield chunk
            
            # Kaggle models - chỉ lấy web_sources
            web_sources, _ = KaggleManager.get_stored_sources(job_info_id)
            await job_info["finish_call"](total, web_sources)
            
=======
    """Stateless Manager class. Depend on `WorkerManager` whichs is statefull.""" 
    @classmethod
    async def pre_inference(cls, session_id: str, user_id: str, text: str, params: GenerationParams) -> ModelPreOutput | None:
        """Auto select suitable worker according to model id and return `ModelPreOutput`. Return `None` when failed."""
        # Performance can worry later
        session = await get_session_with_messages(session_id)
        if session is None: raise Exception("Unknown error while pre inference")
        messages_history = session.messages[-params.get("max_history", 0):]
        history: list[ChatMessage] = [
            ChatMessage(
                role=message.role, #type:ignore
                text=message.text
            ) for message in messages_history
        ] 
        result = await WorkerManager.pre_inference(
            user_id=user_id,
            session_id=session_id,
            text=text,
            history=history,
            params=params   
        )
        return result
>>>>>>> origin/final
    @classmethod
    async def get_models(cls) -> list[ModelInfo]:
        return await WorkerManager.get_models()
    @classmethod
    async def store_chat(cls, user_id: str, session_id: str, user_text: str, user_timestamp: datetime | str, model_output: ModelOutput):
        """
        Update chat in database. Call in worker router only. \n
        Should be used when worker finish inference on their server, then send request to this server to store in database.
        """
        if isinstance(user_timestamp, str):
            user_timestamp = datetime.fromisoformat(user_timestamp)
        bot_timestamp = datetime.now(timezone.utc)
        user_msg_id, bot_msg_id = await add_conversation(
            user_id=user_id,
            session_id=session_id,
            user_text=user_text,
            bot_text=model_output["text"],
            web_sources=model_output["web_sources"],
            rag_sources=model_output["rag_sources"],
            params=model_output["generation_params"],
            user_timestamp=user_timestamp,
            bot_timestamp=bot_timestamp, # Does not prevent incorrect order
            user_extra_data={},
            bot_extra_data=model_output["extra_data"]
        )