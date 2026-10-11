"""
Configuration module for Google Sheets LLM Inference System.

Handles YAML configuration loading, validation, and environment variable expansion.

Configuration Schema:
    google_sheets:
      credentials_file: str  # Path to service account JSON
    
    models:
      - name: str           # Unique identifier, becomes worksheet name
        backend: str        # openai | anthropic | ollama | huggingface
        model_id: str       # Model identifier for the backend
        temperature: float  # 0.0-2.0 (default: 0.7)
        max_tokens: int     # Max response tokens (default: 300)
        # Backend-specific options:
        device: str         # For huggingface: cuda | mps | cpu
        base_url: str       # For ollama: API endpoint; for openai: any
                            # OpenAI-compatible endpoint (OpenRouter, DeepSeek, Moonshot)
        api_key: str        # For openai with base_url: per-model key (${ENV_VAR} ok)
    
    api_keys:
      openai: str           # Can use ${ENV_VAR} syntax
      anthropic: str

Reference:
- PyYAML: https://pyyaml.org/wiki/PyYAMLDocumentation
- python-dotenv: https://github.com/theskumar/python-dotenv
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv


@dataclass
class GoogleSheetsConfig:
    """Google Sheets authentication configuration."""
    credentials_file: str


@dataclass
class ModelConfig:
    """Configuration for a single LLM model."""
    name: str
    backend: str
    model_id: str
    temperature: Optional[float] = 0.7  # null = don't send (newer Claude models reject it)
    max_tokens: int = 300
    device: Optional[str] = None  # For HuggingFace: cuda, mps, cpu
    base_url: Optional[str] = None  # Ollama endpoint, or OpenAI-compatible endpoint (OpenRouter, DeepSeek, Moonshot)
    api_key: Optional[str] = None  # Per-model key for OpenAI-compatible endpoints; overrides api_keys.openai
    system_prompt: Optional[str] = None  # Optional system prompt
    extra_body: Optional[dict] = None  # Extra request fields for openai backend, e.g. OpenRouter reasoning
    
    def __post_init__(self):
        """Validate model configuration."""
        valid_backends = {'openai', 'anthropic', 'ollama', 'huggingface'}
        if self.backend not in valid_backends:
            raise ValueError(
                f"Invalid backend '{self.backend}' for model '{self.name}'. "
                f"Must be one of: {valid_backends}"
            )
        
        if self.temperature is not None and (self.temperature < 0 or self.temperature > 2):
            raise ValueError(
                f"Temperature {self.temperature} out of range for model '{self.name}'. "
                f"Must be between 0.0 and 2.0"
            )
        
        if self.max_tokens < 1:
            raise ValueError(
                f"max_tokens must be positive for model '{self.name}'"
            )
        
        if self.api_key and re.search(r'\$\{[^}]+\}', self.api_key):
            raise ValueError(
                f"api_key for model '{self.name}' references an unset environment "
                f"variable: {self.api_key}"
            )

        if self.backend == 'huggingface' and not self.device:
            # Auto-detect device
            self.device = self._detect_device()
    
    @staticmethod
    def _detect_device() -> str:
        """Auto-detect the best available compute device."""
        try:
            import torch
            if torch.cuda.is_available():
                return 'cuda'
            elif torch.backends.mps.is_available():
                return 'mps'
        except ImportError:
            pass
        return 'cpu'


@dataclass
class ApiKeysConfig:
    """API keys configuration."""
    openai: Optional[str] = None
    anthropic: Optional[str] = None
    huggingface: Optional[str] = None  # For gated models


@dataclass
class Config:
    """Complete application configuration."""
    google_sheets: GoogleSheetsConfig
    models: list[ModelConfig]
    api_keys: ApiKeysConfig = field(default_factory=lambda: ApiKeysConfig())
    
    def get_models_by_backend(self, backend: str) -> list[ModelConfig]:
        """Get all models using a specific backend."""
        return [m for m in self.models if m.backend == backend]
    
    def get_model(self, name: str) -> Optional[ModelConfig]:
        """Get a model by name."""
        for model in self.models:
            if model.name == name:
                return model
        return None


def expand_env_vars(value: Any) -> Any:
    """
    Recursively expand environment variables in configuration values.
    
    Supports ${VAR_NAME} syntax for environment variable substitution.
    If the variable is not set, the original string is preserved.
    
    Args:
        value: Any configuration value (str, dict, list, or primitive)
    
    Returns:
        Value with environment variables expanded
    """
    if isinstance(value, str):
        # Pattern matches ${VAR_NAME}
        pattern = r'\$\{([^}]+)\}'
        
        def replacer(match):
            var_name = match.group(1)
            env_value = os.getenv(var_name)
            if env_value is None:
                # Return original if not found (will fail later if required)
                return match.group(0)
            return env_value
        
        return re.sub(pattern, replacer, value)
    
    elif isinstance(value, dict):
        return {k: expand_env_vars(v) for k, v in value.items()}
    
    elif isinstance(value, list):
        return [expand_env_vars(item) for item in value]
    
    return value


def load_config(config_path: Path) -> Config:
    """
    Load and validate configuration from YAML file.
    
    Automatically loads .env file if present in the same directory or current directory.
    Expands environment variables using ${VAR_NAME} syntax.
    
    Args:
        config_path: Path to YAML configuration file
    
    Returns:
        Validated Config object
    
    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If configuration is invalid
        yaml.YAMLError: If YAML parsing fails
    """
    config_path = Path(config_path)
    
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    # Load .env file for environment variables
    # Check config directory first, then current directory
    env_paths = [
        config_path.parent / '.env',
        Path('.env')
    ]
    for env_path in env_paths:
        if env_path.exists():
            load_dotenv(env_path)
            break
    
    # Load YAML
    with open(config_path, 'r', encoding='utf-8') as f:
        raw_config = yaml.safe_load(f)
    
    if not raw_config:
        raise ValueError("Configuration file is empty")
    
    # Expand environment variables
    raw_config = expand_env_vars(raw_config)
    
    # Validate and construct config objects
    try:
        # Google Sheets config
        gs_config = raw_config.get('google_sheets', {})
        if not gs_config.get('credentials_file'):
            raise ValueError("google_sheets.credentials_file is required")
        
        google_sheets = GoogleSheetsConfig(
            credentials_file=gs_config['credentials_file']
        )
        
        # Validate credentials file exists
        creds_path = Path(google_sheets.credentials_file)
        if not creds_path.exists():
            raise FileNotFoundError(
                f"Google credentials file not found: {creds_path}"
            )
        
        # Models config
        models_raw = raw_config.get('models', [])
        if not models_raw:
            raise ValueError("At least one model must be configured")
        
        models = []
        seen_names = set()
        for model_raw in models_raw:
            if 'name' not in model_raw:
                raise ValueError("Each model must have a 'name'")
            if 'backend' not in model_raw:
                raise ValueError(f"Model '{model_raw['name']}' must have a 'backend'")
            if 'model_id' not in model_raw:
                raise ValueError(f"Model '{model_raw['name']}' must have a 'model_id'")
            
            if model_raw['name'] in seen_names:
                raise ValueError(f"Duplicate model name: {model_raw['name']}")
            seen_names.add(model_raw['name'])
            
            models.append(ModelConfig(
                name=model_raw['name'],
                backend=model_raw['backend'],
                model_id=model_raw['model_id'],
                temperature=model_raw.get('temperature', 0.7),
                max_tokens=model_raw.get('max_tokens', 300),
                device=model_raw.get('device'),
                base_url=model_raw.get('base_url'),
                api_key=model_raw.get('api_key'),
                system_prompt=model_raw.get('system_prompt'),
                extra_body=model_raw.get('extra_body')
            ))
        
        # API keys config
        api_keys_raw = raw_config.get('api_keys', {})
        api_keys = ApiKeysConfig(
            openai=api_keys_raw.get('openai'),
            anthropic=api_keys_raw.get('anthropic'),
            huggingface=api_keys_raw.get('huggingface')
        )
        
        # Validate required API keys for configured backends
        backends_used = {m.backend for m in models}

        # OpenAI-compatible models with their own api_key don't need the global key
        needs_openai_key = any(
            m.backend == 'openai' and not m.api_key for m in models
        )
        if needs_openai_key and not api_keys.openai:
            raise ValueError(
                "OpenAI API key required but not configured. "
                "Set api_keys.openai or OPENAI_API_KEY environment variable"
            )
        
        if 'anthropic' in backends_used and not api_keys.anthropic:
            raise ValueError(
                "Anthropic API key required but not configured. "
                "Set api_keys.anthropic or ANTHROPIC_API_KEY environment variable"
            )
        
        return Config(
            google_sheets=google_sheets,
            models=models,
            api_keys=api_keys
        )
        
    except KeyError as e:
        raise ValueError(f"Missing required configuration key: {e}")


def create_example_config(output_path: Path) -> None:
    """
    Create an example configuration file with documentation.
    
    Args:
        output_path: Where to write the example config
    """
    example = """# Google Sheets LLM Inference System Configuration
# ================================================
# 
# This file configures which models to use and how to authenticate.
# Environment variables can be referenced using ${VAR_NAME} syntax.

# Google Sheets Authentication
# ----------------------------
# Create a service account at: https://console.cloud.google.com/iam-admin/serviceaccounts
# Download the JSON key file and reference it here.
# Share your Google Sheet with the service account email (with edit permissions).
google_sheets:
  credentials_file: service_account.json

# Model Definitions
# -----------------
# Each model creates a worksheet with the same name.
# Supported backends: openai, anthropic, ollama, huggingface
models:
  # OpenAI GPT Models
  # Reference: https://platform.openai.com/docs/models
  - name: gpt-4o
    backend: openai
    model_id: gpt-4o
    temperature: 0.7
    max_tokens: 500

  - name: gpt-4o-mini
    backend: openai
    model_id: gpt-4o-mini
    temperature: 0.7
    max_tokens: 500

  - name: gpt-3.5-turbo
    backend: openai
    model_id: gpt-3.5-turbo
    temperature: 0.7
    max_tokens: 300

  # Anthropic Claude Models
  # Reference: https://docs.anthropic.com/en/docs/about-claude/models
  - name: claude-sonnet-4
    backend: anthropic
    model_id: claude-sonnet-4-20250514
    temperature: 0.7
    max_tokens: 500

  - name: claude-3-5-haiku
    backend: anthropic
    model_id: claude-3-5-haiku-20241022
    temperature: 0.7
    max_tokens: 300

  # Ollama Local Models
  # Reference: https://ollama.com/library
  # Models are auto-pulled if not present locally
  - name: llama3.2
    backend: ollama
    model_id: llama3.2:latest
    temperature: 0.7
    max_tokens: 300
    base_url: http://localhost:11434  # Default Ollama endpoint

  - name: mistral
    backend: ollama
    model_id: mistral:latest
    temperature: 0.7
    max_tokens: 300

  # HuggingFace Transformers (local GPU inference)
  # Reference: https://huggingface.co/models
  - name: phi-3-mini
    backend: huggingface
    model_id: microsoft/Phi-3-mini-4k-instruct
    temperature: 0.7
    max_tokens: 300
    device: cuda  # Options: cuda, mps (Apple Silicon), cpu

# API Keys
# --------
# Use environment variables for security:
#   export OPENAI_API_KEY=sk-...
#   export ANTHROPIC_API_KEY=sk-ant-...
# Or set them directly (not recommended for production)
api_keys:
  openai: ${OPENAI_API_KEY}
  anthropic: ${ANTHROPIC_API_KEY}
  # huggingface: ${HF_TOKEN}  # Only needed for gated models
"""
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(example)


if __name__ == '__main__':
    # Generate example config when run directly
    example_path = Path('models_example.yaml')
    create_example_config(example_path)
    print(f"Created example configuration: {example_path}")
