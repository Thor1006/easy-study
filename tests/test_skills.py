"""The filter must stay free to answer easy requests (router prompt + Claude Code skill)."""

from pathlib import Path

from glass_membrane.roles import SKILL_FILES, SKILLS_DIR

ROOT = Path(__file__).resolve().parent.parent
ROUTER = ROOT / "runtime" / "skills" / "router.md"
SKILL = ROOT / ".claude" / "skills" / "glass-membrane" / "SKILL.md"
BLOCKING_PHRASES = ("only route", "never answer", "always escalate", "use multiple agents")
EXPERIMENT_FLAGS = ("--force-route", "--filter-bypass")


def test_router_prompt_and_skill_do_not_block_direct_answers():
    for path in (ROUTER, SKILL):
        text = path.read_text(encoding="utf-8").lower()
        for phrase in BLOCKING_PHRASES:
            assert phrase not in text, f"{path.name} contains the blocking phrase {phrase!r}"
    assert "try suitably easy completion" in ROUTER.read_text(encoding="utf-8").lower()


def test_skill_never_passes_experiment_flags_and_keeps_input_verbatim():
    text = SKILL.read_text(encoding="utf-8")
    for flag in EXPERIMENT_FLAGS:
        assert flag not in text
    assert text.startswith("---\nname: glass-membrane\n")
    assert "verbatim" in text


def test_every_role_has_an_instruction_file():
    for filename in set(SKILL_FILES.values()):
        assert (SKILLS_DIR / filename).is_file(), filename
