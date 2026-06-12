import pytest

from whisperquiet.cleanup import CleanupConfig, clean

# Golden pairs under the default config. Every entry also feeds the
# idempotency test at the bottom.
GOLDEN = [
    # filler removal
    ("Um, so I think it works.", "So I think it works."),
    ("I was um thinking about it.", "I was thinking about it."),
    ("My umbrella is uh broken.", "My umbrella is broken."),
    ("Take my umbrella.", "Take my umbrella."),  # "um" never fires inside a word
    ("Mm-hmm, that works.", "That works."),
    ("We have apples, um, bananas, and pears.", "We have apples, bananas, and pears."),
    ("That sounds fine, um.", "That sounds fine."),
    ("Um, uh.", ""),
    ("I like this plan.", "I like this plan."),  # "like" survives by default
    # repeat collapse: >2 identical words -> one; legit doubles survive
    ("I think the the the cat is out.", "I think the cat is out."),
    ("I had had enough.", "I had had enough."),
    ("No no no.", "No."),
    ("I'm going to going to going to the store.", "I'm going to the store."),
    ("He said ok ok ok ok ok ok ok ok ok ok ok ok then left.", "He said ok then left."),
    # correction cues
    ("Let's meet Tuesday, no wait, Wednesday.", "Wednesday."),
    ("If you can, come Tuesday, no wait, Wednesday.", "If you can, Wednesday."),
    ("The budget is fifty — no, sixty dollars.", "Sixty dollars."),
    ("Send the doc to Bob, I mean, Alice.", "Alice."),
    ("Order five units, scratch that, ten units.", "Ten units."),
    ("Make the deadline Friday, actually make that Monday.", "Monday."),
    # never rewrites beyond the current sentence
    ("We ship Monday. Use the blue one, no wait, the red one.", "We ship Monday. The red one."),
    # cues that must NOT fire
    ("There is no waiting allowed here.", "There is no waiting allowed here."),
    ("There are no wait times today.", "There are no wait times today."),
    # spoken commands
    ("First point, new line, second point.", "First point\nSecond point."),
    ("Item one, newline, item two.", "Item one\nItem two."),
    ("That's the intro. New paragraph. Now the details.", "That's the intro.\n\nNow the details."),
    ("We launched a new line of products.", "We launched a new line of products."),
    ("The newline character is invisible.", "The newline character is invisible."),
    # whitespace/punctuation normalization
    ("Hello  ,  world  .", "Hello, world."),
    # everything at once
    (
        "Um, send it to Bob, I mean, Alice, and tell her it's it's it's urgent.",
        "Alice, and tell her it's urgent.",
    ),
]

AGGRESSIVE_GOLDEN = [
    ("I was like really tired.", "I was really tired."),
    ("It's complicated, you know.", "It's complicated."),
    ("It was sort of kind of weird.", "It was weird."),
]


@pytest.mark.parametrize("raw, expected", GOLDEN)
def test_golden_pairs(raw, expected):
    assert clean(raw) == expected


@pytest.mark.parametrize("raw, expected", AGGRESSIVE_GOLDEN)
def test_aggressive_golden_pairs(raw, expected):
    assert clean(raw, CleanupConfig(aggressive=True)) == expected


def test_aggressive_fillers_survive_by_default():
    for raw, _ in AGGRESSIVE_GOLDEN:
        assert clean(raw) == raw


def test_hallucination_tail_issue_1():
    # real bug: Whisper appended ~250 repetitions of "Ag" to a transcript
    raw = "This is the transcript." + " Ag" * 250
    assert clean(raw) == "This is the transcript."


def test_hallucination_tail_pure_loop_yields_empty():
    assert clean(("Ag " * 250).strip()) == ""


def test_hallucination_tail_threshold():
    # 10+ at the end is a loop and is dropped; 9 is collapsed to one word
    assert clean("We laughed" + " ha" * 10) == "We laughed"
    assert clean("We laughed" + " ha" * 9) == "We laughed ha"


def test_empty_and_blank_input():
    assert clean("") == ""
    assert clean("   ") == ""
    assert clean("\n\n") == ""


def test_remove_fillers_off():
    assert clean("Um, hi.", CleanupConfig(remove_fillers=False)) == "Um, hi."


def test_custom_filler_list():
    assert clean("It was hmm fine.") == "It was fine."
    assert clean("It was hmm fine.", CleanupConfig(fillers=("uh",))) == "It was hmm fine."


def test_collapse_repeats_off():
    raw = "the the the cat"
    assert clean(raw, CleanupConfig(collapse_repeats=False)) == raw


def test_resolve_corrections_off():
    raw = "Let's meet Tuesday, no wait, Wednesday."
    assert clean(raw, CleanupConfig(resolve_corrections=False)) == raw


def test_spoken_commands_off():
    raw = "First point, new line, second point."
    assert clean(raw, CleanupConfig(spoken_commands=False)) == raw


@pytest.mark.parametrize("raw", [raw for raw, _ in GOLDEN])
def test_idempotent_over_corpus(raw):
    once = clean(raw)
    assert clean(once) == once


@pytest.mark.parametrize("raw", [raw for raw, _ in AGGRESSIVE_GOLDEN])
def test_idempotent_over_aggressive_corpus(raw):
    config = CleanupConfig(aggressive=True)
    once = clean(raw, config)
    assert clean(once, config) == once
