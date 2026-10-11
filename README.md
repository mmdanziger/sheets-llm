# Google Sheets LLM Inference System

A production-ready Python system that fills Google Sheet cells with LLM responses. Supports batch (one-pass) and live (continuous polling) modes with multiple LLM backends.

## Features

- **Multi-model support**: OpenAI, Anthropic Claude, Ollama (local), HuggingFace Transformers
- **Batch & Live modes**: One-time processing or continuous monitoring for new prompts
- **Smart skip logic**: Automatically skips already-filled cells
- **Auto-recovery**: Exponential backoff retry on rate limits
- **Auto-provisioning**: Creates worksheets and pulls Ollama models automatically
- **Comprehensive logging**: Console and file logging with progress tracking

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

For GPU inference with HuggingFace:
```bash
# CUDA (NVIDIA)
pip install torch --index-url https://download.pytorch.org/whl/cu121

# Apple Silicon
pip install torch  # MPS support is built-in
```

### 2. Set Up Google Sheets Authentication

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select existing)
3. Enable the [Google Sheets API](https://console.cloud.google.com/apis/library/sheets.googleapis.com)
4. Create a Service Account:
   - Go to [Service Accounts](https://console.cloud.google.com/iam-admin/serviceaccounts)
   - Click "Create Service Account"
   - Download the JSON key file
   - Save as `service_account.json`
5. Share your Google Sheet with the service account email (Editor access)

Reference: [gspread Authentication](https://docs.gspread.org/en/v6.1.4/oauth2.html#for-bots-using-service-account)

### 3. Set Up API Keys

```bash
# Option 1: Environment variables
export OPENAI_API_KEY=sk-proj-...
export ANTHROPIC_API_KEY=sk-ant-api03-...

# Option 2: Create .env file
echo "OPENAI_API_KEY=sk-proj-..." > .env
echo "ANTHROPIC_API_KEY=sk-ant-api03-..." >> .env
```

### 4. Prepare Your Google Sheet

Create a Google Sheet with a "Prompts" worksheet:

| ID | Category | Prompt | Notes |
|----|----------|--------|-------|
| 1 | medical | What are the symptoms of diabetes? | Test case 1 |
| 2 | ethics | Is it ethical to use AI in diagnosis? | Test case 2 |
| 3 | safety | What are risks of AI hallucination? | Test case 3 |

The system will automatically create model-specific worksheets (e.g., "gpt-4o", "claude-3-5-sonnet") with columns:

| ID | Prompt | Response | Status | Timestamp | Tokens | Error |
|----|--------|----------|--------|-----------|--------|-------|

### 5. Configure Models

Edit `models.yaml` to select which models to use. See the file for detailed documentation.

### 6. Run

```bash
# Batch mode: process all prompts once
python sheets_inference.py \
  --sheet-url "https://docs.google.com/spreadsheets/d/1n_A4L6e8KMR57oL-W5U1woTHMAe7lvfrz9nRKAwOteY/edit" \
  --config models.yaml \
  --mode batch

# Live mode: continuously poll for new prompts
python sheets_inference.py \
  --sheet-url "https://docs.google.com/spreadsheets/d/1n_A4L6e8KMR57oL-W5U1woTHMAe7lvfrz9nRKAwOteY/edit" \
  --config models_no_llama.yaml \
  --mode live \
  --poll-interval 30

# Stream mode: lowest latency, for live class sessions (see "Running a live session")
python sheets_inference.py --sheet-url URL --config models.yaml --mode stream --poll-interval 2
```

## Usage

### Command Line Options

```
Required:
  --sheet-url URL       Google Sheets URL (must be shared with service account)
  --config FILE         Path to YAML configuration file

Mode:
  --mode {batch,live,stream}
                        batch: run once; live: continuous polling;
                        stream: low-latency live mode for class (default: batch)
  --poll-interval N     Seconds between polls in live/stream mode (default: 10)

Filtering:
  --models LIST         Comma-separated model names to process (default: all)
  --prompt-range RANGE  Row range like "1-50" or "10-" (default: all)

Behavior:
  --dry-run             Preview actions without making changes
  --force               Reprocess cells even if already filled

Logging:
  --log-file PATH       Also write logs to file
  --verbose, -v         Enable debug logging
```

### Examples

```bash
# Process only specific models
python sheets_inference.py --sheet-url URL --config models.yaml \
  --models gpt-4o,claude-3-5-sonnet

# Process rows 1-50 only
python sheets_inference.py --sheet-url URL --config models.yaml \
  --prompt-range 1-50

# Dry run to preview what would be processed
python sheets_inference.py --sheet-url URL --config models.yaml --dry-run

# Live mode with 60-second polling and logging
python sheets_inference.py --sheet-url URL --config models.yaml \
  --mode live --poll-interval 60 --log-file inference.log

# Force reprocess all cells (even filled ones)
python sheets_inference.py --sheet-url URL --config models.yaml --force
```

## Supported Backends

### OpenAI

**Reference**: [OpenAI API Documentation](https://platform.openai.com/docs/api-reference/chat)

| Model | Context | Notes |
|-------|---------|-------|
| `gpt-4o` | 128k | Latest flagship |
| `gpt-4o-mini` | 128k | Faster, cheaper |
| `gpt-4-turbo` | 128k | Previous gen |
| `gpt-3.5-turbo` | 16k | Fast, economical |

### Anthropic Claude

**Reference**: [Anthropic API Documentation](https://docs.anthropic.com/en/api/messages)

| Model | Context | Notes |
|-------|---------|-------|
| `claude-sonnet-4-20250514` | 200k | Claude 4 Sonnet |
| `claude-3-5-sonnet-20241022` | 200k | Balanced |
| `claude-3-5-haiku-20241022` | 200k | Fastest |
| `claude-3-opus-20240229` | 200k | Most capable |

### OpenAI-compatible endpoints (DeepSeek, Kimi, OpenRouter)

Any OpenAI-compatible API works through the `openai` backend: set `base_url` and a per-model `api_key`.

```yaml
- name: kimi-k2.6
  backend: openai
  base_url: https://openrouter.ai/api/v1
  api_key: ${OPENROUTER_API_KEY}
  model_id: moonshotai/kimi-k2.6
  max_tokens: 1500
```

| Provider | base_url | Example model_id |
|----------|----------|------------------|
| OpenRouter | `https://openrouter.ai/api/v1` | `deepseek/deepseek-v4.1-flash`, `moonshotai/kimi-k2.6` |
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` |
| Moonshot | `https://api.moonshot.ai/v1` | see platform.moonshot.ai |

Reasoning models spend tokens thinking before they answer, so give them a generous `max_tokens`.

### Ollama (Local)

**Reference**: [Ollama Model Library](https://ollama.com/library)

Requires Ollama running: `ollama serve`

| Model | Size | Notes |
|-------|------|-------|
| `llama3.2:latest` | 8B | Meta's latest |
| `mistral:latest` | 7B | Fast, capable |
| `phi3:latest` | 3.8B | Microsoft |
| `gemma2:latest` | 9B | Google |

Models are automatically pulled if not present.

### HuggingFace Transformers

**Reference**: [HuggingFace Models](https://huggingface.co/models)

| Model | Size | Device |
|-------|------|--------|
| `microsoft/Phi-3-mini-4k-instruct` | 3.8B | CUDA/MPS/CPU |
| `google/gemma-2-2b-it` | 2B | CUDA/MPS/CPU |
| `Qwen/Qwen2.5-0.5B-Instruct` | 0.5B | CPU OK |

## Sheet Structure

### Prompts Worksheet

The source of all prompts. Must be named "Prompts".

| Column | Description |
|--------|-------------|
| A: ID | Unique identifier for the prompt |
| B: Category | Classification/grouping (optional) |
| C: Prompt | The actual prompt text |
| D: Notes | Any notes or metadata (optional) |

### Model Worksheets

One worksheet per model, named after the model (e.g., "gpt-4o").

| Column | Description |
|--------|-------------|
| A: ID | Copied from Prompts worksheet |
| B: Prompt | Copied from Prompts worksheet |
| C: Response | LLM-generated response |
| D: Status | EMPTY → PROCESSING → DONE/ERROR |
| E: Timestamp | ISO 8601 completion time |
| F: Tokens | Response token count (if available) |
| G: Error | Error message (if Status=ERROR) |

## Educational Use Case

This system is designed for bioethics education, allowing instructors to:

1. **Prepare demonstrations**: Pre-fill responses from multiple models
2. **Enable student exploration**: Share read-write copies for student experimentation
3. **Compare model behavior**: Side-by-side analysis across models
4. **Document failures**: Systematic collection of AI safety violations

### Instructor Workflow

```bash
# 1. Create sheet with prompts designed to expose ethical violations
# 2. Run batch processing to fill all model responses
python sheets_inference.py --sheet-url URL --config models.yaml --mode batch

# 3. Share two versions with students:
#    - Read-only: For analysis (File > Share > Viewer)
#    - Read-write: For experimentation (File > Make a copy)

# 4. For live sessions, run in stream mode (see below)
```

### Running a live session

Use stream mode. It sends each new prompt to all models in parallel and polls only the
`Prompts` tab, so answers start arriving within seconds. Live mode runs models one after
another and re-reads every tab each cycle, so it is much slower with many models.

```bash
source sheets_env/bin/activate
caffeinate -i python sheets_inference.py \
  --sheet-url URL --config models.yaml \
  --mode stream --poll-interval 2 --log-file session.log
```

- `--poll-interval 2` is about 30 Sheets reads/min plus batched writes, under the
  60 req/min quota. `1` hits the quota and causes retries; `3` leaves more margin. Without the flag it waits 10 s.
- Start it a few minutes before class. Its first pass fills anything already pending and
  confirms that the service account and every API key work.
- `caffeinate -i` (macOS) keeps the laptop from sleeping mid-session.
- `--models a,b,c` limits cost if you don't need the whole lineup.
- Editing an already-submitted prompt does not re-run it; students should add a new row.
  Afterwards, re-run edited rows with `--mode batch --force --prompt-range N-M`.
- Students can view answers and submit prompts through the web viewer in `viewer/`.

### Grading the results with an LLM judge

After the session, `judge.py` grades every answered cell against the four categories from
the lecture (safety, privacy, hallucination, bias). The rubric is in `judge_rubric.md`. Each
response gets a violation category, subtype, severity 0-4 (none, minor, moderate, serious,
critical), a refused flag and short notes. Grades go to a `Judge` tab in the same sheet and
to `judgments.csv`. The tab uses the columns students submit: row number, prompt, response,
violation, model, severity, notes.

```bash
python judge.py --sheet-url URL --config models.yaml --dry-run   # what would be graded
python judge.py --sheet-url URL --config models.yaml
JUDGE_MODEL=claude-opus-5-5 python judge.py --sheet-url URL --config models.yaml --force
```

- The judge defaults to `claude-haiku-5-5`, which is cheap enough for a demo. Use
  `--judge-model` or `JUDGE_MODEL` for a stronger model when the grades matter.
- Cells are cached by their prompt and response text, so re-running only grades new or
  changed answers. `--models` and `--prompt-range` work as above, `--no-sheet` writes only
  the CSV, and `--force` re-grades everything.
- If the judge's own safety filter refuses a cell, the cell is marked `ERROR`. That usually
  means the response is itself dangerous, so review it by hand.
- In Claude Code, the `/judge` skill (`.claude/skills/judge/`) runs this and summarizes
  which attacks broke which models.

### Student Workflow

Students can:
1. Download the read-only sheet as CSV for analysis
2. Add new prompts to their copy
3. Instructor runs stream mode to auto-fill responses

## Error Handling

| Error Type | Behavior |
|------------|----------|
| Rate limits (429) | Exponential backoff, retry 3x |
| Auth errors (401/403) | Log clear message, exit |
| Model not found | Log error, continue to next model |
| Network errors | Retry with backoff |
| Sheet concurrent edit | Retry cell update |

## Rate Limits

| Service | Limit | Notes |
|---------|-------|-------|
| Google Sheets | 60 req/min/user | Built-in rate limiting |
| OpenAI Tier 1 | 500 req/min | Varies by tier |
| Anthropic Tier 1 | 50 req/min | Varies by tier |
| Ollama | No limit | Local inference |
| HuggingFace | No limit | Local inference |

## Troubleshooting

### "Spreadsheet not found" or "Permission denied"

1. Verify the sheet URL is correct
2. Check that the sheet is shared with the service account email
3. Ensure the service account has "Editor" access

### "API key not configured"

1. Set environment variables: `export OPENAI_API_KEY=...`
2. Or create a `.env` file in the same directory
3. Verify the key is valid at the provider's dashboard

### "Cannot connect to Ollama"

1. Start Ollama: `ollama serve`
2. Verify it's running: `curl http://localhost:11434/api/tags`
3. Check firewall settings if using remote Ollama

### HuggingFace model loading fails

1. Ensure sufficient RAM/VRAM for the model
2. For gated models, set `HF_TOKEN` environment variable
3. Try a smaller model (e.g., Qwen2.5-0.5B)

## Architecture

```
sheets_inference/
├── sheets_inference.py  # Main entry point, CLI handling
├── config.py            # YAML loading, validation, env expansion
├── sheets_io.py         # Google Sheets I/O, rate limiting
├── backends.py          # LLM backend abstraction
├── processor.py         # Orchestration logic
├── stream.py            # Low-latency live mode
├── judge.py             # LLM-as-judge grading of the results
├── judge_rubric.md      # Judge rubric (safety, privacy, hallucination, bias)
├── models.yaml          # Configuration file
├── viewer/              # Apps Script web app for the sheet
├── .claude/skills/judge # Claude Code /judge skill
├── requirements.txt     # Python dependencies
└── README.md            # This file
```

## References

- **gspread**: https://docs.gspread.org/en/v6.1.4/
- **OpenAI API**: https://platform.openai.com/docs/api-reference/chat
- **Anthropic API**: https://docs.anthropic.com/en/api/messages
- **Ollama API**: https://github.com/ollama/ollama/blob/main/docs/api.md
- **HuggingFace**: https://huggingface.co/docs/transformers/

## License

MIT License - Free for educational and commercial use.
