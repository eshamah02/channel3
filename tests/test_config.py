"""Guard the config -> price-table mapping.

ai.MODEL_PRICES is the source of truth for every cost figure in this project.
When a model id has no entry there, ai._log_usage silently falls back to a zero
price and reports a plausible-looking cost of $0.00, so a typo or a new model id
doesn't fail loudly -- it fails quietly and invalidates the write-up's numbers.
This test makes that failure loud instead.

ai.py itself is deliberately unmodified: it is the provided measurement helper,
and the extraction layer calls it rather than replacing it (see extraction/llm.py).
"""

import ai
import config


def _configured_models() -> dict[str, str]:
    """Public model-id constants declared in config.py."""
    return {
        name: value
        for name, value in vars(config).items()
        if name.isupper() and isinstance(value, str)
    }


def test_config_declares_models():
    assert _configured_models(), "config.py declares no model ids"


def test_every_configured_model_is_priced():
    for name, model_id in _configured_models().items():
        assert model_id in ai.MODEL_PRICES, (
            f"config.{name} = {model_id!r} has no entry in ai.MODEL_PRICES, "
            "so its usage would be costed at zero"
        )


def test_price_entries_are_well_formed():
    """_log_usage indexes prices["input"] and prices["output"] directly."""
    for model_id, prices in ai.MODEL_PRICES.items():
        assert {"input", "output"} <= prices.keys(), model_id
        assert prices["input"] > 0 and prices["output"] > 0, model_id
