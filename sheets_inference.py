#!/usr/bin/env python3
"""
Google Sheets LLM Inference System

A production-ready script that fills Google Sheet cells with LLM responses.
Supports batch (one-pass) and live (continuous polling) modes.

Usage:
    # Batch mode: run once, exit
    python sheets_inference.py --sheet-url URL --config models.yaml --mode batch
    
    # Live mode: poll continuously
    python sheets_inference.py --sheet-url URL --config models.yaml --mode live --poll-interval 10

Reference Documentation:
- gspread: https://docs.gspread.org/en/v6.1.4/
- Google Sheets API: https://developers.google.com/sheets/api/reference/rest
- OpenAI API: https://platform.openai.com/docs/api-reference/chat
- Anthropic API: https://docs.anthropic.com/en/api/messages
- Ollama API: https://github.com/ollama/ollama/blob/main/docs/api.md
- HuggingFace Transformers: https://huggingface.co/docs/transformers/main_classes/pipelines

Author: Claude (Anthropic)
Version: 1.0.0
"""

import argparse
import logging
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# Lazy imports - actual imports happen in main() after arg parsing
# This allows --help to work without all dependencies installed

# Configure logging
def setup_logging(log_file: Optional[str] = None, verbose: bool = False) -> logging.Logger:
    """Configure logging to console and optionally to file."""
    level = logging.DEBUG if verbose else logging.INFO
    
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding='utf-8'))
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=handlers
    )
    
    # Reduce noise from libraries
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('google').setLevel(logging.WARNING)
    
    return logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Google Sheets LLM Inference System',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Batch mode with all models
  python sheets_inference.py --sheet-url "https://docs.google.com/spreadsheets/d/..." --config models.yaml --mode batch
  
  # Live mode with specific models
  python sheets_inference.py --sheet-url "..." --config models.yaml --mode live --poll-interval 30 --models gpt-4,claude-3-opus
  
  # Dry run to preview actions
  python sheets_inference.py --sheet-url "..." --config models.yaml --dry-run
  
  # Process specific row range
  python sheets_inference.py --sheet-url "..." --config models.yaml --prompt-range 1-50
        """
    )
    
    # Required arguments
    parser.add_argument(
        '--sheet-url',
        required=True,
        help='Google Sheets URL (must be shared with service account)'
    )
    parser.add_argument(
        '--config',
        required=True,
        type=Path,
        help='Path to YAML configuration file'
    )
    
    # Mode selection
    parser.add_argument(
        '--mode',
        choices=['batch', 'live'],
        default='batch',
        help='Operating mode: batch (run once) or live (continuous polling)'
    )
    parser.add_argument(
        '--poll-interval',
        type=int,
        default=10,
        help='Seconds between polls in live mode (default: 10)'
    )
    
    # Filtering options
    parser.add_argument(
        '--models',
        type=str,
        help='Comma-separated list of model names to process (default: all)'
    )
    parser.add_argument(
        '--prompt-range',
        type=str,
        help='Row range to process, e.g., "1-50" or "10-" (default: all)'
    )
    
    # Behavior options
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview actions without making changes'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Reprocess cells even if already filled'
    )
    
    # Logging options
    parser.add_argument(
        '--log-file',
        type=Path,
        help='Path to log file (in addition to console)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose/debug logging'
    )
    
    return parser.parse_args()


def parse_prompt_range(range_str: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    """Parse prompt range string like '1-50' or '10-' into (start, end) tuple."""
    if not range_str:
        return (None, None)
    
    parts = range_str.split('-')
    if len(parts) != 2:
        raise ValueError(f"Invalid range format: {range_str}. Use 'start-end' or 'start-'")
    
    start = int(parts[0]) if parts[0] else None
    end = int(parts[1]) if parts[1] else None
    
    return (start, end)


def run_batch(processor: 'InferenceProcessor', 
              model_filter: Optional[list[str]], 
              prompt_range: tuple[Optional[int], Optional[int]],
              dry_run: bool,
              force: bool) -> dict:
    """Run single batch processing pass."""
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("Starting batch processing")
    
    stats = processor.process_all(
        model_filter=model_filter,
        prompt_range=prompt_range,
        dry_run=dry_run,
        force=force
    )
    
    logger.info("-" * 60)
    logger.info("Batch complete:")
    logger.info(f"  Filled:  {stats['filled']}")
    logger.info(f"  Skipped: {stats['skipped']}")
    logger.info(f"  Errors:  {stats['errors']}")
    logger.info("=" * 60)
    
    return stats


def run_live(processor: 'InferenceProcessor',
             model_filter: Optional[list[str]],
             prompt_range: tuple[Optional[int], Optional[int]],
             poll_interval: int,
             dry_run: bool,
             force: bool) -> None:
    """Run continuous polling loop."""
    logger = logging.getLogger(__name__)
    
    # Setup graceful shutdown
    shutdown_requested = False
    
    def signal_handler(signum, frame):
        nonlocal shutdown_requested
        logger.info("\nShutdown requested, finishing current cycle...")
        shutdown_requested = True
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logger.info("=" * 60)
    logger.info(f"Starting live mode (poll interval: {poll_interval}s)")
    logger.info("Press Ctrl+C to stop gracefully")
    logger.info("=" * 60)
    
    cycle = 0
    total_stats = {'filled': 0, 'skipped': 0, 'errors': 0}
    
    while not shutdown_requested:
        cycle += 1
        logger.info(f"\n--- Cycle {cycle} at {datetime.now().strftime('%H:%M:%S')} ---")
        
        try:
            stats = processor.process_all(
                model_filter=model_filter,
                prompt_range=prompt_range,
                dry_run=dry_run,
                force=force
            )
            
            # Accumulate stats
            for key in total_stats:
                total_stats[key] += stats[key]
            
            if stats['filled'] > 0 or stats['errors'] > 0:
                logger.info(f"Cycle {cycle}: filled={stats['filled']}, errors={stats['errors']}")
            else:
                logger.debug(f"Cycle {cycle}: no new prompts to process")
                
        except Exception as e:
            logger.error(f"Cycle {cycle} failed: {e}")
        
        if not shutdown_requested:
            logger.debug(f"Sleeping {poll_interval}s...")
            time.sleep(poll_interval)
    
    logger.info("\n" + "=" * 60)
    logger.info("Live mode stopped")
    logger.info(f"Total across {cycle} cycles:")
    logger.info(f"  Filled:  {total_stats['filled']}")
    logger.info(f"  Skipped: {total_stats['skipped']}")
    logger.info(f"  Errors:  {total_stats['errors']}")
    logger.info("=" * 60)


def main():
    """Main entry point."""
    args = parse_args()
    
    # Setup logging
    logger = setup_logging(
        log_file=str(args.log_file) if args.log_file else None,
        verbose=args.verbose
    )
    
    logger.info("Google Sheets LLM Inference System")
    logger.info(f"Mode: {args.mode}")
    
    # Import modules now (after arg parsing, so --help works without deps)
    from config import load_config
    from sheets_io import SheetsClient
    from processor import InferenceProcessor
    
    try:
        # Load configuration
        logger.info(f"Loading config from: {args.config}")
        config = load_config(args.config)
        
        # Parse filters
        model_filter = args.models.split(',') if args.models else None
        prompt_range = parse_prompt_range(args.prompt_range)
        
        if model_filter:
            logger.info(f"Model filter: {model_filter}")
        if prompt_range != (None, None):
            logger.info(f"Prompt range: {prompt_range}")
        if args.dry_run:
            logger.info("DRY RUN MODE - no changes will be made")
        if args.force:
            logger.info("FORCE MODE - will reprocess filled cells")
        
        # Initialize sheets client
        logger.info("Connecting to Google Sheets...")
        sheets = SheetsClient(
            credentials_file=config.google_sheets.credentials_file,
            sheet_url=args.sheet_url
        )
        
        # Initialize processor
        processor = InferenceProcessor(
            sheets=sheets,
            config=config
        )
        
        # Run in selected mode
        if args.mode == 'batch':
            stats = run_batch(
                processor=processor,
                model_filter=model_filter,
                prompt_range=prompt_range,
                dry_run=args.dry_run,
                force=args.force
            )
            sys.exit(1 if stats['errors'] > 0 else 0)
            
        else:  # live mode
            run_live(
                processor=processor,
                model_filter=model_filter,
                prompt_range=prompt_range,
                poll_interval=args.poll_interval,
                dry_run=args.dry_run,
                force=args.force
            )
            
    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        sys.exit(1)
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
