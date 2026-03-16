# Quality checks

# app/evaluator.py
def evaluate_answer(answer: str) -> float:
    """Very simple heuristic score (0..1). Replace with better eval later."""
    score = 1.0
    if "I don't know" in answer:
        score -= 0.3
    if len(answer.strip()) < 50:
        score -= 0.2
    return round(max(score, 0.0), 2)
