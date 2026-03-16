# LangGraph agent

# app/agents.py
from __future__ import annotations

from typing_extensions import TypedDict

from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI

from app.tools import TOOLS
from app.rag_pipeline import query_docs
from app.evaluator import evaluate_answer
from app.observability import log_event
from app.config import settings

llm = ChatOpenAI(model=settings.CHAT_MODEL, temperature=0)

class State(TypedDict, total=False):
    question: str
    context: str
    tool_choice: str | None
    tool_output: str
    final_answer: str
    score: float

async def retrieve(state: State) -> State:
    docs = await query_docs(state["question"])
    state["context"] = "\n\n".join([d.page_content for d in docs])
    log_event("retrieval", {"k": len(docs)})
    return state

def decide_tool(state: State) -> State:
    q = (state.get("question") or "").lower()

    if any(w in q for w in ["regulation", "ordnung", "statute", "policy", "§", "art."]):
        state["tool_choice"] = "summarize_regulation"
    elif any(w in q for w in ["study plan", "curriculum", "module", "ects", "semester"]):
        state["tool_choice"] = "extract_studyplan_facts"
    else:
        state["tool_choice"] = None
    return state

def call_tool(state: State) -> State:
    choice = state.get("tool_choice")
    if not choice:
        return state
    tool_fn = TOOLS[choice]
    state["tool_output"] = str(tool_fn(state.get("context", "")))
    log_event("tool_call", {"tool": choice})
    return state

async def generate_answer(state: State) -> State:
    prompt = f"""You are a helpful assistant for regulations and study plan PDFs.
Use ONLY the provided context. If the context does not contain the answer, say you don't know.

Question:
{state['question']}

Context:
{state.get('context','')}

Optional extracted tool output:
{state.get('tool_output','')}

Write a concise answer. When you quote or reference something, cite it informally (e.g., "… (from the uploaded PDF)").
"""
    resp = await llm.ainvoke(prompt)
    state["final_answer"] = resp.content
    return state

def evaluate(state: State) -> State:
    state["score"] = evaluate_answer(state.get("final_answer", ""))
    log_event("final_score", {"score": state["score"]})
    return state

# Build graph
graph = StateGraph(State)
graph.add_node("retrieve", retrieve)
graph.add_node("decide_tool", decide_tool)
graph.add_node("call_tool", call_tool)
graph.add_node("generate_answer", generate_answer)
graph.add_node("evaluate", evaluate)

graph.set_entry_point("retrieve")
graph.add_edge("retrieve", "decide_tool")
graph.add_edge("decide_tool", "call_tool")
graph.add_edge("call_tool", "generate_answer")
graph.add_edge("generate_answer", "evaluate")
graph.add_edge("evaluate", END)

smartdocs_agent = graph.compile()
