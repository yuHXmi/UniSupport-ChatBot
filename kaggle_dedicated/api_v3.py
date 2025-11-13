DOMAIN = "http://127.0.0.1:8000"
DEPLOY_URL = None#"https://uniadmission.me"


IS_LOCAL = DOMAIN == "http://127.0.0.1:8000"
BASE_PATH = "" if IS_LOCAL else "/kaggle/working"

ws_pipeline = None

import os
import requests
import io
import tarfile
import shutil
def unpack_folder(data: bytes, path: str):
    if os.path.exists(path): # Remove old code
        shutil.rmtree(path)
    with io.BytesIO(data) as tar_buffer:
        with tarfile.open(fileobj=tar_buffer, mode='r:gz') as tar:
            tar.extractall(path=path)
def unpack_file(data: bytes, path: str):
    os.makedirs(f"{BASE_PATH}files", exist_ok=True)
    if os.path.exists(path):
        os.remove(path)
    with open(path, 'wb') as file:
        file.write(data)
def unpack_list(*names: str):
    # if DOMAIN == "http://127.0.0.1:8000": return
    for name in names:
        if "." in name:
            url = f"{DOMAIN}/package/{name}"
        else:
            url = f"{DOMAIN}/package/{name}"
        data = requests.get(url).content
        if "." in name:
            unpack_file(data, f"files/{name}")
        else:
            unpack_folder(data, name)
unpack_list(
    "worker.env", "school_name.json", "school_alias.json","local.pkl"
)

from dotenv import load_dotenv
load_dotenv(f"{BASE_PATH}files/worker.env")

NGROK_PORT = 8002
if DOMAIN != "http://127.0.0.1:8000":
    import subprocess
    subprocess.run(["ngrok", "config", "add-authtoken", os.getenv("NGROK_TOKEN_1", "")])
    subprocess.Popen(["ngrok", "http", str(NGROK_PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)

# Replacement for cmd
    
from huggingface_hub import login
login(token=os.getenv("HUGGING_FACE_TOKEN"))

# cmd = [
#     "hf", "auth", "login",
#     "--token", os.getenv("HUGGING_FACE_TOKEN")
# ]
# import subprocess
# subprocess.run(cmd)
# print("")

from data_retriever import *
from server import *
from school_mapper import SchoolMapper
from typing import AsyncGenerator, NotRequired, Protocol
from typing import Callable, AsyncGenerator
from openai import AsyncOpenAI, OpenAI
from google import genai
from google.genai import types
import os
import pickle
import json
import asyncio
import enum
import traceback
import copy
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from typing import Protocol, AsyncGenerator, TypedDict
class KeywordInfo(TypedDict):
    query: str
    priority: float
    info: str
    school: str
class KeywordModelProtocol(Protocol):
    async def keywords(self, question: str, params: GenerationParams, threshold: float = 0.5) -> list[KeywordInfo]: ...
class RouterModelProtocol(Protocol):
    async def route(self, question: str, params: GenerationParams) -> list[dict]: ...
    
MODEL_ID = "Qwen/Qwen3-4B"
# Retriever config
search_config = WebsearchConfig(
    page_timeout=15,
    file_timeout=15,
)
rag_config = RagConfig(
    embedding_name="intfloat/multilingual-e5-small",
    device="cpu"
)
splitter_config = SplitterConfig(
    tokenizer_name=MODEL_ID,
    chunk_size=512,
    chunk_overlap=0,
    device="cpu"
)
table_merge_config = MergeTableConfig(
    k_max_previous=5,
    k_max_next=5
)
neighbor_config = MergeNeighborConfig(
    k_previous_chunks=1,
    k_next_chunks=1
)
# Sampling Params
PAGE_RERANKER_PARAMS = {
    "temperature": 0.7,
    "top_p": 0.9,
    "max_tokens": 4096
}
KEYWORDS_PARAMS = {
    "temperature": 0.5,
    "top_p": 0.9,
    "max_tokens": 4096
}
ROUTER_PARAMS = {
    "temperature": 0.7,
    "top_p": 0.9,
    "max_tokens": 1024
}
MODELS: list[ModelInfo] = [
    {
        "name": "GPT 4o mini",
        "id": "gpt-4o-mini"
    },
    {
        "name": "Gemini 2.5 flash",
        "id": "gemini-2.5-flash"
    }
]
CLIENT_INFO: WorkerServerInfo = {
    "name": "Test API",
    "domain": "http://127.0.0.1:8002", # Auto change when run with ngrok
    "models": MODELS
}

from instruction import *

class LocalRetriever:
    """Search in static db"""
    def __init__(self) -> None:
        with open(f"{BASE_PATH}files/local.pkl", 'rb') as file:
            self.all_docs = pickle.load(file)
    def _filter_docs(self, school_id: str, section: str) -> list:
        school_docs = [doc for doc in self.all_docs if doc.metadata.get("school_id") == school_id]
        if school_docs:
            filtered_docs = [doc for doc in school_docs if doc.metadata.get("section") == section]
        else:
            filtered_docs = []
        return filtered_docs
    def retrieve(self, keywords: list[dict]) -> tuple[list[WebSource], list[RagSource]]:
        web_sources: list[WebSource] = []
        rag_sources: list[RagSource] = []
        try:
            for kw in keywords:
                school_id = kw.get("school_id")
                section = kw.get("section")
                if school_id and section:
                    docs = self._filter_docs(school_id, section)
                    title = f"Tìm trường ĐH-CĐ - Cốc Cốc ({school_id})"
                    combined_content = "\n\n".join([doc.page_content for doc in docs])
                    description = combined_content[:100] + "..." if len(combined_content) > 100 else combined_content
                    web_source: WebSource = {
                        "query": f"{school_id}:{section}",
                        "title": title,
                        "description": description,
                        "url": "https://hoctap.coccoc.com/tim-truong-dh-cd",
                        "text": combined_content,
                        "files": [],
                        "score": 1
                    }
                    rag_source: RagSource = {
                        "chunk_index": 0,
                        "query": f"{school_id}:{section}",
                        "title": title,
                        "url": "https://hoctap.coccoc.com/tim-truong-dh-cd",
                        "text": combined_content,
                    }
                    web_sources.append(web_source)
                    rag_sources.append(rag_source)
        except:
            traceback.print_exc()
        finally:            
            return web_sources, rag_sources
        
class WebRetriever:
    """Search in web"""
    def __init__(self, llm_ranker: PageRerankModelProtocol, llm_keywords: KeywordModelProtocol) -> None:
        self.pipeline = DataRetrieverPipeline(
            llm_ranker,
            websearch_config=search_config,
            rag_config=rag_config,
            splitter_config=splitter_config,
            neighbor_merge_config=neighbor_config,
            table_merge_config=table_merge_config
        )
        self.llm_keywords = llm_keywords
        self.school_mapper = SchoolMapper(f"{BASE_PATH}files/school_name.json")
    async def start(self):
        """Initialize websearch"""
        await self.pipeline.start()
    async def retrive(self, question: str, params: GenerationParams) -> tuple[list[WebSource], list[RagSource]]:
        data = await self.llm_keywords.keywords(question, params)
        max_query = params.get("max_query", 1)
        queries = []
        school_restrict = params.get("school_domain", False)
        for item in data:
            if not school_restrict:
                queries.append(item["query"])
            else:
                school = item["school"]
                if school.strip() != "":
                    school_domains = self.school_mapper.domains_from_auto(school, 5)[:10]
                    print(f"[DOMAINS]", school_domains)
                    if len(school_domains) > 0:
                        queries.append([item["query"], school_domains])
        return await self.pipeline.retrieve(params, queries[:max_query])
    
class RouterRetriever:
    def __init__(self, llm_router: RouterModelProtocol, web_retriever: WebRetriever, local_retriever: LocalRetriever) -> None:
        self.web_retriever = web_retriever
        self.local_retriever = local_retriever
        self.router = llm_router
    async def retrieve(self, question: str, params: GenerationParams) -> tuple[list[WebSource], list[RagSource]]:
        use_websearch = params.get("use_websearch", False) and params.get("max_query", 0) > 0 and params.get("k_docs", 0) > 0 and params.get("k_pages", 0) > 0
        use_localdb = params.get("use_localdb", False)
        if use_websearch and use_localdb:
            local_queries = await self.router.route(question, params)
            if len(local_queries) > 0:
                return self.local_retriever.retrieve(local_queries)
            else:
                return await self.web_retriever.retrive(question, params)
        elif use_localdb:
            local_queries = await self.router.route(question, params)
            if len(local_queries) > 0:
                return self.local_retriever.retrieve(local_queries)
            else:
                return [], []
        elif use_websearch:
            return await self.web_retriever.retrive(question, params)
        else:
            return [], []
        
import openai
import glob

def _find_reranker_checkpoint(base_path: str = None) -> str | None:
    """Tìm LoRA adapter trong package/lora/qwen_reranker_06b_v1"""
    if base_path is None:
        base_path = BASE_PATH
    print(f"[Reranker] Searching for adapter, BASE_PATH={base_path}, cwd={os.getcwd()}")
    
    # Các đường dẫn có thể có (ưu tiên local trước)
    adapter_paths = [
        # Local paths (ưu tiên)
        "app/package/lora/qwen_reranker_06b_v1",
        os.path.join(os.getcwd(), "app", "package", "lora", "qwen_reranker_06b_v1"),
        # Downloaded paths
        f"{base_path}lora/qwen_reranker_06b_v1",
        f"{base_path}package/lora/qwen_reranker_06b_v1",
        "lora/qwen_reranker_06b_v1",
        "package/lora/qwen_reranker_06b_v1",
        # Other possible paths
        os.path.join(os.getcwd(), "package", "lora", "qwen_reranker_06b_v1"),
        os.path.join(os.getcwd(), "lora", "qwen_reranker_06b_v1"),
    ]
    
    for adapter_dir in adapter_paths:
        # Normalize path
        adapter_dir = os.path.normpath(adapter_dir)
        print(f"[Reranker] Checking: {adapter_dir} (exists: {os.path.exists(adapter_dir)})")
        if os.path.exists(adapter_dir) and os.path.isdir(adapter_dir):
            # Kiểm tra có adapter_model.safetensors
            adapter_file = os.path.join(adapter_dir, "adapter_model.safetensors")
            if os.path.exists(adapter_file):
                print(f"[Reranker] ✓ Found adapter at: {adapter_dir}")
                return adapter_dir  # Trả về thư mục chứa adapter
            else:
                print(f"[Reranker] Directory exists but adapter_model.safetensors not found")
                # List files in directory for debugging
                try:
                    files = os.listdir(adapter_dir)
                    print(f"[Reranker] Files in directory ({len(files)} files): {files}")
                except Exception as e:
                    print(f"[Reranker] Error listing directory: {e}")
        else:
            print(f"[Reranker] Path does not exist: {adapter_dir}")
    
    print("[Reranker] ✗ Adapter not found in any of the checked paths")
    return None

class APIModelCore:
    def __init__(self) -> None:
        self.gpt_client = AsyncOpenAI(api_key=os.getenv("OPEN_AI_API_KEY"))
        self.gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.logger = CmdLogger("Model")
        self._reranker_name = os.getenv("PAGE_RERANKER_MODEL", "Qwen/Qwen3-Reranker-0.6B")
        # Tìm checkpoint từ package/lora/qwen_reranker hoặc env variable
        self._reranker_checkpoint = os.getenv("PAGE_RERANKER_CHECKPOINT", None)
        if self._reranker_checkpoint is None:
            self._reranker_checkpoint = _find_reranker_checkpoint()
        if self._reranker_checkpoint:
            print(f"[Reranker] Found adapter at: {self._reranker_checkpoint}")
        else:
            print("[Reranker] No adapter checkpoint found, will use base model only")
        self._reranker_batch_size = int(os.getenv("PAGE_RERANKER_BATCH_SIZE", "8"))
        self._reranker_max_length = int(os.getenv("PAGE_RERANKER_MAX_LENGTH", "1024"))
        self._reranker_device: torch.device | None = None
        self._reranker_model: AutoModelForSequenceClassification | None = None
        self._reranker_tokenizer: AutoTokenizer | None = None
    async def call(self, call_type: CallType, instruction: str, prompt: str, params: GenerationParams) -> AsyncGenerator[str, None]:
        print(f"[API] {call_type} | Instruction length: {len(instruction)} | Prompt length: {len(prompt)} | kwargs: {params.get('kwargs')}")
        model_id = params["model_id"]
        if "gpt" in model_id:
            while True:
                try:
                    stream = await self.gpt_client.chat.completions.create(
                        model=model_id, 
                        messages=[
                            {"role": "system", "content": instruction},
                            {"role": "user", "content": prompt}
                        ],
                        max_tokens=params.get("max_tokens", 4096),
                        temperature=params.get("temperature", 0.7),
                        top_p=params.get("top_p", 0.9),
                        presence_penalty=params.get("presence_penalty", 0.1),
                        frequency_penalty=params.get("frequency_penalty", 0.0),
                        stream=True
                    )
                    total_text = ""
                    async for event in stream:
                        chunk = event.choices[0].delta.content
                        if chunk is not None:
                            total_text += chunk
                            yield chunk
                    return
                except openai.APIError as e:
                    print(e)
        else:
            gemini_config = types.GenerateContentConfig(
                temperature=params.get("temperature", 0.8),
                top_p=params.get("top_p", 0.9),
                top_k=params.get("top_k", 16),
                max_output_tokens=params.get("max_tokens", 4048)
            )
            stream = await self.gemini_client.aio.models.generate_content_stream(
                model=model_id,
                contents=[
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=instruction)]
                    ),
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=prompt)]
                    )
                ],
                config=gemini_config
            )
            async for chunk in stream:
                if chunk.candidates:
                    if chunk.candidates[0].content:
                        if chunk.candidates[0].content.parts:
                            for part in chunk.candidates[0].content.parts:
                                if part.text:
                                    yield part.text
    async def __call__(self, call_type: CallType, instruction: str, prompt: str, params: GenerationParams) -> AsyncGenerator[str, None]:
        return self.call(call_type, instruction, prompt, params)
    
class APIModel(APIModelCore):
    def _ensure_reranker_loaded(self):
        if self._reranker_model is not None and self._reranker_tokenizer is not None and self._reranker_device is not None:
            return
        device_str = "cuda" if torch.cuda.is_available() else "cpu"
        self._reranker_device = torch.device(device_str)
        self._reranker_tokenizer = AutoTokenizer.from_pretrained(
            self._reranker_name,
            trust_remote_code=True
        )
        # Set padding token if not already set
        if self._reranker_tokenizer.pad_token is None:
            if self._reranker_tokenizer.eos_token is not None:
                self._reranker_tokenizer.pad_token = self._reranker_tokenizer.eos_token
            else:
                # Fallback: add a special pad token
                self._reranker_tokenizer.add_special_tokens({"pad_token": "[PAD]"})
        torch_dtype = torch.float16 if torch.cuda.is_available() else torch.float32
        self._reranker_model = AutoModelForSequenceClassification.from_pretrained(
            self._reranker_name,
            trust_remote_code=True,
            torch_dtype=torch_dtype
        )
        # Resize token embeddings if we added a new pad token
        if self._reranker_tokenizer.pad_token_id is not None:
            if self._reranker_model.config.pad_token_id is None:
                self._reranker_model.config.pad_token_id = self._reranker_tokenizer.pad_token_id
            # Resize embeddings if needed (only if we added a new token)
            if len(self._reranker_tokenizer) > self._reranker_model.get_input_embeddings().weight.shape[0]:
                self._reranker_model.resize_token_embeddings(len(self._reranker_tokenizer))
        self._reranker_model.to(self._reranker_device)
        
        # Load trained LoRA adapter or checkpoint if provided
        if self._reranker_checkpoint is not None and os.path.exists(self._reranker_checkpoint):
            try:
                print(f"[Reranker] Loading from {self._reranker_checkpoint}")
                
                # Check if it's a LoRA adapter directory
                if os.path.isdir(self._reranker_checkpoint):
                    adapter_file = os.path.join(self._reranker_checkpoint, "adapter_model.safetensors")
                    if os.path.exists(adapter_file):
                        # Load LoRA adapter using PEFT or direct loading
                        try:
                            from peft import PeftModel
                            from safetensors.torch import load_file
                            import json
                            
                            # Check adapter config for num_labels
                            adapter_config_file = os.path.join(self._reranker_checkpoint, "adapter_config.json")
                            checkpoint_num_labels = None
                            if os.path.exists(adapter_config_file):
                                try:
                                    with open(adapter_config_file, 'r') as f:
                                        adapter_config = json.load(f)
                                        # Check if num_labels is in the config
                                        if 'num_labels' in adapter_config:
                                            checkpoint_num_labels = adapter_config['num_labels']
                                            print(f"[Reranker] Found num_labels in adapter_config: {checkpoint_num_labels}")
                                except Exception as e:
                                    print(f"[Reranker] Could not read adapter_config.json: {e}")
                            
                            # First, load modules_to_save weights directly from adapter file to check shapes
                            print("[Reranker] Loading modules_to_save weights directly from adapter file...")
                            adapter_state_dict = load_file(adapter_file)
                            
                            # Detect num_labels from score.weight shape if not in config
                            if checkpoint_num_labels is None:
                                for k, v in adapter_state_dict.items():
                                    if k.endswith(".score.weight") or k == "score.weight":
                                        # Shape is [num_labels, hidden_size]
                                        checkpoint_num_labels = v.shape[0]
                                        print(f"[Reranker] Detected num_labels from {k} shape: {checkpoint_num_labels}")
                                        break
                            
                            # Resize score layer if num_labels mismatch
                            if checkpoint_num_labels is not None and checkpoint_num_labels != self._reranker_model.config.num_labels:
                                print(f"[Reranker] Resizing score layer: {self._reranker_model.config.num_labels} -> {checkpoint_num_labels}")
                                # Get hidden size
                                hidden_size = self._reranker_model.config.hidden_size
                                # Create new score layer with correct num_labels
                                import torch.nn as nn
                                new_score = nn.Linear(hidden_size, checkpoint_num_labels, bias=False)
                                # Copy existing weights if possible (take first num_labels if checkpoint has fewer)
                                if hasattr(self._reranker_model, 'score'):
                                    old_weight = self._reranker_model.score.weight
                                    if old_weight.shape[0] >= checkpoint_num_labels:
                                        new_score.weight.data = old_weight[:checkpoint_num_labels].clone()
                                    else:
                                        # Initialize new weights
                                        new_score.weight.data.normal_(mean=0.0, std=0.02)
                                # Replace score layer
                                self._reranker_model.score = new_score
                                self._reranker_model.config.num_labels = checkpoint_num_labels
                                self._reranker_model.num_labels = checkpoint_num_labels
                                self._reranker_model.to(self._reranker_device)
                                print(f"[Reranker] Score layer resized successfully")
                            
                            # Extract modules_to_save weights (score.weight, classifier.weight)
                            modules_to_save_weights = {}
                            model_dict = self._reranker_model.state_dict()
                            
                            for k, v in adapter_state_dict.items():
                                # Look for modules_to_save keys (score, classifier)
                                # They might be: score.weight, base_model.model.score.weight, etc.
                                model_key = None
                                if k == "score.weight" or k.endswith(".score.weight"):
                                    model_key = "score.weight"
                                elif k == "score.bias" or k.endswith(".score.bias"):
                                    model_key = "score.bias"
                                elif k == "classifier.weight" or k.endswith(".classifier.weight"):
                                    model_key = "classifier.weight"
                                elif k == "classifier.bias" or k.endswith(".classifier.bias"):
                                    model_key = "classifier.bias"
                                elif k.startswith("base_model.model.") and ("score" in k or "classifier" in k):
                                    model_key = k.replace("base_model.model.", "")
                                
                                if model_key and model_key in model_dict:
                                    # Handle shape mismatch by resizing if needed
                                    if model_dict[model_key].shape != v.shape:
                                        print(f"[Reranker] Shape mismatch for {model_key}: model={model_dict[model_key].shape}, checkpoint={v.shape}")
                                        # For score.weight, we already resized above, so this shouldn't happen
                                        # But if it does, skip this weight
                                        continue
                                    modules_to_save_weights[model_key] = v.to(self._reranker_device)
                                    print(f"[Reranker] Found modules_to_save: {k} -> {model_key} (shape: {v.shape})")
                            
                            # Load modules_to_save weights into model before PEFT
                            if modules_to_save_weights:
                                print(f"[Reranker] Loading {len(modules_to_save_weights)} modules_to_save weights into model...")
                                missing, unexpected = self._reranker_model.load_state_dict(modules_to_save_weights, strict=False)
                                if missing:
                                    print(f"[Reranker] Missing keys: {missing}")
                                if unexpected:
                                    print(f"[Reranker] Unexpected keys: {unexpected}")
                            
                            print("[Reranker] Loading LoRA adapter using PEFT")
                            self._reranker_model = PeftModel.from_pretrained(
                                self._reranker_model,
                                self._reranker_checkpoint,
                                device=self._reranker_device
                            )
                            
                            # Verify score.weight exists in adapter
                            if hasattr(self._reranker_model, 'peft_config'):
                                print(f"[Reranker] PEFT config loaded: {list(self._reranker_model.peft_config.keys())}")
                            
                            # Check if score.weight is in the model
                            model_state = self._reranker_model.state_dict()
                            score_keys = [k for k in model_state.keys() if 'score' in k]
                            print(f"[Reranker] Keys with 'score' in model: {score_keys}")
                            
                            # Merge LoRA weights into base model for faster inference
                            print("[Reranker] Merging LoRA weights into base model...")
                            self._reranker_model = self._reranker_model.merge_and_unload()
                            # Ensure model is on correct device after merge
                            self._reranker_model.to(self._reranker_device)
                            
                            # Re-load modules_to_save weights after merge (to ensure they're preserved)
                            if modules_to_save_weights:
                                print("[Reranker] Re-loading modules_to_save weights after merge...")
                                missing, unexpected = self._reranker_model.load_state_dict(modules_to_save_weights, strict=False)
                                if missing:
                                    print(f"[Reranker] Missing keys when re-loading: {missing}")
                            
                            # Verify score.weight after merge
                            final_state = self._reranker_model.state_dict()
                            if 'score.weight' in final_state:
                                print(f"[Reranker] ✓ score.weight present after merge (shape: {final_state['score.weight'].shape})")
                                # Check if it's not the default initialized weight
                                if hasattr(self._reranker_model, 'score'):
                                    score_weight = self._reranker_model.score.weight
                                    weight_norm = torch.norm(score_weight).item()
                                    print(f"[Reranker] score.weight norm: {weight_norm:.6f} (should be > 0 if loaded correctly)")
                                    if weight_norm < 1e-6:
                                        print(f"[Reranker] ⚠ WARNING: score.weight norm is very small, might be default initialized!")
                            else:
                                print(f"[Reranker] ✗ score.weight NOT found after merge")
                                print(f"[Reranker] Available keys with 'score': {[k for k in final_state.keys() if 'score' in k]}")
                            
                            print(f"[Reranker] Successfully loaded and merged LoRA adapter from {self._reranker_checkpoint}")
                        except ImportError:
                            print("[Reranker] PEFT not available, loading adapter weights directly")
                            # Load directly from safetensors
                            from safetensors.torch import load_file
                            adapter_state_dict = load_file(adapter_file)
                            
                            print(f"[Reranker] Loaded {len(adapter_state_dict)} keys from adapter")
                            print(f"[Reranker] Sample adapter keys: {list(adapter_state_dict.keys())[:10]}")
                            
                            # Map adapter keys to model keys
                            model_dict = self._reranker_model.state_dict()
                            print(f"[Reranker] Model has {len(model_dict)} keys")
                            print(f"[Reranker] Model keys containing 'score': {[k for k in model_dict.keys() if 'score' in k]}")
                            
                            filtered_state_dict = {}
                            
                            for k, v in adapter_state_dict.items():
                                # LoRA adapter có thể có:
                                # 1. LoRA weights (lora_A, lora_B) - bỏ qua vì không có PEFT để merge
                                # 2. modules_to_save weights (score.weight, classifier.weight) - load trực tiếp
                                # 3. Keys với prefix base_model.model. - remove prefix
                                
                                model_key = k
                                if k.startswith("base_model.model."):
                                    model_key = k.replace("base_model.model.", "")
                                elif k.startswith("lora_"):
                                    # Skip LoRA weights khi không có PEFT
                                    continue
                                # Các keys khác (như score.weight, classifier.weight) giữ nguyên
                                
                                if model_key in model_dict:
                                    if model_dict[model_key].shape == v.shape:
                                        filtered_state_dict[model_key] = v.to(self._reranker_device)
                                        print(f"[Reranker] Loading key: {k} -> {model_key} (shape: {v.shape})")
                                    else:
                                        print(f"[Reranker] Shape mismatch for {k} -> {model_key}: adapter {v.shape} vs model {model_dict[model_key].shape}")
                                else:
                                    print(f"[Reranker] Key not in model: {k} -> {model_key}")
                            
                            print(f"[Reranker] Loading {len(filtered_state_dict)} matching keys into model")
                            missing_keys, unexpected_keys = self._reranker_model.load_state_dict(filtered_state_dict, strict=False)
                            
                            if missing_keys:
                                print(f"[Reranker] Missing keys after load: {missing_keys}")
                            if unexpected_keys:
                                print(f"[Reranker] Unexpected keys: {unexpected_keys}")
                            
                            # Verify score.weight was loaded
                            if 'score.weight' in filtered_state_dict:
                                print(f"[Reranker] ✓ score.weight successfully loaded (shape: {filtered_state_dict['score.weight'].shape})")
                            else:
                                print(f"[Reranker] ✗ score.weight NOT found in adapter or not loaded")
                                print(f"[Reranker] Available adapter keys with 'score': {[k for k in adapter_state_dict.keys() if 'score' in k]}")
                            
                            print(f"[Reranker] Successfully loaded adapter weights from {adapter_file}")
                            print(f"[Reranker] Note: LoRA weights not merged (PEFT not available). Only modules_to_save weights loaded.")
                    else:
                        print(f"[Reranker] adapter_model.safetensors not found in {self._reranker_checkpoint}")
                else:
                    # Handle regular checkpoint file
                    print(f"[Reranker] Loading checkpoint file")
                    
                    # Handle safetensors format
                    if self._reranker_checkpoint.endswith('.safetensors'):
                        try:
                            from safetensors.torch import load_file
                            state_dict = load_file(self._reranker_checkpoint)
                        except ImportError:
                            print("[Reranker] safetensors not installed, falling back to torch.load")
                            state_dict = torch.load(self._reranker_checkpoint, map_location=self._reranker_device)
                    else:
                        checkpoint = torch.load(self._reranker_checkpoint, map_location=self._reranker_device)
                        
                        # Handle different checkpoint formats
                        if isinstance(checkpoint, dict):
                            if 'model_state_dict' in checkpoint:
                                state_dict = checkpoint['model_state_dict']
                            elif 'state_dict' in checkpoint:
                                state_dict = checkpoint['state_dict']
                            else:
                                state_dict = checkpoint
                        else:
                            state_dict = checkpoint
                    
                    # Load state dict, handling mismatched keys
                    model_dict = self._reranker_model.state_dict()
                    
                    # Filter out keys that don't match
                    filtered_state_dict = {}
                    for k, v in state_dict.items():
                        if k in model_dict and model_dict[k].shape == v.shape:
                            filtered_state_dict[k] = v
                        else:
                            print(f"[Reranker] Skipping key {k} (shape mismatch or not in model)")
                    
                    # Load the filtered state dict
                    missing_keys, unexpected_keys = self._reranker_model.load_state_dict(filtered_state_dict, strict=False)
                    
                    if missing_keys:
                        print(f"[Reranker] Missing keys: {missing_keys}")
                    if unexpected_keys:
                        print(f"[Reranker] Unexpected keys: {unexpected_keys}")
                    
                    print(f"[Reranker] Successfully loaded checkpoint from {self._reranker_checkpoint}")
            except Exception as e:
                print(f"[Reranker] Failed to load checkpoint: {e}")
                traceback.print_exc()
        
        self._reranker_model.eval()
    def _make_page_text(self, page: SearchResult) -> str:
        parts = [
            page.get("title", ""),
            page.get("description", ""),
            page.get("url", "")
        ]
        return "\n\n".join([part for part in parts if part])
    async def route(self, question: str, params: GenerationParams) -> list[dict]:
        text = ""
        prompt = ROUTER_TEMPLATE.format(question=question)
        copy_params = copy.deepcopy(params)
        copy_params.update(ROUTER_PARAMS) #type:ignore 
        async for chunk in await self(
            call_type=CallType.ROUTER, 
            instruction=ROUTER_INSTRUCTION+ROUTER_PREFIX, 
            prompt=prompt, 
            params=copy_params
        ):
            text += chunk
        try:
            self.logger.log(text)
            result = json.loads(extract_json(text))
            return result
        except:
            traceback.print_exc()
            return []
    async def _llm_rerank_page(self, pages: list[SearchResult], query: str, relative_threshold: float, params: GenerationParams) -> list[SearchResult]:
        if len(pages) == 0:
            return []
        try:
            self._ensure_reranker_loaded()
        except Exception as e:
            traceback.print_exc()
            raise RuntimeError(f"Failed to load reranker model: {e}") from e
        assert self._reranker_model is not None
        assert self._reranker_tokenizer is not None
        assert self._reranker_device is not None

        page_texts = [self._make_page_text(page) for page in pages]

        def _run_reranker() -> list[float]:
            self._reranker_model.eval()
            scores: list[float] = []
            for start in range(0, len(page_texts), self._reranker_batch_size):
                end = start + self._reranker_batch_size
                batch_texts = page_texts[start:end]
                batch_queries = [query for _ in batch_texts]
                inputs = self._reranker_tokenizer(
                    batch_queries,
                    batch_texts,
                    padding=True,
                    truncation=True,
                    max_length=self._reranker_max_length,
                    return_tensors="pt"
                )
                inputs = {k: v.to(self._reranker_device) for k, v in inputs.items()}
                with torch.no_grad():
                    logits = self._reranker_model(**inputs).logits
                    logits = logits.view(-1)
                    batch_scores = logits.float().cpu().tolist()
                scores.extend(batch_scores)
            return scores

        try:
            scores = await asyncio.to_thread(_run_reranker)
        except Exception as e:
            traceback.print_exc()
            raise RuntimeError(f"Failed to run reranker inference: {e}") from e

        if len(scores) != len(pages):
            raise RuntimeError(f"Reranker returned {len(scores)} scores but expected {len(pages)} scores")

        self.logger.log("-----Original-----")
        if self.logger._enable:
            for page in pages:
                self.logger.log(f'{page["score"]:.3f} + {page["title"]}')

        max_score = float("-inf")
        for score, search_result in zip(scores, pages):
            score = float(score)
            max_score = max(max_score, score)
            search_result["score"] = score

        if max_score == float("-inf"):
            raise RuntimeError("Reranker returned all invalid scores (all -inf)")

        threshold_score = max_score * relative_threshold
        results: list[SearchResult] = []
        for search_result in pages:
            if search_result["score"] >= threshold_score:
                results.append(search_result)
        results = sorted(results, key=lambda r: r["score"], reverse=True)
        self.logger.log("-----Reorder-----")
        if self.logger._enable:
            for page in results:
                self.logger.log(f'{page["score"]:.3f} + {page["title"]}')
        return results
    def _construct_reranker_prompt(self, query: str, data: list[SearchResult]) -> str:
        candidates = [{
            "index": index+1, 
            "title": item["title"],
            "description": item["description"],
        } for index, item in enumerate(data)]
        candidates = "[" + ",\n".join([json.dumps(item, ensure_ascii=False) for item in candidates]) + "]"
        return PAGE_RERANKER_TEMPLATE.format(query=query, pages=candidates)
    async def keywords(self, question: str, params: GenerationParams, threshold: float = 0.5) -> list[KeywordInfo]:
        num_queries = params.get("max_query", 1)
        copy_params = copy.deepcopy(params)
        copy_params.update(KEYWORDS_PARAMS) #type:ignore
        prompt = KEYWORD_TEMPLATE.format(question=question)
        text = ""
        async for chunk in await self(
            call_type=CallType.KEYWORDS, 
            instruction=KEYWORDS_INTRUCTION, 
            prompt=KEYWORDS_PREFIX.replace("{num}", str(num_queries))+prompt, 
            params=copy_params
        ):
            text += chunk
        try:
            self.logger.log(text)
            result: list[KeywordInfo] = json.loads(extract_json(text))
            for item in result:
                self.logger.log(item)
            return result
        except:
            print(text)
            traceback.print_exc()
            return []
    async def _heristic_rerank_page(self, pages: list[SearchResult], query: str, relative_threshold: float, params: GenerationParams) -> list[SearchResult]:
        """Rerank search results using embedding similarity"""
        self.logger.log("-----Original-----")
        if self.logger._enable:
            for page in pages:
                self.logger.log(f'{page["score"]:.3f} + {page["title"]}')
        import numpy as np
        def normalize_text(text: str) -> str:
            text = text.lower().strip()
            text = re.sub(r"[^a-zA-Z0-9\u00C0-\u1EF9\s\.,;]", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            return text
        def detect_school(query: str, schools: dict) -> str | None:
            """Detect school from query using predefined keywords"""
            for school, aliases in schools.items():
                if any(alias in query for alias in aliases):
                    return school
            return None
        schools = {
            school: [normalize_text(alias) for alias in aliases]
            for school, aliases in json.load(open(f"{BASE_PATH}files/school_alias.json", "r", encoding="utf-8")).items()
        }
        embedding = ws_pipeline.retriever.web_retriever.pipeline._rag.embedding
        
        # If no embedding model available, return original results
        if not embedding:
            return pages
        
        query_norm = normalize_text(query)
        detected_school = detect_school(query_norm, schools)
        
        try:
            query_emb = embedding.embed_query(query_norm)
            max_score = 0
            for page in pages:
                title = page.get("title", "") or ""
                desc = page.get("description", "") or ""
                url = page.get("url", "") or ""

                # Chuẩn hóa
                title_norm = normalize_text(title)
                desc_norm = normalize_text(desc)
                url_norm = normalize_text(url)

                # Semantic embedding
                title_emb = embedding.embed_query(title_norm) if title_norm else None
                desc_emb = embedding.embed_query(desc_norm) if desc_norm else None
                url_emb = embedding.embed_query(url_norm) if url_norm else None

                # Cosine similarity
                def cos_sim(a, b):
                    norm_a = np.linalg.norm(a)
                    norm_b = np.linalg.norm(b)
                    if norm_a == 0 or norm_b == 0:
                        return 0.0
                    return float(np.dot(a, b) / (norm_a * norm_b))

                score = 0.0
                weights = {"title": 0.5, "desc": 0.3, "url": 0.2}
                if title_emb is not None:
                    score += cos_sim(query_emb, title_emb) * weights["title"]
                if desc_emb is not None:
                    score += cos_sim(query_emb, desc_emb) * weights["desc"]
                if url_emb is not None:
                    score += cos_sim(query_emb, url_emb) * weights["url"]

                # Heuristic ưu tiên trường trong query
                if detected_school:
                    aliases = [normalize_text(a) for a in schools.get(detected_school, [])]
                    if any(a in text for a in aliases for text in [url_norm, title_norm, desc_norm]):
                        score += 0.5
                    else:
                        for school, other_aliases in schools.items():
                            if school != detected_school:
                                other_aliases_norm = [normalize_text(a) for a in other_aliases]
                                if any(a in text for a in other_aliases_norm for text in [url_norm, title_norm, desc_norm]):
                                    score -= 0.5

                # Heuristic boost
                if any(kw in query_norm for kw in ["tuyển sinh", "ngành đào tạo"]):
                    if "tuyensinh247" in url_norm:
                        score += 0.1
                    if url_norm.endswith(".edu") or ".edu.vn" in url_norm:
                        score += 0.2
                page["score"] = score
                max_score = max(score, max_score)
            threshold_score = max_score * relative_threshold
            # Sort theo score giảm dần
            results = []
            for page in pages:
                if page["score"] >= threshold_score:
                    results.append(page)
            results = sorted(results, key=lambda x: x["score"], reverse=True)
            self.logger.log("-----Reorder-----")
            if self.logger._enable:
                for page in results:
                    self.logger.log(f'{page["score"]:.3f} + {page["title"]}')
            return results
            
        except Exception:
            # If any error occurs, return original results
            return pages
    async def rerank_page(self, pages: list[SearchResult], query: str, relative_threshold: float, params: GenerationParams) -> list[SearchResult]:
        use_llm_rerank = params.get("llm_rerank", False)
        if use_llm_rerank:
            return await self._llm_rerank_page(pages, query, relative_threshold, params)
        else:
            return await self._heristic_rerank_page(pages, query, relative_threshold, params)
        
class CombinedProtocol(ModelProtocol, KeywordModelProtocol, PageRerankModelProtocol, RouterModelProtocol):
    pass
class CustomQA:
    def __init__(self, model_protocol: CombinedProtocol) -> None:
        self.logger = CmdLogger("QA")
        web_retriever = WebRetriever(model_protocol, model_protocol)
        local_retriever = LocalRetriever()
        self.retriever = RouterRetriever(
            model_protocol,
            web_retriever,
            local_retriever
        )
        self.llm_call = model_protocol
    async def start(self):
        await self.retriever.web_retriever.start()
    async def inference(self, prompt: str, request: WorkerChatRequest) -> AsyncGenerator[str, None]:
        text = ""
        async for chunk in await self.llm_call(
            call_type=CallType.READER, 
            instruction=READER_UNTRAINED_INSTRUCTION+READER_UNTRAINED_PREFIX, 
            prompt=prompt, 
            params=request["params"]
        ):
            text += chunk
            yield chunk
    async def pre_inference(
        self,
        question: str,
        stream_id: str,
        params: GenerationParams
    ) -> tuple[str, ModelPreOutput]:
        web_sources, rag_sources = await self.retriever.retrieve(
            question, 
            params
        )
        context = SourceFormat()(rag_sources)
        prompt = READER_TEMPLATE.format(context=context, question=question)
        self.logger.start()
        pre_output: ModelPreOutput = {
            "generation_params": params,
            "web_sources": web_sources,
            "rag_sources": rag_sources,
            "extra_data": {
            },
            "result_url": stream_id,
        }
        return prompt, pre_output
    
async def main():
    global ws_pipeline
    api_model = APIModel()

    ws_pipeline = CustomQA(api_model)
    await ws_pipeline.start()
    import uuid
    class ServerModelImplement(ServerModel):  
        def __init__(self) -> None:
            self.request_storage: dict[str, tuple[str, WorkerChatRequest, ModelPreOutput]] = {}
        async def pre_inference(self, request: WorkerChatRequest) -> ModelPreOutput:
            stream_id = str(uuid.uuid4())
            params = request["params"]
            print(params)
            prompt, pre_output = await ws_pipeline.pre_inference(
                request["text"],
                stream_id,
                request["params"]
            ) 
            self.request_storage[stream_id] = (prompt, request, pre_output)
            return pre_output
        async def inference(self, stream_id: str) -> AsyncGenerator[str, None]:
            prompt, request, pre_output = self.request_storage.pop(stream_id)
            generator = ws_pipeline.inference(prompt, request)
            total = ""
            try:
                async for chunk in generator:
                    total += chunk
                    yield chunk
            finally:
                # Store chat data when finish
                model_output: ModelOutput = {
                    **pre_output,
                    "text": total
                }
                data: WorkerStoreChatData = {
                    "forward_kwargs": request["forward_kwargs"],
                    "model_output": model_output
                }
                await self.store(data)
                
    server_model = ServerModelImplement()
    app = construct_app(
        server_domain=DOMAIN,
        info=CLIENT_INFO,
        server_model=server_model,
        init_tasks=[],
        shutdown_tasks=[],
        is_local=IS_LOCAL,
        deploy_url=DEPLOY_URL
    )
    # CORS policy
    from fastapi.middleware.cors import CORSMiddleware
    origins = [
        "http://127.0.0.1:8000",
        "https://uniadmission.me",
        DOMAIN
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"]
    )
    import uvicorn

    uvicorn_config = uvicorn.Config(app, port=NGROK_PORT)
    uvicorn_server = uvicorn.Server(uvicorn_config)
    await uvicorn_server.serve()
    
asyncio.run(main())