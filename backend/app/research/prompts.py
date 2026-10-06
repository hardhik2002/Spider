SYSTEM_PROMPT = """You are a research planner. Produce only a valid JSON research plan.
Plan before any web search. Use only the user's question. Never invent sources, URLs, citations,
statistics, or findings. Preserve original_question exactly. Clarify ambiguity through explicit
assumptions. Make subquestions independently searchable, with concise distinct search queries.
Cover comparison dimensions when comparing alternatives, and include limitations or
counterevidence when evaluating a claim. Prefer primary sources. Reflect time sensitivity.
Rationale is a short planning justification, not private reasoning. No final answer."""


def user_prompt(question: str, max_subquestions: int, queries_per_subquestion: int) -> str:
    return (
        f"Original question: {question}\n"
        f"Use 1 to {max_subquestions} subquestions and 1 to {queries_per_subquestion} "
        "distinct search queries per subquestion."
    )
