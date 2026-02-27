"""Comparison reporter adapter for the AumOS Benchmark Suite.

Generates cross-version, cross-provider, and historical trend comparison
reports in JSON, HTML, and Markdown formats. Produces benchmark leaderboard data.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from aumos_common.observability import get_logger

logger = get_logger(__name__)

# Supported output formats
_SUPPORTED_FORMATS: frozenset[str] = frozenset({"json", "html", "markdown"})

# Regression severity thresholds (percent change)
_REGRESSION_CRITICAL_THRESHOLD: float = -20.0
_REGRESSION_WARNING_THRESHOLD: float = -10.0


class ComparisonReporter:
    """Generates structured comparison reports from benchmark run data.

    Supports cross-version comparison (A/B), cross-provider comparison
    (AumOS vs Gretel vs MOSTLY AI vs Tonic), historical trend analysis,
    regression highlighting, and benchmark leaderboard data generation.
    Outputs to JSON, HTML, or Markdown formats.
    """

    def __init__(self) -> None:
        """Initialise the comparison reporter."""
        pass

    def compare_versions(
        self,
        run_id: uuid.UUID,
        current_run: dict[str, Any],
        baseline_run: dict[str, Any],
        metric_categories: list[str] | None = None,
    ) -> dict[str, Any]:
        """Generate a cross-version comparison between two benchmark runs.

        Args:
            run_id: Current BenchmarkRun UUID for the report.
            current_run: Full report dict from the current (newer) run.
            baseline_run: Full report dict from the baseline (older) run.
            metric_categories: Optional subset of categories to compare
                (fidelity | privacy | speed). Defaults to all.

        Returns:
            Version comparison dict with per-metric deltas and regression flags.
        """
        categories = metric_categories or ["fidelity", "privacy", "speed"]
        comparison: dict[str, Any] = {
            "run_id": str(run_id),
            "report_type": "version_comparison",
            "current_version": current_run.get("aumos_version", "unknown"),
            "baseline_version": baseline_run.get("aumos_version", "unknown"),
            "current_run_id": current_run.get("run_id"),
            "baseline_run_id": baseline_run.get("run_id"),
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "categories": {},
            "regressions": [],
            "improvements": [],
            "overall_status": "passed",
        }

        current_metrics = current_run.get("metrics", {})
        baseline_metrics = baseline_run.get("metrics", {})

        for category in categories:
            current_list = current_metrics.get(category, [])
            baseline_list = baseline_metrics.get(category, [])

            baseline_lookup = {m["name"]: m["value"] for m in baseline_list}
            category_deltas: list[dict[str, Any]] = []

            for metric in current_list:
                metric_name = metric["name"]
                current_value = metric["value"]
                baseline_value = baseline_lookup.get(metric_name)
                higher_is_better = metric.get("higher_is_better", True)

                if baseline_value is None:
                    continue

                delta = current_value - baseline_value
                safe_baseline = max(abs(baseline_value), 0.0001)
                percent_change = (delta / safe_baseline) * 100.0

                severity = self._classify_regression_severity(
                    percent_change=percent_change,
                    higher_is_better=higher_is_better,
                )

                delta_entry = {
                    "metric_name": metric_name,
                    "current_value": current_value,
                    "baseline_value": baseline_value,
                    "delta": round(delta, 6),
                    "percent_change": round(percent_change, 2),
                    "higher_is_better": higher_is_better,
                    "severity": severity,
                }
                category_deltas.append(delta_entry)

                if severity in ("warning", "critical"):
                    comparison["regressions"].append({
                        "category": category,
                        "metric_name": metric_name,
                        "percent_change": round(percent_change, 2),
                        "severity": severity,
                    })
                elif percent_change > 5.0 if higher_is_better else percent_change < -5.0:
                    comparison["improvements"].append({
                        "category": category,
                        "metric_name": metric_name,
                        "percent_change": round(percent_change, 2),
                    })

            comparison["categories"][category] = category_deltas

        if comparison["regressions"]:
            critical_regressions = [r for r in comparison["regressions"] if r["severity"] == "critical"]
            comparison["overall_status"] = "critical" if critical_regressions else "warning"

        return comparison

    def compare_providers(
        self,
        run_id: uuid.UUID,
        aumos_metrics: dict[str, Any],
        competitor_baselines: dict[str, list[dict[str, Any]]],
        dataset_name: str,
    ) -> dict[str, Any]:
        """Generate a cross-provider comparison (AumOS vs competitors).

        Args:
            run_id: BenchmarkRun UUID.
            aumos_metrics: AumOS metrics grouped by category.
            competitor_baselines: Dict mapping competitor name to list of baseline dicts.
            dataset_name: Dataset on which comparison is made.

        Returns:
            Provider comparison dict with per-metric AumOS advantage analysis.
        """
        providers = ["aumos"] + list(competitor_baselines.keys())
        comparison: dict[str, Any] = {
            "run_id": str(run_id),
            "report_type": "provider_comparison",
            "dataset_name": dataset_name,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "providers": providers,
            "metrics": {},
            "aumos_wins": 0,
            "aumos_losses": 0,
            "aumos_ties": 0,
        }

        for category, metric_list in aumos_metrics.items():
            for metric in metric_list:
                metric_name = metric["name"]
                aumos_value = metric["value"]
                higher_is_better = metric.get("higher_is_better", True)

                metric_entry: dict[str, Any] = {
                    "category": category,
                    "higher_is_better": higher_is_better,
                    "values": {"aumos": aumos_value},
                    "aumos_advantage": {},
                }

                for competitor, baselines in competitor_baselines.items():
                    competitor_lookup = {b["metric_name"]: b["metric_value"] for b in baselines}
                    competitor_value = competitor_lookup.get(metric_name)
                    if competitor_value is None:
                        continue

                    delta = aumos_value - competitor_value
                    aumos_better = delta > 0 if higher_is_better else delta < 0

                    metric_entry["values"][competitor] = competitor_value
                    metric_entry["aumos_advantage"][competitor] = {
                        "delta": round(delta, 6),
                        "aumos_better": aumos_better,
                        "percent_advantage": round(
                            (delta / max(abs(competitor_value), 0.0001)) * 100, 2
                        ),
                    }

                    if aumos_better:
                        comparison["aumos_wins"] += 1
                    elif abs(delta) < 0.001:
                        comparison["aumos_ties"] += 1
                    else:
                        comparison["aumos_losses"] += 1

                comparison["metrics"][metric_name] = metric_entry

        win_rate = comparison["aumos_wins"] / max(
            comparison["aumos_wins"] + comparison["aumos_losses"] + comparison["aumos_ties"], 1
        )
        comparison["aumos_win_rate"] = round(win_rate, 4)

        return comparison

    def analyze_historical_trends(
        self,
        run_id: uuid.UUID,
        historical_runs: list[dict[str, Any]],
        metric_name: str,
        category: str,
    ) -> dict[str, Any]:
        """Analyze historical trend for a specific metric across runs.

        Args:
            run_id: Current BenchmarkRun UUID.
            historical_runs: List of run dicts ordered by timestamp (oldest first).
            metric_name: Metric name to track.
            category: Metric category (fidelity | privacy | speed).

        Returns:
            Trend analysis with data points, direction, and regression alert.
        """
        data_points: list[dict[str, Any]] = []
        values: list[float] = []

        for run in historical_runs:
            metrics = run.get("metrics", {}).get(category, [])
            for metric in metrics:
                if metric.get("name") == metric_name:
                    value = metric.get("value")
                    data_points.append({
                        "run_id": run.get("run_id"),
                        "aumos_version": run.get("aumos_version"),
                        "completed_at": run.get("completed_at"),
                        "value": value,
                    })
                    if value is not None:
                        values.append(value)
                    break

        trend_direction = "stable"
        regression_alert = False

        if len(values) >= 3:
            recent_avg = sum(values[-3:]) / 3
            early_avg = sum(values[:3]) / 3
            change = (recent_avg - early_avg) / max(abs(early_avg), 0.0001)
            if change > 0.05:
                trend_direction = "improving"
            elif change < -0.05:
                trend_direction = "degrading"
                regression_alert = change < -0.15

        return {
            "run_id": str(run_id),
            "metric_name": metric_name,
            "category": category,
            "data_points": data_points,
            "trend_direction": trend_direction,
            "regression_alert": regression_alert,
            "point_count": len(data_points),
            "latest_value": values[-1] if values else None,
            "earliest_value": values[0] if values else None,
        }

    def generate_scorecard(
        self,
        run_id: uuid.UUID,
        version_comparison: dict[str, Any] | None = None,
        provider_comparison: dict[str, Any] | None = None,
        historical_trends: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Generate a concise summary scorecard across all comparison types.

        Args:
            run_id: BenchmarkRun UUID.
            version_comparison: Optional version comparison output.
            provider_comparison: Optional provider comparison output.
            historical_trends: Optional list of historical trend outputs.

        Returns:
            Scorecard dict suitable for dashboard display.
        """
        scorecard: dict[str, Any] = {
            "run_id": str(run_id),
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "overall_health": "green",
            "sections": {},
        }

        if version_comparison:
            regressions = version_comparison.get("regressions", [])
            scorecard["sections"]["version_comparison"] = {
                "status": version_comparison.get("overall_status", "passed"),
                "regression_count": len(regressions),
                "improvement_count": len(version_comparison.get("improvements", [])),
            }
            if version_comparison.get("overall_status") in ("warning", "critical"):
                scorecard["overall_health"] = "yellow" if scorecard["overall_health"] == "green" else "red"

        if provider_comparison:
            scorecard["sections"]["provider_comparison"] = {
                "aumos_win_rate": provider_comparison.get("aumos_win_rate"),
                "aumos_wins": provider_comparison.get("aumos_wins"),
                "aumos_losses": provider_comparison.get("aumos_losses"),
            }

        if historical_trends:
            alerts = [t for t in historical_trends if t.get("regression_alert")]
            scorecard["sections"]["historical_trends"] = {
                "trend_count": len(historical_trends),
                "regression_alerts": len(alerts),
                "degrading_metrics": [
                    t["metric_name"] for t in historical_trends
                    if t.get("trend_direction") == "degrading"
                ],
            }
            if alerts:
                scorecard["overall_health"] = "red"

        return scorecard

    def export_report(
        self,
        report: dict[str, Any],
        output_format: str = "json",
    ) -> str:
        """Export a report dict to the specified format string.

        Args:
            report: Report dict to serialize.
            output_format: Output format (json | html | markdown).

        Returns:
            String representation in the requested format.

        Raises:
            ValueError: If format is not supported.
        """
        if output_format not in _SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported format '{output_format}'. Supported: {_SUPPORTED_FORMATS}"
            )

        if output_format == "json":
            return json.dumps(report, indent=2, default=str)
        if output_format == "html":
            return self._to_html(report)
        if output_format == "markdown":
            return self._to_markdown(report)

        return json.dumps(report, indent=2, default=str)

    def build_leaderboard(
        self,
        run_id: uuid.UUID,
        provider_comparison: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Build a ranked leaderboard from provider comparison data.

        Args:
            run_id: BenchmarkRun UUID.
            provider_comparison: Output from compare_providers.

        Returns:
            Sorted list of provider dicts with rank and composite score.
        """
        provider_scores: dict[str, float] = {}
        provider_wins: dict[str, int] = {"aumos": 0}

        for competitor in provider_comparison.get("providers", []):
            if competitor != "aumos":
                provider_wins[competitor] = 0

        for metric_name, metric_data in provider_comparison.get("metrics", {}).items():
            values = metric_data.get("values", {})
            higher_is_better = metric_data.get("higher_is_better", True)

            if not values:
                continue

            best_provider = (
                max(values, key=lambda p: values[p])
                if higher_is_better
                else min(values, key=lambda p: values[p])
            )
            provider_wins[best_provider] = provider_wins.get(best_provider, 0) + 1

        for provider, wins in provider_wins.items():
            total_metrics = max(len(provider_comparison.get("metrics", {})), 1)
            provider_scores[provider] = wins / total_metrics

        ranked = sorted(
            provider_scores.items(),
            key=lambda item: item[1],
            reverse=True,
        )

        return [
            {
                "rank": rank + 1,
                "provider": provider,
                "composite_score": round(score, 4),
                "metric_wins": provider_wins.get(provider, 0),
            }
            for rank, (provider, score) in enumerate(ranked)
        ]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _classify_regression_severity(
        self,
        percent_change: float,
        higher_is_better: bool,
    ) -> str:
        """Classify a metric change as passed, warning, or critical.

        Args:
            percent_change: Signed percentage change from baseline.
            higher_is_better: True if higher is better for this metric.

        Returns:
            Severity string: "passed" | "warning" | "critical".
        """
        effective_change = percent_change if higher_is_better else -percent_change

        if effective_change <= _REGRESSION_CRITICAL_THRESHOLD:
            return "critical"
        if effective_change <= _REGRESSION_WARNING_THRESHOLD:
            return "warning"
        return "passed"

    def _to_html(self, report: dict[str, Any]) -> str:
        """Convert a report dict to a minimal HTML representation.

        Args:
            report: Report dict.

        Returns:
            HTML string with a styled comparison table.
        """
        title = report.get("report_type", "Benchmark Report").replace("_", " ").title()
        generated_at = report.get("generated_at", "")

        rows = ""
        for key, value in report.items():
            if isinstance(value, (str, int, float, bool)):
                rows += f"<tr><td><strong>{key}</strong></td><td>{value}</td></tr>"

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>AumOS Benchmark — {title}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 2rem; color: #333; }}
    h1 {{ color: #1a1a2e; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
    th {{ background-color: #1a1a2e; color: white; }}
    tr:nth-child(even) {{ background-color: #f9f9f9; }}
  </style>
</head>
<body>
  <h1>AumOS Benchmark — {title}</h1>
  <p>Generated: {generated_at}</p>
  <table>
    <thead><tr><th>Field</th><th>Value</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <pre>{json.dumps(report, indent=2, default=str)}</pre>
</body>
</html>"""

    def _to_markdown(self, report: dict[str, Any]) -> str:
        """Convert a report dict to a Markdown representation.

        Args:
            report: Report dict.

        Returns:
            Markdown string with sections for each top-level key.
        """
        title = report.get("report_type", "Benchmark Report").replace("_", " ").title()
        lines = [
            f"# AumOS Benchmark — {title}",
            "",
            f"Generated: {report.get('generated_at', '')}",
            "",
            "## Summary",
            "",
            "| Field | Value |",
            "| --- | --- |",
        ]

        for key, value in report.items():
            if isinstance(value, (str, int, float, bool)):
                lines.append(f"| {key} | {value} |")

        lines.extend(["", "## Full Report", "", "```json", json.dumps(report, indent=2, default=str), "```"])
        return "\n".join(lines)
