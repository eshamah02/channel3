"""Category assignment by walking down Google's product taxonomy.

Category is the only field with a hard validator, so asking a model for a string and
hoping is a coin flip against 5,595 near-misses. Instead the model sees only the real
children of the current node and picks one by number, which makes the assembled path
valid by construction.

categories.txt lists every node rather than only leaves, so stopping part way down is
a legitimate answer rather than a compromise.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from config import CATEGORY_MODEL
from extraction.llm import LLMClient
from models import CATEGORIES_FILE, VALID_CATEGORIES, Category, CategoryChoice

logger = logging.getLogger(__name__)

SEPARATOR = " > "

# Deeper than the taxonomy goes, so tripping this means a logic error.
_MAX_DEPTH = 8

# The description is what gets "trousers" to "Clothing > Pants" despite sharing no
# words, but it is re-sent at every level, so it is trimmed.
_DESCRIPTION_CHARS = 600

# The answer is a single number, so there is nothing to think about at length.
_REASONING: dict[str, Any] = {"effort": "low"}

_MAX_ATTEMPTS = 2


class CategoryError(RuntimeError):
    """No category could be resolved for this product."""


def _build_tree() -> dict[str, dict]:
    """Nested dict of the taxonomy, preserving file order at every level.

    Order is kept rather than sorted because it is how the options are numbered.
    """
    tree: dict[str, dict] = {}
    with open(CATEGORIES_FILE, encoding="utf-8") as handle:
        for line in handle:
            entry = line.strip()
            # The file opens with a version comment.
            if not entry or entry.startswith("#"):
                continue
            node = tree
            for part in entry.split(SEPARATOR):
                node = node.setdefault(part.strip(), {})
    return tree


TREE = _build_tree()


def children_of(path: Sequence[str] | None = None) -> list[str]:
    """Immediate children of a node, or the top-level entries for None."""
    node = TREE
    for part in path or ():
        node = node.get(part)
        if node is None:
            return []
    return list(node)


_SYSTEM = """\
You place a product into Google's product taxonomy by walking down it one level at
a time.

You will be shown a product and a numbered list of the categories available at the
current position. Reply with the number of the single option that best contains the
product.

- Judge by what the product actually is, not by words it shares with an option
  name. The taxonomy's wording often differs from a shop's wording for the same
  thing.
- Use the description as well as the name. The name alone is frequently ambiguous.
- Prefer to descend. The taxonomy has thousands of specific entries, so a broad
  category is rarely the best answer available; choose the option that contains
  the product and expect to be asked again about its contents.
- If the product genuinely belongs in none of the listed options, choose the final
  "stop here" option when one is offered. Stopping at a broader but correct
  category is better than descending into a specific but wrong one.
"""


def _options(children: list[str], path: list[str], allow_stop: bool) -> tuple[str, int]:
    """Render the numbered choices, returning the text and the stop index."""
    lines = [f"{index}. {child}" for index, child in enumerate(children, start=1)]
    stop_index = 0
    if allow_stop:
        stop_index = len(children) + 1
        lines.append(
            f"{stop_index}. None of these - stop at \"{SEPARATOR.join(path)}\""
        )
    return "\n".join(lines), stop_index


def _messages(
    name: str,
    description: str,
    path: list[str],
    children: list[str],
    allow_stop: bool,
    complaint: str | None,
) -> list[dict[str, Any]]:
    options, _ = _options(children, path, allow_stop)
    position = SEPARATOR.join(path) if path else "(top level)"
    body = (
        f"Product: {name}\n"
        f"Description: {description[:_DESCRIPTION_CHARS]}\n\n"
        f"Current position: {position}\n\n"
        f"Options:\n{options}"
    )
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": body},
    ]
    if complaint:
        messages.append({"role": "user", "content": complaint})
    return messages


async def _choose(
    name: str,
    description: str,
    path: list[str],
    children: list[str],
    client: LLMClient,
    model: str,
    allow_stop: bool,
) -> int | None:
    """Index of the chosen child, or None to stop at the current node."""
    _, stop_index = _options(children, path, allow_stop)
    highest = stop_index or len(children)
    complaint: str | None = None

    for attempt in range(_MAX_ATTEMPTS):
        result = await client.parse(
            model=model,
            input=_messages(name, description, path, children, allow_stop, complaint),
            text_format=CategoryChoice,
            reasoning=_REASONING,
        )

        if result is None:
            complaint = f"Reply with a single number between 1 and {highest}."
            continue

        choice = result.choice
        if 1 <= choice <= len(children):
            return choice - 1
        if allow_stop and choice == stop_index:
            return None

        complaint = (
            f"{choice} is not one of the options. "
            f"Reply with a number between 1 and {highest}."
        )
        logger.debug("category choice out of range at %r: %s", path, choice)

    # Out of range twice: a broader but valid answer beats guessing a child.
    return None


async def _descend(
    name: str,
    description: str,
    client: LLMClient,
    model: str,
    exclude: frozenset[str] = frozenset(),
) -> tuple[list[str], bool]:
    """One pass down the tree, optionally barring some top-level entries.

    Returns the path and whether it ended at a leaf; see resolve_category for why
    that matters.
    """
    path: list[str] = []

    for _ in range(_MAX_DEPTH):
        children = children_of(path)
        if not path and exclude:
            children = [child for child in children if child not in exclude]
        if not children:
            return path, True  # ran out of children: a leaf

        # No "stop here" at the top level: an empty category is not a valid answer.
        chosen = await _choose(
            name, description, path, children, client, model, allow_stop=bool(path)
        )
        if chosen is None:
            return path, False  # declined every child
        path.append(children[chosen])

    return path, False


async def resolve_category(
    name: str,
    description: str,
    client: LLMClient,
    *,
    model: str = CATEGORY_MODEL,
) -> Category:
    """Walk the taxonomy to the most specific category that still fits.

    A descent that ends by declining every child gets one retry from a different
    root. Running out of children means the taxonomy has nothing more specific;
    declining every child usually means the branch was wrong. Cost is self-limiting,
    since a first pass that reaches a leaf never retries.
    """
    path, at_leaf = await _descend(name, description, client, model)

    if path and not at_leaf:
        logger.debug(
            "descent declined all children at %r; retrying from a different root",
            SEPARATOR.join(path),
        )
        second, second_at_leaf = await _descend(
            name, description, client, model, exclude=frozenset(path[:1])
        )
        # A leaf beats a stop; failing that, deeper beats shallower.
        if second and (
            (second_at_leaf and not at_leaf) or (second_at_leaf == at_leaf and len(second) > len(path))
        ):
            path = second

    if not path:
        raise CategoryError(
            f"no top-level category could be chosen for {name!r}"
        )

    assembled = SEPARATOR.join(path)
    # True by construction; kept as a guard against a future change to the descent.
    if assembled not in VALID_CATEGORIES:
        raise CategoryError(f"assembled path is not in the taxonomy: {assembled!r}")

    return Category(name=assembled)
