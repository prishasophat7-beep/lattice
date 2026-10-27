"""Questions back from a sem-ai-kb knowledge base's decision tree (interaction/decision-tree.json).

The tree is written for a person walking a user through ("What do you want to do?" -> "What should
the model do?" -> "Where do the images come from?"). Lattice does not ask it from the top: for a first
question it searches as usual, then asks only the tree's question that tells apart what the search
found, the way a colleague would.

- "How do I train?" finds Training in Image Classification, Anomaly Detection and Text Reading:
  the tree's "What should the model do?" separates them, so that is asked.
- "How do I annotate text?" finds one section, 4.2 Annotating Text, but the tree leads to it with
  "Method 1" or "Method 2" (its results' start_hint): "How will you annotate the text?" is asked.
- "How do I train an anomaly detection model?" finds 3.3 Training; every way through the tree gives
  that same section, so nothing is asked.

A tree option is followed without asking when the question already names it ("... from a camera",
"... anomaly ...": words of its label or of the values it sets that its sibling options lack).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .kbfolder import Extras
from .models import Doc
from .pipeline import _STUCK, MAX_CLARIFY_OPTIONS, Clarification, ClarifyOption
from .search import SectionIndex, tokenize

# "What is the Frontier application?", "Explain training": the person wants to know, not to do something,
# and the tree asks what they want to do ("What would you like to do?"): answered, not asked back
_DEFINITION = re.compile(r"^\W*(?:what|who)\s+(?:is|are|was|were)\b|^\W*(?:tell me about|explain|describe|define|"
                         r"give me an? (?:overview|introduction)|overview of|introduction to)\b", re.IGNORECASE)

# sections scoring at least this share of the best one count as what the question found
KEEP_SHARE = 1 / 1.6
MAX_HITS = 12
# a sub-heading that is one way of doing its section's task: "4.2.2 Method 2: Assisted annotation"
_WAY = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?(option|method|type|mode|way|variant|approach|alternative)\s+([A-Z0-9]{1,3})\b",
                  re.IGNORECASE)


def _way_key(text: str) -> str | None:
    """"method 2" for "4.2.2 Method 2: Assisted annotation" and for "Method 2"."""
    m = _WAY.match(text)
    return f"{m.group(1).lower()} {m.group(2).lower()}" if m else None


# options that are no choice: the app adds its own "Something else..." to every question back
_NO_CHOICE = re.compile(r"^\s*(?:i'?m\s+|i am\s+)?(not sure|not decided|something else|other|none of these|"
                        r"i don'?t know|unsure)\b", re.IGNORECASE)  # "I'm not sure yet" (kb_converter_tool)
# words that say nothing about which option is meant ("Something else", "Not sure", "I", ...)
_NEUTRAL = frozenset(tokenize("something else not sure decided yet other the a an my i we you it this that "
                              "with from for and or of to in on by do does how what which"))


def _with_joins(tokens: list[str]) -> set[str]:
    """The words, and each two neighbouring words written as one: "SingleX" in a question names the
    option "Single X Classification" (office knowledge base, 27 Oct 2026: asked "Single X or Multi X?"
    after "stuck labelling images in SingleX")."""
    return set(tokens) | {a + b for a, b in zip(tokens, tokens[1:])}


@dataclass(frozen=True)
class Target:
    section: str          # kb.json section id
    variant: str | None   # "Method 2: Assisted annotation", when the tree's result names one


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"^\s*\d+(?:\.\d+)*\.?\s+", "", text.lower())).strip()


class Interview:
    def __init__(self, docs: list[Doc], extras: Extras):
        self.docs, self.extras, self.tree = docs, extras, extras.tree or {}
        self._ways: dict[str, str] = {}
        self.where: dict[str, tuple[int, str]] = {}  # kb id -> (doc index, node id)
        for di, doc in enumerate(docs):
            for nid, sec in doc.sections.items():
                for kid in sec.kb_ids:
                    self.where.setdefault(kid, (di, nid))
        self.nodes: dict = self.tree.get("nodes") or {}
        self.results: dict = self.tree.get("results") or {}

    # ── the tree ──

    def _resolve(self, vars_: dict, did: str | None = None) -> str | None:
        """The result the variables set lead to; `did`: a document's own table in a joined tree."""
        own = (self.tree.get("resolves") or {}).get(did) if did else None
        keys = (own or {}).get("keys") or self.tree.get("resolve_keys") or ["task", "framework"]
        parts = [str(vars_.get(k, "unknown")) for k in keys]
        table = (own or {}).get("table") if own else (self.tree.get("resolve") or {})
        table = table or {}
        for key in (":".join(parts), ":".join(parts[:-1] + ["unknown"]), parts[0]):
            if key in table:
                return table[key]
        return None

    def _reach(self, nxt: str | None, vars_: dict, seen: frozenset = frozenset()) -> set[str]:
        """Result ids reachable through `next`."""
        if not nxt or nxt == "open":
            return set()
        if nxt == "resolve" or nxt.startswith("resolve:"):
            rid = self._resolve(vars_, nxt[8:] or None)
            return {rid} if rid in self.results else set()
        if nxt.startswith("result:"):
            return {nxt[7:]} if nxt[7:] in self.results else set()
        node = self.nodes.get(nxt)
        if node is None or nxt in seen:
            return set()
        out: set[str] = set()
        for opt in node.get("options") or []:
            out |= self._reach(opt.get("next"), {**vars_, **(opt.get("set") or {})}, seen | {nxt})
        return out

    # ── what the question found ──

    def _found(self, question: str, index: SectionIndex, allowed: set[int] | None) -> list[str]:
        """kb.json section ids of the sections the question matches, best first."""
        hits = [h for h in index.search(question) if allowed is None or h.doc_index in allowed]
        if not hits:
            return []
        best = hits[0].score
        found: list[str] = []
        self._ways: dict[str, str] = {}
        for h in hits[:MAX_HITS]:
            if h.score < best * KEEP_SHARE:
                break
            sid = self._section_of(h.doc_index, h.node_id)
            if not sid or sid in found:
                continue
            # the same topic in several places ("Training" in three chapters) is what makes a question
            # ambiguous; a section about something else that also matches ("Ticket Volume" for "E-217")
            # is not another meaning of it
            if found and _norm(self.extras.section_titles.get(sid, "")) != _norm(self.extras.section_titles.get(found[0], "")):
                continue
            found.append(sid)
            way = self._way_of(h.doc_index, h.node_id, sid)
            if way:  # the question matched inside one way of doing it ("Propose" is under Method 2)
                self._ways[sid] = way
        return found

    def _way_of(self, di: int, nid: str, sid: str) -> str | None:
        """The sub-heading of section `sid` the node is (or is under), when it names a way ("Method 2: ...")."""
        doc = self.docs[di]
        while nid is not None and sid not in doc.sections[nid].kb_ids:
            if _WAY.match(doc.sections[nid].title):
                return doc.sections[nid].title
            nid = doc.sections[nid].parent
        return None

    def _section_of(self, di: int, nid: str | None) -> str | None:
        """The kb.json section a node belongs to (a sub-heading belongs to its section)."""
        doc = self.docs[di]
        while nid is not None:
            ids = [k for k in doc.sections[nid].kb_ids if not k.startswith("module:")]
            if ids:
                return ids[0]
            if any(k.startswith("module:") for k in doc.sections[nid].kb_ids):
                return None  # a module heading with no intro of its own
            nid = doc.sections[nid].parent
        return None

    def _targets(self, rids: set[str], found: list[str]) -> frozenset[Target]:
        out = set()
        for rid in rids:
            r = self.results[rid]
            mods = set(r.get("modules") or [])
            hint = str(r.get("start_hint") or "")
            for sid in found:
                if self.extras.section_module.get(sid) not in mods:
                    continue
                title = self.extras.section_titles.get(sid, "")
                variant = None
                if r.get("start_section"):  # convert_kb.py: the result names its section, and the way by itself
                    if r.get("way") and sid == r["start_section"]:
                        hit = self._ways.get(sid)
                        if hit and _way_key(hit) and _way_key(str(r["way"])) and _way_key(hit) != _way_key(str(r["way"])):
                            continue
                        variant = str(r["way"])
                    out.add(Target(sid, variant))
                    continue
                # "4.2 Annotating Text, Method 1": the result names a way of doing this very section
                head, _, rest = hint.partition(",")
                if rest.strip() and title and (_norm(head) == _norm(title) or _norm(title) in _norm(head)):
                    variant = rest.strip()
                    hit = self._ways.get(sid)
                    if hit and _way_key(hit) and _way_key(variant) and _way_key(hit) != _way_key(variant):
                        continue  # the question is about another way of doing this section
                out.add(Target(sid, variant))
        return frozenset(out)

    # ── asking ──

    def clarification(self, question: str, index: SectionIndex, allowed: set[int] | None = None) -> Clarification | None:
        if not self.nodes or self.tree.get("start") not in self.nodes or _DEFINITION.match(question):
            return None
        found = self._found(question, index, allowed)
        if not found:
            return None
        asked = _with_joins(tokenize(question))
        node_id, vars_ = self.tree["start"], {}
        for _ in range(len(self.nodes) + 1):  # a tree with a loop ends too
            node = self.nodes[node_id]
            options = []
            for opt in node.get("options") or []:
                if _NO_CHOICE.match(str(opt.get("label") or "")):
                    continue  # "Not sure", "Something else": the app offers its own "Something else..."
                v = {**vars_, **(opt.get("set") or {})}
                targets = self._targets(self._reach(opt.get("next"), v), found)
                if targets:
                    options.append((opt, v, targets))
            if not options:
                return None
            named = self._named(options, asked) or self._way_named(options, question)
            if named:
                options = named
            if len(options) == 1 or len({t for _, _, t in options}) == 1:
                opt, vars_, _ = options[0]
                nxt = opt.get("next") or ""
                if nxt in self.nodes:
                    node_id = nxt
                    continue
                return None  # one place the question leads to: nothing to ask
            # stuck, and the options are ways of doing one section ("Type 1 or Type 2?"): answered with
            # every way, then asked which step (pipeline.STUCK_NOTE), not asked which way first
            one_section = {t.section for _, _, ts in options for t in ts}
            if len(one_section) == 1 and (_STUCK.search(question)
                                          or not self._about_the_ways(question, next(iter(one_section)), index)):
                return None
            return self._ask(node, options, question, found)
        return None

    def _about_the_ways(self, question: str, sid: str, index: SectionIndex) -> bool:
        """Is the question about the part of section `sid` that is done in several ways ("Adding Data":
        "Type 1", "Type 2")? A draft tree asks "Type 1 or Type 2?" for the whole section, so "How do I
        split the dataset for Single X?" was asked it (Qwen 3.5 27B, office format, 27 Oct 2026). The
        best match inside the section must be one of the ways or the heading above them."""
        where = self.where.get(sid)
        if where is None:
            return True
        di, root = where
        doc = self.docs[di]
        inside = {s.node_id for s in doc.subtree(root)}
        ways = {nid for nid in inside if _WAY.match(doc.sections[nid].title)}
        if not ways:
            return True  # the ways are named only in the tree: nothing to tell them apart by
        about = ways | {doc.sections[nid].parent for nid in ways} | {
            s.node_id for nid in ways for s in doc.subtree(nid)}
        best = next((h for h in index.search(question) if h.doc_index == di and h.node_id in inside), None)
        return best is None or best.node_id in about

    def _way_named(self, options: list, question: str) -> list:
        """The options leading to the way the question names by its number ("... with method 2"): the
        tree's labels say "Assisted: the built-in reader proposes", the manual "Method 2: Assisted
        annotation", and "How do I annotate text with method 2?" was asked which method (27 Oct 2026)."""
        m = re.search(r"\b(option|method|type|mode|way|variant|approach|alternative)\s+([A-Z0-9]{1,3})\b", question,
                      re.IGNORECASE)
        if m is None:
            return []
        key = f"{m.group(1).lower()} {m.group(2).lower()}"
        return [item for item in options if any(t.variant and _way_key(t.variant) == key for t in item[2])]

    def _named(self, options: list, asked: set[str]) -> list:
        """The options the question already names, by words only they have."""
        words = []
        for opt, v, _ in options:
            words.append(_with_joins(tokenize(f"{opt.get('label', '')} {' '.join(map(str, (opt.get('set') or {}).values()))}"))
                         - _NEUTRAL)
        picked = []
        for k, item in enumerate(options):
            own = words[k] - set().union(*(w for j, w in enumerate(words) if j != k))
            if own & asked:
                picked.append(item)
        return picked

    def _ask(self, node: dict, options: list, question: str, found: list[str]) -> Clarification:
        choices = []
        for opt, _, targets in options[:MAX_CLARIFY_OPTIONS]:
            label = str(opt.get("label") or "").strip()
            # the option's section: the best match the question found among the places it leads to
            best = next(sid for sid in found if any(t.section == sid for t in targets))
            sections = {best}
            variants = {t.variant for t in targets if t.variant and t.section == best}
            where = self.where.get(next(iter(sections))) if len(sections) == 1 else None
            detail = ""
            if len(sections) == 1:
                sid = next(iter(sections))
                mod = self.extras.module_titles.get(self.extras.section_module.get(sid, ""), "")
                detail = f"{mod} › {self.extras.section_titles.get(sid, '')}".strip(" ›")
            choices.append(ClarifyOption(
                label=label.rstrip("?"), detail=detail,
                doc_ids=[self.docs[where[0]].doc_id] if where else
                sorted({self.docs[self.where[s][0]].doc_id for s in sections if s in self.where}),
                # the picked option is asked as the question with what it settles, read from its section
                # its section is read straight away; a way of doing it ("Method 2") is added to the question
                question=f"{question.rstrip('?')} ({next(iter(variants))})?" if len(variants) == 1 else question,
                section=where[1] if where else None))
        return Clarification(prompt=str(node.get("ask") or "Which one do you mean?"), options=choices)
