# Model Toggle Guide

The course-aligned default is still Gemini 2.5 Flash through Vertex AI:

```bat
python .\src\main.py --provider vertex --quality final --model-preset lecture_flash --strict-provider
```

The project also exposes a wider model toggle so the same agentic pipeline can
be run with Gemini, OpenAI GPT, or Anthropic Claude.

## Preset Toggles

Presets live in `model_policy.json`.

| Preset | Provider | Use case |
|---|---|---|
| `lecture_flash` | `vertex` or `gemini` | Course default: Gemini 2.5 Flash for generation, Flash-Lite for judging |
| `cheap` | `vertex` or `gemini` | Lowest-cost iteration |
| `balanced` | `vertex` or `gemini` | Same as lecture-style balanced Flash setup |
| `pro` | `vertex` or `gemini` | Experimental Gemini Pro comparison |
| `gpt` | `openai` | OpenAI GPT high-quality comparison |
| `gpt_low_cost` | `openai` | Lower-cost OpenAI comparison |
| `claude_opus` | `anthropic` | Claude Opus high-quality comparison |
| `claude_sonnet` | `anthropic` | Lower-cost Claude Sonnet comparison |

## Examples

Lecture/default path:

```bat
python .\src\main.py --provider vertex --quality final --model-preset lecture_flash --strict-provider
```

OpenAI GPT path:

```bat
python -m pip install openai
set OPENAI_API_KEY=your-key
python .\src\main.py --provider openai --quality final --model-preset gpt --strict-provider
```

Anthropic Claude Opus path:

```bat
python -m pip install anthropic
set ANTHROPIC_API_KEY=your-key
python .\src\main.py --provider anthropic --quality final --model-preset claude_opus --strict-provider
```

## Per-Role Overrides

You can override one role without creating a new preset:

```bat
python .\src\main.py --provider openai --quality final --model-preset gpt ^
  --writer-model openai:gpt-5.2 ^
  --judge-model openai:gpt-5-mini ^
  --strict-provider
```

Roles:

- `--planner-model`
- `--writer-model`
- `--answer-model`
- `--judge-model`
- `--final-rewriter-model`

Use provider prefixes in mixed config files:

```text
openai:gpt-5.2
anthropic:claude-opus-4-1-20250805
gemini-2.5-flash
```

The command-line `--provider` still chooses which client is used for the run.
Do not run an Anthropic model with `--provider openai`, or an OpenAI model with
`--provider anthropic`.

## Notes

OpenAI model names are based on the OpenAI API models page. Anthropic model names
are based on Anthropic's models overview. Model availability can change by
account, region, and billing status, so update `model_policy.json` if a vendor
renames or deprecates a model.
