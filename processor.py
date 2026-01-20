"""
Inference Processor for Google Sheets LLM System.

Orchestrates the processing of prompts across multiple models,
handling worksheet management, status tracking, and error recovery.

Processing Logic:
1. Load all prompts from "Prompts" worksheet
2. For each configured model:
   a. Get or create the model's worksheet
   b. For each prompt:
      - Check if response already exists (skip if filled)
      - Mark as PROCESSING
      - Run inference
      - Write response (DONE) or error (ERROR)
3. Report statistics
"""

import logging
from dataclasses import dataclass
from typing import Optional

from config import Config, ModelConfig
from sheets_io import SheetsClient, Prompt, STATUS_DONE, STATUS_ERROR
from backends import (
    BackendFactory, 
    infer_with_retry,
    AuthenticationError,
    ModelNotFoundError,
    BackendError,
    HuggingFaceBackend
)

logger = logging.getLogger(__name__)


@dataclass
class ProcessingStats:
    """Statistics for a processing run."""
    filled: int = 0
    skipped: int = 0
    errors: int = 0
    
    def to_dict(self) -> dict:
        return {
            'filled': self.filled,
            'skipped': self.skipped,
            'errors': self.errors
        }


class InferenceProcessor:
    """
    Main processor for running LLM inference on Google Sheets prompts.
    
    Handles:
    - Loading prompts from the Prompts worksheet
    - Processing each prompt through configured models
    - Writing results back to model-specific worksheets
    - Tracking statistics and handling errors
    """
    
    def __init__(self, sheets: SheetsClient, config: Config):
        """
        Initialize the processor.
        
        Args:
            sheets: Connected SheetsClient instance
            config: Application configuration
        """
        self.sheets = sheets
        self.config = config
        self.backend_factory = BackendFactory(config.api_keys)
    
    def process_all(self,
                    model_filter: Optional[list[str]] = None,
                    prompt_range: tuple[Optional[int], Optional[int]] = (None, None),
                    dry_run: bool = False,
                    force: bool = False) -> dict:
        """
        Process all prompts through all configured models.
        
        Args:
            model_filter: List of model names to process (None = all)
            prompt_range: (start, end) tuple for row filtering (1-indexed, inclusive)
            dry_run: If True, preview actions without making changes
            force: If True, reprocess even if response exists
        
        Returns:
            Dict with 'filled', 'skipped', 'errors' counts
        """
        stats = ProcessingStats()
        
        # Load all prompts
        prompts = self.sheets.get_all_prompts()
        
        # Apply row range filter
        start_row, end_row = prompt_range
        if start_row is not None or end_row is not None:
            prompts = [
                p for p in prompts
                if (start_row is None or p.row >= start_row) and
                   (end_row is None or p.row <= end_row)
            ]
        
        if not prompts:
            logger.info("No prompts to process")
            return stats.to_dict()
        
        logger.info(f"Processing {len(prompts)} prompts")
        
        # Determine which models to process
        models = self.config.models
        if model_filter:
            filter_set = set(model_filter)
            models = [m for m in models if m.name in filter_set]
            
            # Warn about unknown model names
            known_names = {m.name for m in self.config.models}
            unknown = filter_set - known_names
            if unknown:
                logger.warning(f"Unknown models in filter: {unknown}")
        
        if not models:
            logger.warning("No models to process after filtering")
            return stats.to_dict()
        
        # Process each model
        for model in models:
            model_stats = self._process_model(
                model=model,
                prompts=prompts,
                dry_run=dry_run,
                force=force
            )
            
            stats.filled += model_stats.filled
            stats.skipped += model_stats.skipped
            stats.errors += model_stats.errors
        
        return stats.to_dict()
    
    def _process_model(self,
                       model: ModelConfig,
                       prompts: list[Prompt],
                       dry_run: bool,
                       force: bool) -> ProcessingStats:
        """
        Process all prompts for a single model.
        
        Args:
            model: Model configuration
            prompts: List of prompts to process
            dry_run: Preview mode
            force: Reprocess filled cells
        
        Returns:
            ProcessingStats for this model
        """
        stats = ProcessingStats()
        
        logger.info(f"\n--- Model: {model.name} ({model.backend}) ---")
        
        # Get or create the model's worksheet
        worksheet = self.sheets.get_model_worksheet(model.name)
        
        # Ensure worksheet has enough rows
        max_row = max(p.row for p in prompts) if prompts else 1
        self.sheets.ensure_row_exists(worksheet, max_row)
        
        # Get current status for all prompt rows (batch operation)
        prompt_rows = [p.row for p in prompts]
        statuses = self.sheets.batch_get_statuses(worksheet, prompt_rows)
        
        # Get backend (may fail if dependencies not installed)
        try:
            backend = self.backend_factory.get_backend(model.backend)
        except (ValueError, ImportError) as e:
            logger.error(f"Cannot initialize backend for {model.name}: {e}")
            stats.errors = len(prompts)
            return stats
        
        # Filter prompts that need processing
        prompts_to_process = []
        for prompt in prompts:
            status = statuses.get(prompt.row)
            
            # Skip if already has response (unless force mode)
            if status and status.has_response and not force:
                logger.debug(f"  Row {prompt.row}: skipped (already filled)")
                stats.skipped += 1
                continue
            
            # Dry run mode
            if dry_run:
                action = "would reprocess" if status and status.has_response else "would process"
                logger.info(f"  Row {prompt.row}: {action} - {prompt.text[:50]}...")
                stats.filled += 1  # Count as would-be-filled
                continue
            
            prompts_to_process.append(prompt)
        
        if not prompts_to_process:
            logger.info(f"  {model.name} complete: no prompts to process")
            return stats
        
        # Use batch processing for HuggingFace backend
        if backend.supports_batching() and len(prompts_to_process) > 1:
            batch_stats = self._process_batch(
                worksheet=worksheet,
                prompts=prompts_to_process,
                model=model,
                backend=backend
            )
            stats.filled += batch_stats.filled
            stats.errors += batch_stats.errors
        else:
            # Process sequentially for other backends
            for prompt in prompts_to_process:
                try:
                    result = self._process_prompt(
                        worksheet=worksheet,
                        prompt=prompt,
                        model=model,
                        backend=backend
                    )
                    
                    if result:
                        stats.filled += 1
                    else:
                        stats.errors += 1
                        
                except AuthenticationError as e:
                    logger.error(f"Authentication failed for {model.name}: {e}")
                    stats.errors += len(prompts_to_process) - stats.filled
                    break
                    
                except ModelNotFoundError as e:
                    logger.error(f"Model not found: {e}")
                    stats.errors += len(prompts_to_process) - stats.filled
                    break
        
        logger.info(
            f"  {model.name} complete: "
            f"filled={stats.filled}, skipped={stats.skipped}, errors={stats.errors}"
        )
        
        return stats
    
    def _process_batch(self,
                       worksheet,
                       prompts: list[Prompt],
                       model: ModelConfig,
                       backend,
                       batch_size: int = 8) -> ProcessingStats:
        """
        Process prompts in batches using HuggingFace batch inference.
        
        Args:
            worksheet: The model's worksheet
            prompts: List of prompts to process
            model: Model configuration
            backend: HuggingFace backend instance
            batch_size: Number of prompts per batch
        
        Returns:
            ProcessingStats for this batch operation
        """
        stats = ProcessingStats()
        
        logger.info(f"  Using batch inference (batch_size={batch_size}, total={len(prompts)})")
        
        # Mark all as processing first
        for prompt in prompts:
            try:
                self.sheets.set_processing(worksheet, prompt.row, prompt)
            except Exception as e:
                logger.error(f"  Row {prompt.row}: failed to set PROCESSING: {e}")
        
        # Process in batches
        for i in range(0, len(prompts), batch_size):
            batch_prompts = prompts[i:i + batch_size]
            batch_texts = [p.text for p in batch_prompts]
            
            logger.info(f"  Processing batch {i//batch_size + 1} ({len(batch_prompts)} prompts)...")
            
            try:
                results = backend.infer_batch(batch_texts, model, batch_size=batch_size)
                
                # Write results
                for prompt, result in zip(batch_prompts, results):
                    try:
                        self.sheets.write_response(
                            worksheet=worksheet,
                            row=prompt.row,
                            response=result.response,
                            tokens=result.tokens
                        )
                        stats.filled += 1
                        logger.debug(f"  Row {prompt.row}: done ({result.tokens or '?'} tokens)")
                    except Exception as e:
                        logger.error(f"  Row {prompt.row}: failed to write response: {e}")
                        stats.errors += 1
                        
            except BackendError as e:
                logger.error(f"  Batch failed: {e}")
                # Mark all prompts in this batch as errors
                for prompt in batch_prompts:
                    try:
                        self.sheets.write_error(worksheet, prompt.row, str(e))
                    except Exception:
                        pass
                    stats.errors += 1
        
        return stats
    
    def _process_prompt(self,
                        worksheet,
                        prompt: Prompt,
                        model: ModelConfig,
                        backend) -> bool:
        """
        Process a single prompt through a model.
        
        Args:
            worksheet: The model's worksheet
            prompt: The prompt to process
            model: Model configuration
            backend: The backend instance
        
        Returns:
            True if successful, False if error
        """
        logger.debug(f"  Row {prompt.row}: processing...")
        
        # Mark as processing
        try:
            self.sheets.set_processing(worksheet, prompt.row, prompt)
        except Exception as e:
            logger.error(f"  Row {prompt.row}: failed to set PROCESSING status: {e}")
            return False
        
        # Run inference
        try:
            result = infer_with_retry(
                backend=backend,
                prompt=prompt.text,
                model_config=model,
                max_retries=3
            )
            
            # Write successful response
            self.sheets.write_response(
                worksheet=worksheet,
                row=prompt.row,
                response=result.response,
                tokens=result.tokens
            )
            
            logger.info(
                f"  Row {prompt.row}: done "
                f"({result.tokens or '?'} tokens, {result.finish_reason})"
            )
            return True
            
        except BackendError as e:
            # Write error to sheet
            error_msg = str(e)
            logger.warning(f"  Row {prompt.row}: error - {error_msg[:100]}")
            
            try:
                self.sheets.write_error(
                    worksheet=worksheet,
                    row=prompt.row,
                    error=error_msg
                )
            except Exception as write_error:
                logger.error(f"  Row {prompt.row}: failed to write error: {write_error}")
            
            return False
        
        except Exception as e:
            # Unexpected error
            error_msg = f"Unexpected error: {e}"
            logger.exception(f"  Row {prompt.row}: {error_msg}")
            
            try:
                self.sheets.write_error(
                    worksheet=worksheet,
                    row=prompt.row,
                    error=error_msg
                )
            except Exception:
                pass
            
            return False


class InferenceProcessorAsync:
    """
    Async version of the processor for concurrent model processing.
    
    Note: This is a placeholder for future async implementation.
    The current synchronous version is sufficient for most use cases
    given Google Sheets API rate limits (60 requests/minute/user).
    """
    pass  # TODO: Implement if needed for high-throughput scenarios