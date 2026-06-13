"""ForgettingDetector — compare two snapshots and surface degraded skills."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from io import StringIO

from rich.console import Console
from rich.table import Table

from .snapshot import SkillSnapshot
from .utils import console as _shared_console

# Delta-bucket thresholds used when n_items < 2 and effect size is unavailable.
_DELTA_MINOR: float = 0.05
_DELTA_MODERATE: float = 0.15
_DELTA_SEVERE: float = 0.30


@dataclass
class PromptComparison:
    """Before/after scores for a single benchmark prompt."""

    category: str
    prompt: str
    score_before: float
    score_after: float

    @property
    def delta(self) -> float:
        return self.score_after - self.score_before

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "prompt": self.prompt,
            "score_before": round(self.score_before, 4),
            "score_after": round(self.score_after, 4),
            "delta": round(self.delta, 4),
        }


@dataclass
class CategoryComparison:
    """Before/after scores for one skill category."""

    category: str
    score_before: float
    score_after: float
    # Standardized effect size of per-item score deltas (mean_delta / std_delta).
    # Requires n_items ≥ 2; 0.0 when unavailable.
    cohen_d: float = 0.0
    n_items: int = 0

    @property
    def severity_method(self) -> str:
        """Which method was used to compute :attr:`severity`.

        ``"effect_size"`` — standardized effect size of per-item deltas (n_items ≥ 2).
        ``"delta"``       — absolute score-drop buckets (n_items < 2 fallback).
        """
        return "effect_size" if self.n_items >= 2 else "delta"

    @property
    def delta(self) -> float:
        """Absolute change in score (positive = improved, negative = degraded)."""
        return self.score_after - self.score_before

    @property
    def pct_change(self) -> float:
        """Percentage change relative to the before score."""
        if self.score_before == 0.0:
            return 0.0
        return (self.score_after - self.score_before) / self.score_before * 100.0

    @property
    def threshold_based_severity(self) -> str:
        """Severity by absolute score delta — used when n_items < 2 and effect size is unavailable.

        Thresholds (_DELTA_MINOR / _DELTA_MODERATE / _DELTA_SEVERE) are module-level constants.

        OK       — no drop
        MINOR    — |delta| < _DELTA_MINOR  (0.05)
        MODERATE — _DELTA_MINOR  ≤ |delta| < _DELTA_MODERATE (0.15)
        SEVERE   — _DELTA_MODERATE ≤ |delta| < _DELTA_SEVERE  (0.30)
        CRITICAL — |delta| ≥ _DELTA_SEVERE (0.30)
        """
        if self.delta >= 0:
            return "OK"
        d = abs(self.delta)
        if d >= _DELTA_SEVERE:
            return "CRITICAL"
        if d >= _DELTA_MODERATE:
            return "SEVERE"
        if d >= _DELTA_MINOR:
            return "MODERATE"
        return "MINOR"

    @property
    def severity(self) -> str:
        """Human-readable forgetting severity.

        Uses Cohen's d effect size when n_items ≥ 2; falls back to
        threshold_based_severity (absolute delta buckets) for single-item categories.

        OK       — no meaningful drop
        MINOR    — small effect (|d| < 0.2), possible noise
        MODERATE — small-medium effect (0.2 ≤ |d| < 0.5)
        SEVERE   — medium-large effect (0.5 ≤ |d| < 0.8)
        CRITICAL — large effect (|d| ≥ 0.8)
        """
        if self.n_items < 2:
            return self.threshold_based_severity
        if self.delta >= 0:
            return "OK"
        d = abs(self.cohen_d)
        if d >= 0.8:
            return "CRITICAL"
        if d >= 0.5:
            return "SEVERE"
        if d >= 0.2:
            return "MODERATE"
        return "MINOR"


@dataclass
class ForgettingReport:
    """
    Result of a forgetting check.

    Contains per-category comparisons and exposes helpers for printing
    and programmatic inspection.
    """

    snapshot_before: str
    snapshot_after: str
    threshold: float
    category_thresholds: dict[str, float] = field(default_factory=dict)
    comparisons: list[CategoryComparison] = field(default_factory=list)
    prompt_comparisons: list[PromptComparison] = field(default_factory=list)

    def _threshold_for(self, category: str) -> float:
        """Return the effective threshold for *category*, falling back to the global default."""
        return self.category_thresholds.get(category, self.threshold)

    # ── inspection ─────────────────────────────────────────────────────────────

    @property
    def degraded_skills(self) -> list[str]:
        """Categories whose score dropped more than their effective threshold."""
        return [
            c.category
            for c in self.comparisons
            if (c.score_before - c.score_after) > self._threshold_for(c.category)
        ]

    @property
    def is_healthy(self) -> bool:
        """True when no skill degraded beyond the threshold."""
        return len(self.degraded_skills) == 0

    @property
    def severity(self) -> dict[str, str]:
        """Severity label per category — convenience wrapper over per-comparison severity."""
        return {c.category: c.severity for c in self.comparisons}

    def prompts_for_category(self, category: str) -> list[PromptComparison]:
        """Return per-prompt comparisons for *category*, worst delta first."""
        return sorted(
            [p for p in self.prompt_comparisons if p.category == category],
            key=lambda p: p.delta,
        )

    def to_dict(self) -> dict:
        """Return a JSON-serialisable representation of the report."""
        return {
            "is_healthy": self.is_healthy,
            "snapshot_before": self.snapshot_before,
            "snapshot_after": self.snapshot_after,
            "threshold": self.threshold,
            "degraded_skills": self.degraded_skills,
            "comparisons": [
                {
                    "category": c.category,
                    "score_before": round(c.score_before, 4),
                    "score_after": round(c.score_after, 4),
                    "delta": round(c.delta, 4),
                    "pct_change": round(c.pct_change, 2),
                    "threshold": self._threshold_for(c.category),
                    "cohen_d": round(c.cohen_d, 4),
                    "n_items": c.n_items,
                    "severity": c.severity,
                    "severity_method": c.severity_method,
                    "status": "FORGOTTEN"
                    if (c.score_before - c.score_after) > self._threshold_for(c.category)
                    else "OK",
                    "prompts": [p.to_dict() for p in self.prompts_for_category(c.category)],
                }
                for c in self.comparisons
            ],
        }

    def to_json(self, indent: int = 2) -> str:
        """Return the report serialised as a JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    # ── rendering ──────────────────────────────────────────────────────────────

    def __str__(self) -> str:
        buf = StringIO()
        self._render(Console(file=buf, highlight=False))
        return buf.getvalue()

    def print(self, verbose: bool = False) -> None:
        """Print the report to the terminal using rich formatting."""
        self._render(_shared_console, verbose=verbose)

    def _render(self, console: Console, verbose: bool = False) -> None:
        table = Table(
            title=(
                f"Forgetting Report  [dim]{self.snapshot_before}[/dim]"
                f" → [dim]{self.snapshot_after}[/dim]"
            ),
            show_lines=False,
        )
        table.add_column("Skill", style="bold white")
        table.add_column("Before", justify="right")
        table.add_column("After", justify="right")
        table.add_column("Δ Score", justify="right")
        table.add_column("Cohen's d", justify="right")
        table.add_column("Severity", justify="center")

        _SEVERITY_MARKUP = {
            "OK": "[green]   OK   [/green]",
            "MINOR": "[dim yellow]  MINOR  [/dim yellow]",
            "MODERATE": "[yellow] MODERATE [/yellow]",
            "SEVERE": "[red]  SEVERE  [/red]",
            "CRITICAL": "[bold red] CRITICAL [/bold red]",
        }

        for comp in self.comparisons:
            cat_threshold = self._threshold_for(comp.category)
            sign = "+" if comp.delta >= 0 else ""
            delta_str = f"{sign}{comp.delta:.3f} ({sign}{comp.pct_change:.1f}%)"
            delta_style = (
                "red" if comp.delta < -cat_threshold else ("green" if comp.delta >= 0 else "yellow")
            )
            d_sign = "+" if comp.cohen_d >= 0 else ""
            cohen_str = f"{d_sign}{comp.cohen_d:.2f}" if comp.n_items >= 2 else "n/a *"
            status_markup = _SEVERITY_MARKUP.get(comp.severity, comp.severity)

            table.add_row(
                comp.category,
                f"{comp.score_before:.3f}",
                f"{comp.score_after:.3f}",
                f"[{delta_style}]{delta_str}[/{delta_style}]",
                cohen_str,
                status_markup,
            )

        has_single_item = any(c.n_items < 2 for c in self.comparisons)

        console.print()
        console.print(table)
        if has_single_item:
            console.print(
                "[dim]  * Severity estimated from score delta — "
                "Cohen's d requires ≥ 2 prompts per category.[/dim]"
            )

        if self.degraded_skills:
            console.print(
                f"\n[error]⚠  Forgetting detected in: {', '.join(self.degraded_skills)}[/error]"
            )
            console.print(
                "[dim]  Run model.rollback(to='<snapshot>') to restore these skills.[/dim]\n"
            )
        else:
            threshold_note = (
                f"(threshold: {self.threshold:.0%}"
                + (", with per-category overrides" if self.category_thresholds else "")
                + ")"
            )
            console.print(
                f"\n[success]✓  No significant forgetting detected {threshold_note}.[/success]\n"
            )

        if verbose and self.prompt_comparisons:
            categories_to_show = (
                self.degraded_skills
                if self.degraded_skills
                else sorted({p.category for p in self.prompt_comparisons})
            )
            for cat in categories_to_show:
                prompts = self.prompts_for_category(cat)
                if not prompts:
                    continue
                pt = Table(
                    title=f"[bold]{cat}[/bold] — per-prompt breakdown",
                    show_lines=False,
                    title_justify="left",
                )
                pt.add_column("Prompt", no_wrap=False, max_width=60)
                pt.add_column("Before", justify="right")
                pt.add_column("After", justify="right")
                pt.add_column("Δ", justify="right")
                for p in prompts:
                    sign = "+" if p.delta >= 0 else ""
                    delta_style = "red" if p.delta < 0 else "green"
                    pt.add_row(
                        p.prompt,
                        f"{p.score_before:.3f}",
                        f"{p.score_after:.3f}",
                        f"[{delta_style}]{sign}{p.delta:.3f}[/{delta_style}]",
                    )
                console.print(pt)
                console.print()


class ForgettingDetector:
    """
    Compare a before-snapshot and an after-snapshot to detect forgotten skills.

    A skill is considered *forgotten* when its average cosine-similarity score
    drops by more than its effective threshold.  The global *threshold* applies
    to all categories unless overridden in *category_thresholds*.

    Example::

        detector = ForgettingDetector(
            threshold=0.10,
            category_thresholds={"safety": 0.03, "coding": 0.15},
        )
    """

    def __init__(
        self,
        threshold: float = 0.10,
        category_thresholds: dict[str, float] | None = None,
    ) -> None:
        self.threshold = threshold
        self.category_thresholds: dict[str, float] = category_thresholds or {}

    def compare(self, before: SkillSnapshot, after: SkillSnapshot) -> ForgettingReport:
        """
        Return a ForgettingReport comparing *before* and *after* snapshots.

        Categories present in only one snapshot get a score of 0.0 for the missing side.
        """
        before_scores = before.category_scores()
        after_scores = after.category_scores()

        # Build per-prompt comparisons by matching on (category, prompt) key.
        before_map = {(s.category, s.prompt): s.score for s in before.scores}
        after_map = {(s.category, s.prompt): s.score for s in after.scores}
        all_keys = sorted(set(before_map) | set(after_map))
        prompt_comparisons = [
            PromptComparison(
                category=cat,
                prompt=prompt,
                score_before=before_map.get((cat, prompt), 0.0),
                score_after=after_map.get((cat, prompt), 0.0),
            )
            for cat, prompt in all_keys
        ]

        # Compute standardized effect size of per-item deltas per category.
        cat_deltas: dict[str, list[float]] = {}
        for pc in prompt_comparisons:
            cat_deltas.setdefault(pc.category, []).append(pc.score_after - pc.score_before)

        all_categories = sorted(set(before_scores) | set(after_scores))
        comparisons = []
        for cat in all_categories:
            deltas = cat_deltas.get(cat, [])
            n = len(deltas)
            if n >= 2:
                mean_d = sum(deltas) / n
                variance = sum((d - mean_d) ** 2 for d in deltas) / (n - 1)
                std_d = variance**0.5
                cohen_d = mean_d / std_d if std_d > 0.0 else 0.0
            else:
                cohen_d = 0.0
            comparisons.append(
                CategoryComparison(
                    category=cat,
                    score_before=before_scores.get(cat, 0.0),
                    score_after=after_scores.get(cat, 0.0),
                    cohen_d=cohen_d,
                    n_items=n,
                )
            )

        # Warn when snapshots used different scoring methods.
        before_methods = {s.scoring_method for s in before.scores}
        after_methods = {s.scoring_method for s in after.scores}
        if before_methods != after_methods:
            from .utils import console as _c

            _c.print(
                "[warning]⚠  Snapshots used different scoring methods "
                f"({before_methods} vs {after_methods}). "
                "Scores are not directly comparable — retake one snapshot.[/warning]"
            )

        return ForgettingReport(
            snapshot_before=before.name,
            snapshot_after=after.name,
            threshold=self.threshold,
            category_thresholds=self.category_thresholds,
            comparisons=comparisons,
            prompt_comparisons=prompt_comparisons,
        )
