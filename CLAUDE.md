# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Python CLI that fills Google Sheet cells with LLM responses. Each row of the `Prompts` tab is sent to every model in a YAML config, and each model's answer goes into the same row of a tab named after that model. It is used for classroom red-teaming exercises: students submit prompts live and compare how models of different robustness respond. `viewer/` is a separate Google Apps Script web app that displays the same sheet.

## Commands

```bash
source sheets_env/bin/activate          # local virtualenv (already contains requirements)
pip install -r requirements.txt

# One pass over all prompts/models; exits 1 if any cell errored
python sheets_inference.py --sheet-url URL --config models.yaml --mode batch

# Continuous polling (re-runs process_all every poll)
python sheets_inference.py --sheet-url URL --config models.yaml --mode live --poll-interval 10

# Low-latency live mode for in-class use (see stream.py)
python sheets_inference.py --sheet-url URL --config models.yaml --mode stream --poll-interval 2

# Useful flags: --models a,b  --prompt-range 10-50  --dry-run  --force  -v  --log-file PATH
```

There are no tests, linter or CI. To validate a change, run with `--dry-run`, or against a test sheet shared with the service account, narrowed with `--models` and `--prompt-range`.

## Architecture

- `sheets_inference.py`: CLI entrypoint. Heavy modules (`config`, `sheets_io`, `processor`, `stream`) are imported inside `main()` after argument parsing, so `--help` works without the dependencies installed. Keep it that way.
- `config.py`: loads YAML into `Config`/`ModelConfig` dataclasses, loads `.env` (from the config's directory first, then the CWD) and expands `${VAR}`. `ModelConfig.__post_init__` validates backends and rejects an `api_key` that still contains an unexpanded `${...}`.
- `sheets_io.py`: all gspread access. `SheetsClient._with_retry` provides rate limiting and retries for Sheets calls. The column layouts (`PROMPTS_COLUMNS`, `MODEL_COLUMNS`) and status constants (`EMPTY`/`PROCESSING`/`DONE`/`ERROR`) are defined here.
- `processor.py`: `InferenceProcessor.process_all` → `_process_model` → `_process_prompt`. Models run **sequentially**. Statuses are batch-read **before** the backend is created, so a model with nothing to do never loads its backend (which matters for HuggingFace). Backends that support batching (HuggingFace) go through `_process_batch`.
- `backends.py`: `LLMBackend` subclasses for `openai`, `anthropic`, `ollama` and `huggingface`, created lazily and cached by `BackendFactory`. Provider exceptions are translated into `RateLimitError`/`AuthenticationError`/`ModelNotFoundError`. `infer_with_retry` retries only rate limits. To add a backend, add a subclass, register it in `BackendFactory.get_backend`, and add its name to `valid_backends` in `config.py`.
- `stream.py`: `run_stream` first runs `process_all` once, then polls only `Prompts!A2:C`. Each new row goes to all models in parallel on a thread pool, and `PROCESSING` markers and results are written with `values_batch_update` (RAW). It writes to the model tabs by A1 ranges (`C{row}:G{row}`), so it depends on the `MODEL_COLUMNS` layout. Rows already seen are not re-run when their text is edited.

### Invariants that span files

- **Row N of `Prompts` is answered in row N of every model tab.** `processor.py`, `stream.py` and `viewer/Code.gs` all rely on this. `viewer/Code.gs` detects model tabs by the header `ID | Prompt | Response | Status` and flags rows whose stored prompt text no longer matches.
- Tab name == `ModelConfig.name`. Renaming a model in the YAML creates a new tab and the model reprocesses everything.
- A cell is skipped when it already has a response, unless `--force` is passed.

### The OpenAI backend is the generic one

Most models in `models.yaml` use `backend: openai` with a per-model `base_url` + `api_key` (OpenRouter, DeepSeek, Moonshot). Clients are cached per `(base_url, api_key)`. `extra_body` is passed through for provider options such as OpenRouter's `reasoning: {enabled: false}`. `temperature: null` means the parameter is not sent, which current Claude models require.

## Config files

- `models.yaml`: the live red-teaming lineup. Its comments record the reasoning (expected robustness order, prices, why models are routed via OpenRouter); preserve and update them when editing. The credentials path is `${HOME}/.config/gspread/service_account.json`.
- `models_no_llama.yaml`: older example config.
- Keys come from `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY` and `HF_TOKEN` (env or `.env`).

## viewer/

Apps Script (`Code.gs` + `Index.html`) that is pasted into the sheet's script editor and deployed as a web app; it is not run from this repo. After editing it, redeploy as a new version (see `viewer/README.md`).
