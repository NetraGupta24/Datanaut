"""Groq tool-calling loop: the model picks calculations, pandas runs them."""

import json
import logging
from dataclasses import dataclass, field

import groq
import pandas as pd

from .profiling import format_profile
from .tools import run_tool, tool_specs

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4
MAX_TOOL_CALLS_PER_ROUND = 6
MAX_HISTORY_TURNS = 10
MAX_HISTORY_CHARS = 4000

SYSTEM_PROMPT = """You are DATANAUT's data analyst. You answer questions about one dataset named "{name}".

Dataset profile (computed with pandas):
{profile}

Rules:
- Use only numbers from the profile or tool results; if they don't cover the question, say so.
- For any total, average, count, ranking, comparison, trend, correlation, outlier or filtered figure that the profile does not already state, call a tool. Do not estimate or compute numbers yourself.
- For differences or ratios between two groups, use the compare tool rather than subtracting or dividing tool results yourself.
- Don't add units or currency symbols that aren't in the data.
- Use exact column names from the profile.
- Keep answers short: a few sentences or a brief "-" bullet list. Mention the filters and aggregation you used.
- Format as plain text. **Bold** is fine; no Markdown tables or headings (the chat widget shows tool tables separately).
- When the user asks for a chart, graph or plot, call create_chart once. The chat shows the chart with an "Add to dashboard" button, so describe it in a sentence and don't explain how to build it by hand.
- If a tool returns an error, fix the arguments and try again, or explain what is missing.
- If the question is not about this dataset, say what you can help with instead."""


class ChatServiceError(Exception):
    """The LLM provider failed; `status` and message are returned to the client."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class ToolCallRejected(Exception):
    """Groq refused the model's tool call (arguments not matching the schema, or a tool when none was allowed)."""


def _groq_error_message(exc: groq.APIStatusError) -> str:
    body = exc.body if isinstance(exc.body, dict) else {}
    return str(body.get("message") or exc)[:500]


@dataclass
class ChatResult:
    answer: str
    evidence: list[dict] = field(default_factory=list)
    chart: dict | None = None  # create_chart config (no datasetId yet)


def _fallback_answer(chart: dict | None, evidence: list[dict]) -> str:
    if chart:
        return "Here's the chart you asked for."
    if evidence:
        return "I couldn't write a summary, but the results I calculated are below."
    return "I couldn't produce an answer. Please rephrase the question."


def build_messages(name: str, profile: dict, history: list[dict], message: str) -> list[dict]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(name=name, profile=format_profile(profile))}]
    for turn in history[-MAX_HISTORY_TURNS:]:
        content = (turn.get("content") or "").strip()
        if turn.get("role") in ("user", "assistant") and content:
            messages.append({"role": turn["role"], "content": content[:MAX_HISTORY_CHARS]})
    messages.append({"role": "user", "content": message})
    return messages


class ChatEngine:
    def __init__(self, client, model: str, temperature: float = 0.2, max_tokens: int = 1500):
        self.client = client
        self.model = model
        self.temperature = temperature
        # Reasoning models spend part of this budget thinking, so it is larger than the answer needs
        self.max_tokens = max_tokens

    def _complete(self, messages: list[dict], allow_tools: bool):
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_completion_tokens": self.max_tokens,
        }
        if self.model.startswith("openai/gpt-oss"):
            kwargs["reasoning_effort"] = "low"
        if allow_tools:
            kwargs.update(tools=tool_specs(), tool_choice="auto")
        try:
            return self.client.chat.completions.create(**kwargs)
        except groq.RateLimitError as exc:
            raise ChatServiceError(503, "The AI service is busy right now. Please try again in a minute.") from exc
        except groq.APITimeoutError as exc:
            raise ChatServiceError(504, "The AI service took too long to answer. Please try again.") from exc
        except groq.AuthenticationError as exc:
            logger.error("Groq rejected GROQ_API_KEY: %s", exc)
            raise ChatServiceError(502, "The AI service rejected the server's API key.") from exc
        except groq.NotFoundError as exc:
            logger.error("Groq model %r is not available: %s", self.model, exc)
            raise ChatServiceError(502, f"The AI model '{self.model}' isn't available. Set GROQ_MODEL in chatbot-service/.env.") from exc
        except groq.BadRequestError as exc:
            if "tool" in str(exc).lower():
                logger.warning("Groq rejected a tool call: %s", exc)
                raise ToolCallRejected(_groq_error_message(exc)) from exc
            raise ChatServiceError(502, "The AI service rejected the request.") from exc
        except (groq.APIConnectionError, groq.APIStatusError) as exc:
            logger.warning("Groq request failed: %s", exc)
            raise ChatServiceError(502, "Couldn't reach the AI service. Please try again.") from exc

    def answer(self, df: pd.DataFrame, name: str, profile: dict, history: list[dict], message: str) -> ChatResult:
        messages = build_messages(name, profile, history, message)
        evidence: list[dict] = []
        chart = None

        for _ in range(MAX_TOOL_ROUNDS):
            try:
                response = self._complete(messages, allow_tools=True)
            except ToolCallRejected as exc:
                # No tool_call_id to answer, so tell the model in a user turn and let it try again
                messages.append(
                    {"role": "user", "content": f"Your tool call was rejected: {exc}. Call the tool again with valid arguments."}
                )
                continue
            reply = response.choices[0].message
            tool_calls = list(reply.tool_calls or [])[:MAX_TOOL_CALLS_PER_ROUND]
            if not tool_calls:
                answer = (reply.content or "").strip() or _fallback_answer(chart, evidence)
                return ChatResult(answer=answer, evidence=evidence, chart=chart)

            messages.append(
                {
                    "role": "assistant",
                    "content": reply.content or "",
                    "tool_calls": [
                        {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                        for c in tool_calls
                    ],
                }
            )
            for call in tool_calls:
                args, result = run_tool(df, call.function.name, call.function.arguments)
                # The chart config goes to the client, not back to the model; the latest chart wins
                chart = result.pop("chart", None) or chart
                if args is not None:
                    evidence.append({"tool": call.function.name, "args": args, "result_table": result})
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False, default=str)}
                )

        # Out of tool rounds: ask for a final answer from what we have
        messages.append(
            {
                "role": "user",
                "content": "Answer now in plain text using only the tool results above, without calling tools. "
                "If they are not enough, say so.",
            }
        )
        try:
            response = self._complete(messages, allow_tools=False)
            content = (response.choices[0].message.content or "").strip()
        except ToolCallRejected:
            content = ""  # gpt-oss sometimes reaches for its built-in python tool here
        return ChatResult(answer=content or _fallback_answer(chart, evidence), evidence=evidence, chart=chart)


def make_groq_client(api_key: str, timeout_s: float):
    return groq.Groq(api_key=api_key, timeout=timeout_s, max_retries=1)
