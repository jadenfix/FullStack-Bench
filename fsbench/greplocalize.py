"""The grep-only localiser: a scripted baseline that tries to find a task's fault the cheap way.

It pulls literal tokens from the brief (backticked text, ALL_CAPS names, paths, quoted strings,
dotted keys), greps the repo for each, and ranks files by weighted hits. A task is too shallow if
this finds its whole causal path, or if any of the task's hidden literals (names an agent must
reconstruct by reading code) occur verbatim in the code.

Tasks declare, in task.toml [metadata]:
    causal_path     = ["shopsrv.py", "common/util.py", ...]   files the real fix depends on
    hidden_literals = ["PAYMENTS_SIGNING_KEY", ...]           must not appear literally in code
"""

import re
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".go", ".rs", ".java", ".rb", ".sql", ".sh", ".json", ".yaml", ".yml",
                 ".toml"}
STOP = {"the", "and", "for", "with", "that", "this", "from", "your", "must", "only", "not", "are", "was", "has",
        "prod", "production", "staging", "service", "web"}


def brief_tokens(text: str) -> list[str]:
    toks = set()
    toks.update(re.findall(r"`([^`\n]{3,80})`", text))
    toks.update(re.findall(r"\b[A-Z][A-Z0-9_]{3,}\b", text))
    toks.update(re.findall(r"(?<![\w.])/[a-z0-9_\-/]{3,}", text))
    toks.update(re.findall(r'"([^"\n]{3,60})"', text))
    toks.update(re.findall(r"\b[a-z0-9_]+(?:\.[a-z0-9_]+)+\b", text))
    out = []
    for t in toks:
        t = t.strip()
        if len(t) >= 3 and t.lower() not in STOP and not t.startswith("http"):
            out.append(t)
            for part in re.split(r"[\s?=&]+", t):  # `GET /checkout/quote?cart=demo` -> its parts
                if len(part) >= 4 and part != t and part.lower() not in STOP:
                    out.append(part)
    return sorted(set(out))


def code_files(repo: Path):
    for p in sorted(repo.rglob("*")):
        if p.is_file() and p.suffix in CODE_SUFFIXES and ".git" not in p.parts:
            yield p


@dataclass
class Result:
    tokens: list[str]
    ranking: list[tuple[str, int]]
    top_k: list[str]
    causal_found: list[str]
    causal_missed: list[str]
    hidden_literal_hits: dict[str, list[str]]

    @property
    def shallow(self) -> bool:
        return not self.causal_missed or bool(self.hidden_literal_hits)


def localise(task: Path, k: int = 3) -> Result:
    meta = tomllib.loads((task / "task.toml").read_text()).get("metadata", {})
    repo = task / "environment" / "repo"
    tokens = brief_tokens((task / "instruction.md").read_text())
    scores = Counter()
    texts = {p: p.read_text(errors="replace") for p in code_files(repo)}
    for p, text in texts.items():
        low = text.lower()
        for t in tokens:
            n = low.count(t.lower())
            if n:
                scores[str(p.relative_to(repo))] += n * (3 if t.isupper() or "/" in t else 1)
    ranking = scores.most_common()
    top_k = [f for f, _ in ranking[:k]]
    causal = meta.get("causal_path", [])
    hidden = {lit: [str(p.relative_to(repo)) for p, text in texts.items() if lit in text]
              for lit in meta.get("hidden_literals", [])}
    return Result(tokens, ranking, top_k, [c for c in causal if c in top_k], [c for c in causal if c not in top_k],
                  {lit: files for lit, files in hidden.items() if files})
