"""Tests for compute_log_likelihood in pyrecall.utils."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import torch

from pyrecall.utils import compute_log_likelihood


class TestComputeLogLikelihood:
    """Tests for the compute_log_likelihood helper."""

    def _make_model_mock(self, loss_value: float) -> MagicMock:
        output = MagicMock()
        output.loss = torch.tensor(loss_value)
        model = MagicMock()
        model.return_value = output
        return model

    def _make_tokenizer_mock(self, prompt_len: int = 3, total_len: int = 6) -> MagicMock:
        prompt_ids = torch.ones(1, prompt_len, dtype=torch.long)
        full_ids = torch.ones(1, total_len, dtype=torch.long)
        tok = MagicMock()
        tok.side_effect = [
            {"input_ids": prompt_ids, "attention_mask": torch.ones(1, prompt_len)},
            {"input_ids": full_ids, "attention_mask": torch.ones(1, total_len)},
        ]
        return tok

    def test_returns_float_in_zero_one(self) -> None:
        model = self._make_model_mock(loss_value=1.0)
        tokenizer = self._make_tokenizer_mock()
        score = compute_log_likelihood(model, tokenizer, "prompt", "completion")
        assert 0.0 < score <= 1.0

    def test_lower_nll_yields_higher_score(self) -> None:
        """exp(-low_NLL) > exp(-high_NLL)."""
        model_good = self._make_model_mock(loss_value=0.5)
        model_bad = self._make_model_mock(loss_value=3.0)
        tok_good = self._make_tokenizer_mock()
        tok_bad = self._make_tokenizer_mock()
        score_good = compute_log_likelihood(model_good, tok_good, "p", "c")
        score_bad = compute_log_likelihood(model_bad, tok_bad, "p", "c")
        assert score_good > score_bad

    def test_zero_nll_returns_one(self) -> None:
        model = self._make_model_mock(loss_value=0.0)
        tokenizer = self._make_tokenizer_mock()
        score = compute_log_likelihood(model, tokenizer, "prompt", "completion")
        assert score == pytest.approx(1.0)

    def test_prompt_tokens_masked_in_labels(self) -> None:
        """Prompt token positions in labels should be -100."""
        captured_labels: list[torch.Tensor] = []

        output = MagicMock()
        output.loss = torch.tensor(1.0)

        def fake_model(**kwargs: object) -> MagicMock:
            captured_labels.append(kwargs["labels"].clone())  # type: ignore[arg-type]
            return output

        prompt_ids = torch.tensor([[10, 20, 30]])
        full_ids = torch.tensor([[10, 20, 30, 40, 50]])
        tok = MagicMock()
        tok.side_effect = [
            {"input_ids": prompt_ids, "attention_mask": torch.ones(1, 3)},
            {"input_ids": full_ids, "attention_mask": torch.ones(1, 5)},
        ]

        compute_log_likelihood(fake_model, tok, "prompt", "completion")  # type: ignore[arg-type]
        labels = captured_labels[0]
        assert (labels[0, :3] == -100).all(), "Prompt tokens must be masked"
        assert (labels[0, 3:] != -100).all(), "Completion tokens must not be masked"

    def test_score_is_deterministic(self) -> None:
        model = self._make_model_mock(loss_value=2.0)
        tok1 = self._make_tokenizer_mock()
        tok2 = self._make_tokenizer_mock()
        s1 = compute_log_likelihood(model, tok1, "x", "y")
        model.reset_mock()
        model.return_value.loss = torch.tensor(2.0)
        s2 = compute_log_likelihood(model, tok2, "x", "y")
        assert s1 == pytest.approx(s2)

    def test_high_nll_yields_near_zero_score(self) -> None:
        model = self._make_model_mock(loss_value=20.0)
        tokenizer = self._make_tokenizer_mock()
        score = compute_log_likelihood(model, tokenizer, "p", "c")
        assert score < 0.01


class TestScoringMethodInSkillScore:
    """Tests for the scoring_method field on SkillScore."""

    def test_default_scoring_method_is_log_likelihood(self) -> None:
        from pyrecall.snapshot import SkillScore

        s = SkillScore(category="c", prompt="p", response="r", score=0.5)
        assert s.scoring_method == "log_likelihood"

    def test_to_dict_includes_scoring_method(self) -> None:
        from pyrecall.snapshot import SkillScore

        s = SkillScore(category="c", prompt="p", response="r", score=0.5, scoring_method="cosine")
        d = s.to_dict()
        assert d["scoring_method"] == "cosine"

    def test_from_dict_reads_scoring_method(self) -> None:
        from pyrecall.snapshot import SkillScore

        d = {
            "category": "c",
            "prompt": "p",
            "response": "r",
            "score": 0.5,
            "scoring_method": "cosine",
        }
        s = SkillScore.from_dict(d)
        assert s.scoring_method == "cosine"

    def test_from_dict_defaults_to_cosine_for_old_snapshots(self) -> None:
        from pyrecall.snapshot import SkillScore

        d = {"category": "c", "prompt": "p", "response": "r", "score": 0.5}
        s = SkillScore.from_dict(d)
        assert s.scoring_method == "cosine"
