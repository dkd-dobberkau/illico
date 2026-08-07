from illico_compile import get_prompts


def test_german_prompts_carry_distill():
    assert "Destillat" in get_prompts("de").distill


def test_english_prompts_carry_distill():
    assert "distillate" in get_prompts("en").distill


def test_fallback_prompts_carry_distill():
    assert get_prompts(None).distill


def test_prompts_carry_assign():
    assert get_prompts("de").assign
    assert get_prompts("en").assign
