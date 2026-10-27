"""Chat mode: a tool-using assistant for the office profile.

Unlike the manuals pipeline (a fixed two-step flow sized for small local models), the chat
agent decides for itself what to look up. Its tools search and read the installed documents,
list them by type, date or folder, read tables as rows, calculate exactly, search by meaning
(when an embedding server is configured) and read files the user attached. More tools (Jira,
Confluence, ... via MCP) plug in the same way later.

Facts taken from the documents are cited as [S1], [S2] ... in the model's text; after the run the
references it actually used become numbered sources [1], [2] ... and are resolved to sections by
us, never by the model. General knowledge goes in a paragraph marked "General knowledge:", shown
set apart; an answer that cites nothing is general knowledge as a whole.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import openai
from pydantic_ai import (Agent, ModelAPIError, ModelHTTPError, RunContext, UnexpectedModelBehavior,
                         UsageLimitExceeded, UsageLimits)
from pydantic_ai.messages import (FunctionToolCallEvent, ModelMessage, ModelRequest, ModelResponse,
                                  PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta, UserPromptPart)
from pydantic_ai.models import Model
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import FunctionToolset, WrapperToolset

from .attachments import READ_CHARS as ATTACHMENT_READ_CHARS, Attachment
from .calc import CalcError, calculate as evaluate
from .embeddings import SemanticIndex, fuse
from .llm import QUOTA_MESSAGE, BudgetExceeded, RequestBudget, describe_error, quota_exhausted, run_settings
from .models import Doc
from .numbers import codes_in, numbers_in, unverified_numbers, without_step_numbers
from .loops import Looping as _Looping, repeating_from, without_repeated_sentences
from .support import _ASKING, made_up, only_unchecked_figures, stated_as_is, wrong_kind_of_figure
from .pipeline import (_STUCK, STUCK_NOTE, AskResult, Citation, Clarification, ClarifyOption, Evidence, ProviderBusy,
                       plain_typography, refusal)
from .search import SectionIndex, tokenize
from .tables import markdown_tables
from .focus import near as near_place
from .doctools import (doc_map, find_facts as facts_about, find_text as text_hits, passages,
                       partial_table_note, query_table as table_query, read_view, table_highlights,
                       verify_quote)
from .plan import follow_up, parts_note, question_parts

log = logging.getLogger("oasis_engine.chat")

SEARCH_RESULTS = 6
READ_CHARS = 6_000
WHOLE_CHARS = 2_500  # a section this short is read with its subsections
HISTORY_TURNS = 6
HISTORY_ANSWER_CHARS = 1_500
# model calls per question: a large model on a server may plan, look up several parts and check
# its facts; a model on this computer re-reads the whole conversation at every call (minutes on a
# processor), so it gets fewer
MAX_REQUESTS = {"remote": 12, "local": 6}
WRAP_UP_AT = {"remote": 9, "local": 5}  # from this call on, no tools: the model answers now
WRAP_UP_CHARS = 3_000  # of each section read, given for the answer when the step limit is reached
WRAP_UP_TOTAL = 16_000
RETRY_DELAY_S = 2.0
DOCUMENTS_LISTED = 40     # in the instructions; list_documents shows the rest
LIST_RESULTS = 50
ATTACHMENT_PREVIEW_CHARS = 4_000
KB_GUIDE_CHARS = 6_000  # a knowledge base's AGENTS.md, given with the instructions
KB_NOTES_CHARS = 3_000  # its needs-review.md  # of a file the question is about, given with the question
LARGE_CONTEXT = 16_000  # tokens: a model with at least this much room reads whole sections
REFERENCE_CONTEXT = 32_768  # the room the large-model sizes below are set for; they scale with it
MAX_SCALED_CONTEXT = 131_072


@dataclass(frozen=True)
class Room:
    """How much the chat model is given at once, from its context window. A small model on this
    computer (8,192 tokens) gets passages and one page of a section at a time; a large model (Qwen
    3.5 27B on an office server, 32k+) gets the best sections whole, long pages and long outlines, so
    that it answers from the documents' own full text (cut-off excerpts made models guess: a delivery
    table read 2 of its 7 rows, 22 Oct test)."""
    large: bool = False
    search_results: int = SEARCH_RESULTS
    read_chars: int = 4_000          # one page of read_section
    whole_chars: int = WHOLE_CHARS   # a section this short is read with its subsections
    presearch_hits: int = 3
    passage_hits: int = 2            # best matches given with the question as text, not a snippet
    passage_chars: int = 900
    outline_chars: int = 8_000
    history_answer_chars: int = HISTORY_ANSWER_CHARS
    guide_chars: int = KB_GUIDE_CHARS
    notes_chars: int = KB_NOTES_CHARS
    wrap_chars: int = WRAP_UP_CHARS
    wrap_total: int = WRAP_UP_TOTAL
    attachment_preview: int = ATTACHMENT_PREVIEW_CHARS
    max_requests: int = 12
    wrap_up_at: int = 9


def room_for(context_tokens: int, tier: str) -> Room:
    """The Room for a model with this context window (0: not known) on this tier."""
    if tier == "local":
        return Room(max_requests=MAX_REQUESTS["local"], wrap_up_at=WRAP_UP_AT["local"])
    if context_tokens and context_tokens < LARGE_CONTEXT:
        return Room()  # a small server model: passages, as on this computer
    scale = min(max(context_tokens or REFERENCE_CONTEXT, LARGE_CONTEXT), MAX_SCALED_CONTEXT) / REFERENCE_CONTEXT
    return Room(large=True, search_results=8, read_chars=min(int(12_000 * scale), 40_000), whole_chars=6_000,
                presearch_hits=4, passage_hits=3, passage_chars=min(int(8_000 * scale), 24_000),
                outline_chars=min(int(16_000 * scale), 40_000), history_answer_chars=4_000,
                guide_chars=10_000, notes_chars=6_000, wrap_chars=8_000, wrap_total=min(int(40_000 * scale), 100_000),
                attachment_preview=min(int(12_000 * scale), 40_000), max_requests=16, wrap_up_at=13)

CHAT_INSTRUCTIONS = """\
You are Lattice, an assistant for the staff of a manufacturing company.

You can search and read the company's documents (manuals, reports, meeting notes, spreadsheets and \
more); the installed documents are listed below. For any question the documents could cover, use \
the sections provided with the question or search the documents, read the most relevant sections, \
and answer from them. After each fact taken from a section, put that section's reference in square \
brackets exactly as given, for example [S2]. References are internal: never mention them otherwise, \
and never ask the user for one. A line "[Figure: ...]" describes a picture and was written by a machine, not by the documents' authors: use it only for what the picture shows, never as what the documents say something is or does.

{general}

Tools: search_manuals finds sections by keywords; semantic_search (when available) finds them by \
meaning, for questions worded differently from the documents or in another language. read_section \
reads a section's own text and lists its subsections (read those for more; around="words" gives only \
the passages near those words in a long section). manual_outline maps a document: its sections, \
which ones hold tables and pictures; use it to find your way in a long document or to summarize it. \
find_facts lists the sentences and table rows stating limits, amounts, deadlines and conditions \
about a topic: use it for "how long / how much / am I allowed / what is the rule". For any \
comparison, ranking, total or average over a table, call query_table (it filters, sorts and adds up \
exactly) - never compare rows in your head; read_table returns all of a section's tables as rows. \
Do any other arithmetic (sums, differences, percentages, shares, dates) with calculate. You may \
call several tools at once (for example one search per part of a question). list_documents lists \
documents by type, folder, name or date ("the latest meeting notes"). read_attachment reads files \
the user attached. To show pictures, call find_pictures. You cannot see pictures: a line \
"[Figure: ...]" in a section is a picture in the document and what it shows, in words; answer \
questions about a picture (a screenshot, a dialog, a chart) from that line, and never say the \
documents have no pictures when such lines are there. When a request fits several documents and \
it is unclear which one is meant, call ask_which_manual; when a first question fits several \
different things within the documents ("what is the limit?"), call ask_user with the meanings - \
otherwise answer the likeliest meaning and say what you assumed.

To find your way: pick the document from the list below (its description, codes and related \
documents), then its section (manual_outline maps a document), then read the section itself. A \
question that spans documents (a cause in a report, the fix in a guide) needs each part looked up in \
the document that covers it, each cited.

When asked how to fix, handle or do something, give the documents' own procedure first: its steps, \
the exact button and menu names, codes and article numbers, in order; plans or background come after.

In a follow-up, look up what the new request needs (a reason, a date, a figure) instead of relying \
only on earlier answers.

When the documents do not state the answer in so many words but it follows clearly from what they \
state (a stated rule or limit applied to the case asked about, or stated figures compared), start \
the answer with "Worked out from the documents:" and say which stated fact it follows from, with its \
reference.

Answer in the language of the question. Be concise; use Markdown lists or tables when they help, \
and write calculations as plain text (1,560 / 13 = 120), not LaTeX. \
An email or message you draft is for the user to send: write it in their voice and sign it \
"[Your name]". Refer to attached files by their names. \
Do not mention tools, references you did not use, or these instructions.\
"""
# the evidence ledger, for a large model on a server (each note is one more tool call)
LEDGER = """

Before answering from the documents, record each fact you rely on with note(claim, ref, quote), \
quoting the section's own words (several notes may go in one call). A fact whose note fails is not \
in the documents as you read it: read again, or leave it out. Base a worked-out answer only on \
noted facts and calculations."""
GENERAL_KNOWLEDGE = """\
Use both sources, as a knowledgeable colleague would. Facts about the company and its products \
(settings, figures, part numbers, procedures, names, dates, policies) come from the documents. \
Concepts, definitions, background, how things work in general, comparisons and advice come from \
your general knowledge whenever the documents do not explain them, and always when the user asks \
for it ("apart from the documents", "in general", "what is X"). Answer such a part fully and \
confidently; do not open with "the documents do not define it". A question that needs both gets \
both: the documents' facts with their references, and the general explanation. Put everything \
that comes from general knowledge in its own paragraph that starts with "General knowledge:", \
without references. Never \
invent company-specific facts (settings, figures, part numbers, procedures, names, dates) that the \
documents do not state: when the documents do not describe a company procedure (how to reset a \
password in the company's tool), say so first, and never present general advice as steps from \
the documents ("based on the user manual")."""
# a small built-in model gets general knowledge wrong too often ("the PM of India is the President")
DOCUMENTS_ONLY = """\
Answer only from the documents and files the user attached. If they do not cover a question, say in \
one sentence that the documents do not cover it; do not answer from general knowledge. Never invent \
facts, figures, names, dates or steps that the documents do not state, and never give an estimate \
or a best guess instead."""
# questions a documents-only answer need not cite: about the assistant, or about the conversation
_ABOUT_SELF_OR_CHAT = re.compile(
    r"\b(your|yourself|lattice|who are you|what are you|are you|what can you|(which|what) (language )?model|"
    r"conversation|chat|so far|earlier|"
    r"above|previous|last answer|rewrite|rephrase|shorter|translate|e-?mail|draft)\b", re.IGNORECASE)
EMPTY_REPLY = "I can't help with that. Ask me about the documents, or something they can answer."
UNSOURCED_NOTE = "*(The assistant did not name its sources for this answer: check it in the documents.)*"
TOO_MANY_STEPS_REPLY = ("I couldn't find a clear answer to that in the documents. Try asking more specifically, "
                        "for example about one product, error or result.")
RELEVANT_MEANING = 0.40  # closeness in meaning above which a question is about the documents


def about_documents(deps: "ChatDeps") -> bool:
    """Is the question about what the documents cover? Measured on the three sample bundles with
    EmbeddingGemma: questions about them score 0.46-0.69 (reworded and Korean ones too), unrelated
    ones ("who is the PM of India", "tell me a joke", "what is Wi-Fi 6") 0.00-0.33. Keyword scores
    do not separate them (an exchange-rate question scored above some real ones): without search by
    meaning, any word in common counts."""
    if deps.meaning_top is not None:
        return deps.meaning_top >= RELEVANT_MEANING
    return deps.keyword_top > 0
# questions only live information answers; asked of a model with no internet, it invents one
LIVE_QUESTION = re.compile(
    r"\b(weather|forecast|raining|snowing|headlines?|news|stock price|share price|sensex|nifty|"
    r"exchange rate|(live|latest|today'?s?) (score|rate|price)s?|"
    r"what('?s| is) (the )?(time|date)( (now|today))?|today'?s date|what day is (it|today)|"
    r"temperature (outside|today)|"
    # "USD to INR", "dollar vs rupee", "price of gold"
    r"(usd|inr|eur|gbp|jpy|aed|dollars?|rupees?|euros?|pounds?|yen)\s*(to|in|vs\.?|/|against)\s*"
    r"(usd|inr|eur|gbp|jpy|aed|dollars?|rupees?|euros?|pounds?|yen)|"
    r"(gold|silver|bitcoin|crypto|petrol|diesel|fuel) (price|rate)s?|"
    r"price of (gold|silver|bitcoin|petrol|diesel))\b", re.IGNORECASE)
_LIVE_FILLER = re.compile(r"\b(rate|rates|price|prices|today'?s?|latest|current|currently|now|live|right|what|whats|"
                          r"is|are|the|a|an|of|for|me|tell|show|give|please|in|on)\b", re.IGNORECASE)


def live_only(question: str, index) -> bool:
    """A question only live information answers ("USD to INR rate", "latest news"), with no topic
    of the documents beside it: what is left after the live words and fillers finds nothing."""
    if not LIVE_QUESTION.search(question):
        return False
    rest = _LIVE_FILLER.sub(" ", LIVE_QUESTION.sub(" ", question))
    return not re.search(r"\w", rest) or not index.search(rest)


LIVE_REPLY = ("I don't have live information such as weather, news, prices or today's date: I can only "
              "use the documents and what the language model already knows.")
SMALL_MODEL_REPLY = ("I can only answer questions about the documents with the language model on this computer. "
                     "General questions, and longer work such as essays or charts, need a model server on the "
                     "network; your IT team can connect one in Manage.")
WORK_TOPICS_ONLY = (
    "\n\nScope: help with work only: the company's documents, and work topics (manufacturing, machines "
    "and tools, maintenance, quality, safety, using the company's software and IT systems, office work). "
    "Decline anything else (entertainment, sports, personal life, politics, trivia unrelated to work) "
    "with one or two sentences that start with \"" + "Sorry, I can only help with questions about your "
    "documents and work topics" + "\" and say what you can help with."
)
# what the assistant does not do, whatever the topic setting: users asked it for a Python script and to
# book train tickets (27 Oct 2026); it writes text here and reads the documents, nothing else
CANNOT_DO = (
    "What you do not do, whatever the topic: you do not write, fix or review program code or scripts "
    "(explaining a setting or a script mode the documents describe is fine); you cannot act outside this "
    "app - you cannot book travel or tickets, buy anything, send emails or messages, schedule meetings, or "
    "change settings or systems: you only read the documents and write text here; you do not give "
    "personal medical, legal or investment advice. For such a request, answer in one or two sentences "
    "that start with \"Sorry, I can only help with questions about your documents and work topics\", say "
    "plainly what you cannot do, and offer what you can instead (the steps the documents give for it, "
    "or a draft for the user to send themselves). "
    "Say plainly what you do not know: when the user asks about the company and the documents do not "
    "cover it, say so (\"The documents here don't cover ...\") and, where it helps, what they do cover; "
    "never guess company facts.")

# a follow-up that leans on the previous question ("Is it paid?", "and with Care+?")
_REFERS_BACK = re.compile(r"\b(it|its|that|this|these|those|they|them|their|there|same|also)\b"
                          r"|^\W*(and|but|so|what about|how about)\b", re.IGNORECASE)
PRESEARCH_HITS = 3  # sections found by search before the model starts, for models that do not search
CONTEXT_HITS = 2  # in a conversation: more sections, found with the previous turn's words
MAX_EVIDENCE = 4  # checked quotes shown under a Chat answer
PART_HITS = 2  # per part of a question that asks several things (see plan.py)
PASSAGE_HITS = 2  # a model on this computer gets the passages of the best matches themselves
PASSAGE_CHARS = 900
CONTEXT_SEARCH_CHARS = 400  # of the previous answer, searched with the follow-up
_REF_MARKS = re.compile(r"\[\d+\]|\[S\d+\]")
OUTLINE_CHARS = 8_000     # a document's table of contents given to the model
PICTURES_LISTED = 6
PictureFinder = Callable[[str], list[tuple]]  # topic -> pictures (doc index, node id, page, image)
AttachmentReader = Callable[[str], "str | None"]  # attachment id -> its text

_REF = re.compile(r"\[(S\d+)\]")
# a "References:" list of bare markers at the end ("* [1]"): the sources are shown anyway
_BARE_REFS = re.compile(r"\n+(?:\**\s*(?:references|sources)\s*:?\s*\**\s*\n)?"
                        r"(?:\s*[-*]?\s*(?:\[S?\d+\]\s*,?\s*)+\n?)+\s*$", re.IGNORECASE)

TOOL_NAMES = ("find_pictures", "search_manuals", "read_section", "manual_outline", "ask_which_manual",
              "semantic_search", "list_documents", "read_table", "calculate", "read_attachment")
# a line about the assistant's own tools ("To show pictures, call find_pictures: ..."): small models
# sometimes copy a paragraph of their instructions into the answer
_TOOL_TALK = re.compile(r"^[^\n]*\b(" + "|".join(TOOL_NAMES) + r")\b[^\n]*$", re.MULTILINE)
# "Based on the documents ... there is no information regarding the weather." A small model says
# this at length, restating it and inventing what the documents are about instead
_NOT_COVERED = re.compile(
    r"\b(there is|there's|i found|they contain|contains?) no (information|data|details|mention)\b|"
    r"\b(documents?|manuals?|files?)\b[^.]{0,80}\b(do|does|did)\s*(not|n't)\s+"
    r"(contain|cover|mention|include|provide|say|discuss|address)", re.IGNORECASE)
# the model declining in its own words: a reply, not an answer to cite
_MODEL_DECLINE = re.compile(
    r"^\W*(i can only (answer|help)|i (don'?t|do not) have (any )?(live|real-time|current|access)|"
    r"i can'?t (help|answer|provide)|i cannot (help|answer|provide)|i'?m (not able|unable) to (help|answer|provide))",
    re.IGNORECASE)
_NOTE_REF = re.compile(r"\[N(\d+)\]")
NOTE_USED_SHARE = 0.6  # of a noted quote's words in an answer that left its section unmarked
QUOTE_CHARS = 320  # of a checked quote shown under the answer


def _quote_text(quote: str) -> str:
    """A noted quote as shown: Markdown fences and headings out, one line, not too long."""
    text = " ".join(re.sub(r"```\w*|^#+\s*", " ", quote, flags=re.MULTILINE).split())
    return text if len(text) <= QUOTE_CHARS else text[:QUOTE_CHARS].rsplit(" ", 1)[0] + " ..."

# an option of a question back that is no question: "Something else", "Other (please specify)", "None of these"
_NO_OPTION = re.compile(r"^\W*(?:something\s+else|some\s*thing\s+other|other\b|others\b|none\s+of\b|neither\b|"
                        r"not\s+listed|anything\s+else|else\b)", re.IGNORECASE)

# "Worked out from the documents: ..." (see CHAT_INSTRUCTIONS): shown as a label, not as text
# at the start, or as a line or lead-in of its own further down ("...in March.\n\nWorked out from
# the documents:\n* January ...")
_WORKED_OUT = re.compile(r"(?:^|(?<=\n))[^\w\n]*worked out from the documents[^\w\n]*", re.IGNORECASE)
# an inference goes on from what the documents lack: "... does not say this directly, but ..."
_INFERENCE_TURN = re.compile(r"\b(but|however|although|so|therefore|which means|this means|since)\b", re.IGNORECASE)
NOT_COVERED_REPLY = "The documents on this computer don't cover that."
NOT_COVERED_MAX = 700  # longer answers with such a sentence usually answer part of the question


# "Sorry, I can only help with work topics ...": a decline, not an answer
_DECLINE = re.compile(r"^\W*(sorry|i'?m sorry|i am sorry)\b[^.]{0,60}\bcan only (help|assist)\b[^.]{0,70}\bwork",
                      re.IGNORECASE)
# ... added after an answer ("... is not reimbursed.\n\nSorry, I can only help with work topics."; the
# 0.8B in the complex eval): the answer stands, the decline goes
_TRAILING_DECLINE = re.compile(r"\n\s*\W*(?:sorry|i'?m sorry|i am sorry)\b[^.\n]{0,60}\bcan only (?:help|assist)\b"
                               r"[^\n]{0,80}$", re.IGNORECASE)


def without_trailing_decline(answer: str) -> str:
    cut = _TRAILING_DECLINE.sub("", answer.rstrip()).rstrip()
    return cut if cut and not _DECLINE.match(cut) else answer


# a paragraph of general knowledge, as the instructions ask for it: shown set apart as a quote
_GENERAL = re.compile(r"^(?:\*\*|__|\*|_)?general knowledge:?(?:\*\*|__|\*|_)?:?\s*", re.IGNORECASE)


def _about_yourself(model_label: str, documents_only: bool = False) -> str:
    """So that "which model are you?" and "what can you do?" get a true answer, not a guess from
    the documents (small models cite random sections for it), and "plot a chart" is not answered
    with a made-up table."""
    model = f"The language model answering is {model_label}. " if model_label else ""
    general = ("" if documents_only else
               "; general questions, answered from general knowledge and marked as such")
    return ("\n\nAbout yourself: you are Lattice, the company's document assistant, an app on the user's "
            f"computer. {model}If asked what you are or which model you use, say this; it is not in the documents. "
            "If asked what you can do, say it without citing anything: search and read the installed documents "
            "and answer with their sources; compare tables and calculate; show the pictures in the documents; "
            f"read files the user attaches; write summaries, emails and other texts from them{general}. "
            "You cannot draw charts or images: if asked for one, say so, show the documents' own pictures "
            "of it with find_pictures if there are any, and give only the values the documents state, each "
            "for exactly the item it belongs to. "
            f"Today is {datetime.date.today():%A, %B %d, %Y}. You cannot look anything up online: for live "
            "values (exchange rates, prices, news, weather) say that you have no live data and where to check, "
            "and do not give a current value. " + CANNOT_DO)


def _without_tool_talk(answer: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", _TOOL_TALK.sub("", answer)).strip()


# a model's thinking left in its reply: Qwen 3.x on a server started without a reasoning parser writes
# "<think>...</think>" before the answer; an unclosed one is thinking that was cut off
_THINKING = re.compile(r"<(think|thinking|reasoning)>.*?(?:</\1>|\Z)", re.IGNORECASE | re.DOTALL)
_OPENING = ("<think>", "<thinking>", "<reasoning>")
# a tool call written as text, by a server that does not turn the model's tool calls into calls:
# Qwen's "<tool_call>{...}</tool_call>", or a bare {"name": ..., "arguments": ...}
_TEXT_TOOL_CALL = re.compile(r'<tool_call>|^\s*\{\s*"name"\s*:\s*"[a-z_]+"\s*,\s*"(?:arguments|parameters)"',
                             re.IGNORECASE)


def without_thinking(text: str) -> str:
    return _THINKING.sub("", text or "").strip()


def visible_so_far(written: str) -> str:
    """What may be shown of a reply while it streams: no thinking, nor a tag that may be opening one."""
    shown = _THINKING.sub("", written)
    tail = shown[shown.rfind("<"):] if "<" in shown[-12:] else ""
    if tail and any(o.startswith(tail.lower()) for o in _OPENING):
        shown = shown[: len(shown) - len(tail)]
    return shown.lstrip()


# "[Source: S4 – Top Contact Drivers table]", "Sources: S2, S5" on a line of its own
_SOURCE_LINE = re.compile(r"(?im)^[ \t>*_]*[\[(]?\**(?:sources?|references?)\**\s*[:\-–]\s*([^\n]*\bS\d+\b[^\n]*)$")


def sources_line_to_markers(answer: str) -> str:
    """A closing "Source: S4 ..." line becomes a [S4] marker after the text above it (the app shows
    the sources itself, and a bare S4 would stay in the answer)."""
    def marker(m: re.Match) -> str:
        return "\0" + " ".join(f"[{r}]" for r in dict.fromkeys(re.findall(r"S\d+", m.group(1))))
    answer = _SOURCE_LINE.sub(marker, answer)
    return re.sub(r"\s*\n\s*\0", " ", answer).replace("\0", "").strip()


# a closing "Sources:" heading over a list that quotes them again (the 0.8B, final check): the app
# lists the sources under the answer; its markers are kept, after the text above it
_SOURCES_BLOCK = re.compile(r"(?im)\n[ \t#>*_]*(?:sources?|references?|citations?)[ \t*_]*:?[ \t*_]*\n(?:.*(?:\n|$))*\Z")


def without_sources_block(answer: str) -> str:
    m = _SOURCES_BLOCK.search(answer)
    # only a block that cites or quotes again: "Sources:\n- leave_policy.pdf" may be the answer itself
    # ("which documents mention ...?")
    if not m or not answer[:m.start()].strip() or not re.search(r"\[S?\d+\]|[\"“”]", m.group(0)):
        return answer
    body = answer[:m.start()].rstrip()
    markers = " ".join(r for r in dict.fromkeys(re.findall(r"\[S?\d+\]", m.group(0))) if r not in body)
    return (body + (" " + markers if markers else "")).strip()


# "Regarding your question about days of privilege leave: ..." in the answer to "Which company replaced
# the belt?": a small model answering an earlier question of the conversation again (final check)
_OTHER_QUESTION = re.compile(r"^\W*(?:regarding|as for|about|on|for|concerning)\s+your\s+(?:earlier\s+|previous\s+|other\s+)?"
                             r"question\s+(?:about|on|regarding)\s+([^:,.\n]{3,80})", re.IGNORECASE)


def without_other_questions(answer: str, question: str) -> str:
    """The answer without paragraphs that answer another question than this one."""
    asked = set(tokenize(question))
    kept = []
    for para in re.split(r"\n\s*\n", answer):
        m = _OTHER_QUESTION.match(para)
        if m and not ({t for t in tokenize(m.group(1)) if len(t) > 3} & asked):
            continue
        kept.append(para)
    return "\n\n".join(kept) if kept else answer


# "... was 52.3 % [1]. [1] Overall equipment effectiveness (OEE) averaged 72.4 % ... [1] April | 80.9 |
# 76.4 | 52.3": the sections' own lines pasted after the answer, each after its marker (Nemotron)
_QUOTED_TAIL = re.compile(r"(?:(?<=[.!?)\]*])|\n)\s*(\[\d+\]\s+[^\[\n]{12,})$")
QUOTED_SHARE = 0.8  # this share of a tail's words in the sections read: a copy of them, not the answer


def without_quoted_tail(answer: str, read: str) -> str:
    """The answer without lines copied from its sources after it (the app shows the sources)."""
    have = set(tokenize(read))
    while True:
        m = _QUOTED_TAIL.search(answer.rstrip())
        if not m:
            return answer
        words = set(tokenize(re.sub(r"^\[\d+\]", "", m.group(1))))
        if not words or len(words & have) < QUOTED_SHARE * len(words) or not answer[:m.start(1)].strip():
            return answer
        answer = answer.rstrip()[:m.start(1)].rstrip()


_DOLLAR_MATH = re.compile(r"\$(?!\s)([^$\n]{1,160}?[=×*/÷\\^][^$\n]{0,160}?)(?<!\s)\$")


def plain_math(answer: str) -> str:
    """LaTeX the chat window cannot show ("\\[ \\frac{1,560}{13} = 120 \\text{ tickets} \\]") as plain text.
    "$14 / 7 = 2$" (single dollars around a calculation) loses its dollars; "$5 to $10" keeps them."""
    answer = _DOLLAR_MATH.sub(lambda m: m.group(1), answer)
    if "\\" not in answer:
        return answer
    text = re.sub(r"\\d?frac\{([^{}]*)\}\{([^{}]*)\}", r"\1 / \2", answer)
    text = re.sub(r"\\(?:text|mathrm|mathbf|operatorname)\{([^{}]*)\}", r"\1", text)
    # "12{,}000 × \pi / 30 = 400\pi \ rad/s" (Nemotron, OpenRouter check): the thousands brace, \pi, a
    # backslash space
    text = text.replace("{,}", ",")
    for tex, plain in (("\\times", "×"), ("\\cdot", "·"), ("\\approx", "≈"), ("\\le", "≤"), ("\\ge", "≥"),
                       ("\\%", "%"), ("\\,", " "), ("\\;", " "), ("\\pi", "π"), ("\\ ", " "),
                       ("\\degree", "°"), ("^\\circ", "°"), ("\\pm", "±")):
        text = text.replace(tex, plain)
    text = re.sub(r"\\\[\s*(.*?)\s*\\\]", r"\1", text, flags=re.S)  # display math \[ ... \]
    text = re.sub(r"\\\(\s*(.*?)\s*\\\)", r"\1", text, flags=re.S)  # inline math \( ... \)
    text = re.sub(r"\$\$\s*(.*?)\s*\$\$", r"\1", text, flags=re.S)
    return re.sub(r"(?<=\S)  +(?=\S)", " ", text)


# a drafted message ends with a signature; other answers must not ("[Your name]" after a refusal)
_MESSAGE = re.compile(r"(?im)^\s*(subject:|dear |hi |hello |to:)|(best regards|kind regards|regards,|sincerely|thanks,)")


# a search result given with the question, copied into the answer by a small model:
# "--- [S1] employee_handbook_2026.pdf > 3 Working Hours and Leave (p. 4): ...ive within 50 km..."
_ECHOED_EXCERPT = re.compile(r"(?m)^[ \t]*(?:-{3,}[ \t]*)?\[S?\d+\] [^\n]*? > [^\n]*?: [^\n]*$\n?")


def without_echoed_excerpts(answer: str) -> str:
    text = _ECHOED_EXCERPT.sub("", answer)
    if text == answer:
        return answer
    return re.sub(r"(?:\n[ \t]*-{3,}[ \t]*)+\s*$", "", text.rstrip()).rstrip()


def without_stray_signature(answer: str) -> str:
    if answer.rstrip().endswith("[Your name]") and not _MESSAGE.search(answer):
        return answer.rstrip()[:-len("[Your name]")].rstrip()
    return answer


def mark_general(answer: str) -> tuple[str, bool]:
    """Paragraphs that start with "General knowledge:" become a quote with a bold label."""
    found = False
    out = []
    # a "General knowledge:" line inside a paragraph starts a paragraph of its own (the server model
    # wrote "... the limits for action. [1]  \n*General knowledge:* Backlash is ..." in the final test)
    answer = re.sub(r"(?im)\n(?=[ \t]*(?:\*\*|__|\*|_)?general knowledge:)", "\n\n", answer)
    for para in re.split(r"\n\s*\n", answer):
        stripped = para.strip()
        if _GENERAL.match(stripped):
            found = True
            body = _GENERAL.sub("", stripped, count=1).strip()
            out.append("\n".join("> " + line for line in f"**General knowledge:** {body}".splitlines()))
        else:
            out.append(para)
    return "\n\n".join(out), found


def normalize_refs(answer: str) -> str:
    """Every way models write a reference, as [S1]: 【S1】, 【S1†L1-L3】, [S1†L1-L3], ［S1］,
    "Section S1", "sections S1 and S2", "(S2)", "[S2, S3]", "[S6/S7]", "**S2** and **S4**"."""
    answer = re.sub(r"\*\*\s*\[?(S\d+(?:\]?\s*(?:,|;|/|and|&)\s*\[?S\d+)*)\]?\s*\*\*",
                    lambda m: " ".join(f"[{r}]" for r in re.findall(r"S\d+", m.group(1))), answer)
    answer = re.sub(r"[【［]\s*(S\d+)[^】］\n]{0,40}[】］]", r"[\1]", answer)
    answer = re.sub(r"\[\s*(S\d+)\s*†[^\]\n]{0,40}\]", r"[\1]", answer)
    # "[S3, Part 2, Clause 2.1]", "[S2: Delivery record]": a reference with where in it, as [S3]
    answer = re.sub(r"\[\s*(S\d+)\s*[,;:](?!\s*S\d)[^\]\n]{1,60}\]", r"[\1]", answer)
    answer = re.sub(r"\b[Ss]ections?\s+(S\d+(?:\s*(?:,|and|&)\s*S\d+)*)\b",
                    lambda m: " ".join(f"[{r}]" for r in re.findall(r"S\d+", m.group(1))), answer)
    return re.sub(r"[\[(]\s*(S\d+(?:\s*(?:,|;|/|and|&)\s*S\d+)*)\s*[\])]",
                  lambda m: " ".join(f"[{r}]" for r in re.findall(r"S\d+", m.group(1))), answer)


def name_attachments(answer: str, attachments: list[Attachment]) -> str:
    """"(attachment F1A2B3C)", "[F1A2B3C]" -> the file's name: the id is for the tools, not the user."""
    for att in attachments:
        answer = re.sub(rf"[\[(]\s*(?:attachment|file)?\s*{att.id}\s*[\])]", f"({att.name})", answer, flags=re.I)
        answer = re.sub(rf"\b(?:(?:attachment|file)\s+)?{att.id}\b", att.name, answer, flags=re.I)
    return answer


EventFn = Callable[[dict], Awaitable[None]]


def _transient(e: Exception) -> bool:
    """Worth one more try: the endpoint was busy, dropped the connection or failed mid-reply."""
    if isinstance(e, ModelHTTPError):
        return e.status_code == 429 or e.status_code >= 500
    return isinstance(e, (ModelAPIError, openai.APIError))


_CODE = re.compile(r"\b(?=[A-Z0-9-]*\d)[A-Z][A-Z0-9]*(?:[-.][A-Z0-9]+)+\b|\b[A-Z]{1,3}\d{1,4}[A-Z]?\b")
SUBJECT_CODES = 4
SUBJECT_SECTIONS = 3
SUBJECT_CHARS = 4_000


def subject_of(docs: list[Doc], hits, part: str) -> str:
    """What a question's first part is about, for a later part that says "it": the codes its best
    sections name most ("E-217 R7"), else the part's own words. The spike's summary names only R7;
    its ticket-volume section names E-217 too, which finds 4.3 Firmware Update Failure."""
    asked = set(_CODE.findall(part))
    counts: dict[str, int] = {}
    for h in hits[:SUBJECT_SECTIONS]:
        for code in _CODE.findall(docs[h.doc_index].section_text(h.node_id, SUBJECT_CHARS)):
            if code not in asked and not re.fullmatch(r"Q[1-4]|H[12]", code):
                counts[code] = counts.get(code, 0) + 1
    codes = sorted(counts, key=lambda c: -counts[c])
    return " ".join(codes[:SUBJECT_CODES]) or part


MAX_REFERENCED = 2
_REFERS_TO = re.compile(r"\b(?:sections?|chapters?|clauses?|see|in|under|per|of|from)\s+(\d{1,2}(?:\.\d{1,2}){0,2})(?![\d.]*\d)(?!\s*(?:%|days?|weeks?|months?|years?|hours?|h\b|mm|bar|kg|°))",
                        re.IGNORECASE)


def referenced_sections(doc: Doc, nid: str) -> list[str]:
    """The sections of the same document that this section names by number ("the rules in 5.2",
    "according to chapter 7"), in the order named."""
    text = doc.section_text(nid, 20_000)
    by_number = {}
    for other, sec in doc.sections.items():
        m = re.match(r"^\s*(?:chapter\s+)?(\d{1,2}(?:\.\d{1,2}){0,2})\.?\s", sec.title, re.IGNORECASE)
        if m:
            by_number.setdefault(m.group(1), other)
    own = re.match(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,2})", doc.sections[nid].title)
    out = []
    for m in _REFERS_TO.finditer(text):
        target = by_number.get(m.group(1))
        if target and target != nid and target not in out and not (own and m.group(1) == own.group(1)):
            out.append(target)
    return out


# a question that asks for general knowledge itself: "apart from the documents, what is ...", "in general"
GENERAL_ASK = re.compile(
    r"\b(?:apart|aside|besides|other than|outside(?: of)?|beyond|not)\s+(?:from\s+)?(?:the|our|these|your|what)?\s*"
    r"(?:documents?|docs?|manuals?|files?|knowledge base|the documents say)\b|\bin general\b|\bgenerally\b|"
    r"\bgeneral(?:ly)? speaking\b|\bin simple terms\b|\b(?:from|using|with) (?:your |general |common )+knowledge\b|"
    r"\b(?:the )?concept of\b|\bin layman'?s terms\b", re.IGNORECASE)
# a paragraph the model wrote from general knowledge but did not label ("In general, 5 GHz is faster ...")
_GENERAL_LEAD = re.compile(r"^\s*(?:\*\*|__)?(?:in general|generally(?: speaking)?|in simple terms|broadly speaking|"
                           r"as a general rule|in layman'?s terms)\b[,:]?", re.IGNORECASE)


def label_general_leads(answer: str) -> str:
    """Paragraphs that open "In general, ..." and cite nothing get the "General knowledge:" label."""
    out = []
    for p in re.split(r"\n\s*\n", answer):
        if _GENERAL_LEAD.match(p) and not re.search(r"\[S?\d+\]", p) and not _GENERAL.match(p.strip()):
            p = "General knowledge: " + p.strip()
        out.append(p)
    return "\n\n".join(out)


_HEDGE = re.compile(r"^\W*(?:the |our )?(?:provided )?(?:documents?|manuals?|guides?)\b[^.\n]{0,40}\b(?:do|does)\s*(?:not|n't)\s+"
                    r"(?:explicitly |specifically |directly )?(?:define|explain|describe|cover|say what)\b[^.\n]{0,80}[.:]\s*$",
                    re.IGNORECASE)


def without_hedge_before_general(answer: str) -> str:
    """"The documents do not define a C wire." alone in a paragraph (or line), followed by the general
    explanation of it: the explanation is the answer; the hedge goes (the general-knowledge label
    already says where it comes from)."""
    if not re.search(r"(?im)^[ \t>*_]*general knowledge:", answer):
        return answer
    out = []
    for para in re.split(r"\n\s*\n", answer):
        lines = [ln for ln in para.split("\n") if not _HEDGE.match(ln.strip())]
        if lines and any(ln.strip() for ln in lines):
            out.append("\n".join(lines))
    return "\n\n".join(out)


DRAWN_SHARE = 0.5  # of an answer's words found in what was read, for it to be drawn from it


def drawn_from(answer: str, read: str) -> bool:
    """Most of the answer's own words are in what was read: written from it ("Click Propose, then
    correct the mistakes"), not from general knowledge ("Torque is a turning force")."""
    words = {w for w in tokenize(re.sub(r"\[\d+\]", " ", answer)) if len(w) > 3 and w not in _ASKING}
    return bool(words) and len(words & set(tokenize(read))) >= DRAWN_SHARE * len(words)


def whole_section(doc: Doc, nid: str, chars: int) -> str:
    """A section with its subsections, up to `chars` (a large model's own reading)."""
    text = doc.section_text(nid, chars + 1)
    return text if len(text) <= chars else text[:chars].rsplit(" ", 1)[0] + " [... continues: read_section part=2]"


@dataclass
class ChatDeps:
    docs: list[Doc]
    index: SectionIndex
    allowed: set[int] | None
    refs: dict[str, tuple[int, str]] = field(default_factory=dict)  # "S3" -> (doc index, node id)
    # how well the question itself matches the documents (see about_documents)
    keyword_top: float = 0.0
    meaning_top: float | None = None  # None: no search by meaning
    read: dict[str, str] = field(default_factory=dict)  # ref -> text the model read
    # (ref, part, around, whole) of every read: the same read again gets a pointer, not the text again
    # (Nemotron read S1 four times in a row for "stuck labelling images", 10 calls and 64 s; 26 Oct 2026)
    reads: set = field(default_factory=set)
    shown: dict[str, str] = field(default_factory=dict)  # ref -> snippet given with the question
    find_pictures: PictureFinder | None = None
    pictures: list[tuple] = field(default_factory=list)  # pictures to show with the reply
    clarify: Clarification | None = None  # set by ask_which_manual
    semantic: SemanticIndex | None = None
    attachments: list[Attachment] = field(default_factory=list)
    attachment_text: AttachmentReader | None = None
    worked: list[str] = field(default_factory=list)  # attachment text read: a source too
    computed: list[str] = field(default_factory=list)  # calculate results: match with any unit, rounded
    about: set[str] = field(default_factory=set)  # attachments this question is about (shown with it)
    files_used: list[str] = field(default_factory=list)  # names of attachments the answer could draw on
    documents_only: bool = False  # a small built-in model: no general-knowledge answers
    model_label: str = ""  # the language model answering ("Qwen3.5-4B-Q4_K_M, on this computer")
    # "remote": a large model on a server (more steps, the evidence ledger); "local": on this computer
    tier: str = "remote"
    notes: list[dict] = field(default_factory=list)  # the evidence ledger: {claim, ref, quote, ok}
    first_turn: bool = True  # the conversation's first question (only then may it ask back)
    # a sem-ai-kb knowledge base: its AGENTS.md (how to behave) and needs-review.md (known gaps)
    kb_guide: str = ""
    kb_notes: str = ""
    room: Room = field(default_factory=Room)

    def in_scope(self) -> list[int]:
        return [i for i in range(len(self.docs)) if self.allowed is None or i in self.allowed]

    def manual_named(self, name: str) -> int | None:
        """The document in scope whose title best matches a name the model gave."""
        scope = self.in_scope()
        if not scope:
            return None
        key = name.casefold().strip()
        score = {i: SequenceMatcher(None, key, self.docs[i].display_title.casefold()).ratio()
                 + (0.5 if key and key in self.docs[i].display_title.casefold() else 0) for i in scope}
        best = max(score, key=score.get)
        return best if score[best] >= 0.4 or len(scope) == 1 else None

    def ref_for(self, di: int, nid: str) -> str:
        for ref, target in self.refs.items():
            if target == (di, nid):
                return ref
        ref = f"S{len(self.refs) + 1}"
        self.refs[ref] = (di, nid)
        return ref

    def hit_rows(self, hits, query: str) -> list[dict]:
        out = []
        for h in hits:
            doc = self.docs[h.doc_index]
            out.append({"ref": self.ref_for(h.doc_index, h.node_id), "document": doc.display_title,
                        "section": " > ".join(doc.short_path(h.node_id)), "location": doc.locator(h.node_id),
                        "snippet": self.index.snippet(h.doc_index, h.node_id, query)})
        return out


_WHOLE_TABLE = re.compile(r"\b(highest|lowest|maximum|minimum|max|min|peak|total|sum|average|mean)\b", re.IGNORECASE)
REPEAT_SIMILARITY = 0.8  # an answer this close to the last one, to a different question, is a repeat


def repeats_last(question: str, answer: str, last: tuple[str, str]) -> bool:
    """Is the answer the previous answer again, given to a question about something else?"""
    last_q, last_a = last
    asked, before = set(tokenize(question)), set(tokenize(last_q))
    if not last_a or (asked and len(asked & before) >= 0.5 * len(asked)):
        return False  # the same question again, reworded: the same answer is right
    # source marks "[1]", list marks "(1)" and "1." are not figures
    norm = lambda t: " ".join(re.sub(r"(?m)\[\d+\]|\(\d+\)|^\s*\d+[.)]\s|[*_`#]", " ", t).split()).casefold()[:800]  # noqa: E731
    if not numbers_in(norm(last_a)) <= numbers_in(norm(answer)):
        # "The revenue in March was 291,400" after "... January was 248,500": a new answer. One that
        # has all the last one's figures may add some ("10 holidays (8 fixed and 2 optional)")
        return False
    if SequenceMatcher(None, norm(answer), norm(last_a)).ratio() >= REPEAT_SIMILARITY:
        return True
    # the same figures in other words: about the last question's topic and not this one's ("the
    # minimum RAM is 4 GB ... 10 holidays", reworded, for "how much does it cost per user?")
    topic = lambda t: {w for w in tokenize(t) if len(w) > 3 and w.isalpha() and w not in _ASKING}  # noqa: E731
    said, new, old = topic(answer), topic(question) - topic(last_q), topic(last_q) - topic(question)
    return bool(numbers_in(norm(answer))) and bool(new) and not new & said and len(old & said) >= 2


def _replaced_note(deps: ChatDeps, di: int, nid: str) -> str:
    """A note after a clause an amendment replaced, naming the replacing clause by its ref."""
    new = deps.index.replaced.get((di, nid))
    if new is None:
        return ""
    title = deps.docs[new[0]].sections[new[1]].title
    return (f"\n[REPLACED by [{deps.ref_for(*new)}] \"{title}\": this clause no longer applies; "
            "answer from the replacing one]")


def _citation(docs: list[Doc], di: int, nid: str) -> Citation:
    doc, s = docs[di], docs[di].sections[nid]
    pages = list(range(s.start_page, s.end_page + 1)) if doc.kind == "pages" else None
    return Citation(doc_id=doc.doc_id, doc_name=doc.display_title, node_id=nid, title=s.title,
                    locator=doc.locator(nid), pages=pages)


def history_messages(turns: list[tuple[str, str]], answer_chars: int = HISTORY_ANSWER_CHARS) -> list[ModelMessage]:
    """Earlier (question, answer) turns as chat messages, oldest first."""
    out: list[ModelMessage] = []
    for q, a in turns[-HISTORY_TURNS:]:
        a = a if len(a) <= answer_chars else a[:answer_chars] + " ..."
        out += [ModelRequest(parts=[UserPromptPart(content=q)]), ModelResponse(parts=[TextPart(content=a)])]
    return out


def _date_ok(added: str, after: str, before: str) -> bool:
    if not (after or before):
        return True
    if not added:
        return False
    return (not after or added >= after[:10]) and (not before or added <= before[:10])


@dataclass
class _WrapUp(WrapperToolset[ChatDeps]):
    """From the wrap-up call on, lookups reply that it is time to answer: the model answers from
    what it has gathered (before, reaching the step limit threw it all away). The tools stay
    listed, since a model that calls one anyway must not fail."""

    async def call_tool(self, name, tool_args, ctx: RunContext[ChatDeps], tool):
        if ctx.run_step >= ctx.deps.room.wrap_up_at and name not in _ALWAYS:
            return WRAP_UP_REPLY
        return await super().call_tool(name, tool_args, ctx, tool)


WRAP_UP_REPLY = ("Enough has been looked up: answer now from what you have, saying for any part the "
                 "documents did not answer that they do not cover it.")
_ALWAYS = {"note", "calculate", "ask_user", "ask_which_manual"}  # not lookups: fine to the end


class ChatAgent:
    def __init__(self, model: Model, *, max_tokens: int = 4000, work_topics_only: bool = True,
                 budget: RequestBudget | None = None, temperature: float = 0.2):
        self.budget = budget
        topics = WORK_TOPICS_ONLY if work_topics_only else ""
        self.tools: FunctionToolset[ChatDeps] = FunctionToolset()

        # parallel tool calls: several searches or reads in one model call (a two-part question looks
        # up both parts at once) - each call re-reads the whole conversation, so fewer calls is faster
        self.agent = Agent(model, deps_type=ChatDeps, toolsets=[_WrapUp(self.tools)],
                           model_settings={"temperature": temperature, "max_tokens": max_tokens,
                                           "parallel_tool_calls": True})
        # the answer from what was gathered, when the step limit is reached anyway
        self.writer = Agent(model, model_settings={"temperature": temperature, "max_tokens": max_tokens})

        # All instructions come from this one function: separate instruction blocks become separate
        # system messages, and some chat templates (Qwen's in llama.cpp) reject a second one.
        @self.agent.instructions
        def instructions(ctx: RunContext[ChatDeps]) -> str:
            d = ctx.deps
            base = CHAT_INSTRUCTIONS.replace("{general}", DOCUMENTS_ONLY if d.documents_only else GENERAL_KNOWLEDGE) + topics
            docs = [doc for i, doc in enumerate(d.docs) if d.allowed is None or i in d.allowed]
            if not docs:
                return base + _about_yourself(d.model_label, d.documents_only) + "\n\nNo documents are available for the selected station."
            lines = [f"- {doc.display_title}" + (f": {doc.description}" if doc.description else "")
                     + (f" [codes: {doc.meta['codes']}]" if doc.meta.get("codes") else "")
                     + (f" [related: {doc.meta['related']}]" if doc.meta.get("related") else "")
                     for doc in docs[:DOCUMENTS_LISTED]]
            more = (f"\n(and {len(docs) - DOCUMENTS_LISTED} more; list_documents lists them)"
                    if len(docs) > DOCUMENTS_LISTED else "")
            text = (base + (LEDGER if d.tier == "remote" else "") + _about_yourself(d.model_label, d.documents_only) +
                    "\n\nDocuments installed on this machine:\n" + "\n".join(lines) + more)
            if d.kb_guide:  # the knowledge base's own instructions; the rules above still come first
                text += ("\n\nInstructions that come with this knowledge base (follow them where they do not "
                         "conflict with the rules above):\n" + d.kb_guide[:d.room.guide_chars])
            if d.kb_notes:
                text += ("\n\nKnown gaps in the documents (do not guess these; say the documents do not cover "
                         "them):\n" + d.kb_notes[:d.room.notes_chars])
            if d.attachments:
                text += "\n\nFiles the user attached to this conversation (read them with read_attachment):\n" + \
                        "\n".join(f"- {a.id}: {a.name} ({a.chars:,} characters)" for a in d.attachments)
            return text

        async def when_semantic(ctx: RunContext[ChatDeps], tool: ToolDefinition) -> ToolDefinition | None:
            return tool if ctx.deps.semantic is not None and ctx.deps.semantic.ready else None

        async def when_attached(ctx: RunContext[ChatDeps], tool: ToolDefinition) -> ToolDefinition | None:
            return tool if ctx.deps.attachments and ctx.deps.attachment_text else None

        @self.tools.tool
        def search_manuals(ctx: RunContext[ChatDeps], query: str) -> list[dict]:
            """Search the company documents by keywords (names, part numbers, codes, exact terms).
            Returns the best-matching sections, each with a reference to read or cite."""
            d = ctx.deps
            hits = [h for h in d.index.search(query) if d.allowed is None or h.doc_index in d.allowed]
            return d.hit_rows(hits[:d.room.search_results], query) or [
                {"result": "No matching sections. Try other words" +
                           (", or semantic_search" if d.semantic is not None and d.semantic.ready else "") +
                           ", or answer without the documents."}]

        @self.tools.tool(prepare=when_semantic)
        async def semantic_search(ctx: RunContext[ChatDeps], query: str) -> list[dict]:
            """Search the company documents by meaning: finds sections worded differently from the
            query (synonyms, descriptions, other languages). Returns sections with references."""
            d = ctx.deps
            hits = await d.semantic.search(query, d.allowed, d.room.search_results)
            return d.hit_rows(hits, query) or [{"result": "Nothing close in meaning. Try search_manuals."}]

        async def when_remote(ctx: RunContext[ChatDeps], tool: ToolDefinition) -> ToolDefinition | None:
            return tool if ctx.deps.tier == "remote" else None

        @self.tools.tool
        def read_section(ctx: RunContext[ChatDeps], ref: str, part: int = 1, around: str = "",
                         whole: bool = False) -> str:
            """Read a document section by the reference a search gave (e.g. S2): its own text, then
            its subsections with their refs (read those for more). around: only the passages near
            these words (in the section and its subsections), for a long section. whole: the section
            with all its subsections at once. A long text comes in parts (part=2, ...)."""
            d = ctx.deps
            ref = ref.strip().strip("[]【】").upper()
            if ref not in d.refs:
                return f"Unknown reference {ref!r}. Use a ref returned by a search."
            key = (ref, part, " ".join(around.lower().split()), whole)
            if key in d.reads:
                return (f"[{ref}] You have already read this; its text is above. Answer from it now, or read "
                        "another part (part=2), a subsection, or another section.")
            d.reads.add(key)
            di, nid = d.refs[ref]
            doc = d.docs[di]
            # a short section is read with its subsections at once: one step fewer, which matters
            # most on a model on this computer
            limit = d.room.whole_chars
            whole = whole or (not around and part == 1 and len(doc.section_text(nid, limit + 1)) <= limit)
            view = read_view(doc, nid, part=part, around=around, whole=whole,
                             ref_for=lambda child: d.ref_for(di, child), page_chars=d.room.read_chars)
            d.read[ref] = view
            return f"[{ref}] {view}{_replaced_note(d, di, nid)}"

        @self.tools.tool
        def query_table(ctx: RunContext[ChatDeps], ref: str, table: int = 0, where: list[list] | None = None,
                        columns: list[str] | None = None, sort: str = "", descending: bool = False,
                        limit: int = 20, aggregate: dict[str, str] | None = None) -> str:
            """Filter, sort and add up a table in a section (by reference), computed exactly. Use it
            for every comparison, ranking, total or average over table rows.
            where: [[column, op, value], ...], op one of < <= > >= = != ~ (contains);
            columns: the columns to show; sort: a column (descending=true for highest first);
            aggregate: {"sum"|"avg"|"min"|"max"|"count": column}. Leave out "Total" rows when adding
            up (where [["Month", "!=", "Q1 Total"]]). Units come from the column names."""
            d = ctx.deps
            ref = ref.strip().strip("[]【】").upper()
            if ref not in d.refs:
                return f"Unknown reference {ref!r}. Use a ref returned by a search."
            di, nid = d.refs[ref]
            text, computed = table_query(d.docs[di], nid, table=table, where=where, columns=columns, sort=sort,
                                         descending=descending, limit=limit, aggregate=aggregate)
            d.computed += computed
            d.read[ref] = d.docs[di].section_text(nid, 60_000)
            return f"[{ref}] {text}"

        @self.tools.tool
        def find_facts(ctx: RunContext[ChatDeps], topic: str, kind: str = "any") -> str:
            """The sentences and table rows that state limits, amounts, deadlines, conditions or
            definitions about a topic, by section. kind: "limit", "amount", "deadline", "condition",
            "definition" or "any". Use it for "how long / how much / am I allowed / what is the rule"
            questions, and to see whether a vague question has one meaning or several."""
            d = ctx.deps
            facts = facts_about(d.index, topic, kind, d.allowed)
            if not facts:
                return "No such statements found. Try other words or search_manuals."
            lines = []
            for di, nid, text, k in facts:
                ref = d.ref_for(di, nid)
                d.shown[ref] = (d.shown.get(ref, "") + " " + text).strip()
                lines.append(f"[{ref}] {d.docs[di].display_title} > {d.docs[di].sections[nid].title} ({k}): {text}")
            return "\n".join(lines)

        @self.tools.tool(prepare=when_remote)
        def find_text(ctx: RunContext[ChatDeps], text: str, document: str = "") -> str:
            """Where exact words occur ("45 days", "HR-POL-007"), line by line with section refs.
            document: limit to one document (its title)."""
            d = ctx.deps
            di = d.manual_named(document) if document else None
            found = text_hits(d.index, text, d.allowed, di)
            if not found:
                return f"{text!r} does not occur in the documents" + (f" ({document})" if document else "") + "."
            out = []
            for i, nid, line in found:
                ref = d.ref_for(i, nid)
                d.shown[ref] = (d.shown.get(ref, "") + " " + line).strip()
                out.append(f"[{ref}] {d.docs[i].display_title} > {d.docs[i].sections[nid].title}: {line}")
            return "\n".join(out)

        @self.tools.tool(prepare=when_remote)
        def note(ctx: RunContext[ChatDeps], claim: str, ref: str, quote: str) -> str:
            """Record a fact you will rely on, with the exact words of the section that state it
            (quote, copied from what you read). Checked against the section: only checked facts may
            be stated as from the documents. Call it for each fact before answering."""
            d = ctx.deps
            ref = ref.strip().strip("[]【】").upper()
            if ref not in d.refs:
                return f"Unknown reference {ref!r}."
            di, nid = d.refs[ref]
            ok, nearest = verify_quote(d.docs[di], nid, quote)
            d.notes.append({"claim": claim, "ref": ref, "quote": quote if ok else nearest, "ok": ok})
            if ok:
                return f"Checked. Cite it as [{ref}]."
            return (f"Not found in {ref}." + (f" Closest: \"{nearest}\"" if nearest else "") +
                    " Quote the section's own words, or do not state this as from the documents.")

        @self.tools.tool
        def read_table(ctx: RunContext[ChatDeps], ref: str) -> str:
            """The tables in a section (by reference, e.g. S2) as JSON: columns and rows. Use it to
            compare, sort, filter or add up table values; take units from the column names."""
            d = ctx.deps
            ref = ref.strip().strip("[]【】").upper()
            if ref not in d.refs:
                return f"Unknown reference {ref!r}. Use a ref returned by a search."
            di, nid = d.refs[ref]
            doc = d.docs[di]
            text = doc.section_text(nid, 60_000)
            tables = markdown_tables(text)
            if not tables:
                return f"[{ref}] has no tables that can be read as rows; use read_section."
            d.read[ref] = text
            return f"[{ref}] {doc.display_title} > {' > '.join(doc.short_path(nid))}\n" + \
                json.dumps(tables, ensure_ascii=False)

        @self.tools.tool
        def calculate(ctx: RunContext[ChatDeps], expression: str) -> str:
            """Evaluate arithmetic exactly, e.g. "412 / 770 * 100" or "(186 - 142) * 1000".
            Numbers, + - * / **, parentheses, % after a number (12% = 0.12) and round, abs, min, max,
            sum, avg, sqrt. Use it for every sum, difference, percentage or share you report."""
            try:
                result = evaluate(expression)
            except CalcError as e:
                return f"Error: {e}"
            ctx.deps.computed.append(result)
            return result

        @self.tools.tool
        def list_documents(ctx: RunContext[ChatDeps], file_type: str = "", name_contains: str = "",
                           folder: str = "", tag: str = "", added_after: str = "", added_before: str = "") -> list[dict]:
            """List the installed documents with what is known about them (type, folder, date added,
            pages, tags, description), newest first. All filters are optional: file_type ("pdf",
            "docx", "xlsx" ...), name_contains, folder, tag, added_after / added_before (YYYY-MM-DD)."""
            d = ctx.deps
            rows = []
            for i in d.in_scope():
                doc, meta = d.docs[i], d.docs[i].meta
                if file_type and meta.get("type", "").lower() != file_type.lower().lstrip("."):
                    continue
                if name_contains and name_contains.casefold() not in doc.display_title.casefold():
                    continue
                if folder and folder.casefold() not in meta.get("folder", "").casefold():
                    continue
                if tag and tag.casefold() not in meta.get("tags", "").casefold():
                    continue
                if not _date_ok(meta.get("added", ""), added_after, added_before):
                    continue
                rows.append({"title": doc.display_title, **meta, "sections": len(doc.sections),
                             "description": (doc.description or "")[:300]})
            rows.sort(key=lambda r: r.get("added", ""), reverse=True)
            if not rows:
                return [{"result": "No document matches these filters."}]
            return rows[:LIST_RESULTS] + ([{"note": f"{len(rows) - LIST_RESULTS} more not shown"}]
                                          if len(rows) > LIST_RESULTS else [])

        @self.tools.tool
        def manual_outline(ctx: RunContext[ChatDeps], manual: str) -> str:
            """The map of one document (by its title): its sections with a short summary and a
            reference each, marked ▦ where a section has a table (its columns) and ▣ where it has
            pictures. Use it to find where things are in a long document, or to summarize it."""
            d = ctx.deps
            di = d.manual_named(manual)
            if di is None:
                return "No document with that title. Titles: " + "; ".join(d.docs[i].display_title for i in d.in_scope())
            doc = d.docs[di]

            def ref_for(nid: str) -> str:
                ref = d.ref_for(di, nid)
                # figures quoted from a summary are not "unverified"
                d.shown.setdefault(ref, " ".join(doc.sections[nid].summary.split()))
                return ref
            return doc_map(doc, ref_for, chars=d.room.outline_chars)

        @self.tools.tool
        def find_pictures(ctx: RunContext[ChatDeps], topic: str = "") -> str:
            """Find pictures (diagrams, charts, drawings, photos) in the documents about a topic, or
            all of them when the topic is empty. The app shows the pictures found with your reply."""
            d = ctx.deps
            if d.find_pictures is None:
                return "Pictures cannot be shown: the knowledge base has neither pictures nor original PDFs."
            found = [p for p in d.find_pictures(topic) if p not in d.pictures][:PICTURES_LISTED]
            if not found:
                return f"No pictures{' about ' + topic if topic else ''} were found."
            d.pictures += found
            return "Shown to the user: " + "; ".join(
                f"{d.docs[ref[0]].display_title} > {d.docs[ref[0]].sections[ref[1]].title}" for ref in found)

        @self.tools.tool(prepare=when_attached)
        def read_attachment(ctx: RunContext[ChatDeps], attachment_id: str, start: int = 0) -> str:
            """Read a file the user attached (by its id, e.g. F1A2B3C), from character `start`.
            Long files come in pieces; the reply says where the next piece starts."""
            d = ctx.deps
            key = attachment_id.strip().upper()
            att = next((a for a in d.attachments if a.id == key), None)
            text = d.attachment_text(key) if att else None
            if att is None or text is None:
                return "Unknown attachment. Attached: " + ", ".join(f"{a.id} ({a.name})" for a in d.attachments)
            start = max(0, int(start))
            piece = text[start:start + ATTACHMENT_READ_CHARS]
            d.worked.append(piece)
            if att.name not in d.files_used:
                d.files_used.append(att.name)
            end = start + len(piece)
            more = f"\n[... {len(text) - end:,} more characters; continue with start={end}]" if end < len(text) else ""
            return f"{att.name} (characters {start:,}-{end:,} of {len(text):,}):\n{piece}{more}"

        async def when_first(ctx: RunContext[ChatDeps], tool: ToolDefinition) -> ToolDefinition | None:
            # a model on a server may ask back later in a conversation too ("which of the two
            # settings?"); one on this computer only on a first question (it asked back too often)
            return tool if ctx.deps.first_turn or ctx.deps.tier == "remote" else None

        @self.tools.tool(prepare=when_first)
        def ask_user(ctx: RunContext[ChatDeps], question: str, options: list[str]) -> str:
            """Ask what the user means when the question fits several different things in the
            documents (for example "What is the limit?": the upload limit, the members per project,
            the carry-forward limit). options: 2 to 4 complete questions the user could mean, each
            answerable from the documents. Only when the meanings really differ; otherwise answer the
            likeliest one and say what you assumed."""
            d = ctx.deps
            # "Something else - please clarify what you mean by ...": no question to answer; picked, it
            # was sent to the model as one. The app offers its own "Something else..." to type it in
            picked = [o.strip() for o in options if o and o.strip() and not _NO_OPTION.match(o)][:4]
            if len(picked) < 2:
                return "Give at least two options, or answer the likeliest meaning and say what you assumed."
            d.clarify = Clarification(prompt=question.strip() or "Which one do you mean?", options=[
                ClarifyOption(label=o.rstrip("?"), detail="", doc_ids=[], question=o if o.endswith("?") else o + "?")
                for o in picked])
            return "The user now sees these choices. End your reply here with just the question."

        @self.tools.tool
        def ask_which_manual(ctx: RunContext[ChatDeps], question: str, manuals: list[str]) -> str:
            """Ask the user which of several documents they mean; they get one button per document.
            question: a short question to show; manuals: the titles to choose from."""
            d = ctx.deps
            picked = list(dict.fromkeys(i for i in (d.manual_named(m) for m in manuals) if i is not None))
            if len(picked) < 2:
                picked = d.in_scope()[:4]
            d.clarify = Clarification(prompt=question.strip() or "Which document do you mean?", options=[
                ClarifyOption(label=d.docs[i].display_title, detail=d.docs[i].description or "",
                              doc_ids=[d.docs[i].doc_id]) for i in picked[:4]])
            return "The user now sees these choices. End your reply here with just the question."

    async def _run(self, prompt: str, deps: ChatDeps, context: list[tuple[str, str]], emit) -> tuple[str, int, bool]:
        """One agent run: (answer, model requests, cut off)."""
        cut_off = False
        written = ""  # the current reply's text so far, checked for a repeating block
        run = None
        try:
            async with self.agent.iter(prompt, deps=deps,
                                       message_history=history_messages(context, deps.room.history_answer_chars),
                                       model_settings=run_settings(),
                                       usage_limits=UsageLimits(request_limit=deps.room.max_requests)) as run:
                async for node in run:
                    if Agent.is_model_request_node(node):
                        if self.budget:  # every model call counts, not only the first
                            self.budget.ensure(1)
                        await emit({"stage": "writing"})  # a new reply starts: drop any earlier draft text
                        written, sent = "", ""
                        async with node.stream(run.ctx) as stream:
                            async for event in stream:
                                piece = ""
                                if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                                    piece = event.part.content
                                elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                                    piece = event.delta.content_delta or ""
                                if piece:
                                    written += piece
                                    shown = visible_so_far(written)  # thinking is not shown
                                    if shown.startswith(sent) and len(shown) > len(sent):
                                        await emit({"stage": "delta", "text": shown[len(sent):]})
                                        sent = shown
                                    cut = repeating_from(shown)
                                    if cut is not None:
                                        raise _Looping(shown[:cut])
                    elif Agent.is_call_tools_node(node):
                        cut_off = node.model_response.finish_reason == "length"
                        async with node.stream(run.ctx) as stream:
                            async for event in stream:
                                if isinstance(event, FunctionToolCallEvent):
                                    args = event.part.args_as_dict() if event.part.args else {}
                                    await emit({"stage": "tool", "tool": event.part.tool_name,
                                                "detail": str(args.get("query") or args.get("ref")
                                                              or args.get("manual") or args.get("topic")
                                                              or args.get("expression") or args.get("text")
                                                              or "")})
            assert run.result is not None
            return run.result.output, run.result.usage.requests, cut_off
        except _Looping as loop:  # keep what came before the repetition
            usage = run.usage() if callable(run.usage) else run.usage
            return loop.text, usage.requests, True

    async def _wrap_up(self, question: str, deps: ChatDeps) -> tuple[str, int, bool]:
        """The answer from what was read and noted, without tools: (answer, requests, cut off).
        Empty when nothing was gathered."""
        gathered = [f"[{ref}] {text[:deps.room.wrap_chars]}" for ref, text in deps.read.items()]
        gathered += [f"[{ref}] {text}" for ref, text in deps.shown.items() if ref not in deps.read]
        notes = [f"- {n['claim']} [{n['ref']}]: \"{n['quote']}\"" for n in deps.notes if n["ok"]]
        if not gathered and not notes:
            return "", 0, False
        prompt = (f"QUESTION: {question}\n\nWhat was found in the documents:\n" + "\n\n".join(gathered)[:deps.room.wrap_total]
                  + ("\n\nChecked facts:\n" + "\n".join(notes) if notes else "") +
                  "\n\nAnswer the question from this only, citing each fact with its reference in square "
                  "brackets ([S2]). For any part it does not answer, say in one sentence that the "
                  "documents do not cover it.")
        if self.budget:
            self.budget.ensure(1)
        result = await self.writer.run(prompt)
        return result.output, 1, False

    async def ask(self, question: str, docs: list[Doc], index: SectionIndex, *,
                  allowed: set[int] | None = None, context: list[tuple[str, str]] | None = None,
                  on_event: EventFn | None = None, find_pictures: PictureFinder | None = None,
                  semantic: SemanticIndex | None = None, attachments: list[Attachment] | None = None,
                  attachment_text: AttachmentReader | None = None, about: list[str] = (),
                  model_label: str = "", documents_only: bool = False, tier: str = "remote",
                  earlier: list[tuple[int, str]] = (), place: tuple[int, str] | None = None,
                  picked: tuple[int, str] | None = None, kb_guide: str = "", kb_notes: str = "",
                  context_tokens: int = 0) -> AskResult:
        """about: ids of attached files this question is about; their text comes with the question.
        documents_only: no general-knowledge answers (a model too small to get them right).
        tier: "remote" (a large model on a server) or "local" (on this computer: fewer, larger steps).
        context_tokens: the model's context window (0: not known), for how much it is given (Room).
        earlier: (doc index, node id) of the sections earlier answers in the conversation cited.
        place: (doc index, node id) of where the conversation is (see focus.py).
        picked: (doc index, node id) of the section the user picked after a question back."""
        async def emit(event: dict) -> None:
            if on_event is not None:
                await on_event(event)

        t0 = time.perf_counter()
        if self.budget:
            self.budget.ensure(1)
        for attempt in (1, 2):
            deps = ChatDeps(docs=docs, index=index, allowed=allowed, find_pictures=find_pictures,
                            semantic=semantic, attachments=list(attachments or []), attachment_text=attachment_text,
                            about=set(about), model_label=model_label, documents_only=documents_only,
                            tier=tier, first_turn=not context, kb_guide=kb_guide, kb_notes=kb_notes,
                            room=room_for(context_tokens, tier))
            prompt = await self._with_presearch(question, deps, context or [], earlier, place, picked)
            if _STUCK.search(question):  # "I'm stuck labelling images": the manual's steps, then which step
                prompt += "\n\n(" + STUCK_NOTE + ")"
            # weather, news, prices today: no model here knows them, and any answer would be invented
            if LIVE_QUESTION.search(question) and not deps.attachments and not about_documents(deps):
                return AskResult(question=question, found=False, answer=LIVE_REPLY, mode="chat",
                                 seconds={"total": round(time.perf_counter() - t0, 2)})
            # a small model, a question the documents do not cover ("write a poem about cats"): it would
            # only be declined after the model ran (minutes on a processor), so it is declined now
            if (documents_only and not deps.attachments and not context and not about_documents(deps)
                    and not _ABOUT_SELF_OR_CHAT.search(question)):
                return AskResult(question=question, found=False, answer=SMALL_MODEL_REPLY, mode="chat",
                                 seconds={"total": round(time.perf_counter() - t0, 2)})
            try:
                answer, requests, cut_off = await self._run(prompt, deps, context or [], emit)
                answer = without_thinking(answer)
                if _TEXT_TOOL_CALL.search(answer):
                    # the server passed the model's tool call on as text: answered from what was found
                    log.warning("chat reply was a tool call written as text; answering from what was found")
                    await emit({"stage": "writing"})
                    wrapped, more, cut_off = await self._wrap_up(question, deps)
                    answer, requests = without_thinking(wrapped) or TOO_MANY_STEPS_REPLY, requests + more
                break
            except UsageLimitExceeded:  # searched and read without settling on an answer
                log.warning("chat agent hit its step limit: %s", question[:200])
                answer, requests, cut_off = await self._wrap_up(question, deps)
                if not answer:
                    return AskResult(question=question, found=False, answer=TOO_MANY_STEPS_REPLY, mode="chat",
                                     seconds={"total": round(time.perf_counter() - t0, 2)})
                break
            except (ModelHTTPError, ModelAPIError, UnexpectedModelBehavior, openai.APIError) as e:
                log.warning("chat model call failed (attempt %d): %s", attempt, describe_error(e))
                if quota_exhausted(e):
                    raise BudgetExceeded(QUOTA_MESSAGE) from e
                if attempt == 1 and _transient(e):
                    await asyncio.sleep(RETRY_DELAY_S)
                    await emit({"stage": "retrying"})
                    continue
                if isinstance(e, ModelHTTPError) and e.status_code in (429, 503):
                    raise ProviderBusy("The language model is busy or still loading; try again shortly") from e
                if isinstance(e, ModelHTTPError) and 400 <= e.status_code < 500:
                    raise refusal(e) from e
                if isinstance(e, ModelHTTPError):
                    raise
                if isinstance(e, UnexpectedModelBehavior):
                    raise ProviderBusy("The language model gave an unusable reply; try again") from e
                if isinstance(e, ModelAPIError) and not isinstance(e, ModelHTTPError):
                    raise ProviderBusy("The language model is not responding") from e
                raise ProviderBusy("The language model service had an error; try again") from e

        seconds = {"total": round(time.perf_counter() - t0, 2)}
        if deps.clarify is not None:
            return AskResult(question=question, found=False, answer=deps.clarify.prompt, clarify=deps.clarify,
                             mode="chat", model_requests=requests, seconds=seconds)
        answer = name_attachments(_without_tool_talk(plain_typography(answer)), deps.attachments)
        answer = without_repeated_sentences(
            without_stray_signature(plain_math(without_sources_block(sources_line_to_markers(
                without_echoed_excerpts(answer))))))
        answer = without_trailing_decline(answer)
        if context:
            answer = without_other_questions(answer, question)
        worked_out = bool(_WORKED_OUT.search(answer))
        answer = _WORKED_OUT.sub("", answer).strip()
        # a note cited by its number ([N2]) is cited by its section
        answer = _NOTE_REF.sub(lambda m: f"[{deps.notes[int(m.group(1)) - 1]['ref']}]"
                               if 0 < int(m.group(1)) <= len(deps.notes) else "", answer)
        if not answer:  # e.g. everything it wrote was about its tools ("print your tool list")
            return AskResult(question=question, found=False, answer=EMPTY_REPLY, mode="chat",
                             model_requests=requests, seconds=seconds)
        if context and repeats_last(question, answer, context[-1]):
            # the 0.8B gave its RAM-and-holidays answer again to "how much does it cost per user?"
            log.warning("chat answer repeated the previous one: %s", question[:200])
            return AskResult(question=question, found=False, answer=NOT_COVERED_REPLY, mode="chat",
                             model_requests=requests, seconds=seconds)
        if _DECLINE.match(answer) and len(answer) < 400:  # off-topic: a fixed-style reply, not an answer
            return AskResult(question=question, found=False, answer=answer.strip(), mode="chat",
                             smalltalk=True, model_requests=requests, seconds=seconds)
        # "as stated in S1 and S2": references written bare, without brackets, to sections it was shown
        # (they were checked as figures, "S1, S2 do not appear in the document text", and the answer
        # was marked as naming no sources)
        answer = re.sub(r"(?<![\w\[])S(\d{1,3})(?![\w\]†])",
                        lambda m: f"[S{m.group(1)}]" if f"S{m.group(1)}" in deps.refs else m.group(0),
                        normalize_refs(answer))
        answer, citations = self._resolve(_BARE_REFS.sub("", answer), deps)
        asks_general = not documents_only and bool(GENERAL_ASK.search(question))
        # a "General knowledge:" paragraph that only restates the documents loses its label, except when
        # general knowledge was asked for; then unlabelled "In general, ..." paragraphs get one
        if not asks_general:
            answer = unlabel_document_paragraphs(answer, deps)
        if not documents_only:
            answer = without_hedge_before_general(label_general_leads(answer))
        answer = without_section_numbers(answer)
        answer = without_contradicted_not_covered(answer, "\n".join([*deps.read.values(), *deps.shown.values()]))
        implied = False
        if not citations and not deps.files_used and not asks_general:  # small models often forget the markers
            citations = [_citation(docs, *deps.refs[r]) for r in implied_sources(_documents_part(answer), deps, question)]
            implied = bool(citations)
        answer, general_part = mark_general(answer)
        # earlier answers count too: "summarize our chat" repeats their figures
        # and the names of the documents read ("the R7 guide": R7 is in the title, not the text)
        titles = {f"{docs[di].display_title} {docs[di].description}" for di, _ in (deps.refs[r] for r in {*deps.read, *deps.shown})}
        sources = "\n".join([*deps.read.values(), *deps.shown.values(), *deps.worked, *titles,
                             *(a for _, a in context or [])])
        answer = without_quoted_tail(answer, "\n".join([*deps.read.values(), *deps.shown.values()]))

        def not_covered(reply: str) -> AskResult:  # no sources, pictures or figure check
            return AskResult(question=question, found=False, answer=reply, mode="chat",
                             model_requests=requests, seconds=seconds)
        about_self = bool(_ABOUT_SELF_OR_CHAT.search(question))
        # the evidence ledger: each source the answer cites, backed by a checked quote from it. A
        # noted section the answer plainly draws on but forgot to mark (the second part of a
        # two-part answer) is a source too
        answer_words = set(tokenize(answer))
        for n in deps.notes:
            if not n["ok"]:
                continue
            key = (docs[deps.refs[n["ref"]][0]].doc_id, deps.refs[n["ref"]][1])
            quoted = set(tokenize(n["quote"]))
            if key not in {(c.doc_id, c.node_id) for c in citations} and quoted and \
                    len(quoted & answer_words) >= NOTE_USED_SHARE * len(quoted):
                citations.append(_citation(docs, *deps.refs[n["ref"]]))
        where = {(c.doc_id, c.node_id): i for i, c in enumerate(citations)}
        evidence, quoted_already = [], set()
        for n in deps.notes:
            key = (docs[deps.refs[n["ref"]][0]].doc_id, deps.refs[n["ref"]][1]) if n["ok"] else None
            text = _quote_text(n["quote"]) if n["ok"] else ""
            if key in where and text and text not in quoted_already:
                quoted_already.add(text)
                evidence.append(Evidence(text=text, citation=where[key]))
        grounded = bool(citations) and {e.citation for e in evidence} == set(range(len(citations)))
        if _MODEL_DECLINE.match(answer) and len(answer) < NOT_COVERED_MAX and not about_self:
            # the model declined in its own words ("I can only answer questions about the documents")
            return not_covered(answer.strip())
        first = re.split(r"(?<=[.!?])\s", answer.strip(), maxsplit=1)[0]
        if worked_out and _NOT_COVERED.search(first) and not _INFERENCE_TURN.search(answer[len(first):]):
            # labelled worked out, but only says the documents lack it ("... do not contain information
            # about resetting a password."): not an inference, a not-covered answer
            worked_out = False
        if (_NOT_COVERED.search(first) and len(answer) < NOT_COVERED_MAX and not about_self and not worked_out
                and not (general_part and not documents_only) and not deps.files_used):
            # "The documents do not contain pricing information ...": an answer that there is none
            return not_covered(NOT_COVERED_REPLY if about_documents(deps) or not documents_only
                               else SMALL_MODEL_REPLY)
        own = re.sub(r"\[\d+\]", " ", without_step_numbers(_LIST_MARKERS.sub(" ", _documents_part(answer))))
        # an answer worked out from the documents, or one whose every source is backed by a checked
        # quote, is not judged by its wording (an inference adds words the documents lack)
        # (a model on this computer labels made-up steps "worked out" too: its label exempts nothing)
        if (own.strip() and not about_self and not deps.files_used and not deps.computed
                and not (worked_out and deps.tier != "local") and not grounded and (citations or documents_only)):
            # presented as from the documents: invented steps for a topic they lack, or a figure
            # they do not have ("How do I reset my password?", "the price is $19.99 per user")
            cited_text = "\n".join(docs[di].section_text(c.node_id, 20_000) for c in citations
                                   for di in [next((i for i, d in enumerate(docs) if d.doc_id == c.doc_id), None)]
                                   if di is not None)
            if (made_up(question, own, sources, index, cited_text)
                    or only_unchecked_figures(own, unverified_numbers(own, sources, question), question)
                    or wrong_kind_of_figure(question, own)):
                if implied and not documents_only:
                    # the model named no source, and the sections guessed for it do not hold the answer:
                    # it answered from general knowledge ("Apart from the documents, what is a C wire?"
                    # was replaced by "The documents don't cover that"; 26 Oct). Shown as that, not hidden
                    citations = []
                else:
                    return not_covered(NOT_COVERED_REPLY if about_documents(deps) or not documents_only
                                       else SMALL_MODEL_REPLY)
        if documents_only:  # a small model: general knowledge is not shown, however it was written
            if general_part:
                answer, general_part = _documents_part(answer).strip(), False
            if not (citations or deps.files_used) and not _ABOUT_SELF_OR_CHAT.search(question):
                if not about_documents(deps):  # "who is the PM of India?"
                    return AskResult(question=question, found=False, answer=SMALL_MODEL_REPLY, mode="chat",
                                     model_requests=requests, seconds=seconds)
                # about the documents, but the small model named no source ("how do I escalate ...?",
                # a Korean answer to a Korean question): shown, with a pointer to check it
                answer += "\n\n" + UNSOURCED_NOTE
        if _WHOLE_TABLE.search(question):
            # "the highest response time" from a table the document gives only in part: its note says
            # what the whole file holds (the 10 rows shown peak at 133 ms, the file at 1840.6 ms)
            note = next((n for n in map(partial_table_note, [*deps.read.values(), *deps.shown.values()]) if n), None)
            if note and not numbers_in(answer) <= numbers_in(note):
                answer += f"\n\n*Note: the document includes only part of this table. It says: {note}*"
        unsourced = (not documents_only and not general_part and not citations and not deps.files_used
                     and not about_self and not asks_general and bool(deps.read) and about_documents(deps)
                     and drawn_from(answer, "\n".join(deps.read.values())))
        if unsourced:
            # it read the documents and wrote no general-knowledge part, but named no source: an answer
            # from the documents that forgot its markers, not a general answer (InspectAI "How do I
            # annotate text?", Nimbus E2 were labelled general; 22 Oct test)
            answer += "\n\n" + UNSOURCED_NOTE
        if cut_off:
            answer += "\n\n*(The answer was cut off.)*"
        general = not documents_only and (general_part or not (citations or deps.files_used or about_self or unsourced))
        return AskResult(
            question=question, found=True, answer=answer, citations=citations, mode="chat",
            considered=[_citation(docs, *deps.refs[r]) for r in deps.read],
            seconds=seconds, model_requests=requests, files=list(dict.fromkeys(deps.files_used)),
            # general knowledge: a marked paragraph, or an answer from neither documents nor attached
            # files (except about the assistant itself or the conversation: "what can you do?"); a
            # documents-only model gives none
            general=general,
            worked_out=worked_out and not general and not stated_as_is(_documents_part(answer), sources, question),
            evidence=evidence[:MAX_EVIDENCE],
            # source markers "[1]" are not figures; general-knowledge paragraphs are not checked. An
            # answer without sources is checked too: with nothing to check against, its figures are
            # flagged rather than shown as matching the documents
            unverified_numbers=([] if general and not general_part else
                                unverified_numbers(re.sub(r"\[\d+\]", " ", _documents_part(answer)), sources,
                                                   question, deps.computed)),
            picture_pages=deps.pictures)

    @staticmethod
    async def _with_presearch(question: str, deps: ChatDeps, context: list[tuple[str, str]] = (),
                              earlier: list[tuple[int, str]] = (),
                              place: tuple[int, str] | None = None,
                              picked: tuple[int, str] | None = None) -> str:
        """The question plus the best matches (keyword search, fused with search by meaning when it
        is set up), so that a model that does not search on its own still sees the relevant sections.
        In a conversation, a few matches for the previous turn too: "Why did it go up?" names no topic."""
        def keyword_search(text: str) -> list:
            return [h for h in deps.index.search(text) if deps.allowed is None or h.doc_index in deps.allowed]

        # "And in March?" after "the revenue in January?": searched as the question it stands for
        meant = follow_up([q for q, _ in context], question) if context else None
        question_as_asked, question = question, meant or question
        keyword = keyword_search(question)
        room = deps.room
        hits = keyword[:room.presearch_hits]
        deps.keyword_top = keyword[0].score if keyword else 0.0
        if deps.semantic is not None and deps.semantic.ready:
            meaning = await deps.semantic.search(question, deps.allowed, room.presearch_hits)
            # a failed search (the embedding server stopped) is no search: judged by keywords then
            deps.meaning_top = (meaning[0].score if meaning else 0.0) if deps.semantic.status == "ready" else None
            if meaning:
                hits = fuse(question, deps.index, keyword, meaning, limit=room.presearch_hits)
        if context and _REFERS_BACK.search(question):
            # "Is it paid?" is about the documents when the question it follows was
            joined = f"{context[-1][0]} {question}"
            joined_keyword = keyword_search(joined)
            if joined_keyword:
                deps.keyword_top = max(deps.keyword_top, joined_keyword[0].score)
            if deps.meaning_top is not None:
                joined_meaning = await deps.semantic.search(joined, deps.allowed, 1)
                deps.meaning_top = max(deps.meaning_top, joined_meaning[0].score if joined_meaning else 0.0)
        parts = question_parts(question)
        subject = ""
        for k, part in enumerate(parts):  # "which packages ... and which leave ...": each part's own best sections
            seen = {(h.doc_index, h.node_id) for h in hits}
            # "... and how should agents fix it?": "it" is what the first part found ("the ticket spike
            # in Q3" -> the R7 2.9.0 rollout, error E-217), so the codes of that part's best section go
            # with the search (searched alone, it found Customer Feedback, not 4.3 Firmware Update Failure)
            query = f"{part} {subject}" if k and subject and _REFERS_BACK.search(part) else part
            found = keyword_search(query)
            if k == 0 and found:
                subject = subject_of(deps.docs, found, part)
            if deps.semantic is not None and deps.semantic.ready:
                found = fuse(part, deps.index, found, await deps.semantic.search(part, deps.allowed, PART_HITS), limit=PART_HITS)
            hits += [h for h in found if (h.doc_index, h.node_id) not in seen][:PART_HITS]
        if context:
            last_question, last_answer = context[-1]
            followed = keyword_search(f"{last_question} {_REF_MARKS.sub(' ', last_answer)[:CONTEXT_SEARCH_CHARS]} {question}")
            seen = {(h.doc_index, h.node_id) for h in hits}
            hits += [h for h in followed if (h.doc_index, h.node_id) not in seen][:CONTEXT_HITS]
        lines = []
        # the best matches come as text, not a snippet to read in full first: a model on this computer
        # gets their passages (often it can answer in one call, and each call takes minutes); a large
        # model the sections whole. Given only snippets, a server model guessed rather than read on
        # (DeepSeek: 2 of 7 delivery rows, invented orders; 22 Oct test)
        for i, h in enumerate(hits):
            doc = deps.docs[h.doc_index]
            ref = deps.ref_for(h.doc_index, h.node_id)
            if i < room.passage_hits and room.large:
                text = whole_section(doc, h.node_id, room.passage_chars)
                deps.read[ref] = text
            elif i < room.passage_hits:
                text = passages(doc, h.node_id, question, chars=PASSAGE_CHARS, most=2)
                deps.read[ref] = text
            else:
                text = deps.index.snippet(h.doc_index, h.node_id, question)
                deps.shown[ref] = text
            lines.append(f"[{ref}] {doc.display_title} > {' > '.join(doc.short_path(h.node_id))} "
                         f"({doc.locator(h.node_id)}): {text}{_replaced_note(deps, h.doc_index, h.node_id)}")
        # a section given whole that points elsewhere ("covered only if the warm-up rules in 5.2 have been
        # followed"): the section it names comes too, so the answer has its steps (DeepSeek answered
        # "follow the warm-up program" without O9000, which only 5.2 states; 26 Oct)
        if room.large:
            given = {(h.doc_index, h.node_id) for h in hits}
            for i, h in enumerate(hits[:room.passage_hits]):
                for nid in referenced_sections(deps.docs[h.doc_index], h.node_id)[:MAX_REFERENCED]:
                    if (h.doc_index, nid) in given:
                        continue
                    given.add((h.doc_index, nid))
                    doc = deps.docs[h.doc_index]
                    ref = deps.ref_for(h.doc_index, nid)
                    text = whole_section(doc, nid, room.passage_chars)
                    deps.read[ref] = text
                    lines.append(f"[{ref}] {doc.display_title} > {' > '.join(doc.short_path(nid))} "
                                 f"({doc.locator(nid)}), which [{deps.ref_for(h.doc_index, h.node_id)}] refers to: {text}")
        # models rank and subtract table rows unreliably (a small one often, a server model too: DeepSeek
        # read 2 of 7 delivery rows): what the question ranks, adds up or compares, worked out in code
        # from the best matches' tables (as in Manuals mode)
        table_lines = []
        for h in hits[:room.passage_hits + 1]:
            found_lines, values = table_highlights(deps.docs[h.doc_index], h.node_id, question)
            table_lines += [f"[{deps.ref_for(h.doc_index, h.node_id)}] {line}" for line in found_lines]
            deps.computed += values
        earlier_lines = []
        for di, nid in earlier:  # the sections earlier answers came from: the same refs as then
            if deps.allowed is not None and di not in deps.allowed:
                continue
            ref = deps.ref_for(di, nid)
            deps.shown.setdefault(ref, deps.docs[di].sections[nid].summary)
            earlier_lines.append(f"[{ref}] {deps.docs[di].display_title} > {' > '.join(deps.docs[di].short_path(nid))}")
        prompt = question_as_asked
        if meant:
            prompt += f"\n\n(This follow-up asks: {meant})"
        if parts:
            prompt += "\n\n(" + parts_note(parts) + ")"
        # the files this question is about, from their start: a model that does not call
        # read_attachment still sees them (longer files are read on with read_attachment)
        for att in deps.attachments:
            if att.id not in deps.about or deps.attachment_text is None:
                continue
            text = deps.attachment_text(att.id) or ""
            piece = text[:room.attachment_preview]
            deps.worked.append(piece)
            deps.files_used.append(att.name)
            more = (f"\n[... {len(text) - len(piece):,} more characters: read_attachment {att.id} "
                    f"start={len(piece)}]" if len(text) > len(piece) else "")
            prompt += f"\n\n---\nAttached file {att.id} ({att.name}):\n{piece}{more}"
        if picked is not None:
            # an option picked after a question back ("Multi X Classification", "Type 2"): its section
            di, nid = picked
            doc = deps.docs[di]
            prompt += (f"\n\n---\nThe user picked [{deps.ref_for(di, nid)}] {doc.display_title} > "
                       f"{' > '.join(doc.short_path(nid))}: answer from that section (read it first).")
            deps.shown.setdefault(deps.ref_for(di, nid), doc.sections[nid].summary)
        elif place is not None and context and (deps.allowed is None or place[0] in deps.allowed):
            # where the conversation is: "How do I test it?" in Multi X is Multi X's testing, though
            # the search ranks Single X's first (see focus.py)
            di, nid = place
            doc = deps.docs[di]
            prompt += (f"\n\n---\nThe conversation is in [{deps.ref_for(di, nid)}] {doc.display_title} > "
                       f"{' > '.join(doc.short_path(nid))}. A follow-up that names no other part of the "
                       f"documents is about this part.")
            deps.shown.setdefault(deps.ref_for(di, nid), doc.sections[nid].summary)
            near = near_place(question_as_asked, deps.index, di, nid)
            if near is not None and near != nid:
                prompt += (f" This question most likely means [{deps.ref_for(di, near)}] "
                           f"{' > '.join(doc.short_path(near))}: read it first.")
                deps.shown.setdefault(deps.ref_for(di, near), doc.sections[near].summary)
        if earlier_lines:
            prompt += "\n\n---\nSections the earlier answers in this conversation came from:\n" + "\n".join(earlier_lines)
        if lines:
            prompt += ("\n\n---\nPossibly relevant document sections" +
                       (f" (the first {min(room.passage_hits, len(lines))} in full; read the others before relying on them)"
                        if room.large else " (read a section in full before relying on it)") + ":\n" + "\n".join(lines))
        if table_lines:
            prompt += "\n\n---\nWorked out from the tables (exact):\n" + "\n".join(table_lines)
        return prompt

    @staticmethod
    def _resolve(answer: str, deps: ChatDeps) -> tuple[str, list[Citation]]:
        """[S7] [S2] ... -> [1] [2] in order of first use; unknown references are dropped."""
        order: list[str] = []
        for ref in _REF.findall(answer):
            if ref in deps.refs and ref not in order:
                order.append(ref)

        def renumber(m: re.Match) -> str:
            ref = m.group(1)
            return f"[{order.index(ref) + 1}]" if ref in order else ""

        answer = re.sub(r"\s*\[(S\d+)\]", lambda m: (" " if m.group(1) in order else "") + renumber(m), answer)
        answer = re.sub(r"(\[\d+\])(\s*\1)+", r"\1", answer)  # "[1] [1]" -> "[1]"
        answer = re.sub(r"\(\s*((?:\[\d+\]\s*)+?)\s*\)", r"\1", answer)  # "( [1])" -> "[1]"
        return answer.strip(), [_citation(deps.docs, *deps.refs[r]) for r in order]


IMPLIED_SHARE = 0.4   # of an uncited answer's words that must come from the sections cited for it
IMPLIED_EACH = 0.15   # ... of which each cited section adds at least this share
IMPLIED_MIN_WORDS = 6
IMPLIED_MAX = 3
IMPLIED_SECTION_CHARS = 20_000
NAMED_TITLE_CHARS = 8  # a section title this long, named in the answer, makes that section its source
# words a small model wraps every answer in; they say nothing about where the answer came from
_ANSWER_FILLER = frozenset(tokenize(
    "based on the documents provided installed document according specifically section sections cited "
    "source sources excerpt excerpts text states stated mentioned mentions information answer details "
    "regarding explicitly listed shows following context"))


def implied_sources(text: str, deps: ChatDeps, question: str = "") -> list[str]:
    """The sections an answer without source markers was written from (see _implied_guess), but
    only if every figure it gives is in what the model read or was shown, in the question, or the
    sum or difference of two such figures: a small model that makes up "7 days of jury duty leave"
    while naming the leave section must not get that section as its source."""
    picked = _implied_guess(text, deps)
    if not picked:
        return []
    # with the documents' names: "Based on the HX-500 manual, ..." (the 4B, final test) names a
    # figure the warranty section itself never repeats
    sources = "\n".join(f"{deps.docs[di].display_title} {deps.docs[di].name}\n"
                        + deps.docs[di].section_text(nid, IMPLIED_SECTION_CHARS)
                        for di, nid in (deps.refs[r] for r in sorted({*deps.shown, *deps.read})))
    made_up = [f for f in unverified_numbers(_LIST_MARKERS.sub(" ", text), sources, question)
               if not _derived(f, sources)]
    return [] if made_up else picked


def _derived(figure: str, sources: str) -> bool:
    """Is the figure the sum or difference of two figures in the sources ("4.3 s faster" from
    9.4 and 5.1)? Compared at the figure's own precision."""
    try:
        value = float(figure.replace(",", ""))
    except ValueError:
        return False
    places = len(figure.split(".")[1]) if "." in figure else 0
    known = sorted({float(n) for n in numbers_in(sources) if n.replace(".", "", 1).isdigit()})[:300]
    return any(round(a + b, places) == value or round(abs(a - b), places) == value
               for i, a in enumerate(known) for b in known[i + 1:])


# "1. Open ..." list numbering and [1] markers are not figures the answer gives
_LIST_MARKERS = re.compile(r"(?m)^\s*\d+[.)]\s|\[\d+\]")


def _implied_guess(text: str, deps: ChatDeps) -> list[str]:
    """The sections a text without source markers was plainly written from ("The MCN5 is a 50 MP,
    1/1.56-inch stacked CMOS sensor ..."), among those the model read or was shown:
    - sections it names by title ("the section titled Password Hygiene says ..."), else
    - at least IMPLIED_SHARE of its words (codes and figures included, the "Based on the documents
      provided" filler left out) are in them, else
    - one section holds every figure it gives ("91.3%" in an answer too short to judge by words).
    General knowledge shares few words and figures with them, so it stays uncited."""
    refs = sorted({*deps.shown, *deps.read})
    texts = {ref: deps.docs[deps.refs[ref][0]].section_text(deps.refs[ref][1], IMPLIED_SECTION_CHARS) for ref in refs}
    lowered = text.casefold()
    named = [ref for ref in refs if len(title := _plain_title(deps, ref)) >= NAMED_TITLE_CHARS and title in lowered]
    if named:
        return named[:IMPLIED_MAX]
    # figures specific enough to tell where they came from ("91.3", "1,870"; not "12")
    figures = {f for f in numbers_in(text) if len(f.replace(".", "").replace(",", "")) >= 3}
    words = set(tokenize(text)) - _ANSWER_FILLER
    if len(words) < IMPLIED_MIN_WORDS:
        return _only_holder(figures, deps, refs)
    found: dict[str, set[str]] = {}
    for ref in refs:  # the whole section, not the snippet the model was shown
        found[ref] = words & set(tokenize(texts[ref]))
    # a few sections that together account for the answer (an essay draws on several), each adding
    # a fair share of its words
    covered: set[str] = set()
    picked: list[str] = []
    for ref in sorted(found, key=lambda r: (-len(found[r]), r)):
        if len(found[ref] - covered) >= IMPLIED_EACH * len(words):
            picked.append(ref)
            covered |= found[ref]
        if len(picked) == IMPLIED_MAX:
            break
    if len(covered) >= IMPLIED_SHARE * len(words):
        return picked
    return _only_holder(figures, deps, refs)


def _only_holder(figures: set[str], deps: ChatDeps, refs: list[str]) -> list[str]:
    """The one section whose own text (not its subsections') holds every figure; none when several
    do (the figure does not tell where it is from)."""
    holding = [ref for ref in refs if figures
               and figures <= numbers_in(deps.docs[deps.refs[ref][0]].sections[deps.refs[ref][1]].own_text)]
    return holding if len(holding) == 1 else []


def _plain_title(deps: ChatDeps, ref: str) -> str:
    """A section's title without its number ("3.1 Remote Work" -> "remote work"), lowercased."""
    di, nid = deps.refs[ref]
    return re.sub(r"^\s*(sheet:\s*)?[\d.]+\s+", "", deps.docs[di].sections[nid].title).strip().casefold()


def _is_general(paragraph: str) -> bool:
    p = paragraph.lstrip()
    return p.startswith("> **General knowledge:**") or bool(_GENERAL.match(p.lstrip("> ")))


def _documents_part(answer: str) -> str:
    """The answer without its general-knowledge paragraphs (their figures are not from the documents)."""
    return "\n\n".join(p for p in re.split(r"\n\s*\n", answer) if not _is_general(p))


# 'section [1] "Top Contact Drivers"', 'the section titled "Ticket Volume" in [1]', 'section [2]'
_SECTION_TITLED = re.compile(r"\b(?:the )?sections? (\[\d+\])(?: titled| called)?\s*(\"[^\"\n]{1,80}\")", re.IGNORECASE)
_SECTION_TITLED_IN = re.compile(r"\b(?:the )?sections? (?:titled |called )?(\"[^\"\n]{1,80}\") in (\[\d+\])", re.IGNORECASE)
_SECTION_NUMBER = re.compile(r"(?<!\" )\b(?:the )?sections? (\[\d+\])", re.IGNORECASE)


def without_section_numbers(answer: str) -> str:
    """The model's own numbering of what it read ('specifically section [1] "Top Contact Drivers"')
    means nothing to the reader: it becomes the section's title with the source marker after it."""
    answer = _SECTION_TITLED.sub(r'the \2 section \1', answer)
    answer = _SECTION_TITLED_IN.sub(r'the \1 section \2', answer)
    return _SECTION_NUMBER.sub(r'the cited section \1', answer)


def unlabel_document_paragraphs(answer: str, deps: ChatDeps) -> str:
    """A "General knowledge:" paragraph that repeats the sections the model read is from the
    documents ("... applies to products purchased on or after 1 March 2026"): it loses the label."""
    out = []
    for p in re.split(r"\n\s*\n", answer):
        if _is_general(p):
            body = _GENERAL.sub("", p.lstrip().lstrip("> ").strip(), count=1)
            # ... only a stated fact (a figure, a date, a code) that the sections hold; a definition or an
            # explanation in the documents' words keeps its label ("Power stealing means a thermostat
            # draws ... through the call wire" lost it, and read as if from the documents; 26 Oct)
            if (numbers_in(body) or codes_in(body)) and implied_sources(body, deps):
                p = body
        out.append(p)
    return "\n\n".join(out)


# "The documents do not state the RAM needed. However, the system requirements list 4 GB [1].": a part
# of the answer that says the documents lack something, then gives it from them (the 0.8B in the final
# computer-use check, once per part of a two-part question)
_LACKS = re.compile(r"\b(documents?|manuals?|files?|excerpts?|sources?|policy|guide)\b[^.]{0,80}\b(do|does|did)\s*"
                    r"(not|n't)\s+(state|specify|list|give|contain|cover|mention|include|provide|say)\b|"
                    r"\b(is|are)\s+not\s+(stated|specified|mentioned|given|listed)\b", re.IGNORECASE)
_TURN = re.compile(r"^\W*(however|but|that said|instead|although)\b[,:]?\s*", re.IGNORECASE)


def without_contradicted_not_covered(answer: str, sources: str) -> str:
    """The answer without a "the documents do not state ..." sentence that the rest of its paragraph
    (or list item) answers with a figure the documents have."""
    known = numbers_in(sources)
    out = []
    for para in answer.split("\n"):
        sentences = re.split(r"(?<=[.!?])\s+", para.strip())
        lead = re.match(r"^(\s*(?:\*\*[^*]{0,40}\*\*:?\s*|[-*]\s+|\d+[.)]\s+)?)", sentences[0]) if sentences else None
        head = sentences[0][lead.end():] if lead else ""
        rest = " ".join(sentences[1:])
        if len(sentences) > 1 and _LACKS.search(head) and numbers_in(rest) & known:
            rest = _TURN.sub("", rest)
            para = (lead.group(1) if lead else "") + rest[:1].upper() + rest[1:]
        out.append(para)
    return "\n".join(out)
