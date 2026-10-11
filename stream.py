"""
Stream mode: low-latency live processing for in-class use.

Live mode re-reads every model tab each cycle and runs models one after another,
so with many models a new prompt can wait half a minute before anything happens.
Stream mode instead:
  - polls only the Prompts tab (one Sheets request per poll),
  - sends each new prompt to all models in parallel,
  - writes "PROCESSING" markers and finished answers in batched requests,
so answers start arriving within seconds and Sheets quota use stays flat
(about two requests per poll, however many people are submitting).

On startup it runs one normal pass (process_all) to fill anything already pending.
After that, only rows that gain a prompt are processed; editing an existing
prompt's text does not re-run it (use batch mode with --force for that).
"""

import logging
import queue
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

from backends import infer_with_retry
from sheets_io import Prompt, STATUS_DONE, STATUS_ERROR, STATUS_PROCESSING

logger = logging.getLogger(__name__)


def _tab(name: str) -> str:
    """A1 sheet-name quoting."""
    return "'" + name.replace("'", "''") + "'"


def run_stream(processor, model_filter: Optional[list[str]] = None,
               poll_interval: float = 3.0, workers: int = 16) -> None:
    sheets, config = processor.sheets, processor.config
    models = [m for m in config.models if not model_filter or m.name in model_filter]
    # Create backends and tabs up front: BackendFactory isn't thread-safe
    backends = {m.name: processor.backend_factory.get_backend(m.backend) for m in models}
    tabs = {m.name: sheets.get_model_worksheet(m.name) for m in models}
    capacity = {name: ws.row_count for name, ws in tabs.items()}
    prompts_ws = sheets.get_prompts_worksheet()

    stop = False

    def on_signal(signum, frame):
        nonlocal stop
        if stop:
            raise SystemExit(1)
        stop = True
        logger.info("\nStopping after in-flight requests finish... (Ctrl+C again to force quit)")

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    logger.info("Stream mode: one pass over existing prompts first")
    processor.process_all(model_filter=model_filter)
    seen = {p.row for p in sheets.get_all_prompts()}
    logger.info(f"Watching for new prompts every {poll_interval}s "
                f"({len(seen)} existing, {len(models)} models in parallel)")

    results = queue.Queue()
    pool = ThreadPoolExecutor(max_workers=workers)
    in_flight = 0

    def write(updates):
        # RAW so model output starting with "=" isn't parsed as a formula
        sheets._with_retry(lambda: sheets.spreadsheet.values_batch_update(
            {'valueInputOption': 'RAW', 'data': updates}))

    def run_one(model, prompt):
        try:
            r = infer_with_retry(backends[model.name], prompt.text, model)
            results.put((model.name, prompt.row, r.response, r.tokens, None))
        except Exception as e:  # report every failure into the sheet
            results.put((model.name, prompt.row, None, None, str(e)))

    def flush():
        nonlocal in_flight
        ts = datetime.now().isoformat(timespec='seconds')
        items = []
        while True:
            try:
                items.append(results.get_nowait())
            except queue.Empty:
                break
        updates = []
        for name, row, response, tokens, err in items:
            if err is None:
                vals = [response, STATUS_DONE, ts, '' if tokens is None else str(tokens), '']
                logger.info(f"  Row {row}: {name} done")
            else:
                vals = ['', STATUS_ERROR, ts, '', err[:500]]
                logger.warning(f"  Row {row}: {name} error - {err[:100]}")
            updates.append({'range': f"{_tab(name)}!C{row}:G{row}", 'values': [vals]})
        if not updates:
            return
        try:
            write(updates)
            in_flight -= len(items)
        except Exception:
            for item in items:  # keep them for the next flush
                results.put(item)
            raise

    while not stop:
        try:
            rows = sheets._with_retry(lambda: prompts_ws.get_values('A2:C'))
            new = []
            for i, row in enumerate(rows, start=2):
                row = list(row) + [''] * (3 - len(row))
                text = row[2].strip()
                if text and i not in seen:
                    seen.add(i)
                    new.append(Prompt(row=i, id=row[0], category=row[1], text=text, notes=''))

            if new:
                marks = []
                for p in new:
                    logger.info(f"Row {p.row}: new prompt - {p.text[:60]}")
                    for m in models:
                        if p.row > capacity[m.name]:
                            sheets.ensure_row_exists(tabs[m.name], p.row)
                            capacity[m.name] = p.row + 100
                        marks.append({'range': f"{_tab(m.name)}!A{p.row}:G{p.row}",
                                      'values': [[p.id, p.text, '', STATUS_PROCESSING, '', '', '']]})
                write(marks)
                for p in new:
                    for m in models:
                        in_flight += 1
                        pool.submit(run_one, m, p)

            flush()
        except Exception as e:
            logger.error(f"Stream cycle failed: {e}")
        time.sleep(poll_interval)

    while in_flight > 0:
        time.sleep(1)
        flush()
    pool.shutdown()
    logger.info("Stream mode stopped")
