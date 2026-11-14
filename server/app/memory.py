from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select

from .database import SessionLocal
from .models import Memory


# ---------------- BM25 检索 ----------------

def _tokenize(text: str) -> list[str]:
    """中文按字 + 相邻 bigram，英文按小写空格分词。"""
    tokens: list[str] = []
    # 连续 ASCII 字母数字归为一个英文 token
    buffer: list[str] = []
    def flush_english() -> None:
        nonlocal buffer
        if buffer:
            word = "".join(buffer).lower()
            if word:
                tokens.append(word)
            buffer.clear()
    for ch in text:
        if ch.isascii() and ch.isalnum():
            buffer.append(ch)
        else:
            flush_english()
            if ch.strip():
                tokens.append(ch)
    flush_english()
    # 字 bigram（中文 token 是单字）
    chinese_tokens = [t for t in tokens if len(t) == 1 and not t.isascii()]
    for i in range(len(chinese_tokens) - 1):
        tokens.append(chinese_tokens[i] + chinese_tokens[i + 1])
    return tokens


class Bm25Index:
    """纯 Python BM25，不依赖任何第三方库。"""

    k1 = 1.2
    b = 0.75

    def __init__(self, docs: list[str]) -> None:
        self.docs = docs
        self.doc_count = len(docs)
        # 每个 doc 的 tf Counter
        self.doc_tfs: list[Counter[str]] = [Counter(_tokenize(d)) for d in docs]
        # df: 每个 token 出现在多少个 doc 里
        self.df: Counter[str] = Counter()
        for tf in self.doc_tfs:
            for token in tf:
                self.df[token] += 1
        self.avgdl = (
            sum(len(tf) for tf in self.doc_tfs) / self.doc_count if self.doc_count else 0
        )

    def score(self, query: str, index: int) -> float:
        if self.doc_count == 0:
            return 0.0
        tokens = _tokenize(query)
        if not tokens:
            return 0.0
        tf = self.doc_tfs[index]
        dl = len(tf)
        score = 0.0
        for token in tokens:
            f = tf.get(token, 0)
            if f == 0:
                continue
            df = self.df.get(token, 0)
            if df == 0:
                continue
            idf = math.log((self.doc_count - df + 0.5) / (df + 0.5) + 1)
            denom = f + self.k1 * (1 - self.b + self.b * dl / max(self.avgdl, 1))
            score += idf * f * (self.k1 + 1) / denom
        return score

    def rank(self, query: str) -> list[tuple[int, float]]:
        scored = [(i, self.score(query, i)) for i in range(self.doc_count)]
        return sorted(scored, key=lambda pair: pair[1], reverse=True)


# ---------------- 注入段格式化 ----------------

BUDGET_CHARS = 2200       # kb 实战硬约束：预算要硬，超了整条丢弃
PER_ITEM_CHARS = 200      # 单条记忆上限
MAX_ITEMS = 12


def _truncate_fact(fact: str) -> str:
    fact = fact.strip()
    # 过滤掉 bullet 前缀、JSON 尾巴
    fact = re.sub(r"^[-\*•●]\s*", "", fact)
    fact = re.sub(r"\{[^}]*\}", "", fact).strip()
    if len(fact) > PER_ITEM_CHARS:
        fact = fact[: PER_ITEM_CHARS - 1] + "…"
    return fact


def build_memory_prompt(user_id: int, query: str) -> str | None:
    """按 user_id 取记忆 → BM25 召回 → 预算控制 → 拼成 system prompt 段。"""
    with SessionLocal() as db:
        rows = db.execute(
            select(Memory).where(Memory.user_id == user_id).order_by(Memory.created_at.desc())
        ).scalars().all()
    if not rows:
        return None

    facts = [_truncate_fact(row.fact) for row in rows if row.fact]
    docs = facts
    index = Bm25Index(docs)
    ranked = index.rank(query)

    picks: list[tuple[int, str]] = []
    for idx, score in ranked:
        if score <= 0 and picks:
            # 相关性为 0 后不再加
            break
        if len(picks) >= MAX_ITEMS:
            break
        fact = docs[idx]
        if fact in {f for _, f in picks}:
            continue
        picks.append((idx, fact))

    # 预算硬控制：按顺序累加字符，超了整条丢弃（不截断）
    section_lines = ["【长期记忆】"]
    total = sum(len(line) + 1 for line in section_lines)
    final_picks: list[str] = []
    for idx, fact in picks:
        line = f"- {fact}"
        if total + len(line) + 1 > BUDGET_CHARS:
            break
        section_lines.append(line)
        total += len(line) + 1
        final_picks.append(fact)

    if len(section_lines) <= 1:
        return None

    # 结尾放命中数（kb 教训：计数放末尾，不影响前缀缓存）
    section_lines.append(f"（共 {len(rows)} 条，本轮命中 {len(final_picks)} 条）")
    return "\n".join(section_lines)


# ---------------- 蒸馏 ----------------

DISTILL_PROMPT = """\
你是记忆提炼器。阅读下面一轮对话，提取不超过 3 条长期事实，每条一行，≤60 字：
- 用户身份/偏好/背景事实
- 明确的承诺/决定
- 重要的约束/禁忌
忽略临时情绪、寒暄、具体数字、一次性安排。

对话：
用户: {user}
AI: {assistant}

输出（每条一行，纯文本，不要 bullet，不要空行）："""


def _parse_distill(text: str) -> list[str]:
    """把蒸馏输出解析成干净的事实列表。"""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # 去掉模型有时加的 bullet 编号前缀
        line = re.sub(r"^[\d\.\-•●\*]\s*", "", line).strip()
        if not line:
            continue
        # 过滤掉模型偶尔吐出的 JSON 片段（kb 教训）
        if line.startswith("{") or line.startswith("[") or line.endswith("}") or line.endswith("]"):
            continue
        if len(line) > PER_ITEM_CHARS:
            line = line[: PER_ITEM_CHARS - 1] + "…"
        lines.append(line)
    # 去重（保持顺序）
    seen: set[str] = set()
    unique: list[str] = []
    for l in lines:
        if l not in seen:
            seen.add(l)
            unique.append(l)
    return unique


async def distill_round(llm, user_text: str, assistant_text: str) -> list[str]:
    """用 LLM 做一轮记忆蒸馏。同步调用（非 fire-and-forget 场景）。"""
    prompt = DISTILL_PROMPT.format(user=user_text[:1000], assistant=assistant_text[:2000])
    # 蒸馏独立于 history，只用 system + 本轮 prompt
    pieces: list[str] = []
    async for piece in llm.stream(user_text=prompt, history=[], extra_system=None):
        pieces.append(piece)
    raw = "".join(pieces).strip()
    return _parse_distill(raw)


def persist_facts(user_id: int, facts: list[str]) -> int:
    """把蒸馏出的事实写入 Memory 表（去重：和已存在的比较完全匹配）。返回新增条数。"""
    if not facts:
        return 0
    added = 0
    with SessionLocal() as db:
        existing = set(
            row[0]
            for row in db.execute(
                select(Memory.fact).where(Memory.user_id == user_id)
            ).all()
        )
        for fact in facts:
            if fact in existing:
                continue
            db.add(Memory(user_id=user_id, fact=fact, source="chat"))
            existing.add(fact)
            added += 1
        if added:
            db.commit()
    return added


# 蒸馏一次的字符开销估算（prompt 模板 + 用户 ≤1000 + 回复 ≤2000），供成本围栏旁路预算用。
DISTILL_COST_CHARS = 3200


async def distill_background(
    llm,
    user_text: str,
    reply: str,
    user_id: int,
    guard=None,
    budget_key: str | None = None,
) -> None:
    """回复后异步蒸馏：不阻塞用户，失败静默。供 chat.py 和 websocket.py 复用。

    成本围栏 g4：小时预算吃紧时跳过蒸馏（省下一次附加 LLM 调用）。
    """
    import logging
    log = logging.getLogger(__name__)
    try:
        if guard is not None and budget_key and not guard.allow_side_call(
            budget_key, DISTILL_COST_CHARS
        ):
            log.info("成本围栏：跳过用户 %s 的记忆蒸馏（小时预算吃紧）", user_id)
            return
        facts = await distill_round(llm, user_text, reply)
        if facts:
            persist_facts(user_id, facts)
        if guard is not None and budget_key:
            guard.record_side_call(budget_key, DISTILL_COST_CHARS)
    except Exception:
        log.exception("memory distill failed for user %s", user_id)
