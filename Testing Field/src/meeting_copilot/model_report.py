"""Compare compatible, non-secret API evaluation reports."""
import json
from pathlib import Path


VARIANTS = ('skills', 'direct', 'auto')


def _required(report, key):
    if key not in report:
        raise ValueError(f'evaluation report is missing {key}')
    return report[key]


def _number(value, digits=0):
    if value is None:
        return '—'
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return '—'
    return f'{value:.{digits}f}'


def _cost(value):
    return '—' if value is None else '$' + _number(value, 4)


def _load(path):
    path = Path(path)
    try:
        report = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'could not read evaluation report {path.name}') from exc
    if not isinstance(report, dict) or not str(report.get('mode', '')).startswith('API evaluation'):
        raise ValueError(f'{path.name} is not an API evaluation report')
    if report.get('tool_rounds') != 0:
        raise ValueError(f'{path.name} did not use the comparable tool_rounds=0 setting')
    comparison = _required(report, 'skill_comparison')
    variants = _required(comparison, 'variants')
    if not all(name in variants and isinstance(variants[name], dict) for name in VARIANTS):
        raise ValueError(f'{path.name} is missing a comparable skill variant')
    return path, report


def comparison_markdown(paths):
    """Return a concise Markdown comparison for compatible API evaluation reports."""
    loaded = [_load(path) for path in paths]
    if len(loaded) < 2:
        raise ValueError('compare at least two API evaluation reports')
    fixtures = {_required(report, 'fixture_sha256') for _, report in loaded}
    if len(fixtures) != 1:
        raise ValueError('evaluation reports use different fixtures and cannot be compared')

    lines = [
        '# Model validation comparison',
        '',
        'All reports use the same frozen fixture and the evaluator\'s grounded-QA settings: tool rounds, re-search, general answers, and wide transcript context are disabled.',
        '',
        '## Auto routing summary',
        '',
        '| Configuration | Evaluated | Correct | Citation-supported | Abstained | Provider calls | p50 / p95 answer latency | Input / output tokens | Estimated cost |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for path, report in loaded:
        row = report['skill_comparison']['variants']['auto']
        lines.append(
            f"| {report['provider']} / {report['model']} | {report.get('created_at_utc', '—')} | "
            f"{_number(row.get('correct'))}/{_number(row.get('total'))} | "
            f"{_number(row.get('citation_supported'))}/{_number(row.get('total'))} | "
            f"{_number(row.get('abstained'))} | {_number(row.get('provider_calls'))} | "
            f"{_number(row.get('p50_ms'))} / {_number(row.get('p95_ms'))} ms | "
            f"{_number(row.get('input_tokens'))} / {_number(row.get('output_tokens'))} | "
            f"{_cost(row.get('estimated_cost_usd'))} |"
        )

    lines += [
        '',
        '## Skills direct and auto breakdown',
        '',
        '| Configuration | Variant | Correct | Citation-supported | Calls | p50 / p95 answer latency | Input / output tokens | Estimated cost |',
        '|---|---|---:|---:|---:|---:|---:|---:|',
    ]
    for _, report in loaded:
        name = f"{report['provider']} / {report['model']}"
        for variant in VARIANTS:
            row = report['skill_comparison']['variants'][variant]
            lines.append(
                f"| {name} | {variant} | {_number(row.get('correct'))}/{_number(row.get('total'))} | "
                f"{_number(row.get('citation_supported'))}/{_number(row.get('total'))} | "
                f"{_number(row.get('provider_calls'))} | {_number(row.get('p50_ms'))} / {_number(row.get('p95_ms'))} ms | "
                f"{_number(row.get('input_tokens'))} / {_number(row.get('output_tokens'))} | "
                f"{_cost(row.get('estimated_cost_usd'))} |"
            )

    lines += [
        '',
        '## Comparability and limits',
        '',
        f"- Fixture SHA-256: `{next(iter(fixtures))}`.",
        '- Each row is one run. Network and provider variance are included in latency; do not treat a one- or two-case difference as a quality ranking.',
        '- Correctness is expected-term matching plus the expected abstention. Citation support checks exact quotation membership, not semantic entailment.',
        '- `—` means the provider did not return usable usage data or the evaluation report did not have a cost estimate.',
        '- Run the endpoint smoke checks in `docs/MODEL_VALIDATION.md` separately before marking a configuration verified.',
        '',
        '## Source reports',
        '',
    ]
    lines.extend(f'- `{path.name}`' for path, _ in loaded)
    return '\n'.join(lines) + '\n'


def compare_evaluations(paths, output):
    """Write a Markdown comparison and return safe summary metadata."""
    paths = [Path(path) for path in paths]
    markdown = comparison_markdown(paths)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown, encoding='utf-8')
    return {'comparison': str(output), 'reports': [path.name for path in paths], 'format': 'markdown'}
