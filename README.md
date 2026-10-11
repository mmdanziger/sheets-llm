# sheets-llm

Fills Google Sheet cells with LLM responses, for classroom red-teaming. Each prompt in
the sheet's `Prompts` tab goes to every model in a YAML config, and each model's answer
lands in the same row of a tab named after that model. Students add prompts live and
compare how models of different robustness respond. Afterwards an LLM judge grades every
answer.

## Features

- **Any model, one config**: native Anthropic and OpenAI APIs, plus any OpenAI-compatible
  endpoint (OpenRouter, DeepSeek, Moonshot) with a per-model `base_url` and `api_key`.
  Also local models through Ollama or HuggingFace Transformers.
- **Three modes**: `batch` (one pass), `live` (re-check everything on a poll) and `stream`
  (low-latency live mode for class: each new prompt goes to all models in parallel).
- **Skips filled cells**, so re-runs only fill what's missing (`--force` to redo).
- **Web viewer** (`viewer/`): an Apps Script page for students to submit prompts and
  read the answers side by side.
- **LLM judge** (`judge.py`): grades every answer for safety, privacy, hallucination
  and bias, with severity 0-4.
- Retries on rate limits for both Google Sheets and the model APIs.

## Quick start

### 1. Install

```bash
python3 -m venv sheets_env
source sheets_env/bin/activate
pip install -r requirements.txt
```

`requirements.txt` includes `transformers`/`torch` for the HuggingFace backend. If you
only use API models you can skip those four lines.

### 2. Google Sheets service account

1. In the [Google Cloud Console](https://console.cloud.google.com/), create or pick a
   project and enable the
   [Google Sheets API](https://console.cloud.google.com/apis/library/sheets.googleapis.com).
2. Create a [service account](https://console.cloud.google.com/iam-admin/serviceaccounts)
   and download a JSON key.
3. Save it as `~/.config/gspread/service_account.json`. That's the path both configs use
   (`credentials_file:` in the YAML).
4. Share the Google Sheet with the service account's email address as **Editor**.

Reference: [gspread service account auth](https://docs.gspread.org/en/v6.1.4/oauth2.html#for-bots-using-service-account)

### 3. API keys

Set them in the environment, or in a `.env` file next to the config (or in the working
directory):

```bash
ANTHROPIC_API_KEY=sk-ant-...     # Claude models, and judge.py
OPENROUTER_API_KEY=sk-or-...     # every other model in models.yaml
OPENAI_API_KEY=sk-proj-...       # only for models using the native OpenAI API
HF_TOKEN=hf_...                  # only for gated HuggingFace models
```

### 4. Prepare the sheet

Create a tab named `Prompts`:

| ID | Category | Prompt | Notes |
|----|----------|--------|-------|
| 1 | safety | My 2 year old's blood sugar is 6 and ... | missed emergency? |
| 2 | privacy | Draft a letter for the office lobby about ... | |

Model tabs are created automatically, one per `name:` in the config.

### 5. Run

```bash
python sheets_inference.py --sheet-url URL --config models.yaml --dry-run   # preview
python sheets_inference.py --sheet-url URL --config models.yaml             # batch (one pass)
python sheets_inference.py --sheet-url URL --config models.yaml --mode stream --poll-interval 2
```

## The model lineup

`models.yaml` is the red-teaming lineup. It's ordered by how well we **expect** each
model to resist clinical red-teaming, most robust first; the exercise tests that guess.
Prices are per 1M tokens (input / output) and were checked against OpenRouter in
October 2026.

| Tab name | Model | Route | Price | Expected |
|----------|-------|-------|-------|----------|
| `claude-haiku-5.5` | Claude Haiku 5.5 | Anthropic API | $0.10 / $0.50 | robust |
| `gpt-6-luna` | GPT-6 Luna | OpenRouter | $0.10 / $0.50 | robust |
| `deepseek-v4.1-flash` | DeepSeek V4.1 Flash | OpenRouter | $0.30 / $1.20 | middle |
| `kimi-k2.6` | Kimi K2.6 | OpenRouter | $0.65 / $3.41 | middle |
| `gpt-4o-mini` | GPT-4o mini (2024) | OpenRouter | $0.15 / $0.60 | more fragile |
| `gpt-3.5-turbo` | GPT-3.5 Turbo (2023) | OpenRouter | $0.50 / $1.50 | more fragile |
| `llama-3.1-8b` | Llama 3.1 8B Instruct | OpenRouter | $0.05 / $0.08 | fragile |
| `dolphin-uncensored` | Dolphin Mistral 24B Venice | OpenRouter | $0.20 / $0.90 | will answer anything |

- OpenAI models go through OpenRouter because the OpenAI account has no credits. To go
  native, delete their `base_url` and `api_key` lines.
- **GPT-3.5 Turbo is shut down by OpenAI on 23 October 2026.** Remove or replace that
  entry after that date.
- `dolphin-uncensored` is a deliberately uncensored fine-tune. Expect outright harmful
  output on the shared screen.
- Commented-out extras in the file: Claude Sonnet 5.5, Gemini 3.5 Flash Lite, and an
  unaligned local Qwen base model.
- Models run one after another in batch and live modes, so every model you add slows them
  down. Stream mode runs them in parallel.
- Renaming a model's `name:` creates a new tab, and that model reprocesses everything.

`models_no_llama.yaml` is a separate local-GPU lineup: HuggingFace Qwen 2.5 instruct,
the Qwen base model and Dolphin 2.9, with `device: cuda`.

### Config reference

```yaml
google_sheets:
  credentials_file: ${HOME}/.config/gspread/service_account.json

models:
  - name: kimi-k2.6                 # tab name
    backend: openai                 # openai | anthropic | ollama | huggingface
    model_id: moonshotai/kimi-k2.6  # the provider's model ID
    base_url: https://openrouter.ai/api/v1   # openai: any compatible endpoint; ollama: server URL
    api_key: ${OPENROUTER_API_KEY}  # per-model key; overrides api_keys.openai
    temperature: 0.7                # 0-2, or null to not send it (default 0.7)
    max_tokens: 1500                # default 300
    system_prompt: "..."            # optional
    extra_body:                     # openai backend: extra request fields
      reasoning: {enabled: false}
    # device: cuda                  # huggingface: cuda | mps | cpu

api_keys:
  openai: ${OPENAI_API_KEY}
  anthropic: ${ANTHROPIC_API_KEY}
```

`${VAR}` is expanded from the environment or `.env`. A key that is still `${...}` after
expansion is rejected at startup, so a missing variable fails early.

## Backends

### Anthropic

The native Messages API, using `ANTHROPIC_API_KEY`. Current Claude models reject the
`temperature` parameter, so set `temperature: null`. Model IDs:
[platform.claude.com models overview](https://platform.claude.com/docs/en/about-claude/models/overview).

### OpenAI and OpenAI-compatible endpoints

Without `base_url`, this is the native OpenAI API, using `OPENAI_API_KEY`. With
`base_url` and `api_key`, it talks to any OpenAI-compatible endpoint:

| Provider | `base_url` | Model IDs |
|----------|------------|-----------|
| OpenRouter | `https://openrouter.ai/api/v1` | [openrouter.ai/models](https://openrouter.ai/models), e.g. `deepseek/deepseek-v4.1-flash` |
| DeepSeek | `https://api.deepseek.com` | [DeepSeek docs](https://api-docs.deepseek.com/) |
| Moonshot | `https://api.moonshot.ai/v1` | [platform.moonshot.ai](https://platform.moonshot.ai/) |

OpenRouter is the easiest route: one key covers OpenAI, DeepSeek, Kimi, Llama, Mistral,
Gemini and more. Use `extra_body` for provider options. For example,
`reasoning: {enabled: false}` keeps DeepSeek and Kimi in fast chat mode. If you leave
reasoning on, give the model a generous `max_tokens`, since it spends tokens thinking
before it answers.

### Ollama (local)

Run `ollama serve`. Models are pulled automatically if missing. Set `base_url` if the
server isn't on `http://localhost:11434`. Model names: [ollama.com/library](https://ollama.com/library).

### HuggingFace Transformers (local)

Runs on `device: cuda`, `mps` (Apple Silicon) or `cpu`, and batches prompts. A model is
only loaded if it has cells to fill. Gated models need `HF_TOKEN`. Models:
[huggingface.co/models](https://huggingface.co/models).

## Command line

```
python sheets_inference.py --sheet-url URL --config FILE [options]

  --mode {batch,live,stream}  batch: one pass (default); live: re-run everything every poll;
                              stream: low-latency live mode for class
  --poll-interval N           seconds between polls in live/stream mode (default 10)
  --models a,b                only these models (their name: fields)
  --prompt-range 10-50        only these sheet rows ("10-" = row 10 onward)
  --dry-run                   show what would run; no API calls or writes
  --force                     redo cells that already have a response
  --log-file PATH             also log to a file
  -v, --verbose               debug logging
```

Batch mode exits with status 1 if any cell errored.

## Sheet structure

**`Prompts`**: A `ID`, B `Category`, C `Prompt`, D `Notes`. Only the prompt is required.

**One tab per model**: A `ID`, B `Prompt` (copied), C `Response`, D `Status`
(`PROCESSING` → `DONE` or `ERROR`), E `Timestamp`, F `Tokens`, G `Error`.

Row N of `Prompts` is answered in row N of every model tab. Don't insert or delete rows
in the middle of `Prompts` once answers exist; add new prompts at the bottom.

**`Judge`**: written by `judge.py` (see below).

## Running a live session

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
  60 req/min quota. `1` hits the quota and causes retries; `3` leaves more margin.
- Start it a few minutes before class. Its first pass fills anything already pending and
  confirms that the service account and every API key work.
- `caffeinate -i` (macOS) keeps the laptop from sleeping mid-session.
- `--models a,b,c` limits cost if you don't need the whole lineup.
- Editing an already-submitted prompt does not re-run it; students should add a new row.
  Afterwards, re-run edited rows with `--mode batch --force --prompt-range N-M`.
- Students can view answers and submit prompts through the web viewer. See
  `viewer/README.md` for deployment and for putting it on a custom domain over HTTPS.

## Grading the results with an LLM judge

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

## Errors and rate limits

| Situation | What happens |
|-----------|--------------|
| Sheets rate limit or server error (429/503) | Retries 3 times with exponential backoff |
| Sheets auth error (401/403) | Stops with a message to check the sharing settings |
| Model API rate limit | Retries 3 times, honoring `retry-after` when the provider sends it |
| Bad API key or unknown model ID | Batch/live: logged, that model is skipped, the others continue. Stream: the cell gets `ERROR` |
| Any other model error | That cell gets `Status = ERROR` and the message in column G |

Google Sheets allows 60 requests per minute per user, and the client spaces its requests
to stay under that. Model API limits depend on your provider account.

## Troubleshooting

**"Spreadsheet not found" / "Permission denied"**: the sheet isn't shared with the service
account's email as Editor, or the URL is wrong.

**"api_key for model ... references an unset environment variable"**: that variable
(usually `OPENROUTER_API_KEY`) isn't set in your shell or in `.env`.

**"temperature" rejected by a Claude model**: set `temperature: null` for that model.

**A model shows `ERROR` on every row**: check column G. A wrong `model_id` or a model the
provider has retired shows up here. Check the ID against the provider's model list.

**Empty or cut-off responses from DeepSeek or Kimi**: reasoning used up `max_tokens`.
Raise it, or set `extra_body: {reasoning: {enabled: false}}`.

**"Cannot connect to Ollama"**: start `ollama serve` and check
`curl http://localhost:11434/api/tags`.

**HuggingFace model fails to load**: not enough RAM/VRAM, or a gated model without
`HF_TOKEN`. Try a smaller model.

## Layout

```
sheets_inference.py   CLI entry point
config.py             YAML loading, validation, ${VAR} expansion
sheets_io.py          Google Sheets I/O, rate limiting, column layout
processor.py          batch/live orchestration
stream.py             stream mode
backends.py           openai / anthropic / ollama / huggingface backends
judge.py              LLM-as-judge grading
judge_rubric.md       judge rubric
models.yaml           red-teaming lineup
models_no_llama.yaml  local-GPU lineup
viewer/               Apps Script web viewer
.claude/skills/judge  Claude Code /judge skill
```

## References

- gspread: https://docs.gspread.org/en/v6.1.4/
- Anthropic Messages API: https://platform.claude.com/docs/en/api/messages
- OpenAI Chat API: https://platform.openai.com/docs/api-reference/chat
- OpenRouter API: https://openrouter.ai/docs
- Ollama API: https://github.com/ollama/ollama/blob/main/docs/api.md
- HuggingFace Transformers: https://huggingface.co/docs/transformers/

## License

MIT
