"""Question answering: a fixed two-step workflow built for small local models.

1. Route   (no LLM)  BM25 picks the candidate documents and, for large documents,
                     the relevant part of their table of contents.
2. Select  (LLM)     The model reads the table of contents and returns up to 3
                     section ids. Ids are validated; one retry if none are valid.
3. Answer  (LLM)     The model reads only those sections and answers, citing the
                     section ids it used. Citations are resolved by us, not the model.

Every model call returns small, shallow JSON validated by Pydantic, and each run
is capped, so a small model cannot loop.
"""
from __future__ import annotations

import logging
import re
import time
from difflib import get_close_matches
from types import SimpleNamespace
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

import openai
from pydantic import BaseModel, BeforeValidator, Field
from pydantic_ai import (Agent, ModelAPIError, ModelHTTPError, ModelRetry, NativeOutput, PromptedOutput,
                         RunContext, ToolOutput, UnexpectedModelBehavior, UsageLimits)
from pydantic_ai.models import Model

from .evidence import quotes
from .focus import _VARIANT as _VARIANT_WORD, Focus, heading, named_one, near as near_place, short_text, variants
from .llm import QUOTA_MESSAGE, BudgetExceeded, OutputMode, RequestBudget, describe_error, quota_exhausted, run_settings
from .models import Doc
from .numbers import numbers_in, unverified_numbers
from .doctools import excerpt, find_facts, partial_table_note, ranking_words, scored_table_cell, statements, table_cell, table_highlights
from .loops import Looping, repeating_from, without_repeated_sentences
from .plan import follow_up, language_of, parts_note, question_parts, starts_fresh
from .support import _ASKING, made_up, only_unchecked_figures, stated_as_is, wrong_kind_of_figure
from .embeddings import SemanticIndex, union
from .search import Hit, SectionIndex, is_code, tokenize

log = logging.getLogger("oasis_engine.pipeline")

MAX_SECTIONS = 3
MAX_DOCS = 3
OUTLINE_BUDGET_CHARS = 14_000
SUMMARY_CHARS = 350
PRUNED_HITS_PER_DOC = 30
MEANING_HITS = 5             # sections found by meaning that steer which documents are shown
NO_MATCH_MEANING = 0.35      # below this (and no keyword match) a question is about none of the documents
EXCERPT_CHARS = 9_000
FAST_PATH_RATIO = 2.0        # keyword score over the best unrelated section, to skip selection
FAST_PATH_CHILD_SHARE = 0.6  # a subsection this close to its parent's score is preferred
# Ask which manual is meant when another manual's best keyword match scores this close to the top
# one and the question matches the same words in both (nothing in it tells them apart).
CLARIFY_SHARE = 0.7
MAX_CLARIFY_OPTIONS = 4
CLARIFY_PROMPT = "Several documents cover this. Which one do you mean?"
CLARIFY_ALIKE = 0.5  # word overlap above which two sections are versions of the same text
# Output caps per step for the local model (reasoning off). Real replies are far shorter (a few ids;
# a short answer); a reply cut off at its cap is a small model stuck repeating itself, and is stopped
# rather than retried. Reasoning models count their thinking as output and need far larger caps.
SELECT_MAX_TOKENS = 300
ANSWER_MAX_TOKENS = 800
THINKING_MAX_TOKENS = 6000  # a step's cap once the model is seen thinking (config.REASONING_TOKENS)
TOTAL_EXCERPT_CHARS = 16_000  # all excerpts together must fit the model's context (8k tokens)
NOT_FOUND = "The documents don't cover this."
PART_SECTIONS = 2  # sections read for each part of a question answered part by part
_TYPO_EXAMPLE = "wat dose the amber led meen"  # in ANSWER_INSTRUCTIONS
_EXAMPLE_WORDS = frozenset(tokenize(_TYPO_EXAMPLE)) - {"the"}


def _foreign_words(answer: str, source: str) -> bool:
    """The answer copies the examples in the model's own instructions ("wat dose the amber led meen"):
    two or more of their words that neither the excerpts nor the question have."""
    copied = set(tokenize(answer)) & _EXAMPLE_WORDS
    return len(copied - set(tokenize(source))) >= 2


_LANGUAGE_LINE = re.compile(r"\s*\(Write the answer in [A-Za-z ]{2,30}\.\)")


def _echoed_question(answer: str, question: str) -> bool:
    """The answer is the question written back (with the instruction after it, or without)."""
    def plain(s: str) -> str:
        return " ".join(re.sub(r"[^\w]+", " ", _LANGUAGE_LINE.sub("", s)).split()).casefold()
    return bool(answer.strip()) and plain(answer) == plain(question)


_CODE_WORD = re.compile(r"\b(?=[A-Z0-9_-]*[A-Z])(?=[A-Z0-9_-]*[_\d-])[A-Z][A-Z0-9]*(?:[_-][A-Z0-9]+)+\b")  # RLY_OB, HX9-4471


def _close(a: str, b: str) -> bool:
    """One character changed, or two next to each other swapped."""
    if len(a) != len(b) or a == b:
        return False
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    if any(a[i].isdigit() and b[i].isdigit() for i in diff):
        return False  # HX9-4471 and HX9-4472 are two parts, not one misspelled
    return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]]
                                and a[diff[1]] == b[diff[0]])


def _codes_as_written(answer: str, source: str) -> str:
    """Codes in the answer spelled as the source spells them, when exactly one source code is
    one letter off and the answer's spelling is nowhere in the source."""
    written = set(_CODE_WORD.findall(source))
    for code in set(_CODE_WORD.findall(answer)):
        if code in written:
            continue
        near = [w for w in written if _close(code, w)]
        if len(near) == 1:
            answer = re.sub(rf"\b{re.escape(code)}\b", near[0], answer)
    return answer


_ASKS_TWO = re.compile(r"\w\s+and\s+(?:how|what|which|at what|when|where|who)?\s*\w", re.IGNORECASE)  # "flash and SRAM"
# a part that points back at what an earlier part's answer named ("the replacement part", "its price")
_REFERS_BACK = re.compile(r"\b(it|its|that|this|these|those|they|them|replacement|same)\b", re.IGNORECASE)
_ANSWER_CODE = re.compile(r"\b[A-Za-z]{1,6}\d{0,3}-\d[\w-]*\b")  # "HX9-4471", "C-2087"
# words a statement need not repeat to answer the question ("the limit for encashment")
STATED_GENERIC = frozenset("limit limits maximum minimum amount number count value size total period duration "
                           "between about from into with within after before during what was were".split())
_COMPARING = re.compile(r"\b(more|less|fewer|greater|higher|lower|better|worse|best|worst|highest|lowest|most|least|"
                        r"compar\w*|vs\.?|versus)\b", re.IGNORECASE)
_YES = re.compile(r"\W*yes\b", re.IGNORECASE)
# a rule that forbids ("may not drive", "is not reimbursed", "must not be loaded"), and its words in an answer
_NEGATED = re.compile(r"\b(not|cannot|can't|never|no one|nobody|prohibited|forbidden|isn't|aren't|don't|doesn't)\b", re.IGNORECASE)
PROHIBIT_WEIGHT = 1.3
# a follow-up that only makes sense with the question before it ("and at shutdown?", "what about M10?")
_ELLIPTICAL = re.compile(r"^\W*(?:and|but|or|so|what about|how about)\b", re.IGNORECASE)
_QUESTION_SHAPE = re.compile(r"\b(what|which|who|whom|whose|when|where|why|how|is|are|was|were|do|does|did|can|could|"
                             r"should|will|would|may|must|has|have|and|tell|show|explain|list|give)\b", re.IGNORECASE)
_PROHIBITS = re.compile(r"\b(cannot|can not|can't|not|never|only|prohibited|forbidden|no one|nobody)\b", re.IGNORECASE)
# a question over a whole table: highest, lowest, total, average, how many
_RANKS = re.compile(r"\b(highest|lowest|maximum|minimum|max|min|most|least|largest|smallest|peak|total|sum|"
                    r"average|mean|how many)\b", re.IGNORECASE)
_MORE = re.compile(r"\b(more|most|higher|highest|best|greater|greatest|larger|largest|longer|longest|bigger|biggest)\b",
                   re.IGNORECASE)
_LESS = re.compile(r"\b(less|least|fewer|fewest|lower|lowest|worst|smaller|smallest|shorter|shortest)\b", re.IGNORECASE)
_CHANGE = re.compile(r"\b(increase\w*|decrease\w*|grow\w*|growth|rise|rose|fall|fell|drop\w*|chang\w*)\b", re.IGNORECASE)
FULL_SECTION_CHARS = 400_000  # a section read whole for checks made in code
NAME_DOC_SHARE = 0.5  # a name in at most this share of the documents names one thing: answers about it must cite one
CODE_HOLDERS = 2  # sections holding a code the question names, read besides the model's choice ...
CODE_HOLDER_HITS = 10  # ... looked for among this many search hits
LINKED_CODES = 5  # lines added to a long excerpt for codes the other excerpts name ...
LINKED_LINE_CHARS = 300  # ... each at most this long
CITED_WORD_SHARE = 0.5  # a cited section holding the answer's figure keeps its place with this share of its words
CELL_MIN_SCORE = 2  # how strongly a question must name a table cell for it to be read in code
BARE_FIGURE_CHARS = 20  # an answer this short with a figure in it, part of a cell, is completed to the cell
WHOLE_CELL_CHARS = 200
BARE_WORDS = 3  # an answer this short ("No.", "Yes, it is.") is bare: the rule it rests on is added
RESCUE_SHARE = 0.9  # of an answer marked "not found" that must be the excerpts' own words to count
# what small models write in `answer` when they have none, even with found=true
# Greetings and chit-chat get a fixed reply: no model call, nothing searched, not saved to history.
_SMALLTALK = [
    (re.compile(r"(hi+|hello+|hey+|hiya|yo|good (morning|afternoon|evening|day)|greetings|namaste|hallo)"
                r"( there| lattice| oasis| assistant)?"),
     "Hello! I answer questions from the documents in this knowledge base, with the page or section each "
     "answer comes from. Ask about a procedure, a value, a date or a result."),
    (re.compile(r"(what can you do|who are you|what are you|help|how does (this|it) work|how do i use (this|you))"),
     "I find answers in the documents installed on this machine and show where each answer comes from. "
     "Ask a specific question, for example about a setting, a procedure or a result. I can also summarize "
     "a document or show its pictures. You can browse and search the documents under Manuals."),
    (re.compile(r"(thanks?( you)?( very much| a lot)?|thx|ty|cheers)"), "You're welcome."),
    # "nice work", "great job, thanks", "well done", "ok got it"
    (re.compile(r"((ok(ay)? )?((very |really |so )?(great|good|nice|awesome|excellent|perfect|cool|brilliant|super)"
                r"( work| job| answer| one| stuff)?|ok(ay)?|got it|understood|well done|thumbs up|that helps?)"
                r"( thanks?( you)?)?)"), "Glad it helped."),
    (re.compile(r"(bye|goodbye|see you|see ya)"), "Goodbye."),
    (re.compile(r"((hi|hey|hello) )?(how are you( doing| today)?|how's it going|how is it going|what's up|whats up|sup)"),
     "I'm doing well, thanks. What would you like to know from the documents?"),
]
ASK_A_QUESTION = "Ask me a question about the documents, for example about a procedure, a value or a result."
# replies that end a thought or answer chit-chat (not a greeting): Chat mode gives them without the
# model too; a greeting goes to a capable model, which can answer it in its own words
CLOSING_REPLIES = frozenset({"You're welcome.", "Glad it helped.", "Goodbye.", ASK_A_QUESTION,
                             "I'm doing well, thanks. What would you like to know from the documents?"})

# Requests answered the same way whatever the model: getting into devices or accounts without
# authorization (a small model "helpfully" answered "how can I break into a customer's router admin
# page without their password?"), and attempts to read or override the assistant's instructions.
_UNSAFE = re.compile(
    r"\b(break(ing)? into|hack(ing)?|crack(ing)?|brute[- ]?forc\w*|bypass\w*|get around|circumvent\w*|spy on|"
    r"steal\w*)\b.*\b(password|passcode|login|log-in|account|admin|router|device|system|security|network|"
    r"wi-?fi|camera|computer|e-?mail|phone)", re.IGNORECASE)
_INJECTION = re.compile(
    r"\b(ignore|disregard|forget|override)\b.{0,40}\b(instructions|rules|prompts?|guidelines|programming)\b|"
    r"\b(system|developer|hidden) prompt\b|\bjailbreak|\bdeveloper mode\b|"
    # "pretend you are DAN", "act as an unrestricted AI", "do anything now"
    r"\bpretend (you are|you're|to be)\b|\bact as (an? )?(unrestricted|unfiltered|uncensored)\b|"
    r"\bdo anything now\b|(?-i:\bDAN\b)|"
    r"\b(print|show|reveal|repeat|tell me|what are|list)\b.{0,20}\byour (instructions|prompts?|system prompt|rules|"
    r"tool ?list|tools|configuration|guidelines)\b", re.IGNORECASE)
UNSAFE_REPLY = ("I can't help with getting into devices, accounts or systems without authorization. If a customer "
                "is locked out, or you suspect unauthorized access, follow the documented procedure, for example "
                "the escalation for a suspected security issue.")
INJECTION_REPLY = "I can't change or share my instructions. Ask me a question about the documents."


def fixed_reply(question: str) -> str | None:
    """The reply to a request that is refused whatever the model, or None."""
    if _INJECTION.search(question):
        return INJECTION_REPLY
    if _UNSAFE.search(question):
        return UNSAFE_REPLY
    return None


_SUMMARY_WORDS = ("summary", "summarize", "summarise", "summarization", "overview", "outline", "gist",
                  "synopsis", "recap", "tldr")
_SUMMARY_PHRASE = re.compile(r"\b(what('s| is)|tell me) (this|the|that) (doc|document|manual|pdf|file) "
                             r"(is )?(about|cover|covers)\b|\bkey (points|topics)\b|\btl;?dr\b")
_ALL_MANUALS = re.compile(r"\b(all|every|each)\b.*\b(manuals?|docs?|documents?)\b|\b(manuals|documents|docs)\b")


def summary_request(question: str) -> str | None:
    """"summary" (one manual), "all" (every manual) or None when the question asks for no summary.
    Typos are tolerated ("sumary", "summery", "overveiw")."""
    text = question.lower()
    words = re.findall(r"[a-z;]+", text)
    close = any(w in _SUMMARY_WORDS or (len(w) >= 6 and get_close_matches(w, _SUMMARY_WORDS, n=1, cutoff=0.8))
                for w in words)
    if not close and not _SUMMARY_PHRASE.search(text):
        return None
    return "all" if _ALL_MANUALS.search(text) else "summary"


def smalltalk_reply(question: str) -> str | None:
    text = re.sub(r"[^\w\s']", " ", question.lower())
    text = " ".join(text.split())
    if not text:  # "?", "..."
        return ASK_A_QUESTION
    for pattern, reply in _SMALLTALK:
        if pattern.fullmatch(text):
            return reply
    return None


# "(Excerpt [A5])", "(see excerpt A5)", "excerpts [A2], [A3]": prompt plumbing, not for the operator
_EXCERPT_REF = re.compile(r"\s*\((?:see |from |in |per )?(?:the )?(?:excerpts?|sections?)\s*"
                          r"\[?[A-Z]-?\d+\]?(?:\s*(?:,|and)\s*\[?[A-Z]-?\d+\]?)*\)", re.IGNORECASE)


# no-break and thin spaces, non-breaking hyphens: larger models write "30 minutes", "HX‑500"
# (Nemotron on OpenRouter, 19 Oct 2026), which the checks against the documents' "HX-500" then miss
_TYPOGRAPHY = str.maketrans({" ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
                             "‑": "-", "‐": "-", "​": None, "﻿": None})


def plain_typography(text: str) -> str:
    return text.translate(_TYPOGRAPHY)


def _clean_answer(answer: str, ids: dict) -> str:
    answer = _EXCERPT_REF.sub("", plain_typography(answer))
    answer = re.sub(r"\s*\[([A-Z]-?\d+)\]", lambda m: "" if m.group(1) in ids else m.group(0), answer)
    return answer.strip()


_NO_ANSWER = re.compile(r"(none|n/?a|null|unknown|not (found|available|specified|mentioned|stated)|"
                        r"no (answer|information|data))[.!]?", re.IGNORECASE)
# ... or a one-sentence refusal: "The document does not contain information regarding 'budjet'."
_REFUSAL = re.compile(
    r"(i (could|can)(not|'t) find|there (is|are) no (information|mention|data|details)|"
    r"(the|these|this) (provided |given )?(documents?|manuals?|excerpts?|text|sources?|context|information)"
    r"[^.]{0,40}? (does|do)(n't| not) (contain|mention|cover|provide|include|specify|say|state|have))",
    re.IGNORECASE)


# "The FY2028 budget is not explicitly stated in the provided excerpts."
_NOT_IN_SOURCES = re.compile(
    r"\b(is|are)(n't| not) (explicitly |directly )?(stated|mentioned|given|provided|specified|present|included)"
    r" in the (provided |given )?(excerpts?|documents?|text|sources?|context)", re.IGNORECASE)


# an answer to a yes/no question: "No, do not ...", "Yes - it is covered"
_YES_NO = re.compile(r"\s*(no|yes)\b[,.;:!\s-]", re.IGNORECASE)
# "No", "Yes." alone, and a question it answers ("Can I ...?", "Is it ...?")
_BARE_YES_NO = re.compile(r"^\W*(no|yes)\b", re.IGNORECASE)
_YES_NO_QUESTION = re.compile(r"^\W*(can|could|is|are|was|were|do|does|did|should|may|must|will|would|has|have)\b",
                              re.IGNORECASE)
# the same after a statement: "I lost a receipt for INR 3,000, can I still claim it?"
_ASKS_YES_NO = re.compile(r"(^|[,;:.!?—–-]\s*)(can|could|is|are|do|does|should|may|will|would)\s+"
                          r"(i|we|you|they|he|she|it|this|that|a|an|the|my|our|contractors?|employees?)\b[^?]*\?",
                          re.IGNORECASE)


ECHO_SNIPPET_CHARS = 400


def _echoes(answer: str, question: str) -> bool:
    """The answer only repeats words of the question ("E-217?" -> "E-217"). Every word counts, also
    short ones: "Line A" to "Which lines met the target?" names a line (the search tokens drop "A",
    and the answer was taken for an echo and replaced by "not covered": seen in the final test)."""
    words = set(tokenize(answer))
    every = lambda t: set(re.findall(r"\w+", t.casefold()))  # noqa: E731
    return bool(words) and words <= set(tokenize(question)) and every(answer) <= every(question)


def _no_answer(answer: str) -> bool:
    """A reply that has no answer, however it is worded. A first sentence saying the documents lack
    it counts even when a guess follows ("... not stated. Based on +5.9%, about 430K.")."""
    text = answer.strip()
    if _NO_ANSWER.fullmatch(text):
        return True
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return _REFUSAL.match(first) is not None or _NOT_IN_SOURCES.search(first) is not None

SELECT_INSTRUCTIONS = (
    "You find where the answer to a question is located. You are given the table of contents "
    "of one or more documents. Each line is: [id] title (location): summary. Choose up to "
    f"{MAX_SECTIONS} ids of the sections most likely to contain the answer. Prefer specific, deeper "
    "sections over broad parent sections. Copy ids exactly as written inside the brackets. If no "
    "section could contain the answer, return an empty list."
)
ROUTE_INSTRUCTIONS = (
    "You sort requests sent to an assistant that answers from company documents (manuals, reports, "
    "notes, spreadsheets). Choose one kind:\n"
    "- question: asks for information found in the documents (a value, a procedure, what something means "
    "or how to do something), even when it mentions a diagram, chart, outline or summary as its subject, "
    "for example 'How do I reset the error summary?', 'What does the wiring diagram show?' or 'Is "
    "there a figure for the torque?' (a figure meaning a number)\n"
    "- pictures: wants to see pictures, images, diagrams, charts, graphs, drawings or photos from the "
    "documents, or asks whether there are any, for example 'show me the diagrams of the tool head', "
    "'graphs of pressure vs time' or 'is there a picture in the safety manual?'\n"
    "- summary: wants a summary, an overview or the key points of one manual, for example 'summarize "
    "this doc', 'what is this manual about?' or 'give me the key points of the safety manual'\n"
    "- all_manuals: asks which manuals there are, or for an overview of all of them\n"
    "topic: for pictures or summary, copy the words of the request that name a subject or a manual; "
    "leave it empty when the request names none (as in 'show me the diagrams' or 'summarize this doc')."
)
ROUTE_MAX_TOKENS = 80
SUMMARY_INSTRUCTIONS = (
    "You summarize a document (a manual, report, meeting notes or spreadsheet) for people at work. "
    "You are given its title, description and table of contents with a short summary of each "
    "section. Give an overview (one sentence on what the document covers) and 3 to 6 short points "
    "naming its main topics, procedures, results, decisions, settings or error codes, with key "
    "values where the summaries give them. Use only the "
    "information given; never add figures that are not in it."
)
FOLLOWUP_SELECT_INSTRUCTIONS = (
    "You find where the answer to a question is located. The question is part of a conversation and "
    "may depend on it (\"and for M10 bolts?\", \"what does it mean?\"). First write the question as a "
    "complete question that makes sense without the conversation, in standalone_question; if it "
    "already does, or it starts a new topic, copy it unchanged. Then, using the table of contents "
    "(each line is: [id] title (location): summary), choose up to "
    f"{MAX_SECTIONS} ids of the sections most likely to answer the standalone question. Prefer "
    "specific, deeper sections. Copy ids exactly as written inside the brackets. If no section "
    "could contain the answer, return an empty list."
)
MAX_CONTEXT_TURNS = 2
CONTEXT_ANSWER_CHARS = 400
# "1. Open ..." step numbering: not figures the answer gives
_LIST_NUMBERS = re.compile(r"(?m)^\s*(?:[-*]\s*)?\**\d{1,2}[.)]\**\s")
ANSWER_INSTRUCTIONS = (
    "You answer questions for factory staff using ONLY the provided excerpts. If the excerpts do "
    "not contain the answer, set found to false and say briefly that the documents do not cover it; "
    "never guess or use outside knowledge. Documents often refer to their subject indirectly (\"the "
    "Company\", \"the tool\", \"this unit\"); use the document descriptions to tell what they refer to. "
    "Likewise a table row answers the question when it is about the same thing in other words, and a "
    "section's figures are for the period or product its document covers (a Q3 review gives Q3 figures). "
    "Questions are often typed quickly with typos and abbreviations (\"wat dose the amber led meen\"); "
    "read them as the words they were meant to be. "
    "Keep the answer short and include exact figures, units, tolerances, part numbers and steps as "
    "written. A figure in a table takes its unit from the column header (under \"Cost ($K)\" it is "
    "thousands of dollars, written with K); do not convert units. Give a value only for the item the "
    "excerpt states it for; never repeat it for other items. You cannot draw charts: if asked for one, "
    "say so and give only the values the excerpts state. "
    "For an error code or fault, give its meaning and what to do. Write for the operator: "
    "do not mention excerpts, ids or the documents you were given, just answer. Answer in the language "
    "of the question, keeping figures, codes and part numbers exactly as written. List the ids of "
    "the excerpts you used in section_ids. If the answer is not stated in so many words but follows "
    "clearly from what the excerpts state (a stated rule or limit applied to the case asked about, or "
    "stated figures compared or ranked), it is an answer: give it with the stated fact it follows "
    "from, and set found and worked_out to true."
)


CUT_OFF_NOTE = "*(The answer was cut off. Ask about a narrower part for the rest.)*"


@dataclass
class _Salvaged:
    """What was written of a streamed answer that broke off, shaped like a finished run's result
    (.output, .usage.requests)."""
    output: Any
    usage: Any = field(default_factory=lambda: SimpleNamespace(requests=1))


class ProviderBusy(RuntimeError):
    """The model endpoint rate-limited the request (HTTP 429)."""


class ModelRefused(RuntimeError):
    """The model service turned the request down for good (a key it does not accept, a model it does
    not offer any more): not worth retrying, and the user can fix it in Manage."""


def refusal(e: ModelHTTPError) -> RuntimeError:
    """What to tell the user about a request the model service refused. OpenRouter withdrew a free
    model mid-test (404 "This model is unavailable for free"), which showed as a bare server error.
    An error of the provider behind the service ("Provider returned error", a 404 of Nvidia's own an
    hour after the same model answered) is passing: busy, try again."""
    if "provider returned error" in str(e.body).lower():
        return ProviderBusy("The model service's provider had an error; try again shortly")
    if e.status_code in (401, 403):
        return ModelRefused("The model service did not accept the API key. Check the connection in Manage.")
    if e.status_code == 404:
        return ModelRefused(f'The model service does not offer the model "{e.model_name}" (any more). '
                            "Choose another model in Manage.")
    return ModelRefused(f"The model service refused the request (error {e.status_code}). "
                        "Check the connection in Manage.")


class ModelRanAway(RuntimeError):
    """The model kept writing until its output cap (a repetition loop) instead of answering."""


ProgressFn = Callable[[dict], Awaitable[None]]


ID_CHARS, MAX_IDS = 24, 8


def _bounded_ids(value: Any) -> Any:
    """Cut an over-long id list short instead of rejecting it (see SectionIds)."""
    if isinstance(value, list):
        return [v[:ID_CHARS] if isinstance(v, str) else v for v in value[:MAX_IDS]]
    return value


# Bounded in the JSON schema, so a local model's grammar cannot loop inside the list: a small model
# once copied a whole excerpt header into section_ids until it hit its token cap.
SectionIds = Annotated[list[Annotated[str, Field(max_length=ID_CHARS)]],
                       Field(max_length=MAX_IDS), BeforeValidator(_bounded_ids)]


class SectionChoice(BaseModel):
    section_ids: SectionIds = Field(default_factory=list, description="ids from the table of contents, e.g. A12")


class FollowupChoice(BaseModel):
    standalone_question: str = Field(description="the question rewritten to make sense on its own")
    section_ids: SectionIds = Field(default_factory=list, description="ids from the table of contents, e.g. A12")


class Route(BaseModel):
    kind: Literal["question", "pictures", "summary", "all_manuals"]
    topic: str = Field(default="", description="for pictures or summary: the subject or manual named, else empty")


class SummaryDraft(BaseModel):
    overview: str = Field(description="one sentence: what the document covers")
    points: list[str] = Field(default_factory=list,
                              description="3 to 6 short points: main topics, procedures, settings, error codes")

    def text(self) -> str:
        points = [p.strip().lstrip("-*• ").strip() for p in self.points if p and p.strip()]
        unique: dict[str, str] = {}  # small models sometimes repeat a point
        for p in points:
            unique.setdefault(" ".join(p.casefold().split()), p)
        points = list(unique.values())
        bullets = "\n".join(f"- {p}" for p in points)
        return "\n\n".join(x for x in (self.overview.strip(), bullets) if x)


class DraftAnswer(BaseModel):
    found: bool = Field(description="true only if the excerpts contain the answer or it follows clearly "
                                    "from them (also when the answer is \"no\")")
    answer: str
    worked_out: bool = Field(default=False, description="true when the answer is not stated in the excerpts "
                                                       "but follows from them")
    section_ids: SectionIds = Field(default_factory=list, description="ids of the excerpts used, e.g. A12")


class Citation(BaseModel):
    doc_id: str
    doc_name: str
    node_id: str
    title: str
    locator: str
    pages: list[int] | None = None


class Evidence(BaseModel):
    text: str
    citation: int  # index into AskResult.citations


class Figure(BaseModel):
    citation: int            # index into AskResult.citations
    page: int | None = None  # the page it is on (of the original PDF, when image is None)
    image: str | None = None  # a picture extracted by the web platform (GET /api/docs/<id>/images/<image>)
    caption: str | None = None      # the picture's caption in the document ("Sprint burndown")
    description: str | None = None  # what the export says the picture shows


class ClarifyOption(BaseModel):
    label: str       # the manual's title (or, for a vague question, the section's)
    detail: str      # the section that matched, e.g. "Error E17"
    doc_ids: list[str]
    # a vague question's meaning, asked as a new question when picked ("What is the limit for
    # Inviting Members?"); without it, picking searches only these manuals (see vague.py)
    question: str | None = None
    # the section the option stands for (one manual's "Training" among several parts): its answer
    # is read from that section, not searched for again
    section: str | None = None


class Clarification(BaseModel):
    prompt: str
    options: list[ClarifyOption]


class AskResult(BaseModel):
    question: str
    found: bool
    answer: str
    citations: list[Citation] = []
    considered: list[Citation] = []
    seconds: dict[str, float] = {}
    model_requests: int = 0
    # figures in the answer that appear neither in its sources nor in the question (see numbers.py)
    unverified_numbers: list[str] = []
    # set by the API once the answer is saved to history; feedback refers to it
    history_id: str | None = None
    # a greeting or chit-chat answered with a fixed reply (no search, no model, no history)
    smalltalk: bool = False
    # a follow-up rewritten to stand on its own ("and for M10?" -> "What torque for M10 bolts?")
    standalone_question: str | None = None
    # "manuals": the cited two-step pipeline; "chat": the tool-using assistant (office profile)
    mode: Literal["manuals", "chat"] = "manuals"
    # the manual's own sentences backing the answer, picked without the model (see evidence.py)
    evidence: list[Evidence] = []
    # the question fits several manuals equally: ask which one instead of guessing
    clarify: Clarification | None = None
    # cited PDF pages that hold a figure (set by the API, which knows the original PDFs)
    figures: list[Figure] = []
    # the answer is only a list of pictures ("show me the diagrams"), found without the model
    pictures_only: bool = False
    # a summary of a whole manual (or of all of them), written as Markdown bullets
    summary: bool = False
    # chat mode: pages with pictures the agent asked to show; the API turns them into figures
    picture_pages: list[tuple] = Field(default_factory=list, exclude=True)
    # Chat mode: the answer, or a marked part of it, is general knowledge rather than the documents
    general: bool = False
    # Chat mode: attached files the answer was given (their names)
    files: list[str] = []
    # not stated in the documents in so many words, but worked out from what they state ("the limit
    # is 45 days, so 50 is not allowed"): shown as such, with the sections it follows from
    worked_out: bool = False
    # where the conversation is in the documents after this answer (see focus.py)
    focus: Focus | None = None


@dataclass
class PromptIds:
    """Short ids shown to the model (A12 = document A, node 0012) and what they point at."""
    by_id: dict[str, tuple[int, str]] = field(default_factory=dict)
    letters: dict[int, str] = field(default_factory=dict)
    picked: list[str] = field(default_factory=list)  # ids the model chose (vs. added by keyword search)

    def normalize(self, raw_ids: list, limit: int) -> list[str]:
        out: list[str] = []
        only_letter = next(iter(self.letters.values())) if len(self.letters) == 1 else None
        for raw in raw_ids:
            lead = re.match(r"\s*\[\s*([A-Za-z]-?[A-Za-z0-9]+)\s*\]", str(raw))  # "[A4] report.pdf > ..."
            s = lead.group(1).upper() if lead else re.sub(r"[\s\[\]()\"'`]", "", str(raw)).upper()
            cand = s
            if cand not in self.by_id:
                m = re.fullmatch(r"([A-Z])?-?0*(\d+)", s)
                if m and (m.group(1) or only_letter):
                    cand = f"{m.group(1) or only_letter}{int(m.group(2))}"
            if cand in self.by_id and cand not in out:
                out.append(cand)
            if len(out) == limit:
                break
        return out


def _stem(word: str) -> str:
    """A rough stem for matching ("tools" -> "tool", "loaded" -> "load", "terminate" -> "termin")."""
    for end in ("ation", "ing", "ate", "ed", "es", "s", "e"):
        if word.endswith(end) and len(word) - len(end) >= 3:
            return word[:-len(end)]
    return word


def _pages_note(doc: Doc, nid: str) -> str:
    """' (pages 3-4)' after an excerpt's heading path, when its pages are known."""
    s = doc.sections[nid]
    if not s.start_page:
        return ""
    return f" (page {s.start_page})" if s.end_page in (None, s.start_page) else f" (pages {s.start_page}-{s.end_page})"


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " ..."


def _short_id(letter: str, node_id: str) -> str:
    return f"{letter}{int(node_id)}" if node_id.isdigit() else f"{letter}-{node_id}".upper()


def _output(model_cls, mode: OutputMode):
    return {"native": NativeOutput, "tool": ToolOutput, "prompted": PromptedOutput}[mode](model_cls)


class Pipeline:
    def __init__(self, docs: list[Doc], model: Model, *, output_mode: OutputMode = "native",
                 budget: RequestBudget | None = None, max_tokens: int = 2048,
                 select_max_tokens: int = SELECT_MAX_TOKENS, answer_max_tokens: int = ANSWER_MAX_TOKENS,
                 fast_path: bool = True, may_think: bool = False):
        self.docs = docs
        # a model that thinks although asked not to: a step cut off at its cap is tried again with
        # room to think, and every step after it gets that room too
        self.may_think = may_think
        self.thinking = False
        self.index = SectionIndex(docs)
        self.fast_path = fast_path  # answer a clear keyword match in one model call
        self.budget = budget
        select_settings = {"temperature": 0.0, "max_tokens": min(max_tokens, select_max_tokens)}
        answer_settings = {"temperature": 0.0, "max_tokens": min(max_tokens, answer_max_tokens)}
        self.selector = Agent(model, output_type=_output(SectionChoice, output_mode), deps_type=PromptIds,
                              instructions=SELECT_INSTRUCTIONS, retries=1, model_settings=select_settings)
        self.followup_selector = Agent(model, output_type=_output(FollowupChoice, output_mode), deps_type=PromptIds,
                                       instructions=FOLLOWUP_SELECT_INSTRUCTIONS, retries=1,
                                       model_settings=select_settings)
        self.answerer = Agent(model, output_type=_output(DraftAnswer, output_mode), deps_type=PromptIds,
                              instructions=ANSWER_INSTRUCTIONS, retries=1, model_settings=answer_settings)
        self.router = Agent(model, output_type=_output(Route, output_mode), instructions=ROUTE_INSTRUCTIONS,
                            retries=1, model_settings={"temperature": 0.0,
                                                       "max_tokens": min(max_tokens, ROUTE_MAX_TOKENS)})
        self.summarizer = Agent(model, output_type=_output(SummaryDraft, output_mode), deps_type=PromptIds,
                                instructions=SUMMARY_INSTRUCTIONS, retries=1, model_settings=answer_settings)

        @self.selector.output_validator
        def _check_choice(ctx: RunContext[PromptIds], out: SectionChoice) -> SectionChoice:
            ids = ctx.deps.normalize(out.section_ids, MAX_SECTIONS)
            if out.section_ids and not ids:
                example = next(iter(ctx.deps.by_id))
                raise ModelRetry(f"None of {out.section_ids} are ids from the table of contents. "
                                 f"Copy ids exactly as shown in brackets, for example {example}.")
            return SectionChoice(section_ids=ids)

        @self.followup_selector.output_validator
        def _check_followup(ctx: RunContext[PromptIds], out: FollowupChoice) -> FollowupChoice:
            ids = ctx.deps.normalize(out.section_ids, MAX_SECTIONS)
            if out.section_ids and not ids:
                example = next(iter(ctx.deps.by_id))
                raise ModelRetry(f"None of {out.section_ids} are ids from the table of contents. "
                                 f"Copy ids exactly as shown in brackets, for example {example}.")
            return FollowupChoice(standalone_question=" ".join(out.standalone_question.split()), section_ids=ids)

        @self.answerer.output_validator
        def _check_answer(ctx: RunContext[PromptIds], out: DraftAnswer) -> DraftAnswer:
            ids = ctx.deps.normalize(out.section_ids, len(ctx.deps.by_id))
            if out.found and not ids:  # model forgot to cite: credit the excerpts it chose to read
                ids = ctx.deps.picked or list(ctx.deps.by_id)
            return DraftAnswer(found=out.found, answer=out.answer.strip(), section_ids=ids, worked_out=out.worked_out)

    # ── prompt building ──

    def _outline(self, question: str, allowed: set[int] | None = None,
                 meaning: list[Hit] = ()) -> tuple[str, PromptIds]:
        """The tables of contents of the documents that best match the question. meaning: sections
        found by meaning (best first); their documents come right after the best keyword match's."""
        hits = self.index.search(question)
        ids = PromptIds()
        blocks, used = [], 0
        by_keyword = self.index.rank_docs(question)
        order = [*by_keyword[:1], *(h.doc_index for h in meaning), *by_keyword[1:]] if hits else \
            [*(h.doc_index for h in meaning), *by_keyword]
        ranked = [di for di in dict.fromkeys(order) if allowed is None or di in allowed]
        found = union(hits, list(meaning), limit=len(hits) + len(meaning))
        for rank, di in enumerate(ranked[:MAX_DOCS]):
            doc, letter = self.docs[di], chr(ord("A") + rank)
            lines = self._doc_lines(doc, letter, None)
            if used + sum(map(len, lines)) > OUTLINE_BUDGET_CHARS:
                doc_hits = [h for h in found if h.doc_index == di][:PRUNED_HITS_PER_DOC]
                keep = {h.node_id for h in doc_hits}
                keep |= {a for nid in keep for a in self._ancestors(doc, nid)}
                lines = self._doc_lines(doc, letter, keep)
            if not lines or (blocks and used + sum(map(len, lines)) > OUTLINE_BUDGET_CHARS):
                break
            header = f"DOCUMENT {letter}: {doc.display_title}" + (f" - {doc.description}" if doc.description else "")
            blocks.append(header + "\n" + "\n".join(lines))
            used += sum(map(len, lines))
            ids.letters[di] = letter
            for nid in doc.sections:
                ids.by_id[_short_id(letter, nid)] = (di, nid)
        return "\n\n".join(blocks), ids

    def which_way(self, question: str, di: int, nid: str) -> Clarification | None:
        """A section the manual describes in several ways ("Adding Data": "Type 1", "Type 2"), asked
        without saying which: asked back, each option answered from its own subsection (see
        focus.variants). None when the question names one, or the section has no such choice."""
        doc = self.docs[di]
        kids = variants(doc, nid)
        if not kids or named_one(question, doc, kids):
            return None
        title = heading(doc.sections[nid].title)
        kind = _VARIANT_WORD.match(doc.sections[kids[0]].title).group(1).lower()
        asked = question.strip().rstrip("?").strip()
        return Clarification(
            prompt=f"The manual gives {len(kids)} {kind}s for {title}. Which one do you mean?",
            options=[ClarifyOption(label=heading(doc.sections[c].title), detail=short_text(doc, c),
                                   doc_ids=[doc.doc_id], question=f"{asked}: {heading(doc.sections[c].title)}?",
                                   section=c)
                     for c in kids[:MAX_CLARIFY_OPTIONS]])

    def _with_doc(self, ids: PromptIds, di: int) -> None:
        """Give a document an id letter, when the outline left it out."""
        if di in ids.letters:
            return
        letter = next(chr(c) for c in range(ord("A"), ord("Z") + 1) if chr(c) not in ids.letters.values())
        ids.letters[di] = letter
        for nid in self.docs[di].sections:
            ids.by_id[_short_id(letter, nid)] = (di, nid)

    async def read_place(self, question: str, di: int, nid: str, on_progress: ProgressFn | None = None,
                         read_as: str | None = None) -> AskResult:
        """"next" / "go back" (see focus.py): the section answered from directly, no search."""
        t0 = time.perf_counter()
        ids = PromptIds()
        self._with_doc(ids, di)
        winner = _short_id(ids.letters[di], nid)
        considered = [self._citation(di, nid)]
        if on_progress is not None:
            await on_progress({"stage": "reading", "sections": [c.model_dump() for c in considered]})
        asked = read_as or f"What does {self.docs[di].sections[nid].title} say?"
        fast = PromptIds(by_id={winner: ids.by_id[winner]}, letters=ids.letters, picked=[winner])
        result = await self._answer(asked, asked, fast, ids, considered, {"route": 0.0, "select": 0.0}, 0,
                                    None, on_progress)
        result.question = question
        if not result.found:  # a heading with little text: still the place the user stepped to
            result.citations = result.citations or considered
        result.seconds["total"] = round(time.perf_counter() - t0, 2)
        return result

    @staticmethod
    def _ancestors(doc: Doc, nid: str) -> list[str]:
        out, cur = [], doc.sections[nid].parent
        while cur is not None:
            out.append(cur)
            cur = doc.sections[cur].parent
        return out

    @staticmethod
    def _doc_lines(doc: Doc, letter: str, keep: set[str] | None) -> list[str]:
        lines = []
        for nid, s in doc.sections.items():
            if keep is not None and nid not in keep:
                continue
            summary = s.summary if len(s.summary) <= SUMMARY_CHARS else s.summary[:SUMMARY_CHARS] + "..."
            lines.append(f"{'  ' * s.depth}[{_short_id(letter, nid)}] {s.title} ({doc.locator(nid)}): {summary}")
        return lines

    def citation(self, di: int, nid: str) -> Citation:
        return self._citation(di, nid)

    def _citation(self, di: int, nid: str) -> Citation:
        doc, s = self.docs[di], self.docs[di].sections[nid]
        pages = list(range(s.start_page, s.end_page + 1)) if doc.kind == "pages" else None
        return Citation(doc_id=doc.doc_id, doc_name=doc.display_title, node_id=nid, title=s.title,
                        locator=doc.locator(nid), pages=pages)

    async def _run(self, agent: Agent, prompt: str, deps: PromptIds,
                   on_partial: Callable[[Any], Awaitable[None]] | None = None):
        """One model step. on_partial, if given, receives the partial output while it streams. A
        server model that spent the step's short cap thinking (Qwen 3.5 27B ignores "do not think"
        on some servers: every question was "stuck writing") gets the step again with room to think,
        and keeps that room for the steps after it."""
        try:
            return await self._run_once(agent, prompt, deps, on_partial)
        except (ModelRanAway, ProviderBusy) as e:
            cut_off = isinstance(e, ModelRanAway) or isinstance(e.__cause__, UnexpectedModelBehavior)
            if not (self.may_think and not self.thinking and cut_off):
                raise
            log.warning("model step cut off at its cap: the model thinks; trying again with %d tokens",
                        THINKING_MAX_TOKENS)
            self.thinking = True
            return await self._run_once(agent, prompt, deps, on_partial)

    async def _run_once(self, agent: Agent, prompt: str, deps: PromptIds,
                        on_partial: Callable[[Any], Awaitable[None]] | None = None):
        if self.budget:
            self.budget.ensure(1)
        last = None  # the latest partial output of a streamed step
        settings = run_settings()
        if self.thinking:
            settings = {**(settings or {}), "max_tokens": THINKING_MAX_TOKENS}
        try:
            async with agent.iter(prompt, deps=deps, model_settings=settings,
                                  usage_limits=UsageLimits(request_limit=2)) as run:
                async for node in run:
                    if on_partial is not None and Agent.is_model_request_node(node):
                        async with node.stream(run.ctx) as stream:
                            async for partial in stream.stream_output(debounce_by=0.15):
                                last = partial
                                await on_partial(partial)
                    # a reply cut off at its token cap: retrying would only loop again
                    elif Agent.is_call_tools_node(node) and node.model_response.finish_reason == "length":
                        raise ModelRanAway("The language model got stuck writing this answer")
            assert run.result is not None
            return run.result
        except (ModelHTTPError, ModelAPIError, openai.APIError, UnexpectedModelBehavior) as e:
            # the provider's own words go to the log; users get a short message
            log.warning("model call failed: %s", describe_error(e))
            # a streamed answer that ran out of room ("EOF while parsing a string"): what was written
            # is kept, marked as cut off, instead of an error after the user watched it appear
            if isinstance(e, UnexpectedModelBehavior) and isinstance(last, DraftAnswer) and last.answer.strip():
                return _Salvaged(DraftAnswer(
                    found=True, answer=last.answer.rstrip() + " ...\n\n" + CUT_OFF_NOTE,
                    section_ids=list(last.section_ids) or list(deps.picked) or list(deps.by_id)))
            if quota_exhausted(e):
                raise BudgetExceeded(QUOTA_MESSAGE) from e
            if isinstance(e, ModelHTTPError):
                if e.status_code == 429:
                    raise ProviderBusy("The model endpoint is rate-limited; try again shortly") from e
                if e.status_code == 503:  # llama-server answers 503 while it is still loading the model
                    raise ProviderBusy("The language model is still loading; try again shortly") from e
                if 400 <= e.status_code < 500:
                    raise refusal(e) from e
                raise
            if isinstance(e, ModelAPIError):  # connection refused or dropped: the server is down or restarting
                raise ProviderBusy("The language model is not responding; it may be restarting") from e
            if isinstance(e, openai.APIError):  # an error inside a streamed reply ("upstream overloaded")
                raise ProviderBusy("The language model service had an error; try again") from e
            # an empty or malformed reply, or unusable output twice
            raise ProviderBusy("The language model gave an unusable reply; try again") from e

    async def _select(self, agent: Agent, prompt: str, ids: PromptIds, fallback: BaseModel):
        """The selection step. An unusable choice (the 0.8B filled the ids with "DOCUMENT A:
        performance" twice, and the question failed as "busy": seen in the final test) falls back to
        the keyword search, which picks the sections next."""
        try:
            return await self._run(agent, prompt, ids)
        except ProviderBusy as e:
            if not isinstance(e.__cause__, UnexpectedModelBehavior):
                raise
            log.warning("selection step unusable, using the keyword search: %s", describe_error(e.__cause__))
            return _Salvaged(fallback)

    # ── public API ──

    async def route(self, question: str) -> Route:
        """What kind of request this is (a question, pictures, a summary, all manuals), decided by the
        model: keyword rules mistake "How do I reset the error summary?" for a summary request."""
        result = await self._run(self.router, f"REQUEST: {question}", PromptIds())
        return result.output

    def overview(self, question: str, allowed: set[int] | None, mode: str = "manuals") -> AskResult:
        """"Summarize all the manuals": one line per manual from the catalog, without the model."""
        docs = [(i, d) for i, d in enumerate(self.docs) if allowed is None or i in allowed]
        lines = [f"- **{d.display_title}**: {d.description or 'no description'}" for _, d in docs]
        return AskResult(question=question, found=True, mode=mode, summary=True, seconds={"total": 0.0},
                         answer=f"This knowledge base has {len(docs)} document{'s' if len(docs) != 1 else ''}:\n\n"
                                + "\n".join(lines))

    async def summarize(self, question: str, di: int, on_progress: ProgressFn | None = None,
                        mode: str = "manuals") -> AskResult:
        """A summary of one whole manual, written from its table of contents and section summaries."""
        t0 = time.perf_counter()
        doc = self.docs[di]
        depth_limit = max(s.depth for s in doc.sections.values()) if doc.sections else 0
        while True:  # a long manual: leave out the deepest levels until the outline fits
            lines = [f"{'  ' * s.depth}{s.title} ({doc.locator(s.node_id)}): "
                     f"{_short(s.summary, SUMMARY_CHARS)}" for s in doc.sections.values() if s.depth <= depth_limit]
            if sum(map(len, lines)) <= OUTLINE_BUDGET_CHARS or depth_limit == 0:
                break
            depth_limit -= 1
        outline = "\n".join(lines)[:OUTLINE_BUDGET_CHARS]
        top = min((s.depth for s in doc.sections.values()), default=0)
        chapters = [self._citation(di, s.node_id) for s in doc.sections.values() if s.depth == top][:8]
        if on_progress:
            await on_progress({"stage": "reading", "sections": [c.model_dump() for c in chapters]})

        async def on_partial(partial: SummaryDraft) -> None:
            if getattr(partial, "overview", ""):
                await on_progress({"stage": "draft", "text": partial.text()})

        draft = await self._run(
            self.summarizer, f"DOCUMENT: {doc.display_title}\nDESCRIPTION: {doc.description or 'none'}\n\n"
                             f"TABLE OF CONTENTS:\n{outline}\n\nREQUEST: {question}", PromptIds(),
            on_partial=on_partial if on_progress else None)
        text = draft.output.text()
        return AskResult(question=question, found=bool(text), mode=mode, summary=True,
                         answer=text or NOT_FOUND, citations=chapters,
                         unverified_numbers=unverified_numbers(text, doc.description + "\n" + outline, question),
                         model_requests=draft.usage.requests,
                         seconds={"total": round(time.perf_counter() - t0, 2)})

    async def warm_up(self) -> None:
        """One tiny request with the answer step's instructions, sent once the model server is up:
        the first question then doesn't pay for paging in the model's weights or reading those
        instructions (llama-server keeps them cached). Any failure is ignored."""
        try:
            await self.answerer.run("DOCUMENTS:", deps=PromptIds(), model_settings={"max_tokens": 1},
                                    usage_limits=UsageLimits(request_limit=1))
        except Exception:
            pass

    async def ask(self, question: str, on_progress: ProgressFn | None = None,
                  doc_filter: set[int] | None = None,
                  context: list[tuple[str, str]] | None = None, clarify: bool = True,
                  previous: list[tuple[str, str]] = (), semantic: SemanticIndex | None = None,
                  focus: tuple[int, str] | None = None, place: tuple[int, str] | None = None) -> AskResult:
        """Answer a question. on_progress, if given, receives {"stage": ...} events as work proceeds.
        doc_filter limits the search to these document indexes (e.g. one station's manuals).
        context: earlier (question, answer) turns of the same conversation, oldest first; the
        question may then be a follow-up, which the selection step rewrites to stand on its own.
        previous: (doc id, node id) of the sections the last answer cited; a follow-up reads the first.
        semantic: the search-by-meaning index, if one is set up and ready; else keywords only.
        clarify: when the question fits several manuals equally, return a Clarification instead
        of answering (False once the user has picked the manuals to search).
        focus: (document index, node id) of the section the user picked after a clarifying question:
        read first, as the one clear match; the full flow follows only if it does not answer.
        place: (document index, node id) of where the conversation is (see focus.py): a follow-up
        naming a section of that part of the manual ("How do I test it?") reads that section first."""
        async def progress(event: dict) -> None:
            if on_progress is not None:
                await on_progress(event)

        question = question.strip()
        t0 = time.perf_counter()
        reply = smalltalk_reply(question)
        if reply is not None:
            return AskResult(question=question, found=False, answer=reply, smalltalk=True, seconds={"total": 0.0})
        if doc_filter is not None and not doc_filter:
            return AskResult(question=question, found=False, seconds={"total": 0.0},
                             answer="No documents are tagged for the selected station.")
        context = (context or [])[-MAX_CONTEXT_TURNS:]
        if context and starts_fresh(question, context):
            # a new topic in the same conversation: answered as a first question (see plan.py)
            context, previous = [], ()
        if not context and focus is None:
            from .overview import overview  # (overview -> vague -> pipeline)
            found_overview = overview(question, self.index, doc_filter)
            if found_overview is not None:
                # "receipts", "what is the leave policy?": the section's or document's overview (see overview.py)
                text, di, nid = found_overview
                return AskResult(question=question, found=True, answer=text, citations=[self._citation(di, nid)],
                                 considered=[self._citation(di, nid)],
                                 seconds={"total": round(time.perf_counter() - t0, 2)})
        words = [t for t in tokenize(question) if t.isascii() and t.isalpha() and len(t) > 2]
        shaped = "?" in question or _QUESTION_SHAPE.search(question)  # "how long does it take?" is a follow-up
        if context and words and not shaped and not self.index.search(question) \
                and not self.index.search(self.index.spell(question)):
            # "asdfgh qwerty" in a conversation: no word of it is in the documents, and the follow-up
            # rewrite glued it onto the last question and answered that again (seen in the final test)
            return AskResult(question=question, found=False, answer=NOT_FOUND,
                             seconds={"total": round(time.perf_counter() - t0, 2)})
        if clarify and not context:
            which = self._which_manual(question, doc_filter)
            if which is not None:
                return AskResult(question=question, found=False, answer=which.prompt, clarify=which,
                                 seconds={"total": round(time.perf_counter() - t0, 2)})
        # a follow-up ("and for M10?") alone has too few words to find the manual: add the last question
        route_query = f"{context[-1][0]} {question}" if context else question
        # search by meaning, when an embedding model is set up: reworded and other-language questions
        use_meaning = semantic is not None and semantic.ready
        meaning = await semantic.search(route_query, doc_filter, MEANING_HITS) if use_meaning else []
        use_meaning = use_meaning and semantic.status == "ready"  # the embedding server may have stopped
        # no word of it in the documents, and nothing close in meaning ("asdfgh qwerty", "tell me a
        # joke"): not the model's to answer (a small one described some section instead)
        if use_meaning and not context and not self.index.search(route_query) and \
                (not meaning or meaning[0].score < NO_MATCH_MEANING):
            return AskResult(question=question, found=False, answer=NOT_FOUND,
                             seconds={"total": round(time.perf_counter() - t0, 2)})
        outline, ids = self._outline(route_query, doc_filter, meaning)
        seconds: dict[str, float] = {"route": round(time.perf_counter() - t0, 3)}

        # Fast path: when the keyword search clearly points at one section, skip the selection step
        # (one model call instead of two). A follow-up needs the selection step to be rewritten.
        # A question in several parts needs a section for each part.
        winner = None if context or not self.fast_path or question_parts(question) else \
            self._clear_winner(question, ids)
        near = None
        if focus is None and context and place is not None and (doc_filter is None or place[0] in doc_filter):
            near = near_place(question, self.index, *place)  # "How do I test it?" in Multi X: its Testing
            if near is not None and not _ELLIPTICAL.match(question):
                focus, near = (place[0], near), None  # read straight away, as one clear match
            # "and at shutdown?" means the last question about shutdown: rewritten as usual, the
            # section read with what the model picks (below)
        anchor = None  # the section picked, or the one of the conversation's part the question names
        if focus is not None and (not context or focus[0] == (place or (None,))[0]):
            self._with_doc(ids, focus[0])
            winner = _short_id(ids.letters[focus[0]], focus[1])  # the part the user picked
            anchor = focus
        if winner is not None:
            di_w, nid_w = ids.by_id[winner]
            doc_w = self.docs[di_w]
            # "with method 2": that way, whether the match was the step or one of its ways
            parent = doc_w.sections[nid_w].parent
            named = named_one(question, doc_w, variants(doc_w, nid_w)) or \
                (named_one(question, doc_w, variants(doc_w, parent)) if parent is not None else None)
            if named is not None:  # "How do I add data with Type 1?": read Type 1 alone
                winner = _short_id(ids.letters[di_w], named)
            which = self.which_way(question, *ids.by_id[winner])
            if which is not None:  # "How do I add data?": Type 1 or Type 2?
                seconds["total"] = round(time.perf_counter() - t0, 2)
                return AskResult(question=question, found=False, answer=which.prompt, clarify=which, seconds=seconds)
            seconds["select"] = 0.0
            fast = PromptIds(by_id={winner: ids.by_id[winner]}, letters=ids.letters, picked=[winner])
            considered = [self._citation(*ids.by_id[winner])]
            await progress({"stage": "reading", "sections": [c.model_dump() for c in considered]})
            result = await self._answer(question, question, fast, ids, considered, seconds, 0, None,
                                        progress if on_progress else None)
            if result.found:
                result.seconds["total"] = round(time.perf_counter() - t0, 2)
                return result
            # not in that section after all: fall back to the full two-step flow

        await progress({"stage": "selecting"})
        t = time.perf_counter()
        if context:
            talk = "\n".join(f"Q: {q}\nA: {_short(a, CONTEXT_ANSWER_CHARS)}" for q, a in context)
            choice = await self._select(self.followup_selector, f"CONVERSATION SO FAR:\n{talk}\n\n"
                                        f"TABLE OF CONTENTS:\n{outline}\n\nQUESTION: {question}", ids,
                                        FollowupChoice(standalone_question=question, section_ids=[]))
            rewritten = choice.output.standalone_question
            standalone = rewritten if 3 <= len(rewritten) <= 500 else question
            earlier = {q.casefold().strip(" ?") for q, _ in context}
            if standalone.casefold().strip(" ?") in earlier and question.casefold().strip(" ?") not in earlier:
                # a small model sometimes just repeats the last question; keep the new part explicitly
                standalone = f"{context[-1][0].rstrip(' ?')}: {question}"
            # "And in March?" after "the revenue in January?": the same question about March, made in
            # code when the model's version drops a word of it (the 0.8B wrote "the profit in March")
            swapped = follow_up([q for q, _ in context], question)
            if swapped and set(tokenize(swapped)) - set(tokenize(standalone)):
                standalone = swapped
        else:
            choice = await self._select(self.selector, f"TABLE OF CONTENTS:\n{outline}\n\nQUESTION: {question}",
                                        ids, SectionChoice(section_ids=[]))
            standalone = question
        seconds["select"] = round(time.perf_counter() - t, 2)
        requests = choice.usage.requests
        chosen = list(choice.output.section_ids)
        rewritten_as = standalone if standalone.casefold() != question.casefold() else None
        # Small models sometimes pass over the section a keyword search clearly points at: read it too.
        top = next((h for h in self.index.search(standalone) if h.doc_index in ids.letters), None)
        if top is not None:
            top_id = _short_id(ids.letters[top.doc_index], top.node_id)
            if top_id in ids.by_id and top_id not in chosen:
                chosen.append(top_id)
        # the sections holding a code the question names ("the price of part HX9-4471"): a manual
        # section mentioning the part ranked first and the parts list was never read (large-document
        # test). Whole codes only: "hx9" alone is in every row
        codes_asked = {t for t in tokenize(standalone) if is_code(t) and t not in self.index.title_words}
        codes_asked = {t for t in codes_asked if not any(t != u and t in re.split(r"[-_./]", u) for u in codes_asked)}
        if codes_asked:
            holders = [h for h in self.index.search(standalone)[:CODE_HOLDER_HITS]
                       if h.doc_index in ids.letters
                       and codes_asked & self.index._section_tokens.get((h.doc_index, h.node_id), set())]
            for h in holders[:CODE_HOLDERS]:
                hid = _short_id(ids.letters[h.doc_index], h.node_id)
                if hid in ids.by_id and hid not in chosen:
                    chosen.append(hid)
        # a question that asks several things: each part's best section too (searched as one string,
        # the part with the rarer words wins and the other part's section never reaches the model)
        for part in question_parts(standalone):
            best_part = next((h for h in self.index.search(part) if h.doc_index in ids.letters), None)
            if best_part is not None:
                part_id = _short_id(ids.letters[best_part.doc_index], best_part.node_id)
                if part_id in ids.by_id and part_id not in chosen:
                    chosen.append(part_id)
        # ... and the section closest in meaning (a question in other words or another language)
        if use_meaning:
            closest = meaning if standalone == route_query else \
                await semantic.search(standalone, doc_filter, MEANING_HITS)
            best = next((h for h in closest if h.doc_index in ids.letters), None)
            if best is not None:
                best_id = _short_id(ids.letters[best.doc_index], best.node_id)
                if best_id in ids.by_id and best_id not in chosen:
                    chosen.append(best_id)
        # a follow-up is often about the section the last answer came from ("how fast will security
        # answer?" after the steps for reporting a phishing email), worded differently from it
        if context and previous:
            by_doc_id = {d.doc_id: di for di, d in enumerate(self.docs)}
            di = by_doc_id.get(previous[0][0])
            if di in ids.letters:
                prev_id = _short_id(ids.letters[di], previous[0][1])
                if prev_id in ids.by_id and prev_id not in chosen:
                    chosen.append(prev_id)
        # the section of the conversation's part the follow-up names, or the one picked: first. Also
        # when read straight away above and the model's reply was unusable: Regenerate of "How do I
        # test it?" in Multi X then fell to the keyword search and answered from Object Detection's
        # Testing (Nemotron on OpenRouter, real-model test, 20 Oct 2026)
        first = (place[0], near) if near is not None else anchor
        if first is not None:
            self._with_doc(ids, first[0])
            first_id = _short_id(ids.letters[first[0]], first[1])
            chosen = [first_id, *(i for i in chosen if i != first_id)]
        considered = [self._citation(*ids.by_id[i]) for i in chosen]
        if chosen:
            await progress({"stage": "reading", "sections": [c.model_dump() for c in considered]})
        if not chosen:
            seconds["total"] = round(time.perf_counter() - t0, 2)
            return AskResult(question=question, found=False, seconds=seconds, model_requests=requests,
                             answer=NOT_FOUND, standalone_question=rewritten_as)

        shown = PromptIds(by_id={i: ids.by_id[i] for i in chosen}, letters=ids.letters,
                          picked=list(choice.output.section_ids))
        result = await self._answer(question, standalone, shown, ids, considered, seconds, requests,
                                    rewritten_as, progress if on_progress else None,
                                    earlier=context[-1][0] if context else "")
        parts = question_parts(standalone)
        if parts and (not result.found or result.unverified_numbers):
            # a small model given two parts at once often answers neither ("RAM and holidays"), or one
            # with a figure the documents lack ("invitations stay valid for 14 days"): each part on its
            # own, then together. Only when the joint answer failed: no cost otherwise
            result = await self._answer_parts(question, parts, ids, result, seconds)
        result.seconds["total"] = round(time.perf_counter() - t0, 2)
        return result

    async def _answer_parts(self, question: str, parts: list[str], ids: PromptIds, joint: AskResult,
                            seconds: dict[str, float]) -> AskResult:
        """Each part answered from its own best sections, the answers numbered in one result."""
        lines, citations, evidence, flagged = [], [], [], []
        requests, any_found = joint.model_requests, False
        named = {t for t in tokenize(question) if is_code(t)}
        found_codes: list[str] = []  # codes an earlier part's answer named, not the question
        for n, part in enumerate(parts, 1):
            shown_part = part
            if found_codes and _REFERS_BACK.search(part) and not any(is_code(t) for t in tokenize(part)):
                # "what does the replacement part cost?" after "replace the wiper strip (part HX9-4471)":
                # that part's row, not a price from another row (10,480 for 2,350; final eval)
                part = f"{part.rstrip('?. ')} ({', '.join(found_codes[:2])})?"
            picked = []
            for h in self.index.search(part):
                pid = _short_id(ids.letters[h.doc_index], h.node_id) if h.doc_index in ids.letters else None
                if pid in ids.by_id and pid not in picked:
                    picked.append(pid)
                if len(picked) == PART_SECTIONS:
                    break
            if not picked:
                lines.append(f"{n}. {shown_part.rstrip('?. ')}: {NOT_FOUND}")
                continue
            shown = PromptIds(by_id={i: ids.by_id[i] for i in picked}, letters=ids.letters, picked=picked)
            r = await self._answer(part, part, shown, ids, [self._citation(*ids.by_id[i]) for i in picked],
                                   {}, 0, None, None)
            requests += r.model_requests
            if not r.found:
                lines.append(f"{n}. {shown_part.rstrip('?. ')}: {NOT_FOUND}")
                continue
            any_found = True
            lines.append(f"{n}. {shown_part.rstrip('?. ')}: {r.answer}")
            found_codes += [m.group(0).upper() for m in _ANSWER_CODE.finditer(r.answer)
                            if m.group(0).lower() not in named]
            found_codes = list(dict.fromkeys(found_codes))
            index_of = {}
            for k, c in enumerate(r.citations):
                key = (c.doc_id, c.node_id)
                if key not in {(x.doc_id, x.node_id) for x in citations}:
                    citations.append(c)
                index_of[k] = next(j for j, x in enumerate(citations) if (x.doc_id, x.node_id) == key)
            evidence += [Evidence(text=e.text, citation=index_of[e.citation]) for e in r.evidence if e.citation in index_of]
            flagged += [f for f in r.unverified_numbers if f not in flagged]
        if not any_found:
            return joint
        return AskResult(question=question, found=True, answer="\n".join(lines), citations=citations,
                         considered=joint.considered, seconds=seconds, model_requests=requests,
                         unverified_numbers=flagged, standalone_question=joint.standalone_question,
                         evidence=evidence[:4])

    def _other_half(self, answer: str, question: str, sections: list[tuple[int, str]]) -> str | None:
        """A bare answer to one half of "X and Y" ("786 KB" for "How much flash and SRAM ...?",
        "STM32U585" for "Which microcontroller ... and at what clock speed?"): the statement holding
        it, when that covers the other half too, else the answer and the statement that does (0.8B,
        samples eval). None when the answer is not bare or no statement covers what is missing."""
        bare = answer.strip().rstrip(".")
        if len(bare) > BARE_FIGURE_CHARS or "\n" in bare:
            return None
        for di, nid in sections:
            doc = self.docs[di]
            around = set(tokenize(" ".join([doc.display_title, *doc.path(nid)])))
            asked = {t for t in tokenize(question) if len(t) >= 3 and t.isalpha() and t not in _ASKING
                     and t not in STATED_GENERIC and t not in around}
            said = [s for s in statements(doc, nid) if len(s) <= WHOLE_CELL_CHARS]
            asked &= set(tokenize(" ".join(said)))  # words the section has at all ("have" is not one)
            holding = next((s for s in said if bare.casefold() in s.casefold()), None)
            if holding is None:
                continue
            missing = asked - set(tokenize(holding)) - set(tokenize(bare))
            if not missing:
                return holding  # "It runs at 160 MHz with 2 MB of flash and 786 KB of SRAM."
            other = max((s for s in said if s != holding and numbers_in(s)),
                        key=lambda s: len(missing & set(tokenize(s))), default=None)
            if other and missing & set(tokenize(other)):
                return f"{bare}. {other}"
        return None

    def _stated(self, question: str, shown: PromptIds, worked: dict[str, list[str]],
                earlier: str = "") -> tuple[str, str, bool] | None:
        """(excerpt id, statement, worked out) of the one statement or table result in the shown
        excerpts that holds the most words of the question, when the excerpt has all of them (none
        for "the profit in April": no section says April) and no other statement holds as many."""
        asked = {t for t in tokenize(question) if len(t) >= 3 and t.isalpha() and t not in _ASKING
                 and not any(r.fullmatch(t) for r in (_COMPARING, _MORE, _LESS))}  # "less": a direction
        if not asked - STATED_GENERIC:
            return None
        # a model or part code ("HX-700", "G2", "A302") the section must name: the HX-500's spindle
        # speed was given for "the HX-700" (seen in the complex eval of 15 Oct 2026)
        codes = {t for t in tokenize(question) if is_code(t)}
        scored = []
        for i, (di, nid) in shown.by_id.items():
            if (di, nid) in self.index.replaced:
                continue  # an amendment replaced it: what it states no longer applies
            doc = self.docs[di]
            around = set(tokenize(" ".join([doc.section_text(nid, 200_000), doc.display_title, *doc.path(nid),
                                            *worked.get(i, [])])))
            if any(_CHANGE.match(w) for w in worked.get(i, [])):  # "grow": the increase worked out
                around |= {t for t in asked if _CHANGE.fullmatch(t)}
            if asked - STATED_GENERIC - around or codes - around:
                continue
            # "which award gives more days?" is not answered by one row: only what was worked out
            said = [] if _COMPARING.search(question) else [(s, False) for s in statements(doc, nid)]
            # "which gives more days?": the highest, whichever row names more of the question's words
            other = "Lowest" if _MORE.search(question) else "Highest" if _LESS.search(question) else None
            results = [w for w in worked.get(i, []) if not (other and w.startswith(other + " "))]
            for text, is_worked in said + [(w, True) for w in results]:
                words = set(tokenize(text))
                # a section and its parent state the same row: one statement, from the first read
                if (asked - STATED_GENERIC) & words and all(s[2] != text for s in scored):
                    scored.append((len(asked & words), i, text, is_worked))
        if not scored:
            return None
        top = max(s[0] for s in scored)
        best = [s for s in scored if s[0] == top]
        if top < min(2, len(asked - STATED_GENERIC)):
            return None
        if len(best) > 1 and all(s[3] for s in best):
            # "March vs January" has a difference and a change line: the change when one is asked
            change = [s for s in best if _CHANGE.search(s[2])]
            best = change[:1] if _CHANGE.search(question) and change else [s for s in best if s not in change][:1]
        if len(best) > 1 and earlier:
            # "And what is the limit for encashment?" after a question about privilege leave: the
            # privilege leave row, not the casual leave one
            before = set(tokenize(earlier)) - asked
            ranked = sorted(best, key=lambda s: -len(before & set(tokenize(s[2]))))
            if len(before & set(tokenize(ranked[0][2]))) > len(before & set(tokenize(ranked[1][2]))):
                best = ranked[:1]
        if len(best) != 1:
            return None
        return best[0][1], best[0][2], best[0][3]

    def _replaced_note(self, di: int, nid: str) -> str:
        """' (REPLACED by ...)' after the heading of a clause an amendment replaced."""
        new = self.index.replaced.get((di, nid))
        if new is None:
            return ""
        return (f" (REPLACED by \"{self.docs[new[0]].sections[new[1]].title}\": no longer applies, "
                "answer from the replacing clause)")

    def _doc_words(self) -> list[set[str]]:
        """Each document's words (its titles, description and text), worked out once."""
        if getattr(self, "_doc_word_sets", None) is None:
            self._doc_word_sets = [set(tokenize(" ".join(
                [doc.name, doc.title or "", doc.description or ""]
                + [f"{s.title} {s.own_text}" for s in doc.sections.values()]))) for doc in self.docs]
        return self._doc_word_sets

    def _rule_for(self, question: str, shown: PromptIds, among: list[str] = (),
                  no: bool = False, forbidding: bool = False) -> tuple[str, tuple] | None:
        """(excerpt id, fact) of the first rule, limit or amount the shown excerpts state about the
        question (the cited ones first)."""
        order = [i for i in among if i in shown.by_id] + [i for i in shown.by_id if i not in among]
        asked = set(tokenize(question))
        # by word stems ("tool"/"Tools", "load"/"loaded"), question words only: "Can I load an 8 kg
        # tool?" was given "Max table load: 600 kg" over "Tools heavier than 7 kg must not be loaded"
        asked = {_stem(t) for t in asked if t not in _ASKING and t.isalpha() and len(t) > 1}
        best, best_score = None, 0
        idf = self.index.bm25.idf if self.index.bm25 else {}
        for rank, i in enumerate(order):
            di, nid = shown.by_id[i]
            if (di, nid) in self.index.replaced:
                continue
            for fact in find_facts(self.index, question, hits=[Hit(di, nid, 1.0)]):
                # a table row is judged cell by cell, each with the row's name ("Leave Type: Privilege
                # Leave (PL) · Carry Forward: Up to 45 days"): a whole row matches everything
                cells = fact[2].split(" · ")
                pieces = [f"{cells[0]} · {c}" for c in cells[1:]] if len(cells) > 2 else [fact[2]]
                for piece in pieces:
                    if forbidding and not _NEGATED.search(piece):
                        continue
                    # the question's words in it, the rarer the more ("combined" over "leave"), then
                    # the cited excerpts first; for a "No", what is not allowed a little more
                    matched = {_stem(t): t for t in tokenize(piece) if _stem(t) in asked}
                    score = sum(idf.get(t, 1.0) for t in matched.values()) * 10 - rank
                    if no and matched and _PROHIBITS.search(piece):
                        score *= PROHIBIT_WEIGHT
                    if score > best_score:
                        best, best_score = (i, (*fact[:2], piece, *fact[3:])), score
        return best

    def _which_manual(self, question: str, allowed: set[int] | None) -> Clarification | None:
        """A question back when the question fits several manuals equally, else None."""
        best: dict[int, Any] = {}
        for h in self.index.search(question):
            if allowed is None or h.doc_index in allowed:
                best.setdefault(h.doc_index, h)
        if len(best) < 2:
            return None
        ranked = sorted(best.values(), key=lambda h: -h.score)
        top = ranked[0]
        words = self.index.matched_terms(top.doc_index, top.node_id, question)
        # only near-identical sections make a question ambiguous (the same tool's manual for two
        # stations); a report and the meeting notes discussing it can both be read and answered from
        close = [h for h in ranked if h.score >= CLARIFY_SHARE * top.score
                 and self.index.matched_terms(h.doc_index, h.node_id, question) == words
                 and (h is top or self._alike(top, h))]
        if len(close) < 2:
            return None
        close = close[:MAX_CLARIFY_OPTIONS]
        return Clarification(prompt=CLARIFY_PROMPT, options=[
            ClarifyOption(label=self.docs[h.doc_index].display_title,
                          detail=self.docs[h.doc_index].sections[h.node_id].title,
                          doc_ids=[self.docs[h.doc_index].doc_id]) for h in close])

    def _alike(self, a, b) -> bool:
        """Are these two sections (search hits) versions of the same text?"""
        sa, sb = self.docs[a.doc_index].sections[a.node_id], self.docs[b.doc_index].sections[b.node_id]
        if " ".join(sa.title.split()).casefold() == " ".join(sb.title.split()).casefold():
            return True
        ta = set(tokenize(self.docs[a.doc_index].section_text(a.node_id, 3000)))
        tb = set(tokenize(self.docs[b.doc_index].section_text(b.node_id, 3000)))
        return len(ta & tb) >= CLARIFY_ALIKE * max(1, len(ta | tb))

    def _clear_winner(self, question: str, ids: PromptIds) -> str | None:
        """The short id of the one section a keyword search clearly points at, or None.

        Clear: it scores FAST_PATH_RATIO times the best hit that is not its own parent or
        subsection, and it matches two or more question words, or a code like E17 or M6."""
        hits = [h for h in self.index.search(question) if h.doc_index in ids.letters]
        if not hits:
            return None
        top = hits[0]
        doc = self.docs[top.doc_index]
        family = {top.node_id, *self._ancestors(doc, top.node_id), *(s.node_id for s in doc.subtree(top.node_id))}
        rival = next((h for h in hits[1:] if h.doc_index != top.doc_index or h.node_id not in family), None)
        if rival is not None and top.score < FAST_PATH_RATIO * rival.score:
            return None
        # prefer a matching subsection over its parent ("Error E17" rather than "Error codes")
        sub = {s.node_id for s in doc.subtree(top.node_id)[1:]}
        best = next((h for h in hits if h.doc_index == top.doc_index and h.node_id in sub
                     and h.score >= FAST_PATH_CHILD_SHARE * top.score), top)
        matched = self.index.matched_terms(best.doc_index, best.node_id, question)
        if len(matched) < 2 and not any(any(c.isdigit() for c in t) for t in matched):
            return None
        short = _short_id(ids.letters[best.doc_index], best.node_id)
        return short if short in ids.by_id else None

    async def _answer(self, question: str, standalone: str, shown: PromptIds, ids: PromptIds,
                      considered: list[Citation], seconds: dict[str, float], requests: int,
                      rewritten_as: str | None, progress: ProgressFn | None, earlier: str = "") -> AskResult:
        """The answer step: read the shown sections and answer, citing them. With progress, the
        answer text is streamed as {"stage": "draft", "text": ...} events while it is written."""
        # a clause an amendment replaced ("1.5 Payment terms"): the replacing clause is read with it
        added = {}
        for di, nid in shown.by_id.values():
            new = self.index.replaced.get((di, nid))
            if new is not None and new not in shown.by_id.values() and new[0] in ids.letters:
                new_id = _short_id(ids.letters[new[0]], new[1])
                if new_id in ids.by_id:
                    added[new_id] = new
        if added:
            shown = PromptIds(by_id={**shown.by_id, **added}, letters=shown.letters, picked=shown.picked)
        used_docs = dict.fromkeys(di for di, _ in shown.by_id.values())
        descriptions = "\n".join(
            f"- {self.docs[di].display_title}: {self.docs[di].description or 'no description'}"
            for di in used_docs)
        per_excerpt = min(EXCERPT_CHARS, TOTAL_EXCERPT_CHARS // len(shown.by_id))
        # a long section: the passages that match the question, not its first pages
        texts = {i: excerpt(self.docs[di], nid, standalone, per_excerpt) for i, (di, nid) in shown.by_id.items()}
        # a long section given in passages: also its lines with the codes the other excerpts name. The
        # alarm text names part HX9-4471, whose price is in row 157 of a 500-row parts list the
        # passages for "alarm C-2087 ... cost" never reached (large-document test)
        for i, (di, nid) in shown.by_id.items():
            if "(passages of a long section)" not in texts[i]:
                continue
            others = " ".join(t for j, t in texts.items() if j != i)
            linked = [c for c in dict.fromkeys(tokenize(others)) if is_code(c) and "-" in c
                      and c not in self.index.title_words and c not in tokenize(texts[i])][:LINKED_CODES]
            whole = self.docs[di].section_text(nid, FULL_SECTION_CHARS)
            lines = [ln.strip() for ln in whole.splitlines()
                     if any(re.search(rf"(?<![\w-]){re.escape(c)}(?![\w-])", ln, re.IGNORECASE) for c in linked)]
            if lines:
                texts[i] += "\n(lines with codes named in the other excerpts)\n" + "\n".join(
                    ln[:LINKED_LINE_CHARS] for ln in lines[:LINKED_CODES])
        excerpts = "\n\n".join(
            f"=== [{i}] {self.docs[di].display_title} > {' > '.join(self.docs[di].path(nid))}"
            f"{_pages_note(self.docs[di], nid)}{self._replaced_note(di, nid)} ===\n{texts[i]}"
            for i, (di, nid) in shown.by_id.items())
        # "which month had the best margin?": ranked and added up in code (a small model ranked a
        # 4-row table wrongly); the model reads the result next to the table
        worked, computed, worked_by = [], [], {}
        # the tables the question names by their title ("downtime": "Sheet: Downtime hours by cause"):
        # only those, when there are any (the 0.8B took the production total for "total downtime")
        topic = {t for t in tokenize(standalone) if len(t) > 2 and t.isalpha() and t not in _ASKING}
        titled = {i for i, (di, nid) in shown.by_id.items()
                  if topic & set(tokenize(self.docs[di].sections[nid].title))}
        for i, (di, nid) in shown.by_id.items():
            if titled and i not in titled:
                continue
            lines, values = table_highlights(self.docs[di], nid, standalone)
            worked += [f"[{i}] {line}" for line in lines]
            worked_by[i] = lines
            computed += values
        # "the revenue in March": the one cell the question names, read in code (the 0.8B gave March's
        # profit from the row next to it)
        cells = {}
        for i, (di, nid) in shown.by_id.items():
            cell = scored_table_cell(self.docs[di], nid, standalone)
            if cell is not None and all(c[1] != cell[1] for c in cells.values()):
                cells[i] = cell
        # several sections naming a cell ("G3" in the grades, the travel classes and the approvals):
        # the one the question names most strongly, when one does
        top = max((c[0] for c in cells.values()), default=0)
        # the one cell of a row the question names, however weakly: a bare figure from it is completed below
        weak_cell = next(iter(cells.items())) if len(cells) == 1 else None
        # named by its row alone (one word) is too weak: "G4 in Goa" is not "G4 · Examples: Senior managers"
        cells = {i: c[1:] for i, c in cells.items() if c[0] == top and top >= CELL_MIN_SCORE}
        cell_at = next(iter(cells.items())) if len(cells) == 1 and not _COMPARING.search(standalone) \
            and not _CHANGE.search(standalone) else None
        if cell_at is not None:
            worked.append(f"[{cell_at[0]}] {cell_at[1][0]}")
        if worked:
            excerpts += "\n\nWORKED OUT FROM THE TABLES (exact):\n" + "\n".join(worked)
        parts = question_parts(standalone)

        async def on_partial(partial: DraftAnswer) -> None:  # show the answer while it is written
            text = getattr(partial, "answer", "") or ""
            cut = repeating_from(text)
            if cut is not None:  # stuck repeating itself: stop, and keep what came before
                raise Looping(text[:cut])
            if progress and getattr(partial, "found", False) and text:
                await progress({"stage": "draft", "text": _clean_answer(text, ids.by_id)})

        # a question full of typos ("wat dose amber wps led meen") also goes to the model with the
        # documents' own spelling, which it reads more reliably
        spelled = self.index.spell(standalone)
        asked = standalone if spelled == standalone else f"{standalone}\n(spelling corrected: {spelled})"
        language = language_of(question)
        if language:
            # a Hindi question answered in English (final computer-use check): the general instruction
            # is not enough for a small model; the language named next to the question is
            asked += f"\n(Write the answer in {language}.)"
        if parts:
            asked += "\n(" + parts_note(parts) + ")"
        t = time.perf_counter()
        try:
            draft = await self._run(
                self.answerer, f"DOCUMENTS:\n{descriptions}\n\nEXCERPTS:\n{excerpts}\n\nQUESTION: {asked}", shown,
                on_partial=on_partial if progress else None)
        except Looping as loop:
            log.warning("answer stopped: the model kept repeating itself")
            draft = _Salvaged(DraftAnswer(found=bool(loop.text.strip()), answer=loop.text.rstrip(),
                                          section_ids=list(shown.picked) or list(shown.by_id)))
        except ProviderBusy as e:
            if not isinstance(e.__cause__, UnexpectedModelBehavior):
                raise
            # an unusable reply twice: no answer from the model, but the statement and table checks
            # below may still find it in the excerpts (better than "try again" for the same result)
            log.warning("answer step unusable: %s", describe_error(e.__cause__))
            draft = _Salvaged(DraftAnswer(found=False, answer="", section_ids=[]))
        if language and _echoed_question(draft.output.answer or "", standalone):
            # "EVT2 बिल्ड कब निर्धारित है? (Write the answer in Hindi.)": the 0.8B wrote the question back
            # (samples eval); asked again as before the language line, it answered "October 20, 2026"
            log.warning("answer echoed the question: asked again without the language line")
            asked = asked.replace(f"\n(Write the answer in {language}.)", "")
            try:
                draft = await self._run(
                    self.answerer, f"DOCUMENTS:\n{descriptions}\n\nEXCERPTS:\n{excerpts}\n\nQUESTION: {asked}", shown)
            except (Looping, ProviderBusy):
                pass
        seconds["answer"] = round(time.perf_counter() - t, 2)
        out = draft.output
        out.answer = _LANGUAGE_LINE.sub("", out.answer or "").strip()  # an echo is shown as its passage below
        cut = repeating_from(out.answer or "")  # not streamed: checked once written
        if cut is not None:
            out.answer = out.answer[:cut].rstrip()
        out.answer = without_repeated_sentences(_clean_answer(out.answer, ids.by_id))
        # small models mark a "no" answer as not found ("No, do not factory reset for E-217."):
        # an answer that cites what it read and does not say the documents lack it is an answer
        first = re.split(r"(?<=[.!?])\s", out.answer.strip(), maxsplit=1)[0]
        found = bool(out.answer) and not _no_answer(out.answer) and (
            out.found or (bool(out.section_ids) and bool(_YES_NO.match(out.answer))
                          and not _REFUSAL.search(first) and not _NOT_IN_SOURCES.search(first)))
        cited = out.section_ids
        # check figures against every excerpt the model read (it may cite only some of them), the
        # document titles and descriptions it was shown ("Q1 FY25 earnings release") and what was
        # worked out from the tables
        source_text = descriptions + "\n" + "\n".join(texts.values()) + "\n" + "\n".join(worked)
        if found and not language and _foreign_words(out.answer, source_text + "\n" + standalone):
            # "The amber led meen" for "How do I reset my password?" (the 0.8B copied the typo example
            # of its instructions; reasoning eval): words neither the excerpts nor the question have
            log.warning("answer made of words not in the excerpts: %s", out.answer[:120])
            found = False
        if not found and out.answer and not _no_answer(out.answer) and not _REFUSAL.search(first) \
                and not _NOT_IN_SOURCES.search(first):
            # the 0.8B answers "March" (right) with found=false and no sources: an answer made of the
            # excerpts' own words is an answer, cited by the excerpts that hold it
            words = {t for t in tokenize(out.answer) if len(t) > 1}
            # something the question did not already say: a bare "No" to "is there an overtime
            # policy?" is the model's not-found, not an answer
            new = words - set(tokenize(standalone)) - {"yes", "no", "not", "none"}
            if new and len(words & set(tokenize(source_text))) >= RESCUE_SHARE * len(words):
                found = True
                cited = cited or [i for i in shown.by_id if words & set(tokenize(texts[i]))] or list(shown.by_id)
            elif _BARE_YES_NO.match(out.answer) and _YES_NO_QUESTION.match(standalone):
                # "Can I carry 50 days forward?" answered a bare "No": an answer when the excerpts
                # state a rule about it ("Carry Forward: Up to 45 days"), shown under it as evidence
                rule = self._rule_for(standalone, shown, no=out.answer.strip().lower().startswith("no"))
                if rule is not None:
                    found, cited = True, [rule[0]]
        if found and _BARE_YES_NO.match(out.answer) and len(out.answer.split()) <= BARE_WORDS \
                and _YES_NO_QUESTION.match(standalone):
            # a bare "No" says nothing of why: the rule it rests on goes with it
            rule = self._rule_for(standalone, shown, cited, no=out.answer.strip().lower().startswith("no"))
            asked_stems = {_stem(t) for t in tokenize(standalone) if t not in _ASKING}
            if rule is not None and len({_stem(t) for t in tokenize(rule[1][2])} & asked_stems) < 2:
                # "Does the R7 support Wi-Fi 7?" -> "No. The documents state: Recommend the 5 GHz
                # network ..." (samples eval): a rule about something else is no reason. A bare "no"
                # with nothing on the topic is the model's not-found
                rule = None
                if out.answer.strip().lower().startswith("no"):
                    found = False
            if rule is not None:
                said = out.answer.strip().rstrip(".")
                if said.lower().startswith("yes") and _NEGATED.search(rule[1][2]):
                    # "Yes. The documents state: Tools heavier than 7 kg ... must not be loaded" (8 kg
                    # asked, final test): a yes with a rule that says no is the rule alone
                    out.answer, out.worked_out = f"The documents state: {rule[1][2]}", False
                else:
                    out.answer = f"{said}. The documents state: {rule[1][2]}"
                cited = cited or [rule[0]]
        elif found and _YES.match(out.answer) and (_YES_NO_QUESTION.match(standalone) or _ASKS_YES_NO.search(standalone)) \
                and not _NEGATED.search(re.split(r"(?<=[.!?;…])\s|,?\s+(?:but|however|though|provided|unless|except|"
                                                 r"as long as|so long as)\b", out.answer)[0]):
            # (the yes itself, not a later "but it is not reimbursed": "Yes, you can still claim it ...
            # not reimbursed" for a lost INR 3,000 receipt, final test, is a yes the rule overrides)
            # "Yes, contractors may drive our forklifts" while the section says "Contractors may not drive
            # Helix forklifts" (the 0.8B in the final test): a plain yes against a rule that says no.
            # The rule is shown instead of the yes
            rule = self._rule_for(standalone, shown, cited, no=True, forbidding=True)
            # one that shares the question's words (two or more), not any "not" in the section
            if rule is not None and len({_stem(t) for t in tokenize(rule[1][2])}
                                        & {_stem(t) for t in tokenize(standalone) if t not in _ASKING}) >= 2:
                out.answer, cited, out.worked_out = f"The documents state: {rule[1][2]}", [rule[0]], False
        # Small models tend to cite everything they read. When the answer quotes figures, keep the
        # sources that contain them (if any do).
        figures = numbers_in(out.answer) - numbers_in(standalone)
        if figures and len(cited) > 1:
            cited = [i for i in cited if figures & numbers_in(texts[i])] or cited
            # ... and of those, the ones that hold the answer's words too: "72" is also "In stock: 72"
            # in a parts list, beside the commissioning section the "72-hour dry run" came from
            # (large-document check)
            said = {t for t in tokenize(out.answer) if not t.isdigit() and len(t) > 2}
            shared = {i: len(said & set(tokenize(texts[i]))) for i in cited}
            if len(cited) > 1 and max(shared.values()) > 0:
                cited = [i for i in cited if shared[i] >= CITED_WORD_SHARE * max(shared.values())]
        flagged = unverified_numbers(out.answer, source_text, standalone, computed) if found else []
        own = _LIST_NUMBERS.sub(" ", out.answer)
        cited_text = "\n".join(texts[i] for i in cited if i in texts)
        if found and (made_up(standalone, own, source_text, self.index, cited_text)
                      or only_unchecked_figures(own, flagged, standalone) or wrong_kind_of_figure(standalone, own)):
            # steps for a topic the documents lack ("reset my password"), or a figure they do not have
            # ("$1,200 per user"): not an answer from the documents (see support.py)
            found = False
        codes = {t for t in tokenize(standalone) if is_code(t) and t not in self.index.title_words}
        # names the documents use elsewhere, rarely: "the OEE of the Chennai plant" answered with the
        # Pune plant's figure (final test); Chennai is only in the travel policy's city list
        # (checked in the sections the answer cites: "Python" in a two-part question cites none)
        doc_words = self._doc_words()
        names = {t for w in re.findall(r"(?<![.?!]\s)(?<!^)\b[A-Z][a-z]{2,}\b", standalone.strip())
                 for t in tokenize(w) if t not in self.index.title_words
                 and 0 < sum(t in d for d in doc_words) <= NAME_DOC_SHARE * len(doc_words)}

        # the manual the question names by its code ("the HX-900"): an answer from another manual (the
        # HX-500's torque table, large-document test) is not about it
        named_docs = {t for t in tokenize(standalone) if t in self.index.title_words and is_code(t)}
        carriers = {di for di, n in enumerate(self.index._doc_names) if named_docs & n}

        def foreign(ids: list[str], with_names: bool = True) -> bool:
            """The sections lack the question's code, their documents one of the names it uses, or
            none of them is the manual it names."""
            # the whole section, not the excerpt the model saw: part HX9-4471 is in row 157 of a
            # 500-row table that the excerpt gives only in passages
            words = set().union(*(set(tokenize(self.docs[shown.by_id[i][0]].section_text(
                shown.by_id[i][1], FULL_SECTION_CHARS))) for i in ids if i in shown.by_id))
            docs_cited = {shown.by_id[i][0] for i in ids if i in shown.by_id}
            named = set().union(*(doc_words[di] for di in docs_cited))
            return bool(codes and not codes & words) or (with_names and bool(names - named)) \
                or bool(carriers and not docs_cited & carriers)
        if found and foreign(cited or list(texts), with_names=bool(cited) and not parts):
            # "Alarm A302 indicates a 'No Machine' condition", cited from a section that never says A302,
            # or the HX-500's spindle speed for "the HX-700", which no excerpt names (final test): an
            # answer about a code from sections without it is not from the documents
            found = False
        if not found and cell_at is not None and not wrong_kind_of_figure(standalone, cell_at[1][1]) \
                and not foreign([cell_at[0]]) and not _YES_NO_QUESTION.match(standalone):
            # (not for "Can I load an 8 kg tool?": "Tool magazine · 24 tools" is no answer to it; the
            # rule it rests on is, below)
            # "Which class can a G3 book for a 9-hour flight?" / "Who approves INR 30,000?" answered
            # "not found" (final test): the one cell the question names, read in code
            i, (line, _value) = cell_at
            found, cited, flagged, out.worked_out = True, [i], [], False
            out.answer = f"From the documents: {line}"
        if not found and (_YES_NO_QUESTION.match(standalone) or _ASKS_YES_NO.search(standalone)):
            # "Is laundry reimbursed on a 3-night trip?" answered "not found" while "laundry for trips
            # shorter than 4 nights" is in a list of what is not reimbursed (final test): the rule
            # about it, as stated, without a yes or no the code cannot judge
            rule = self._rule_for(standalone, shown)
            if rule is not None and not foreign([rule[0]]) \
                    and len({_stem(t) for t in tokenize(rule[1][2])} & {
                    _stem(t) for t in tokenize(standalone) if t not in _ASKING and t.isalpha()}) >= 2:
                found, cited, flagged, out.worked_out = True, [rule[0]], [], False
                out.answer = f"The documents state: {rule[1][2]}"
        if not found and not _YES_NO_QUESTION.match(standalone):
            # "the minimum RAM?" answered "not found" (or with something else) while "Memory · Minimum:
            # 4 GB RAM" was in front of it: the one statement or table result holding the question's words
            stated = self._stated(standalone, shown, worked_by, earlier)
            if stated is not None and not foreign([stated[0]]):
                i, line, is_worked = stated
                found, cited, flagged = True, [i], []
                out.answer = f"{'Worked out from the table' if is_worked else 'From the documents'}: {line}"
                out.worked_out = is_worked
        value = cell_at[1][1] if cell_at is not None else ""
        if found and cell_at is not None and not foreign([cell_at[0]]) and not _YES_NO_QUESTION.match(standalone) \
                and (not numbers_in(value) & numbers_in(out.answer) if numbers_in(value)
                     else not set(tokenize(value)) & set(tokenize(out.answer))):
            # a figure from another cell of the table, or another cell's words ("Premium economy" for
            # a 9-hour flight, the 0.8B in the large-document test): the cell the question names instead
            i, (line, _value) = cell_at
            out.answer, cited, flagged, out.worked_out = f"From the documents: {line}", [i], [], False
        rank = re.search(r"\b(highest|lowest|most|least|largest|smallest|longest|shortest|biggest|greatest|"
                         r"fewest|cheapest|costliest|priciest|slowest|fastest|quickest|heaviest|lightest|"
                         r"hottest|coldest)\b", standalone, re.IGNORECASE)
        if rank and worked_by:
            # "Which line had the highest total downtime?" answered "Highest Line C: Breakdowns (196)"
            # (one row), and "the lowest production on Line C" answered March (final test): what was
            # worked out in code answers it, from the table the question names most by its title
            want = "Lowest" if rank.group(1).lower() in ("lowest", "least", "smallest", "shortest", "fewest",
                                                         "cheapest", "fastest", "quickest", "lightest",
                                                         "coldest") else "Highest"
            # "the most expensive part": the price column, which the question does not name
            words = set(re.findall(r"\w+", standalone.lower())) | ranking_words(standalone)
            total = "total" in words

            def ranked(lines: list[str]) -> str | None:
                for ln in lines:
                    head, _, rest = ln.partition(": ")
                    if total and head == f"{want} column total" and \
                            set(tokenize(rest)) & set(tokenize(standalone)):  # "Line C" for "which line"
                        return ln
                    if not total and head.startswith(f"{want} ") and head != f"{want} column total" and \
                            set(re.findall(r"\w+", re.sub(r"\([^)]*\)", " ", head[len(want):]).lower())) <= words:
                        # (a column's unit in brackets is not asked for: "Lead time (days)")
                        return ln
                return None

            def title_match(i: str) -> int:
                di, nid = shown.by_id[i]
                return len(set(tokenize(self.docs[di].sections[nid].title)) & set(tokenize(standalone)))
            for i in sorted(worked_by, key=title_match, reverse=True):
                line = ranked(worked_by[i])
                if line is None:
                    continue
                if not numbers_in(line) <= numbers_in(out.answer if found else "") and not foreign([i]):
                    found, cited, flagged, out.worked_out = True, [i], [], True
                    out.answer = f"Worked out from the table: {line}"
                break
        if found and _RANKS.search(standalone):
            # "the highest response time" from a table given only in part: the 0.8B ranked the 10 rows
            # shown (133 ms); the document's note says what the whole file holds (1840.6 ms)
            for i in cited or list(shown.by_id):
                note = partial_table_note(texts.get(i, ""))
                if note and not numbers_in(out.answer) <= numbers_in(note):
                    out.answer = ("The document includes only part of this table, so it can't be worked out "
                                  f"from the rows. The document says: {note}")
                    cited, flagged, out.worked_out = [i], [], False
                    break
        if found and cell_at is not None and out.answer.strip().rstrip(".") == cell_at[1][1]:
            # a bare "2,350" or "10": with the unit its column names ("Price (INR)", "Lead time (days)")
            column = cell_at[1][0].split(" · ")[-1].rsplit(": ", 1)[0]
            unit = re.search(r"\(([^()]{1,15})\)\s*$", column)
            if unit:
                out.answer = f"{cell_at[1][1]} {unit.group(1)}"
        bare = out.answer.strip().rstrip(".")
        if found and weak_cell is not None and len(bare) <= BARE_FIGURE_CHARS and numbers_in(bare) \
                and bare != weak_cell[1][2] and bare in weak_cell[1][2] and len(weak_cell[1][2]) <= WHOLE_CELL_CHARS \
                and not foreign([weak_cell[0]]):
            # "What is the 401(k) match?" answered "100%" from "100% match on the first 4% of salary,
            # vesting immediately": the whole cell, not its first figure (0.8B, samples eval)
            out.answer = weak_cell[1][2]
            if weak_cell[0] not in cited:
                cited = [weak_cell[0], *cited]
        if found:
            # "RYL_OB" for the document's "RLY_OB" (the 0.8B misspelled a signal name it copied)
            out.answer = _codes_as_written(out.answer, "\n".join(
                self.docs[di].section_text(nid, FULL_SECTION_CHARS) for di, nid in
                [shown.by_id[i] for i in cited or list(shown.by_id)]))
        if found and _ASKS_TWO.search(standalone):
            completed = self._other_half(out.answer, standalone, [shown.by_id[i] for i in cited or list(shown.by_id)])
            if completed:
                out.answer = completed
        cited_sections = [shown.by_id[i] for i in cited] if found else []
        if cited_sections and _echoes(out.answer, standalone):
            # "E-217?" answered "E-217": the matching passage of the section says more than that
            # from the cited section that contains most of the question's words (not just the first)
            di, nid = max(cited_sections, key=lambda s: len(self.index.matched_terms(s[0], s[1], standalone)))
            if set(tokenize(self.index.spell(standalone))) <= self.index.matched_terms(di, nid, standalone):
                out.answer = "From the documents: " + self.index.snippet(di, nid, standalone, width=ECHO_SNIPPET_CHARS)
            else:
                # "the name of Acme's CEO?" answered "Acme CEO": no section has what was asked, and a
                # passage about something else would read as the answer
                found, cited_sections = False, []
        evidence = [Evidence(text=q.text, citation=q.citation) for q in quotes(
            out.answer, standalone, [(self.docs[di], nid) for di, nid in cited_sections])] if found else []
        return AskResult(
            question=question, found=found, answer=out.answer if found else NOT_FOUND,
            citations=[self._citation(di, nid) for di, nid in cited_sections],
            considered=considered, seconds=seconds, model_requests=requests + draft.usage.requests,
            unverified_numbers=flagged if found else [],
            standalone_question=rewritten_as, evidence=evidence,
            # the label says "not stated in these words": not for an answer in the cited sections' own words
            worked_out=found and out.worked_out and not stated_as_is(
                out.answer, "\n".join(texts[i] for i in cited if i in texts) or source_text, standalone))
