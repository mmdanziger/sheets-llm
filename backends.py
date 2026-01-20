"""
LLM Backend Abstraction for Google Sheets Inference System.

Provides a unified interface for multiple LLM providers:
- OpenAI (GPT-4o, GPT-4, GPT-3.5-turbo, etc.)
- Anthropic (Claude 3.5, Claude 3 Opus, etc.)
- Ollama (local models: Llama, Mistral, etc.)
- HuggingFace Transformers (local GPU inference)

Each backend returns a standardized InferenceResult with:
- response: str - The generated text
- tokens: int | None - Token count if available
- finish_reason: str - Why generation stopped

Reference Documentation:
- OpenAI Chat API: https://platform.openai.com/docs/api-reference/chat/create
- Anthropic Messages API: https://docs.anthropic.com/en/api/messages
- Ollama API: https://github.com/ollama/ollama/blob/main/docs/api.md
- HuggingFace Pipeline: https://huggingface.co/docs/transformers/main_classes/pipelines
"""

import logging
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from config import ModelConfig

logger = logging.getLogger(__name__)


@dataclass
class InferenceResult:
    """Standardized result from any LLM backend."""
    response: str
    tokens: Optional[int]
    finish_reason: str


class BackendError(Exception):
    """Base exception for backend errors."""
    pass


class RateLimitError(BackendError):
    """Raised when rate limited by the API."""
    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class AuthenticationError(BackendError):
    """Raised when API authentication fails."""
    pass


class ModelNotFoundError(BackendError):
    """Raised when the specified model doesn't exist."""
    pass


class LLMBackend(ABC):
    """Abstract base class for LLM backends."""
    
    @abstractmethod
    def infer(self, prompt: str, model_config: ModelConfig) -> InferenceResult:
        """
        Generate a response for the given prompt.
        
        Args:
            prompt: The input prompt text
            model_config: Configuration for the model
        
        Returns:
            InferenceResult with response, tokens, and finish_reason
        
        Raises:
            RateLimitError: If rate limited (should retry)
            AuthenticationError: If API auth fails
            ModelNotFoundError: If model doesn't exist
            BackendError: For other errors
        """
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """Check if the backend is available (dependencies installed)."""
        pass
    
    def supports_batching(self) -> bool:
        """Check if this backend supports batch inference. Override in subclasses."""
        return False
    
    def infer_batch(self, prompts: list[str], model_config: ModelConfig,
                    batch_size: int = 8) -> list[InferenceResult]:
        """
        Batch inference for multiple prompts. Override in subclasses that support batching.
        Default implementation falls back to sequential inference.
        """
        return [self.infer(prompt, model_config) for prompt in prompts]


class OpenAIBackend(LLMBackend):
    """
    OpenAI Chat Completions API backend.
    
    Supports all OpenAI chat models including GPT-4o, GPT-4, GPT-3.5-turbo.
    
    Reference: https://platform.openai.com/docs/api-reference/chat/create
    
    Rate Limits (vary by tier):
    - Tier 1: 500 RPM, 30,000 TPM
    - Tier 2: 5,000 RPM, 450,000 TPM
    - Tier 3+: Higher limits
    """
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = None
    
    @property
    def client(self):
        """Lazy-load OpenAI client."""
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key)
        return self._client
    
    def is_available(self) -> bool:
        try:
            import openai
            return True
        except ImportError:
            return False
    
    def infer(self, prompt: str, model_config: ModelConfig) -> InferenceResult:
        from openai import (
            APIError, 
            AuthenticationError as OpenAIAuthError,
            RateLimitError as OpenAIRateLimitError,
            NotFoundError
        )
        
        messages = []
        
        # Add system prompt if configured
        if model_config.system_prompt:
            messages.append({
                "role": "system",
                "content": model_config.system_prompt
            })
        
        messages.append({
            "role": "user",
            "content": prompt
        })
        
        try:
            response = self.client.chat.completions.create(
                model=model_config.model_id,
                messages=messages,
                temperature=model_config.temperature,
                max_tokens=model_config.max_tokens
            )
            
            choice = response.choices[0]
            
            return InferenceResult(
                response=choice.message.content or '',
                tokens=response.usage.completion_tokens if response.usage else None,
                finish_reason=choice.finish_reason or 'unknown'
            )
            
        except OpenAIAuthError as e:
            raise AuthenticationError(f"OpenAI authentication failed: {e}")
        
        except OpenAIRateLimitError as e:
            # Try to extract retry-after from headers
            retry_after = None
            if hasattr(e, 'response') and e.response:
                retry_after = e.response.headers.get('retry-after')
                if retry_after:
                    retry_after = float(retry_after)
            raise RateLimitError(f"OpenAI rate limit: {e}", retry_after)
        
        except NotFoundError as e:
            raise ModelNotFoundError(f"OpenAI model not found: {model_config.model_id}")
        
        except APIError as e:
            raise BackendError(f"OpenAI API error: {e}")


class AnthropicBackend(LLMBackend):
    """
    Anthropic Messages API backend.
    
    Supports Claude 3.5 Sonnet, Claude 3 Opus, Claude 3 Haiku, etc.
    
    Reference: https://docs.anthropic.com/en/api/messages
    
    Rate Limits (Tier 1):
    - 50 requests per minute
    - 40,000 tokens per minute (input)
    - 8,000 tokens per minute (output)
    """
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = None
    
    @property
    def client(self):
        """Lazy-load Anthropic client."""
        if self._client is None:
            from anthropic import Anthropic
            self._client = Anthropic(api_key=self.api_key)
        return self._client
    
    def is_available(self) -> bool:
        try:
            import anthropic
            return True
        except ImportError:
            return False
    
    def infer(self, prompt: str, model_config: ModelConfig) -> InferenceResult:
        from anthropic import (
            APIError,
            AuthenticationError as AnthropicAuthError,
            RateLimitError as AnthropicRateLimitError,
            NotFoundError
        )
        
        try:
            kwargs = {
                "model": model_config.model_id,
                "max_tokens": model_config.max_tokens,
                "messages": [{"role": "user", "content": prompt}]
            }
            
            # Temperature is optional for Anthropic
            if model_config.temperature is not None:
                kwargs["temperature"] = model_config.temperature
            
            # Add system prompt if configured
            if model_config.system_prompt:
                kwargs["system"] = model_config.system_prompt
            
            response = self.client.messages.create(**kwargs)
            
            # Extract text from content blocks
            response_text = ''
            for block in response.content:
                if block.type == 'text':
                    response_text += block.text
            
            return InferenceResult(
                response=response_text,
                tokens=response.usage.output_tokens if response.usage else None,
                finish_reason=response.stop_reason or 'unknown'
            )
            
        except AnthropicAuthError as e:
            raise AuthenticationError(f"Anthropic authentication failed: {e}")
        
        except AnthropicRateLimitError as e:
            # Anthropic returns retry-after header
            retry_after = None
            if hasattr(e, 'response') and e.response:
                retry_header = e.response.headers.get('retry-after')
                if retry_header:
                    retry_after = float(retry_header)
            raise RateLimitError(f"Anthropic rate limit: {e}", retry_after)
        
        except NotFoundError as e:
            raise ModelNotFoundError(f"Anthropic model not found: {model_config.model_id}")
        
        except APIError as e:
            raise BackendError(f"Anthropic API error: {e}")


class OllamaBackend(LLMBackend):
    """
    Ollama local inference backend.
    
    Supports local models like Llama 3.2, Mistral, Phi-3, etc.
    Auto-pulls models if not present locally.
    
    Reference: https://github.com/ollama/ollama/blob/main/docs/api.md
    
    Default endpoint: http://localhost:11434
    """
    
    DEFAULT_BASE_URL = "http://localhost:11434"
    
    def __init__(self):
        self._client = None
        self._pulled_models = set()  # Track which models we've already pulled
    
    def _get_client(self, base_url: Optional[str] = None):
        """Get or create Ollama client."""
        from ollama import Client
        url = base_url or self.DEFAULT_BASE_URL
        return Client(host=url)
    
    def is_available(self) -> bool:
        try:
            import ollama
            return True
        except ImportError:
            return False
    
    def _ensure_model_available(self, model_id: str, base_url: Optional[str] = None) -> None:
        """
        Check if model is available locally, pull if not.
        
        Reference: https://github.com/ollama/ollama/blob/main/docs/api.md#list-local-models
        """
        if model_id in self._pulled_models:
            return
        
        client = self._get_client(base_url)
        
        try:
            # List available models - API returns list of Model objects
            response = client.list()
            
            # Handle both old dict format and new list format
            if isinstance(response, dict):
                models_list = response.get('models', [])
            else:
                # New format: response is a ListResponse with .models attribute
                models_list = getattr(response, 'models', []) or []
            
            # Extract model names - handle both dict and object formats
            available = set()
            for m in models_list:
                if isinstance(m, dict):
                    name = m.get('name', '')
                else:
                    name = getattr(m, 'model', '') or getattr(m, 'name', '')
                if name:
                    available.add(name)
            
            # Normalize model name (handle :latest suffix)
            model_base = model_id.split(':')[0]
            
            if model_id not in available and f"{model_base}:latest" not in available:
                logger.info(f"Pulling Ollama model: {model_id} (this may take a while)")
                client.pull(model_id)
                logger.info(f"Successfully pulled: {model_id}")
            
            self._pulled_models.add(model_id)
            
        except Exception as e:
            logger.debug(f"Could not list Ollama models: {e}, will attempt inference anyway")
    
    def infer(self, prompt: str, model_config: ModelConfig) -> InferenceResult:
        from ollama import ResponseError
        
        base_url = model_config.base_url
        
        # Ensure model is available (auto-pull if needed)
        try:
            self._ensure_model_available(model_config.model_id, base_url)
        except Exception as e:
            raise BackendError(f"Failed to ensure Ollama model availability: {e}")
        
        client = self._get_client(base_url)
        
        try:
            # Build messages
            messages = []
            
            if model_config.system_prompt:
                messages.append({
                    "role": "system",
                    "content": model_config.system_prompt
                })
            
            messages.append({
                "role": "user",
                "content": prompt
            })
            
            response = client.chat(
                model=model_config.model_id,
                messages=messages,
                options={
                    "temperature": model_config.temperature,
                    "num_predict": model_config.max_tokens
                }
            )
            
            # Extract response
            message = response.get('message', {})
            response_text = message.get('content', '')
            
            # Token count if available
            tokens = None
            if 'eval_count' in response:
                tokens = response['eval_count']
            
            # Finish reason
            finish_reason = 'stop'
            if response.get('done_reason'):
                finish_reason = response['done_reason']
            
            return InferenceResult(
                response=response_text,
                tokens=tokens,
                finish_reason=finish_reason
            )
            
        except ResponseError as e:
            if "model" in str(e).lower() and "not found" in str(e).lower():
                raise ModelNotFoundError(f"Ollama model not found: {model_config.model_id}")
            raise BackendError(f"Ollama error: {e}")
        
        except Exception as e:
            # Connection errors, etc.
            if "connection" in str(e).lower() or "refused" in str(e).lower():
                raise BackendError(
                    f"Cannot connect to Ollama at {base_url or self.DEFAULT_BASE_URL}. "
                    f"Is Ollama running? Start with: ollama serve"
                )
            raise BackendError(f"Ollama error: {e}")


class HuggingFaceBackend(LLMBackend):
    """
    HuggingFace Transformers local inference backend.
    
    Supports local GPU/CPU inference with models like Phi-3, Llama, etc.
    
    Reference: https://huggingface.co/docs/transformers/main_classes/pipelines
    
    Device options:
    - cuda: NVIDIA GPU
    - mps: Apple Silicon GPU
    - cpu: CPU (slow for large models)
    """
    
    def __init__(self, hf_token: Optional[str] = None):
        self.hf_token = hf_token
        self._pipelines = {}  # Cache pipelines by model_id
    
    def is_available(self) -> bool:
        try:
            import torch
            import transformers
            return True
        except ImportError:
            return False
    
    def _get_pipeline(self, model_config: ModelConfig):
        """Get or create a text generation pipeline for the model."""
        cache_key = (model_config.model_id, model_config.device)
        
        if cache_key not in self._pipelines:
            import torch
            from transformers import pipeline, AutoModelForCausalLM, AutoTokenizer
            
            logger.info(f"Loading HuggingFace model: {model_config.model_id}")
            logger.info(f"Device: {model_config.device}")
            
            # Determine device
            device = model_config.device or 'cpu'
            
            # Check device availability
            if device == 'cuda' and not torch.cuda.is_available():
                logger.warning("CUDA requested but not available, falling back to CPU")
                device = 'cpu'
            elif device == 'mps' and not torch.backends.mps.is_available():
                logger.warning("MPS requested but not available, falling back to CPU")
                device = 'cpu'
            
            # Determine torch dtype based on device
            if device == 'cuda':
                torch_dtype = torch.float16  # Use FP16 for CUDA
            elif device == 'mps':
                torch_dtype = torch.float16  # MPS works well with FP16
            else:
                torch_dtype = torch.float32  # CPU uses FP32
            
            try:
                # Load tokenizer
                tokenizer = AutoTokenizer.from_pretrained(
                    model_config.model_id,
                    token=self.hf_token,
                    trust_remote_code=False
                )
                
                # Load model with appropriate settings
                model = AutoModelForCausalLM.from_pretrained(
                    model_config.model_id,
                    token=self.hf_token,
                    torch_dtype=torch_dtype,
                    device_map=device if device != 'cpu' else None,
                    trust_remote_code=False
                )
                
                if device == 'cpu':
                    model = model.to('cpu')
                
                # Create pipeline
                pipe = pipeline(
                    "text-generation",
                    model=model,
                    tokenizer=tokenizer,
                    device=None if device != 'cpu' else -1  # device_map handles non-CPU
                )
                
                self._pipelines[cache_key] = pipe
                logger.info(f"Model loaded successfully on {device}")
                
            except Exception as e:
                if "gated" in str(e).lower() or "access" in str(e).lower():
                    raise AuthenticationError(
                        f"Model {model_config.model_id} requires authentication. "
                        f"Set HF_TOKEN environment variable or api_keys.huggingface in config."
                    )
                raise BackendError(f"Failed to load model: {e}")
        
        return self._pipelines[cache_key]
    
    def infer(self, prompt: str, model_config: ModelConfig) -> InferenceResult:
        try:
            pipe = self._get_pipeline(model_config)
            
            # Build the full prompt with system message if present
            if model_config.system_prompt:
                # Use chat template if available
                if hasattr(pipe.tokenizer, 'apply_chat_template'):
                    messages = [
                        {"role": "system", "content": model_config.system_prompt},
                        {"role": "user", "content": prompt}
                    ]
                    full_prompt = pipe.tokenizer.apply_chat_template(
                        messages,
                        tokenize=False,
                        add_generation_prompt=True
                    )
                else:
                    # Fallback for models without chat template
                    full_prompt = f"{model_config.system_prompt}\n\nUser: {prompt}\n\nAssistant:"
            else:
                if hasattr(pipe.tokenizer, 'apply_chat_template'):
                    messages = [{"role": "user", "content": prompt}]
                    full_prompt = pipe.tokenizer.apply_chat_template(
                        messages,
                        tokenize=False,
                        add_generation_prompt=True
                    )
                else:
                    full_prompt = prompt
            
            # Generate
            outputs = pipe(
                full_prompt,
                max_new_tokens=model_config.max_tokens,
                temperature=model_config.temperature if model_config.temperature > 0 else None,
                do_sample=model_config.temperature > 0,
                return_full_text=False,
                pad_token_id=pipe.tokenizer.eos_token_id
            )
            
            generated_text = outputs[0]['generated_text']
            
            # Estimate token count (rough approximation)
            tokens = len(pipe.tokenizer.encode(generated_text))
            
            return InferenceResult(
                response=generated_text.strip(),
                tokens=tokens,
                finish_reason='stop'
            )
            
        except Exception as e:
            if isinstance(e, (AuthenticationError, BackendError)):
                raise
            raise BackendError(f"HuggingFace inference error: {e}")
    
    def infer_batch(self, prompts: list[str], model_config: ModelConfig, 
                    batch_size: int = 8) -> list[InferenceResult]:
        """
        Batch inference for multiple prompts.
        
        Significantly faster than sequential inference on GPU.
        Reference: https://huggingface.co/docs/transformers/main_classes/pipelines#pipeline-batching
        
        Args:
            prompts: List of prompt strings
            model_config: Model configuration
            batch_size: Number of prompts to process at once (default: 8)
        
        Returns:
            List of InferenceResult, one per prompt
        """
        try:
            pipe = self._get_pipeline(model_config)
            
            # Prepare all prompts with chat template
            full_prompts = []
            for prompt in prompts:
                if model_config.system_prompt:
                    if hasattr(pipe.tokenizer, 'apply_chat_template'):
                        messages = [
                            {"role": "system", "content": model_config.system_prompt},
                            {"role": "user", "content": prompt}
                        ]
                        full_prompt = pipe.tokenizer.apply_chat_template(
                            messages,
                            tokenize=False,
                            add_generation_prompt=True
                        )
                    else:
                        full_prompt = f"{model_config.system_prompt}\n\nUser: {prompt}\n\nAssistant:"
                else:
                    if hasattr(pipe.tokenizer, 'apply_chat_template'):
                        messages = [{"role": "user", "content": prompt}]
                        full_prompt = pipe.tokenizer.apply_chat_template(
                            messages,
                            tokenize=False,
                            add_generation_prompt=True
                        )
                    else:
                        full_prompt = prompt
                full_prompts.append(full_prompt)
            
            # Batch generate with pipeline
            # Reference: https://huggingface.co/docs/transformers/main_classes/pipelines#pipeline-batching
            outputs = pipe(
                full_prompts,
                max_new_tokens=model_config.max_tokens,
                temperature=model_config.temperature if model_config.temperature > 0 else None,
                do_sample=model_config.temperature > 0,
                return_full_text=False,
                pad_token_id=pipe.tokenizer.eos_token_id,
                batch_size=batch_size
            )
            
            # Process results
            results = []
            for output in outputs:
                generated_text = output[0]['generated_text']
                tokens = len(pipe.tokenizer.encode(generated_text))
                
                results.append(InferenceResult(
                    response=generated_text.strip(),
                    tokens=tokens,
                    finish_reason='stop'
                ))
            
            return results
            
        except Exception as e:
            if isinstance(e, (AuthenticationError, BackendError)):
                raise
            raise BackendError(f"HuggingFace batch inference error: {e}")
    
    def supports_batching(self) -> bool:
        """Check if this backend supports batch inference."""
        return True


class BackendFactory:
    """
    Factory for creating and caching LLM backend instances.
    
    Backends are lazily instantiated and cached for reuse.
    """
    
    def __init__(self, api_keys: 'ApiKeysConfig'):
        self.api_keys = api_keys
        self._backends = {}
    
    def get_backend(self, backend_name: str) -> LLMBackend:
        """
        Get or create a backend instance.
        
        Args:
            backend_name: One of 'openai', 'anthropic', 'ollama', 'huggingface'
        
        Returns:
            LLMBackend instance
        
        Raises:
            ValueError: If backend is unknown
            ImportError: If backend dependencies not installed
        """
        if backend_name in self._backends:
            return self._backends[backend_name]
        
        backend: LLMBackend
        
        if backend_name == 'openai':
            if not self.api_keys.openai:
                raise ValueError("OpenAI API key not configured")
            backend = OpenAIBackend(self.api_keys.openai)
            
        elif backend_name == 'anthropic':
            if not self.api_keys.anthropic:
                raise ValueError("Anthropic API key not configured")
            backend = AnthropicBackend(self.api_keys.anthropic)
            
        elif backend_name == 'ollama':
            backend = OllamaBackend()
            
        elif backend_name == 'huggingface':
            backend = HuggingFaceBackend(self.api_keys.huggingface)
            
        else:
            raise ValueError(f"Unknown backend: {backend_name}")
        
        # Check if backend dependencies are installed
        if not backend.is_available():
            raise ImportError(
                f"Backend '{backend_name}' dependencies not installed. "
                f"Run: pip install {backend_name}"
            )
        
        self._backends[backend_name] = backend
        return backend


def infer_with_retry(backend: LLMBackend, 
                     prompt: str, 
                     model_config: ModelConfig,
                     max_retries: int = 3,
                     base_backoff: float = 2.0) -> InferenceResult:
    """
    Run inference with exponential backoff retry on rate limits.
    
    Args:
        backend: The LLM backend to use
        prompt: The input prompt
        model_config: Model configuration
        max_retries: Maximum retry attempts
        base_backoff: Base backoff time in seconds
    
    Returns:
        InferenceResult on success
    
    Raises:
        BackendError: If all retries exhausted or non-retryable error
    """
    last_error = None
    
    for attempt in range(max_retries + 1):
        try:
            return backend.infer(prompt, model_config)
            
        except RateLimitError as e:
            last_error = e
            
            if attempt < max_retries:
                # Use retry-after if available, otherwise exponential backoff
                if e.retry_after:
                    backoff = e.retry_after
                else:
                    backoff = base_backoff * (2 ** attempt)
                
                logger.warning(
                    f"Rate limited for {model_config.name}, "
                    f"retrying in {backoff:.1f}s (attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(backoff)
            
        except (AuthenticationError, ModelNotFoundError):
            # Don't retry auth or model errors
            raise
    
    # All retries exhausted
    raise last_error or BackendError("Unknown error during inference")
