"""
Google Sheets I/O module for LLM Inference System.

Handles all interactions with Google Sheets including authentication,
reading prompts, writing responses, and worksheet management.

Reference Documentation:
- gspread: https://docs.gspread.org/en/v6.1.4/
- Service Account Auth: https://docs.gspread.org/en/v6.1.4/oauth2.html
- Google Sheets API Limits: https://developers.google.com/sheets/api/limits
  - 300 requests per minute per project
  - 60 requests per minute per user per project

Sheet Structure:
    "Prompts" worksheet: ID | Category | Prompt | Notes
    Per-model worksheets: ID | Prompt | Response | Status | Timestamp | Tokens | Error
"""

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)


# Worksheet column mappings
PROMPTS_COLUMNS = {
    'id': 1,        # A
    'category': 2,  # B
    'prompt': 3,    # C
    'notes': 4      # D
}

MODEL_COLUMNS = {
    'id': 1,        # A
    'prompt': 2,    # B
    'response': 3,  # C
    'status': 4,    # D
    'timestamp': 5, # E
    'tokens': 6,    # F
    'error': 7      # G
}

# Status values
STATUS_EMPTY = 'EMPTY'
STATUS_PROCESSING = 'PROCESSING'
STATUS_DONE = 'DONE'
STATUS_ERROR = 'ERROR'


@dataclass
class Prompt:
    """A prompt to process."""
    row: int  # 1-indexed row number in the sheet
    id: str
    category: str
    text: str
    notes: str


@dataclass
class CellStatus:
    """Status of a response cell in a model worksheet."""
    has_response: bool
    status: str
    row: int


class SheetsClient:
    """
    Client for Google Sheets operations with rate limiting and retry logic.
    
    Implements exponential backoff for rate limits and automatic worksheet creation.
    """
    
    # Rate limiting: 60 requests/min per user
    MIN_REQUEST_INTERVAL = 1.1  # seconds between requests (conservative)
    MAX_RETRIES = 3
    BACKOFF_BASE = 2  # seconds, doubles each retry
    
    def __init__(self, credentials_file: str, sheet_url: str):
        """
        Initialize the Sheets client.
        
        Args:
            credentials_file: Path to service account JSON file
            sheet_url: Full URL to the Google Sheet
        
        Raises:
            FileNotFoundError: If credentials file doesn't exist
            gspread.exceptions.APIError: If authentication fails
            gspread.exceptions.SpreadsheetNotFound: If sheet doesn't exist or no access
        """
        self.credentials_file = credentials_file
        self.sheet_url = sheet_url
        self._last_request_time = 0.0
        
        # Extract sheet ID from URL
        self.sheet_id = self._extract_sheet_id(sheet_url)
        
        # Authenticate
        self._authenticate()
        
        # Open spreadsheet
        self.spreadsheet = self._with_retry(
            lambda: self.client.open_by_key(self.sheet_id)
        )
        logger.info(f"Connected to: {self.spreadsheet.title}")
    
    @staticmethod
    def _extract_sheet_id(url: str) -> str:
        """Extract the spreadsheet ID from a Google Sheets URL."""
        # Handles various URL formats:
        # https://docs.google.com/spreadsheets/d/SHEET_ID/edit#gid=0
        # https://docs.google.com/spreadsheets/d/SHEET_ID
        pattern = r'/spreadsheets/d/([a-zA-Z0-9-_]+)'
        match = re.search(pattern, url)
        if not match:
            raise ValueError(f"Could not extract sheet ID from URL: {url}")
        return match.group(1)
    
    def _authenticate(self) -> None:
        """Authenticate with Google Sheets API using service account."""
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive.readonly'
        ]
        
        credentials = Credentials.from_service_account_file(
            self.credentials_file,
            scopes=scopes
        )
        
        self.client = gspread.authorize(credentials)
        logger.debug("Successfully authenticated with Google Sheets API")
    
    def _rate_limit(self) -> None:
        """Enforce rate limiting between API requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.MIN_REQUEST_INTERVAL:
            sleep_time = self.MIN_REQUEST_INTERVAL - elapsed
            time.sleep(sleep_time)
        self._last_request_time = time.time()
    
    def _with_retry(self, operation, retries: int = None):
        """
        Execute operation with exponential backoff retry on rate limits.
        
        Args:
            operation: Callable to execute
            retries: Number of retries (default: MAX_RETRIES)
        
        Returns:
            Result of the operation
        
        Raises:
            gspread.exceptions.APIError: If all retries exhausted
        """
        if retries is None:
            retries = self.MAX_RETRIES
        
        for attempt in range(retries + 1):
            self._rate_limit()
            
            try:
                return operation()
                
            except gspread.exceptions.APIError as e:
                error_code = e.response.status_code if hasattr(e, 'response') else None
                
                # Rate limit (429) or quota exceeded
                if error_code in (429, 503):
                    if attempt < retries:
                        backoff = self.BACKOFF_BASE * (2 ** attempt)
                        logger.warning(
                            f"Rate limited, retrying in {backoff}s "
                            f"(attempt {attempt + 1}/{retries})"
                        )
                        time.sleep(backoff)
                        continue
                
                # Auth error - fail immediately
                if error_code in (401, 403):
                    logger.error(
                        "Authentication failed. Check that:\n"
                        "1. Service account credentials are valid\n"
                        "2. Sheet is shared with the service account email"
                    )
                    raise
                
                raise
    
    def get_or_create_worksheet(self, title: str, headers: list[str]) -> gspread.Worksheet:
        """
        Get existing worksheet or create new one with headers.
        
        Args:
            title: Worksheet title
            headers: List of header strings for first row
        
        Returns:
            gspread.Worksheet object
        """
        try:
            worksheet = self._with_retry(
                lambda: self.spreadsheet.worksheet(title)
            )
            logger.debug(f"Found existing worksheet: {title}")
            return worksheet
            
        except gspread.exceptions.WorksheetNotFound:
            logger.info(f"Creating worksheet: {title}")
            
            # Create with enough rows and columns
            worksheet = self._with_retry(
                lambda: self.spreadsheet.add_worksheet(
                    title=title,
                    rows=1000,
                    cols=len(headers)
                )
            )
            
            # Add headers
            self._with_retry(
                lambda: worksheet.update('A1', [headers])
            )
            
            # Format header row (bold)
            self._with_retry(
                lambda: worksheet.format('1:1', {'textFormat': {'bold': True}})
            )
            
            return worksheet
    
    def get_prompts_worksheet(self) -> gspread.Worksheet:
        """Get or create the Prompts worksheet."""
        headers = ['ID', 'Category', 'Prompt', 'Notes']
        return self.get_or_create_worksheet('Prompts', headers)
    
    def get_model_worksheet(self, model_name: str) -> gspread.Worksheet:
        """Get or create a model's response worksheet."""
        headers = ['ID', 'Prompt', 'Response', 'Status', 'Timestamp', 'Tokens', 'Error']
        return self.get_or_create_worksheet(model_name, headers)
    
    def get_all_prompts(self) -> list[Prompt]:
        """
        Read all prompts from the Prompts worksheet.
        
        Returns:
            List of Prompt objects (skips header row and empty rows)
        """
        worksheet = self.get_prompts_worksheet()
        
        # Get all values
        values = self._with_retry(lambda: worksheet.get_all_values())
        
        prompts = []
        for row_idx, row in enumerate(values[1:], start=2):  # Skip header, 1-indexed
            # Skip empty rows
            if not row or not any(row):
                continue
            
            # Ensure we have enough columns
            while len(row) < 4:
                row.append('')
            
            prompt_text = row[PROMPTS_COLUMNS['prompt'] - 1].strip()
            if not prompt_text:
                continue  # Skip rows with no prompt
            
            prompts.append(Prompt(
                row=row_idx,
                id=row[PROMPTS_COLUMNS['id'] - 1],
                category=row[PROMPTS_COLUMNS['category'] - 1],
                text=prompt_text,
                notes=row[PROMPTS_COLUMNS['notes'] - 1]
            ))
        
        logger.debug(f"Loaded {len(prompts)} prompts")
        return prompts
    
    def get_cell_status(self, worksheet: gspread.Worksheet, row: int) -> CellStatus:
        """
        Check if a row in a model worksheet has a response.
        
        Args:
            worksheet: The model worksheet
            row: Row number (1-indexed)
        
        Returns:
            CellStatus with response status
        """
        # Get response and status cells
        response_cell = f"C{row}"  # Response column
        status_cell = f"D{row}"    # Status column
        
        values = self._with_retry(
            lambda: worksheet.batch_get([response_cell, status_cell])
        )
        
        response_value = values[0][0][0] if values[0] and values[0][0] else ''
        status_value = values[1][0][0] if values[1] and values[1][0] else ''
        
        return CellStatus(
            has_response=bool(response_value.strip()),
            status=status_value or STATUS_EMPTY,
            row=row
        )
    
    def batch_get_statuses(self, worksheet: gspread.Worksheet, 
                           rows: list[int]) -> dict[int, CellStatus]:
        """
        Get status for multiple rows in a single API call.
        
        Args:
            worksheet: The model worksheet
            rows: List of row numbers (1-indexed)
        
        Returns:
            Dict mapping row number to CellStatus
        """
        if not rows:
            return {}
        
        # Build range list for batch get
        ranges = []
        for row in rows:
            ranges.append(f"C{row}")  # Response
            ranges.append(f"D{row}")  # Status
        
        values = self._with_retry(lambda: worksheet.batch_get(ranges))
        
        statuses = {}
        for i, row in enumerate(rows):
            response_idx = i * 2
            status_idx = i * 2 + 1
            
            response_value = ''
            if response_idx < len(values) and values[response_idx]:
                response_value = values[response_idx][0][0] if values[response_idx][0] else ''
            
            status_value = ''
            if status_idx < len(values) and values[status_idx]:
                status_value = values[status_idx][0][0] if values[status_idx][0] else ''
            
            statuses[row] = CellStatus(
                has_response=bool(response_value.strip()),
                status=status_value or STATUS_EMPTY,
                row=row
            )
        
        return statuses
    
    def set_processing(self, worksheet: gspread.Worksheet, row: int, prompt: Prompt) -> None:
        """
        Mark a row as processing and copy prompt.
        
        Args:
            worksheet: The model worksheet
            row: Row number (1-indexed)
            prompt: The prompt being processed
        """
        updates = [
            {
                'range': f'A{row}:D{row}',
                'values': [[prompt.id, prompt.text, '', STATUS_PROCESSING]]
            }
        ]
        
        self._with_retry(
            lambda: worksheet.batch_update(updates)
        )
    
    def write_response(self, worksheet: gspread.Worksheet, row: int,
                       response: str, tokens: Optional[int] = None) -> None:
        """
        Write a successful response to the worksheet.
        
        Args:
            worksheet: The model worksheet
            row: Row number (1-indexed)
            response: The LLM response text
            tokens: Token count (if available)
        """
        timestamp = datetime.now().isoformat(timespec='seconds')
        token_str = str(tokens) if tokens is not None else ''
        
        updates = [
            {
                'range': f'C{row}:G{row}',
                'values': [[response, STATUS_DONE, timestamp, token_str, '']]
            }
        ]
        
        self._with_retry(
            lambda: worksheet.batch_update(updates)
        )
    
    def write_error(self, worksheet: gspread.Worksheet, row: int, error: str) -> None:
        """
        Write an error status to the worksheet.
        
        Args:
            worksheet: The model worksheet
            row: Row number (1-indexed)
            error: Error message
        """
        timestamp = datetime.now().isoformat(timespec='seconds')
        
        updates = [
            {
                'range': f'C{row}:G{row}',
                'values': [['', STATUS_ERROR, timestamp, '', error[:500]]]  # Truncate error
            }
        ]
        
        self._with_retry(
            lambda: worksheet.batch_update(updates)
        )
    
    def ensure_row_exists(self, worksheet: gspread.Worksheet, row: int) -> None:
        """
        Ensure the worksheet has at least this many rows.
        
        Args:
            worksheet: The worksheet to expand
            row: Required row number
        """
        current_rows = worksheet.row_count
        if row > current_rows:
            # Add extra rows with some buffer
            new_rows = row + 100
            self._with_retry(
                lambda: worksheet.resize(rows=new_rows)
            )
            logger.debug(f"Expanded worksheet to {new_rows} rows")


class BatchWriter:
    """
    Accumulates updates and writes them in batches to reduce API calls.
    
    Usage:
        with BatchWriter(sheets_client, worksheet) as writer:
            writer.queue_response(row, response, tokens)
            writer.queue_error(row, error)
        # Writes happen on context exit
    """
    
    MAX_BATCH_SIZE = 50  # Google Sheets recommends batches of 50 or fewer
    
    def __init__(self, client: SheetsClient, worksheet: gspread.Worksheet):
        self.client = client
        self.worksheet = worksheet
        self._pending_updates = []
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.flush()
        return False
    
    def queue_response(self, row: int, response: str, 
                       tokens: Optional[int] = None) -> None:
        """Queue a successful response for batch write."""
        timestamp = datetime.now().isoformat(timespec='seconds')
        token_str = str(tokens) if tokens is not None else ''
        
        self._pending_updates.append({
            'range': f'C{row}:G{row}',
            'values': [[response, STATUS_DONE, timestamp, token_str, '']]
        })
        
        if len(self._pending_updates) >= self.MAX_BATCH_SIZE:
            self.flush()
    
    def queue_error(self, row: int, error: str) -> None:
        """Queue an error for batch write."""
        timestamp = datetime.now().isoformat(timespec='seconds')
        
        self._pending_updates.append({
            'range': f'C{row}:G{row}',
            'values': [['', STATUS_ERROR, timestamp, '', error[:500]]]
        })
        
        if len(self._pending_updates) >= self.MAX_BATCH_SIZE:
            self.flush()
    
    def flush(self) -> None:
        """Write all pending updates to the worksheet."""
        if not self._pending_updates:
            return
        
        logger.debug(f"Flushing {len(self._pending_updates)} updates")
        
        self.client._with_retry(
            lambda: self.worksheet.batch_update(self._pending_updates)
        )
        
        self._pending_updates = []
