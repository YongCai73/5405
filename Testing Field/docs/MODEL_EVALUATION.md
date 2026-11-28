# Run a model API evaluation

Use this guide to validate a named DeepSeek, OpenAI, or Anthropic API
configuration against the same synthetic meeting fixture. It establishes a
reproducible compatibility baseline; it does not select a universal best model
or certify answer quality on real meetings.

## Prepare one local configuration

Copy `examples/model-evaluation-config.example.json` to a location outside the
repository, for example `C:\MeetingCopilot-eval\config.json`. Keep API keys out
of this file, Git, and shared screenshots.

The example is valid JSON and starts as a DeepSeek configuration. Standard JSON
does not support comments, so use this table when changing it for the next run.

| Target | `provider` | `model` | `base_url` | `thinking` |
|---|---|---|---|---|
| DeepSeek | `openai` | `deepseek-flash` | `https://api.deepseek.com` | `disabled` for the comparable baseline; omit it to use the provider default |
| OpenAI | `openai` | An API model ID enabled for the project | Remove the field | Remove the field |
| Anthropic | `anthropic` | An active Claude API model ID enabled for the account | Remove the field | Remove the field |

`api_key_env` stays `MC_MODEL_API_KEY` for every run. Set that environment
variable in the current terminal to the key for the configuration being tested.
The key is not printed or saved in the configuration.

Do not set `input_cost_per_million` or `output_cost_per_million` until the
current pricing and any caching/region rules have been checked for that exact
provider/model. Without both fields, the comparison correctly reports estimated
cost as unavailable rather than inventing a value.

## Run the baseline

In a terminal whose Python environment has Meeting Copilot installed, set the
key and run one evaluation. Replace the paths with your local locations.

```powershell
$env:MC_MODEL_API_KEY = 'paste this run only in your terminal'
python -m meeting_copilot --config 'C:\MeetingCopilot-eval\config.json' evaluate --api --strict --output 'C:\MeetingCopilot-eval\deepseek-eval.json'
```

Before the OpenAI or Anthropic runs, edit only the fields in the table, replace
the terminal's `MC_MODEL_API_KEY`, and change the output filename. The evaluator
stores non-secret provider, model, endpoint host, evaluator settings, fixture
hash, source revision, and runtime information in each result, so reusing one
configuration file does not make completed reports ambiguous.

The evaluator uses the same frozen 22-question fixture for every provider. To
keep the baseline comparable it forces tool rounds, re-search, general answers,
and wide transcript context off. It records expected-term correctness,
exact-quote membership, abstentions, calls, latency, returned usage, and
estimated cost when both usage and configured rates are available.

## Compare completed reports

After two or more successful runs, create a local Markdown comparison:

```powershell
python -m meeting_copilot compare-evaluations `
  'C:\MeetingCopilot-eval\deepseek-eval.json' `
  'C:\MeetingCopilot-eval\openai-eval.json' `
  'C:\MeetingCopilot-eval\anthropic-eval.json' `
  --output 'C:\MeetingCopilot-eval\model-comparison.md'
```

The command refuses reports that use different fixtures or non-comparable tool
round settings. Treat the report as a compatibility and regression record, not
a semantic-quality leaderboard. Run endpoint smoke checks separately for
streaming, a tool round, Chinese questions over English evidence, no-evidence
handling, follow-up conversation, and desktop Settings persistence before
describing a configuration as verified.
