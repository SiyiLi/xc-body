"""Expression names accepted at XC Body runtime boundaries."""

SUPPORTED_EXPRESSIONS = frozenset(
    (
        "idle",
        "agree",
        "pleased",
        "curious",
        "concerned",
        "surprised",
        "embarrassed",
        "mischievous",
    )
)
SEMANTIC_EXPRESSIONS = SUPPORTED_EXPRESSIONS - {"idle"}
