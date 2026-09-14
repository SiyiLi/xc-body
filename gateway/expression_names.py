"""Semantic expression names accepted by the Interaction boundary."""

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
OFFER_EXPRESSIONS = SUPPORTED_EXPRESSIONS - {"idle"}
