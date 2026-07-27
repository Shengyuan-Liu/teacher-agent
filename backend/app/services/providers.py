from functools import lru_cache

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from app.core.config import settings


@lru_cache
def chat_model() -> BaseChatModel:
    match settings.llm_provider:
        case "anthropic":
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                model=settings.llm_model,
                api_key=settings.anthropic_api_key,
                max_tokens=4096,
            )
        case "openai":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(model=settings.llm_model, api_key=settings.openai_api_key)
        case "ollama":
            from langchain_ollama import ChatOllama

            return ChatOllama(model=settings.llm_model, base_url=settings.ollama_base_url)


@lru_cache
def embeddings() -> Embeddings:
    match settings.embedding_provider:
        case "openai":
            from langchain_openai import OpenAIEmbeddings

            return OpenAIEmbeddings(
                model=settings.embedding_model,
                api_key=settings.openai_api_key,
                dimensions=settings.embedding_dimensions,
            )
        case "ollama":
            from langchain_ollama import OllamaEmbeddings

            return OllamaEmbeddings(
                model=settings.embedding_model,
                base_url=settings.ollama_base_url,
            )
