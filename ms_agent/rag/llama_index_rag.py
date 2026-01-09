import os
import copy
import shutil
from typing import Any, List, Optional, Dict

from ms_agent.utils import assert_package_exist
from omegaconf import DictConfig, OmegaConf

from modelscope import snapshot_download
from ..llm import LLM, Message
from .base import RAG
from ms_agent.utils.constants import get_service_config
from ms_agent.utils.logger import logger


def _parse_llamaindex_filters(filters):
    from llama_index.core.vector_stores import (
        MetadataFilter,
        MetadataFilters,
        FilterOperator
    )

    op_map = {
        "eq": FilterOperator.EQ,
        "ge": FilterOperator.GTE,
        "le": FilterOperator.LTE,
        "gt": FilterOperator.GT,
        "lt": FilterOperator.LT,
    }
    filter_list = []

    for key, value in filters.items():

        # case 1: range（dict）
        # eg： time={"ge": "2023-01-01", "le": "2023-12-31"}
        if isinstance(value, dict):
            for op, val in value.items():
                if op not in op_map:
                    raise ValueError(f"Unsupported operator: {op}")
                filter_list.append(
                    MetadataFilter(
                        key=key,
                        value=val,
                        operator=op_map[op]
                    )
                )

        # case 2: multi values OR（list）
        # eg： source=["wiki", "arxiv"]
        elif isinstance(value, (list, tuple, set)):
            for val in value:
                filter_list.append(
                    MetadataFilter(
                        key=key,
                        value=val,
                        operator=FilterOperator.EQ
                    )
                )

        # case 3: single value EQ
        else:
            filter_list.append(
                MetadataFilter(
                    key=key,
                    value=value,
                    operator=FilterOperator.EQ
                )
            )

    return MetadataFilters(filters=filter_list)


def is_scanned_pdf(path):
    """判断 PDF 是否为扫描版（抽样 5 页检查是否为图片为主）"""
    import fitz
    try:
        doc = fitz.open(path)
        total_pages = len(doc)
        if total_pages == 0:
            return False

        # sampling
        sample_indices = sorted({0, total_pages // 4, total_pages // 2, 3 * total_pages // 4, total_pages - 1})

        image_pages = 0
        for idx in sample_indices:
            page = doc[idx]
            if page.get_images():
                image_pages += 1
        return image_pages / len(sample_indices) > 0.5
    except:
        return False


def pdf_supports_text(path):
    """尝试抽样 5 页文本提取，如果文本超过一定长度（20）则认为可解析"""
    import fitz
    try:
        doc = fitz.open(path)
        total_pages = len(doc)
        if total_pages == 0:
            return False

        # sampling
        sample_indices = sorted({0, total_pages // 4, total_pages // 2, 3 * total_pages // 4, total_pages - 1})

        text = ""
        for idx in sample_indices:
            text += doc[idx].get_text()
        return len(text.strip()) > 20
    except:
        return False


def load_single_file(file_path: str):
    from llama_index.readers.file import (
        PyMuPDFReader,
        PDFReader,
    )
    from llama_index.core.readers import SimpleDirectoryReader
    from llama_index.readers.paddle_ocr.base import PDFPaddleOCRReader
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        if is_scanned_pdf(file_path):
            reader = PDFPaddleOCRReader()
        elif pdf_supports_text(file_path):
            reader = PyMuPDFReader()
        else:
            reader = PDFReader()
        docs = reader.load_data(file_path)

        for d in docs:
            d.metadata = d.metadata or {}
            d.metadata["source_file"] = os.path.basename(file_path)
            d.metadata["source_path"] = os.path.abspath(file_path)
            d.metadata["file_type"] = os.path.splitext(file_path)[1].lower()
    else:
        if os.path.isfile(file_path):
            reader = SimpleDirectoryReader(input_files=[file_path])
        else:
            reader = SimpleDirectoryReader(input_dir=file_path)
        docs = reader.load_data()
    return docs


class LlamaIndexRAG(RAG):
    """LlamaIndexRAG class to implement the RAG of llama-index

    The configuration needed in the config yaml:
        - name: LlamaIndexRAG
        - embedding: An embedding model, required, default `Qwen/Qwen3-Embedding-0.6B`
        - chunk_size: The chunk_size of splitting, default `512`
        - chunk_overlap: The overlap of each chunk, default `50`
        - retrieve_only: retrieve only will stop using the llm, only use embedding model,
            thus, query methods will not be available. Default `False`
        - storage_dir: The directory to store and load index files, default `./llama_index`
        If not retrieve_only, the llm model will be the same with the model configured in the `llm` fields.
    """

    def __init__(self, config: DictConfig):
        super().__init__(config)

        self._validate_config(config)
        self.embedding_model = getattr(config.rag, 'embedding',
                                       'Qwen/Qwen3-Embedding-0.6B')
        self.llm_model = getattr(config.rag, 'llm', None)
        self.chunk_size = getattr(config.rag, 'chunk_size', 512)
        self.chunk_overlap = getattr(config.rag, 'chunk_overlap', 50)
        self.retrieve_only = getattr(config.rag, 'retrieve_only', False)
        self.storage_dir = getattr(config.rag, 'storage_dir', './llama_index')
        self._validate_requirements()

        embedder_cfg = getattr(config, "embedder", OmegaConf.create({}))
        service = getattr(embedder_cfg, 'service', 'modelscope')
        self.embedding_api_key = getattr(embedder_cfg, 'api_key', None) \
            or os.getenv(f"{service.upper()}_API_KEY")

        self.embedding_model_name = getattr(embedder_cfg, 'model', 'Qwen/Qwen3-Embedding-8B')
        self.embedding_dims = getattr(embedder_cfg, 'embedding_dims', 1536)

        self.embedding_base_url = getattr(embedder_cfg, "openai_base_url",
                                          get_service_config(service).base_url)

        self.llm_model = getattr(config.rag, 'llm', None)
        self.chunk_size = getattr(config.rag, 'chunk_size', 512)
        self.chunk_overlap = getattr(config.rag, 'chunk_overlap', 50)
        self.retrieve_only = getattr(config.rag, 'retrieve_only', False)
        self.storage_dir = getattr(config.rag, 'storage_dir', './llama_index')

        self.vector_store = getattr(config.rag, 'vector_store', None)

        self._validate_requirements()

        self._setup_embedding_model(config)

        from llama_index.core import Settings
        from llama_index.core.node_parser import SentenceSplitter
        # Set node parser
        Settings.node_parser = SentenceSplitter(
            chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap)

        # If retrieve only, don't set LLM
        if self.retrieve_only:
            Settings.llm = None
        else:
            from llama_index.core.llms import CustomLLM
            from llama_index.core.base.llms.types import LLMMetadata
            from llama_index.core.llms.callbacks import llm_completion_callback
            from llama_index.core.base.llms.types import CompletionResponse
            self._llm_instance = LLM.from_config(self.config)

            class MSCustomLLM(CustomLLM):

                @property
                def metadata(_self) -> LLMMetadata:
                    return LLMMetadata(
                        context_window=65536,  # TODO temp value
                        num_output=4096,
                        model_name=self.config.llm.model,
                    )

                @llm_completion_callback()
                def complete(_self, prompt: str,
                             **kwargs) -> CompletionResponse:
                    message: Message = self._llm_instance.generate(
                        messages=[Message(role='user', content=prompt)],
                        stream=False,
                        **kwargs)
                    return CompletionResponse(text=message.content)

                @llm_completion_callback()
                def stream_complete(_self,
                                    prompt: str,
                                    formatted: bool = False,
                                    **kwargs: Any):
                    for message in self._llm_instance.generate(
                            messages=[Message(role='user', content=prompt)],
                            stream=True,
                            **kwargs):
                        yield CompletionResponse(text=message.content)

            Settings.llm = MSCustomLLM()

        self.index = None
        self.query_engine = None

    def _validate_requirements(self):
        assert_package_exist(
            'llama_index',
            'Please install llama_index to support llama-index-rag:\n'
            '> pip install -U llama-index-core llama-index-embeddings-huggingface llama-index-embeddings-openai '
            'llama-index-llms-openai llama-index-llms-replicate\n')

    def _validate_config(self, config: DictConfig):
        """Validate configuration parameters"""
        if not hasattr(config, 'rag'):
            raise ValueError(
                'Missing rag.embedding parameter in configuration')

        chunk_size = getattr(config.rag, 'chunk_size', 512)
        if chunk_size <= 0:
            raise ValueError('chunk_size must be greater than 0')

    def _setup_embedding_model(self, config: DictConfig):
        from llama_index.core import Settings
        from llama_index.embeddings.openai import OpenAIEmbedding

        try:
            if self.embedding_api_key is None:
                raise RuntimeError("RAG embedding API key未提供")

            Settings.embed_model = OpenAIEmbedding(
                model_name=self.embedding_model_name,
                api_base=self.embedding_base_url,
                api_key=self.embedding_api_key,
                dimensions=self.embedding_dims,
            )

            self.embedding_model = Settings.embed_model

        except Exception as e:
            raise RuntimeError(f"Failed to load OpenAI embedding model: {e}")


    async def add_documents(self, documents: List[str]):
        if not documents:
            raise ValueError('Document list cannot be empty')
        from llama_index.core import (Document, VectorStoreIndex)
        docs = [Document(text=doc) for doc in documents]

        self.index = self.get_index(docs)
        if not self.retrieve_only:
            await self._setup_query_engine()

    async def add_documents_from_files(self, file_paths: List[str]):
        if not file_paths:
            raise ValueError('File path list cannot be empty')

        def load_files_parallel(file_paths, workers=8):
            from multiprocessing import Pool
            with Pool(workers) as p:
                docs = p.map(load_single_file, file_paths)
            return [d for sub in docs for d in sub]

        documents = load_files_parallel(file_paths)

        self.index = self.get_index(documents)

        if not self.retrieve_only:
            await self._setup_query_engine()

    def get_index(self, documents = None, persist_dir: str = None):
        from llama_index.core import StorageContext, VectorStoreIndex
        storage_context = None
        if getattr(self.vector_store, 'service', 'milvus'):
            from llama_index.vector_stores.milvus import MilvusVectorStore
            uri = getattr(self.vector_store, 'url', 'http://localhost:19530')
            token = getattr(self.vector_store, 'token', None)
            collection_name = getattr(self.vector_store, 'collection_name', 'rag_collection_test')
            db_name = getattr(self.vector_store, 'db_name', 'rag_test')

            vector_store = MilvusVectorStore(
                uri=uri,
                token=token,
                collection_name=collection_name,
                dim=self.embedding_dims,
                db_name=db_name,
                embedding_field='embedding',
                overwrite=False  # overwrite exist collection or not
            )
            if not documents:
                return VectorStoreIndex.from_vector_store(vector_store=vector_store)

            storage_context = StorageContext.from_defaults(vector_store=vector_store)
        if storage_context is None:
            storage_context = StorageContext.from_defaults(persist_dir=persist_dir)
        return VectorStoreIndex.from_documents(documents, storage_context=storage_context)

    async def _setup_query_engine(self):
        if self.index is None:
            return

        from llama_index.core import Settings
        # Check if LLM is set
        if Settings.llm is None and not self.retrieve_only:
            return

        self.query_engine = self.index.as_query_engine(
            similarity_top_k=5, response_mode='compact')

    async def _retrieve(self,
                        query: str,
                        limit: int = 5,
                        score_threshold: float = 0.0,
                        **filters) -> List[dict]:
        if self.index is None:
            return []

        if not query.strip():
            return []

        metadata_filters = None if not filters else _parse_llamaindex_filters(**filters)

        from llama_index.core.retrievers import VectorIndexRetriever
        retriever = VectorIndexRetriever(
            index=self.index,
            similarity_top_k=limit,
            filters=metadata_filters
        )

        nodes = retriever.retrieve(query)

        results = []
        for node in nodes:
            if node.score >= score_threshold:
                results.append({
                    'text': node.node.text,
                    'score': float(node.score),
                    'metadata': node.node.metadata,
                    'node_id': node.node.node_id
                })

        return results

    async def retrieve(self,
                       query: str,
                       limit: int = 5,
                       score_threshold: float = 0.0,
                       **filters) -> List[dict]:
        if self.retrieve_only:
            return await self._retrieve(query, limit, score_threshold,
                                        **filters)

        from llama_index.core import Settings
        from llama_index.core.postprocessor import SimilarityPostprocessor
        from llama_index.core.query_engine import RetrieverQueryEngine
        from llama_index.core.retrievers import VectorIndexRetriever
        if self.index is None or Settings.llm is None:
            return []

        retriever = VectorIndexRetriever(
            index=self.index, similarity_top_k=limit)

        postprocessor = SimilarityPostprocessor(
            similarity_cutoff=score_threshold)

        query_engine = RetrieverQueryEngine(
            retriever=retriever, node_postprocessors=[postprocessor])

        response = query_engine.query(query)

        results = []
        for node in response.source_nodes:
            results.append({
                'text': node.node.text,
                'score': float(node.score),
                'metadata': node.node.metadata,
                'node_id': node.node.node_id
            })

        return results

    async def hybrid_search(self, query: str, top_k: int = 5) -> List[dict]:
        """Hybrid retrieval: Vector retrieval + BM25"""
        if self.index is None:
            return []

        from llama_index.core.retrievers import VectorIndexRetriever
        # Try to import BM25 related modules
        try:
            from llama_index.retrievers.bm25 import BM25Retriever
            from llama_index.core.retrievers import QueryFusionRetriever
            bm25_available = True
        except ImportError:
            bm25_available = False

        # Vector retriever
        vector_retriever = VectorIndexRetriever(
            index=self.index, similarity_top_k=top_k)

        if not bm25_available:
            # Use vector retrieval only
            nodes = vector_retriever.retrieve(query)
        else:
            # Use hybrid retrieval
            try:
                bm25_retriever = BM25Retriever.from_defaults(
                    docstore=self.index.docstore, similarity_top_k=top_k)

                fusion_retriever = QueryFusionRetriever(
                    retrievers=[vector_retriever, bm25_retriever],
                    similarity_top_k=top_k,
                    num_queries=1)

                nodes = fusion_retriever.retrieve(query)

            except Exception:  # noqa
                nodes = vector_retriever.retrieve(query)

        results = []
        for node in nodes:
            results.append({
                'text': node.node.text,
                'score': float(node.score),
                'metadata': node.node.metadata,
                'node_id': node.node.node_id
            })

        return results

    async def query(self, query: str, **filters) -> str:
        if self.query_engine is None:
            if self.retrieve_only:
                raise ValueError(
                    'Current mode is retrieve only, question answering not supported'
                )
            else:
                raise ValueError(
                    'Query engine not initialized, please add documents and set LLM first'
                )

        metadata_filters = None if not filters else _parse_llamaindex_filters(filters)

        # deepcopy for support 并发调用
        retriever = copy.deepcopy(self.query_engine._retriever)
        response_synthesizer = self.query_engine._response_synthesizer

        if metadata_filters:
            retriever._filters=metadata_filters
        nodes = retriever.retrieve(query)

        # 使用 synthesizer 构建最终回答
        response = response_synthesizer.synthesize(query, nodes)

        return str(response)

        # try:
        #     response = self.query_engine.query(query)
        #     return str(response)
        # except Exception as e:
        #     return f'Query failed, error: {e}'

    async def save_index(self, persist_dir: Optional[str] = None):
        """Save index"""
        if self.index is None:
            raise ValueError('No index to save, please add documents first')

        save_dir = persist_dir or self.storage_dir

        os.makedirs(save_dir, exist_ok=True)
        self.index.storage_context.persist(persist_dir=save_dir)

    async def load_index(self, persist_dir: Optional[str] = None):
        """Load index"""
        load_dir = persist_dir or self.storage_dir

        if not os.path.exists(load_dir):
            logger.info(f'Index directory does not exist: {load_dir}, try load from remote vector store.')

        self.index = self.get_index(persist_dir=load_dir)

        # Re-setup query engine
        if not self.retrieve_only:
            await self._setup_query_engine()

    def get_index_by_filters(self, filters: Dict[str, Any] = None):
        metadata_filters = None if not filters else _parse_llamaindex_filters(filters)

        json_result = list()
        result = self.index.vector_store.get_nodes(filters=metadata_filters)
        for node in result:
            json_result.append({
                'id': str(node.id_),
                'text': node.text,
                'metadata': str(node.metadata),
            })
        return json_result

    def delete(self, filters: Dict[str, Any] = None, node_ids: List[str] = None):
        try:
            if filters is not None:
                res = self.get_index_by_filters(filters)
                node_ids.extend([item['id'] for item in res])
            self.index.delete(node_ids)
            return True
        except Exception as e:
            logger.warning(f'delete RAG nodes failed: node_ids: {node_ids}, filters: {filters}, fail details: {e}')
            return False

    def get_index_info(self) -> dict:
        """Get index information"""
        if self.index is None:
            return {'status': 'not_initialized'}

        doc_count = len(self.index.docstore.docs)
        return {
            'status': 'initialized',
            'document_count': doc_count,
            'retrieve_only': self.retrieve_only,
            'chunk_size': self.chunk_size,
            'chunk_overlap': self.chunk_overlap,
            'embedding_model': self.embedding_model
        }

    async def remove_all_documents(self):
        """Remove all documents from the index"""
        # Clear the index
        self.index = None

        # Clear the query engine
        self.query_engine = None

        # If storage directory exists, optionally clean it up
        if hasattr(self, 'storage_dir') and os.path.exists(self.storage_dir):
            shutil.rmtree(self.storage_dir, ignore_errors=True)
            os.makedirs(self.storage_dir, exist_ok=True)

    async def clear_storage(self, persist_dir: Optional[str] = None):
        """Clear the persistent storage directory"""
        clear_dir = persist_dir or self.storage_dir
        if os.path.exists(clear_dir):
            shutil.rmtree(clear_dir)
            os.makedirs(clear_dir, exist_ok=True)
