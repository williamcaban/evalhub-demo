#!/usr/bin/env python3
"""Render a comparison-oriented HTML report from one or more Garak JSONL runs.

Only the Python standard library is required. Use Garak's .report.jsonl files
for scores. Hit logs are optional and are included only when explicitly enabled.
Human-authored context (architecture, known issues, and recommendations) belongs
in the JSON metadata file; it is not inferred from scanner output.

Pipeline example::

    python eval-hub/scripts/garak_report_generator.py --scan Baseline=artifacts/base.report.jsonl --scan Guardrailed=artifacts/guarded.report.jsonl --hitlog Guardrailed=artifacts/guarded.hitlog.jsonl --metadata report-metadata.json --include-examples --output report.html

The metadata JSON accepts title, subtitle, benchmark, platform, taxonomy,
baseline, architecture, methodology (a list), category_labels (an object),
scans (an object keyed by scan label; each may include description, deployment,
and architecture layers), known_issues, defense_analysis, recommendations,
generated (an ISO date), and max_examples. See the notebook for a complete
editable metadata example.
"""

import argparse
import html
import json
import sys
import textwrap
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def _read_jsonl(path):
    records = []
    with Path(path).open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}")
            if isinstance(record, dict):
                records.append(record)
    return records


def _number(value, field, path):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{path}: Garak eval field {field!r} must be an integer")
    if number < 0:
        raise ValueError(f"{path}: Garak eval field {field!r} cannot be negative")
    return number


def _without_prefix(value, prefix):
    value = str(value)
    return value.removeprefix(prefix)


def load_garak_report(path, taxonomy=None):
    """Read Garak report JSONL and aggregate eval rows by probe/detector."""
    records = _read_jsonl(path)
    setup = {}
    init = {}
    plugin_cache = {}
    pairs = {}
    unscored_rows = 0

    for record in records:
        entry_type = record.get("entry_type")
        if entry_type == "start_run setup":
            setup = record
        elif entry_type == "init":
            init = record
        elif entry_type == "plugin_cache":
            cache = record.get("plugin_cache", {})
            if isinstance(cache, dict):
                for category, entries in cache.items():
                    if isinstance(entries, dict):
                        plugin_cache.setdefault(category, {}).update(entries)
        elif entry_type == "eval":
            probe = _without_prefix(record.get("probe", ""), "probes.")
            detector = _without_prefix(record.get("detector", ""), "detector.")
            if not probe or not detector:
                raise ValueError(f"{path}: eval row is missing probe or detector")
            total_value = record.get("total_evaluated", record.get("total"))
            passed_value = record.get("passed")
            if total_value is None or passed_value is None:
                raise ValueError(
                    f"{path}: eval row for {probe} / {detector} is missing passed or total_evaluated"
                )
            total = _number(total_value, "total_evaluated", path)
            passed = _number(passed_value, "passed", path)
            if passed > total:
                raise ValueError(f"{path}: passed count exceeds total_evaluated")
            if total == 0:
                unscored_rows += 1
                continue
            key = (probe, detector)
            pair = pairs.setdefault(key, {"passed": 0, "total": 0})
            pair["passed"] += passed
            pair["total"] += total

    if not pairs:
        raise ValueError(f"{path}: no scored Garak eval rows found")

    run_taxonomy = taxonomy or setup.get("reporting.taxonomy")
    return {
        "path": str(path),
        "filename": Path(path).name,
        "setup": setup,
        "init": init,
        "plugin_cache": plugin_cache,
        "pairs": pairs,
        "taxonomy": run_taxonomy,
        "unscored_rows": unscored_rows,
    }


def load_hitlog(path):
    """Return hit-log rows. Call only when including evidence was intended."""
    return [row for row in _read_jsonl(path) if row.get("entry_type") != "digest"]


def _e(value):
    return html.escape("" if value is None else str(value), quote=True)


def _as_percent(numerator, denominator):
    if not denominator:
        return "N/A"
    return f"{100.0 * numerator / denominator:.2f}%"


def _totals(scan, keys=None):
    selected = scan["pairs"].keys() if keys is None else keys
    total = passed = 0
    for key in selected:
        pair = scan["pairs"].get(key)
        if pair:
            total += pair["total"]
            passed += pair["passed"]
    return {"total": total, "passed": passed, "hits": total - passed}


def _asr(totals):
    return (totals["hits"] / totals["total"]) if totals["total"] else None


def _comparison_label(labels, baseline_label):
    candidates = [label for label in labels if label != baseline_label]
    return candidates[-1] if candidates else baseline_label


def _probe_tags(scan, probe):
    cache = scan.get("plugin_cache", {}).get("probes", {})
    candidates = ("probes." + probe, probe)
    for candidate in candidates:
        info = cache.get(candidate)
        if isinstance(info, dict) and isinstance(info.get("tags"), list):
            return info["tags"]
    return []


def _category(scan, probe, taxonomy, category_labels):
    if not taxonomy:
        return probe.split(".", 1)[0]
    prefix = str(taxonomy).casefold() + ":"
    for tag in _probe_tags(scan, probe):
        tag = str(tag)
        if tag.casefold().startswith(prefix):
            category = tag.split(":", 1)[1]
            category = category.split(":", 1)[0]
            return category_labels.get(category, category)
    return "Unmapped"


def _section(section_id, title, body):
    return f'<section id="{_e(section_id)}"><h2>{_e(title)}</h2>{body}</section>'


def _render_summary(scans, labels, baseline_label, metadata):
    cards = []
    for index, label in enumerate(labels):
        scan = scans[label]
        totals = _totals(scan)
        card_color = "red" if index == 0 else ("green" if index == 1 else "yellow")
        cards.append(
            '<div class="kpi {}"><div class="value">{}</div><div class="label">{}<br>weighted detector-evaluation ASR</div></div>'.format(
                card_color,
                _asr(totals) if _asr(totals) is not None else "N/A",
                _e(label),
            )
        )
    notes = []
    baseline = scans[baseline_label]
    for label in labels:
        if label == baseline_label:
            continue
        compared = scans[label]
        matched = sorted(set(baseline["pairs"]) & set(compared["pairs"]))
        base_totals = _totals(baseline, matched)
        compare_totals = _totals(compared, matched)
        if not matched:
            notes.append(
                f"{_e(label)} has no matched probe/detector pairs with {_e(baseline_label)}."
            )
            continue
        base_rate = _asr(base_totals)
        compare_rate = _asr(compare_totals)
        abs_reduction = (
            (
                base_totals["hits"] / base_totals["total"]
                - compare_totals["hits"] / compare_totals["total"]
            )
            if base_totals["total"] and compare_totals["total"]
            else None
        )
        relative = None
        if base_totals["hits"] and base_totals["total"] and compare_totals["total"]:
            relative = 1.0 - (
                (compare_totals["hits"] / compare_totals["total"])
                / (base_totals["hits"] / base_totals["total"])
            )
        notes.append(
            "Matched comparison, {} vs {}: {} vs {} across {} identical probe/detector pairs ({} evaluations vs {}). Absolute ASR reduction: {} percentage points; relative reduction: {}.".format(
                _e(label),
                _e(baseline_label),
                compare_rate,
                base_rate,
                len(matched),
                compare_totals["total"],
                base_totals["total"],
                f"{100.0 * abs_reduction:+.2f}" if abs_reduction is not None else "N/A",
                f"{100.0 * relative:.1f}%" if relative is not None else "N/A",
            )
        )

    comparison_label = _comparison_label(labels, baseline_label)
    primary = scans[comparison_label]
    residual = sorted(
        (
            (key, value)
            for key, value in primary["pairs"].items()
            if value["total"] and value["passed"] < value["total"]
        ),
        key=lambda item: (
            (item[1]["total"] - item[1]["passed"]) / item[1]["total"],
            item[1]["total"],
        ),
        reverse=True,
    )[:3]
    if residual:
        rows = "".join(
            "<li><code>{}</code> / <code>{}</code>: {} hits in {} evaluations ({})</li>".format(
                _e(key[0]),
                _e(key[1]),
                pair["total"] - pair["passed"],
                pair["total"],
                _as_percent(pair["total"] - pair["passed"], pair["total"]),
            )
            for key, pair in residual
        )
        notes.append(
            f"Highest observed residual probe/detector rates in {_e(comparison_label)}:<ul>{rows}</ul>"
        )
    if any(scans[label]["unscored_rows"] for label in labels):
        notes.append(
            "At least one zero-evaluation row was excluded from rate calculations; see scan statistics."
        )
    if not notes:
        notes.append(
            "No detector-flagged hits were present in the scored rows. This does not establish that the system is safe outside the tested scope."
        )
    return (
        '<div class="kpi-row">{}</div>'.format("".join(cards))
        + '<div class="callout green"><strong>Observed findings</strong><ul>{}</ul></div>'.format(
            "".join(f"<li>{note}</li>" for note in notes)
        )
        + '<p class="muted">Rates are descriptive summaries of detector-scored generations. Garak cautions against treating probe scores as a normalized cross-probe safety scale. The matched comparison uses only probe/detector pairs present in both runs.</p>'
    )


def _render_scan_stats(scans, labels, metadata):
    rows = []
    scan_details = metadata.get("scans", {})
    for label in labels:
        scan = scans[label]
        setup, init = scan["setup"], scan["init"]
        totals = _totals(scan)
        detail = scan_details.get(label, {}) if isinstance(scan_details, dict) else {}
        target = setup.get(
            "plugins.target_name", setup.get("plugins.model_name", "Not recorded")
        )
        target_type = setup.get(
            "plugins.target_type", setup.get("plugins.model_type", "")
        )
        version = init.get(
            "garak_version", setup.get("_config.version", "Not recorded")
        )
        start = init.get("start_time", "Not recorded")
        rows.append(
            '<tr><th scope="row">{}</th><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td><code>{}</code></td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>'.format(
                _e(label),
                _e(target),
                _e(target_type),
                _e(version),
                _e(start),
                _e(init.get("run", "Not recorded")),
                _e(scan["filename"]),
                len({key[0] for key in scan["pairs"]}),
                len(scan["pairs"]),
                totals["total"],
                totals["hits"],
                _e(detail.get("errors", "Not supplied")),
            )
        )
    table = '<div class="table-wrap"><table><thead><tr><th>Configuration</th><th>Target</th><th>Target type</th><th>Garak version</th><th>Started</th><th>Garak run ID</th><th>Report file</th><th>Probes</th><th>Probe × detector pairs</th><th>Evaluations</th><th>Hits</th><th>Errors</th></tr></thead><tbody>{}</tbody></table></div>'.format(
        "".join(rows)
    )
    caveats = []
    for label in labels:
        count = scans[label]["unscored_rows"]
        if count:
            caveats.append(f"{count} zero-evaluation row(s) excluded for {_e(label)}.")
    caveat_text = (
        " ".join(caveats)
        if caveats
        else "No zero-evaluation rows were found in the parsed eval records."
    )
    methodology = metadata.get("methodology", [])
    if isinstance(methodology, str):
        methodology = [methodology]
    items = "".join(f"<li>{_e(item)}</li>" for item in methodology)
    if not items:
        items = "<li>ASR = (total_evaluated − passed) / total_evaluated for each detector evaluation.</li><li>Run-wide and taxonomy summaries are weighted by evaluated generations; they are descriptive roll-ups, not a normalized score across probes.</li><li>Comparisons are strongest when model, prompts, probe/detector versions, and run settings match.</li>"
    return (
        table
        + f'<div class="callout"><strong>Method and coverage</strong><ul>{items}</ul><p>{_e(caveat_text)}</p><p>Matched probe/detector pairs do not prove that prompts, generation settings, detector versions, or deployment conditions were identical; provide those controls in methodology metadata.</p></div>'
    )


def _architecture_svg(label, layers):
    if not isinstance(layers, list) or not layers:
        return ""
    box_width, box_height, gap = 184, 64, 38
    width = len(layers) * box_width + (len(layers) - 1) * gap
    pieces = [
        f'<svg class="flow" role="img" aria-label="Request flow for {_e(label)}" viewBox="0 0 {width} 100" width="100%" height="100">',
        f"<title>Request flow for {_e(label)}</title>",
    ]
    for index, layer in enumerate(layers):
        x = index * (box_width + gap)
        fill = "#151515" if index in (0, len(layers) - 1) else "#f0f0f0"
        text_fill = "#ffffff" if index in (0, len(layers) - 1) else "#333333"
        pieces.append(
            f'<rect x="{x}" y="18" width="{box_width}" height="{box_height}" rx="7" fill="{fill}" stroke="#cccccc"/>'
        )
        lines = textwrap.wrap(str(layer), width=22)[:2] or [str(layer)]
        start_y = 46 if len(lines) == 1 else 38
        tspans = "".join(
            f'<tspan x="{x + box_width // 2}" y="{start_y + line_no * 18}">{_e(line)}</tspan>'
            for line_no, line in enumerate(lines)
        )
        pieces.append(
            f'<text text-anchor="middle" font-size="13" font-weight="600" fill="{text_fill}">{tspans}</text>'
        )
        if index < len(layers) - 1:
            start_x = x + box_width + 4
            end_x = x + box_width + gap - 6
            pieces.append(
                f'<line x1="{start_x}" y1="50" x2="{end_x - 6}" y2="50" stroke="#666" stroke-width="2"/>'
            )
            pieces.append(
                f'<path d="M{end_x - 8} 44 L{end_x} 50 L{end_x - 8} 56 Z" fill="#666"/>'
            )
    pieces.append("</svg>")
    return "".join(pieces)


def _render_architecture(scans, labels, metadata):
    details = metadata.get("scans", {})
    rows = []
    for label in labels:
        detail = details.get(label, {}) if isinstance(details, dict) else {}
        description = detail.get(
            "description", "Configuration description not supplied."
        )
        deployment = detail.get("deployment", "")
        rows.append(
            '<div class="config"><h3>{}</h3>{}<p>{}</p>{}</div>'.format(
                _e(label),
                _architecture_svg(label, detail.get("layers", [])),
                _e(description),
                f'<p class="muted">{_e(deployment)}</p>' if deployment else "",
            )
        )
    architecture = metadata.get(
        "architecture", "Architecture details were not supplied in report metadata."
    )
    return '<div class="architecture">{}</div>{}'.format(
        _e(architecture), "".join(rows)
    )


def _render_categories(scans, labels, taxonomy, category_labels):
    aggregate = defaultdict(lambda: defaultdict(lambda: {"passed": 0, "total": 0}))
    for label in labels:
        scan = scans[label]
        for (probe, _detector), pair in scan["pairs"].items():
            category = _category(scan, probe, taxonomy, category_labels)
            aggregate[category][label]["passed"] += pair["passed"]
            aggregate[category][label]["total"] += pair["total"]
    category_rows = []
    for category in sorted(aggregate):
        cells = []
        for label in labels:
            item = aggregate[category][label]
            hits = item["total"] - item["passed"]
            cells.append(
                '<td>{} <span class="muted">({}/{})</span></td>'.format(
                    _as_percent(hits, item["total"]), hits, item["total"]
                )
            )
        category_rows.append(
            '<tr><th scope="row">{}</th>{}</tr>'.format(_e(category), "".join(cells))
        )
    headers = "".join(
        f"<th>{_e(label)} ASR (hits/evaluations)</th>" for label in labels
    )
    if not taxonomy:
        note = "No taxonomy was selected in the report metadata or Garak run setup; rows are grouped by probe module."
    else:
        note = f"Taxonomy: {taxonomy}. Probe tags are read from Garak plugin metadata; untagged probes are shown as Unmapped."
    if not category_rows:
        return '<p class="muted">No category summaries are available.</p>'
    return '<p>{}</p><div class="table-wrap"><table><thead><tr><th>Category</th>{}</tr></thead><tbody>{}</tbody></table></div>'.format(
        _e(note), headers, "".join(category_rows)
    )


def _render_probe_changes(scans, labels, baseline_label):
    if len(labels) < 2:
        return "<p>Provide a second configuration to show probe-level changes.</p>"
    comparison_label = _comparison_label(labels, baseline_label)
    if comparison_label == baseline_label:
        return "<p>Provide a second configuration to show probe-level changes.</p>"
    baseline = scans[baseline_label]
    comparison = scans[comparison_label]
    matched = sorted(set(baseline["pairs"]) & set(comparison["pairs"]))
    if not matched:
        return "<p>No probe/detector pairs are shared by the selected runs.</p>"

    changes = []
    for key in matched:
        base_pair = baseline["pairs"][key]
        compare_pair = comparison["pairs"][key]
        base_rate = (base_pair["total"] - base_pair["passed"]) / base_pair["total"]
        compare_rate = (compare_pair["total"] - compare_pair["passed"]) / compare_pair[
            "total"
        ]
        changes.append((key, base_pair, compare_pair, base_rate - compare_rate))

    def rows_for(items):
        rows = []
        for key, base_pair, compare_pair, reduction in items:
            base_hits = base_pair["total"] - base_pair["passed"]
            compare_hits = compare_pair["total"] - compare_pair["passed"]
            rows.append(
                "<tr><td><code>{}</code></td><td><code>{}</code></td><td>{} ({}/{})</td><td>{} ({}/{})</td><td>{:+.2f} pp</td></tr>".format(
                    _e(key[0]),
                    _e(key[1]),
                    _as_percent(base_hits, base_pair["total"]),
                    base_hits,
                    base_pair["total"],
                    _as_percent(compare_hits, compare_pair["total"]),
                    compare_hits,
                    compare_pair["total"],
                    100.0 * reduction,
                )
            )
        return "".join(rows) or '<tr><td colspan="5">No rows</td></tr>'

    headers = f"<tr><th>Probe</th><th>Detector</th><th>{_e(baseline_label)} ASR (hits/evaluations)</th><th>{_e(comparison_label)} ASR (hits/evaluations)</th><th>ASR reduction</th></tr>"
    improved = sorted(
        (item for item in changes if item[3] > 0),
        key=lambda item: item[3],
        reverse=True,
    )[:10]
    increased = sorted(
        (item for item in changes if item[3] < 0), key=lambda item: item[3]
    )[:10]
    return (
        f"<p>Per-pair ASR changes for {_e(comparison_label)} compared with {_e(baseline_label)}. Positive values indicate a lower detector-flagged rate in the comparison run.</p>"
        + f'<h3>Largest ASR reductions</h3><div class="table-wrap"><table><thead>{headers}</thead><tbody>{rows_for(improved)}</tbody></table></div>'
        + f'<h3>Largest ASR increases</h3><div class="table-wrap"><table><thead>{headers}</thead><tbody>{rows_for(increased)}</tbody></table></div>'
    )


def _render_probe_table(scans, labels):
    all_keys = sorted(set().union(*(set(scans[label]["pairs"]) for label in labels)))
    headers = "".join(
        f"<th>{_e(label)} ASR (hits/evaluations)</th>" for label in labels
    )
    rows = []
    for probe, detector in all_keys:
        cells = []
        for label in labels:
            pair = scans[label]["pairs"].get((probe, detector))
            if pair:
                hits = pair["total"] - pair["passed"]
                cells.append(
                    '<td>{} <span class="muted">({}/{})</span></td>'.format(
                        _as_percent(hits, pair["total"]), hits, pair["total"]
                    )
                )
            else:
                cells.append('<td class="na">Not evaluated</td>')
        rows.append(
            "<tr><td><code>{}</code></td><td><code>{}</code></td>{}</tr>".format(
                _e(probe), _e(detector), "".join(cells)
            )
        )
    return '<div class="table-wrap"><table><thead><tr><th>Probe</th><th>Detector</th>{}</tr></thead><tbody>{}</tbody></table></div>'.format(
        headers, "".join(rows)
    )


def _render_examples(hitlogs, labels, limit):
    examples = []
    for label in labels:
        rows = hitlogs.get(label, [])[:limit]
        for index, row in enumerate(rows, 1):
            prompt = row.get("prompt", "Not recorded")
            output = row.get("output", row.get("response", "Not recorded"))
            trigger = row.get("trigger", "")
            examples.append(
                '<details><summary>{}: {} — hit {}</summary><div class="example"><p><strong>Detector:</strong> {} &nbsp; <strong>Score:</strong> {}</p><p><strong>Prompt</strong></p><pre>{}</pre><p><strong>Model output</strong></p><pre>{}</pre>{}</div></details>'.format(
                    _e(label),
                    _e(row.get("probe", "Probe not recorded")),
                    index,
                    _e(row.get("detector", "Not recorded")),
                    _e(row.get("score", "Not recorded")),
                    _e(prompt),
                    _e(output),
                    f"<p><strong>Detector trigger</strong></p><pre>{_e(trigger)}</pre>"
                    if trigger
                    else "",
                )
            )
    if not examples:
        return "<p>No hit-log examples were included. Provide a hit log and enable example inclusion to add expandable prompt/response evidence.</p>"
    return '<div class="callout yellow">Hit-log content may contain sensitive or customer data. Review access and redact content before distribution.</div>{}'.format(
        "".join(examples)
    )


def _render_interpretation(metadata):
    issues = metadata.get("known_issues", [])
    if isinstance(issues, str):
        issues = [issues]
    if not issues:
        issues = [
            "No known issues or detector-artifact annotations were supplied. Review the top hits before treating them as confirmed vulnerabilities."
        ]
    rendered = []
    for issue in issues:
        if isinstance(issue, dict):
            title = issue.get("title", "Known issue")
            body_fields = [
                ("Impact", issue.get("impact")),
                ("Interpretation", issue.get("interpretation")),
                ("Status / workaround", issue.get("status")),
            ]
            body = "".join(
                f"<p><strong>{_e(name)}:</strong> {_e(value)}</p>"
                for name, value in body_fields
                if value
            )
            rendered.append(
                f'<article class="issue"><h3>{_e(title)}</h3>{body}</article>'
            )
        else:
            rendered.append(f'<article class="issue"><p>{_e(issue)}</p></article>')
    return "".join(rendered)


def _render_defense_analysis(metadata):
    analysis = metadata.get("defense_analysis", [])
    if isinstance(analysis, str):
        analysis = [analysis]
    if not analysis:
        return "<p>No layer-specific effectiveness analysis was supplied. Add human-reviewed observations that link each defense layer to the relevant probe and hit evidence.</p>"
    rendered = []
    for item in analysis:
        if isinstance(item, dict):
            title = item.get("layer", item.get("title", "Defense layer"))
            rows = "".join(
                f"<p><strong>{_e(name)}:</strong> {_e(item.get(key))}</p>"
                for name, key in [
                    ("Observed effect", "observed_effect"),
                    ("Evidence", "evidence"),
                    ("Limitations", "limitations"),
                ]
                if item.get(key)
            )
            rendered.append(
                f'<article class="issue"><h3>{_e(title)}</h3>{rows}</article>'
            )
        else:
            rendered.append(f'<article class="issue"><p>{_e(item)}</p></article>')
    return "".join(rendered)


def _render_recommendations(scans, labels, metadata):
    recommendations = metadata.get("recommendations", [])
    if not recommendations:
        scan = scans[_comparison_label(labels, metadata.get("baseline", labels[0]))]
        ranked = sorted(
            scan["pairs"].items(),
            key=lambda item: (
                (item[1]["total"] - item[1]["passed"]) / item[1]["total"],
                item[1]["total"],
            ),
            reverse=True,
        )
        recommendations = []
        for (probe, detector), pair in ranked:
            hits = pair["total"] - pair["passed"]
            if not hits:
                continue
            recommendations.append(
                {
                    "priority": "Triage",
                    "recommendation": "Review the hit evidence and validate detector behavior before classifying this as a confirmed weakness.",
                    "addresses": "{} / {} ({} hits / {} evaluations)".format(
                        probe, detector, hits, pair["total"]
                    ),
                    "expected_impact": "Human review required; no impact estimate inferred from scanner output.",
                }
            )
            if len(recommendations) == 5:
                break
    if not recommendations:
        return "<p>No detector hits were recorded in the scored rows. Preserve the tested scope and limitations when communicating this result.</p>"
    rows = []
    for item in recommendations:
        if isinstance(item, dict):
            rows.append(
                "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                    _e(item.get("priority", "Not ranked")),
                    _e(item.get("recommendation", "")),
                    _e(item.get("addresses", "")),
                    _e(item.get("expected_impact", item.get("rationale", ""))),
                )
            )
        else:
            rows.append(
                f"<tr><td>Not ranked</td><td>{_e(item)}</td><td></td><td></td></tr>"
            )
    return '<div class="table-wrap"><table><thead><tr><th>Priority</th><th>Action</th><th>Evidence addressed</th><th>Expected impact / rationale</th></tr></thead><tbody>{}</tbody></table></div>'.format(
        "".join(rows)
    )


def build_html(scans, metadata=None, hitlogs=None, include_examples=False):
    metadata = metadata or {}
    hitlogs = hitlogs or {}
    labels = list(scans)
    if not labels:
        raise ValueError("At least one Garak scan is required")
    baseline_label = metadata.get("baseline", labels[0])
    if baseline_label not in scans:
        raise ValueError(
            f"Baseline {baseline_label!r} is not one of the supplied scan labels"
        )
    taxonomy = metadata.get("taxonomy") or next(
        (
            scans[label].get("taxonomy")
            for label in labels
            if scans[label].get("taxonomy")
        ),
        None,
    )
    category_labels = metadata.get("category_labels", {})
    examples = (
        _render_examples(hitlogs, labels, int(metadata.get("max_examples", 6)))
        if include_examples
        else _render_examples({}, labels, 0)
    )
    title = metadata.get("title", "AI Red Teaming Report")
    subtitle = metadata.get(
        "subtitle", "Garak vulnerability scan and safety evaluation results"
    )
    benchmark = metadata.get("benchmark", "Not supplied")
    platform = metadata.get("platform", "Not supplied")
    report_date = metadata.get(
        "generated", datetime.now().astimezone().date().isoformat()
    )

    content = []
    content.append(
        _section(
            "executive-summary",
            "1. Executive Summary",
            _render_summary(scans, labels, baseline_label, metadata),
        )
    )
    content.append(
        _section(
            "methodology",
            "2. Scope, Methodology, and Scan Statistics",
            _render_scan_stats(scans, labels, metadata),
        )
    )
    content.append(
        _section(
            "architecture",
            "3. Test Architecture and Configurations",
            _render_architecture(scans, labels, metadata),
        )
    )
    content.append(
        _section(
            "taxonomy-results",
            "4. Results by Risk Taxonomy",
            _render_categories(scans, labels, taxonomy, category_labels),
        )
    )
    content.append(
        _section(
            "probe-changes",
            "5. Probe-Level Comparison",
            _render_probe_changes(scans, labels, baseline_label),
        )
    )
    content.append(
        _section(
            "probe-results",
            "6. Detailed Probe and Detector Results",
            _render_probe_table(scans, labels),
        )
    )
    content.append(_section("hit-evidence", "7. Attack Bypass Examples", examples))
    content.append(
        _section(
            "interpretation",
            "8. Known Issues and Interpretation",
            _render_interpretation(metadata),
        )
    )
    content.append(
        _section(
            "defense-analysis",
            "9. Defense Layer Analysis",
            _render_defense_analysis(metadata),
        )
    )
    content.append(
        _section(
            "recommendations",
            "10. Recommendations",
            _render_recommendations(scans, labels, metadata),
        )
    )

    nav = "".join(
        f'<a href="#{section_id}">{label}</a>'
        for section_id, label in [
            ("executive-summary", "Summary"),
            ("methodology", "Methodology"),
            ("architecture", "Architecture"),
            ("taxonomy-results", "Taxonomy"),
            ("probe-changes", "Probe comparison"),
            ("probe-results", "Probe results"),
            ("hit-evidence", "Hit evidence"),
            ("interpretation", "Interpretation"),
            ("defense-analysis", "Defense analysis"),
            ("recommendations", "Recommendations"),
        ]
    )
    css = """
:root{--red:#ee0000;--dark:#151515;--light:#f0f0f0;--green:#3e8635;--yellow:#f0ab00;--blue:#0066cc;--ink:#333;--line:#e2e2e2}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;color:var(--ink);font:16px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
.container{max-width:1240px;margin:auto;padding:0 28px}header{background:var(--dark);color:white;border-bottom:4px solid var(--red);padding:24px 0}h1{font-size:1.8rem;margin:0 0 4px}header .subtitle{color:#ddd}header .meta{color:#aaa;font-size:.84rem;margin-top:6px}
nav{position:sticky;top:0;z-index:3;background:#f6f6f6;border-bottom:1px solid #ddd}nav .container{display:flex;flex-wrap:wrap;gap:4px 16px;padding-top:9px;padding-bottom:9px}nav a{color:var(--blue);text-decoration:none;font-size:.85rem}
main{padding:28px 0 60px}section{margin:0 0 42px;scroll-margin-top:64px}h2{font-size:1.4rem;color:var(--dark);border-bottom:2px solid var(--red);padding-bottom:6px}h3{font-size:1.08rem;margin:18px 0 8px}p{margin:.6rem 0}.muted{color:#666;font-size:.86rem}.kpi-row{display:flex;gap:16px;flex-wrap:wrap;margin:20px 0}.kpi{flex:1;min-width:190px;border:1px solid var(--line);border-radius:8px;text-align:center;padding:16px;background:white;box-shadow:0 1px 3px #0001}.kpi .value{font-size:2rem;font-weight:700}.kpi .label{font-size:.84rem;color:#666}.kpi.red .value{color:var(--red)}.kpi.green .value{color:var(--green)}.kpi.yellow .value{color:#bd7900}
.callout{background:var(--light);border-left:4px solid var(--blue);border-radius:0 6px 6px 0;padding:14px 18px;margin:16px 0}.callout.green{border-color:var(--green)}.callout.yellow{border-color:var(--yellow)}.callout ul{padding-left:22px}
.table-wrap{overflow-x:auto;margin:14px 0}table{border-collapse:collapse;width:100%;font-size:.88rem}th{background:var(--dark);color:#fff;text-align:left;font-weight:600;white-space:nowrap}td,th{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}tbody tr:hover{background:#f8f9fb}.na{color:#777;font-style:italic}.architecture{white-space:pre-wrap;background:#f8f9fa;border:1px solid var(--line);border-radius:8px;padding:18px}.flow{max-width:100%;height:auto;display:block;margin:12px 0}.config,.issue{border:1px solid var(--line);border-radius:7px;padding:4px 16px;margin:12px 0}
details{border:1px solid var(--line);border-radius:6px;margin:8px 0;overflow:hidden}summary{cursor:pointer;background:#fafafa;padding:9px 14px;font-weight:600}summary:hover{background:#f0f0f0}.example{padding:12px 16px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#1e1e1e;color:#eee;padding:12px;border-radius:5px;max-height:360px;overflow:auto;font: .82rem/1.5 ui-monospace,SFMono-Regular,Consolas,monospace}code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}
footer{background:var(--dark);color:#aaa;padding:20px 0;font-size:.82rem} @media(max-width:720px){.container{padding:0 16px}.kpi-row{flex-direction:column}h1{font-size:1.45rem}table{font-size:.8rem}}
@media print{nav{position:static}header{print-color-adjust:exact;-webkit-print-color-adjust:exact}details{break-inside:avoid}a{color:inherit;text-decoration:none}section{break-inside:avoid}}
"""
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{css}</style></head><body>
<header><div class="container"><h1>{title}</h1><div class="subtitle">{subtitle}</div>
<div class="meta">Generated: {generated} &nbsp;|&nbsp; Benchmark: {benchmark} &nbsp;|&nbsp; Platform: {platform} &nbsp;|&nbsp; Scanner: Garak</div></div></header>
<nav><div class="container">{nav}</div></nav><main><div class="container">{sections}</div></main>
<footer><div class="container">Generated from Garak JSONL records. Detector results require contextual review and do not establish overall system safety.</div></footer>
</body></html>""".format(
        title=_e(title),
        subtitle=_e(subtitle),
        generated=_e(report_date),
        benchmark=_e(benchmark),
        platform=_e(platform),
        css=css,
        nav=nav,
        sections="".join(content),
    )


def write_report(
    scans, output_path, metadata=None, hitlogs=None, include_examples=False
):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        build_html(
            scans, metadata=metadata, hitlogs=hitlogs, include_examples=include_examples
        ),
        encoding="utf-8",
    )
    return output_path


def _parse_labeled_path(value, argument_name):
    if "=" not in value:
        raise argparse.ArgumentTypeError(f"{argument_name} must use LABEL=PATH")
    label, path = value.split("=", 1)
    if not label.strip() or not path.strip():
        raise argparse.ArgumentTypeError(
            f"{argument_name} must use non-empty LABEL=PATH"
        )
    return label.strip(), path.strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scan",
        action="append",
        required=True,
        metavar="LABEL=REPORT.JSONL",
        help="Garak report JSONL; repeat for baseline and defended runs. First scan is baseline unless metadata overrides it.",
    )
    parser.add_argument(
        "--hitlog",
        action="append",
        default=[],
        metavar="LABEL=HITLOG.JSONL",
        help="Optional Garak hit log for a scan label; repeat as needed.",
    )
    parser.add_argument(
        "--metadata",
        help="Optional JSON report metadata (architecture, issues, recommendations, labels).",
    )
    parser.add_argument(
        "--taxonomy", help="Override the taxonomy used to group probe tags, e.g. owasp."
    )
    parser.add_argument("--output", required=True, help="Destination HTML file.")
    parser.add_argument(
        "--include-examples",
        action="store_true",
        help="Include hit-log prompts and outputs. Review for sensitive content before sharing.",
    )
    args = parser.parse_args(argv)

    metadata = {}
    if args.metadata:
        with Path(args.metadata).open("r", encoding="utf-8") as source:
            metadata = json.load(source)
        if not isinstance(metadata, dict):
            parser.error("metadata JSON must be an object")
    taxonomy = args.taxonomy or metadata.get("taxonomy")
    scans = {}
    for item in args.scan:
        label, path = _parse_labeled_path(item, "--scan")
        if label in scans:
            parser.error(f"duplicate scan label: {label}")
        scans[label] = load_garak_report(path, taxonomy=taxonomy)
    hitlogs = {}
    for item in args.hitlog:
        label, path = _parse_labeled_path(item, "--hitlog")
        if label not in scans:
            parser.error(f"hit-log label {label!r} has no matching --scan")
        hitlogs[label] = load_hitlog(path)

    try:
        output = write_report(
            scans,
            args.output,
            metadata=metadata,
            hitlogs=hitlogs,
            include_examples=args.include_examples,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
