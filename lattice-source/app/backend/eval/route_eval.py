"""How well does a model sort requests (question / pictures / summary / all_manuals)?

    python eval/route_eval.py http://127.0.0.1:18080/v1 [native|prompted]

Runs the manuals pipeline's routing step on labelled requests, the way the app does: requests
the keyword rules clear go straight to "question" without asking the model.
"""
import asyncio
import sys
import time

from oasis_engine.api import Engine, _ABOUT_MANUALS
from oasis_engine.figures import fix_typos, picture_request
from oasis_engine.llm import LLMConfig, make_model
from oasis_engine.pipeline import Pipeline, summary_request

CASES = [
    # (request, expected kind)
    ("shoe me some images from the manual", "pictures"),
    ("Show me the diagrams", "pictures"),
    ("any pics of the tool head?", "pictures"),
    ("diagarm for the shutdown", "pictures"),
    ("can I see a photo of the paint booth", "pictures"),
    ("where is the wiring diagram", "pictures"),
    ("give me a summary of this doc", "summary"),
    ("summarize the station 7 manual", "summary"),
    ("sumary please", "summary"),
    ("What is this manual about?", "summary"),
    ("give me the key points of the torque manual", "summary"),
    ("overview of the safety manual", "summary"),
    ("which manuals do you have?", "all_manuals"),
    ("summarize all the documents", "all_manuals"),
    ("list the manuals", "all_manuals"),
    ("How do I reset the error summary?", "question"),
    ("What does the outline of the booth look like?", "question"),
    ("What does the wiring diagram show?", "question"),
    ("What torque for M6 bolts?", "question"),
    ("What does E17 mean?", "question"),
    ("How often should the filters be changed?", "question"),
    ("Is there a figure for the maximum pressure?", "question"),
    ("what do the manuals say about PPE?", "question"),
    ("Which document covers lockout?", "question"),
    # the web platform's sample export (a report, meeting notes, a budget workbook)
    ("show me the SNR chart", "pictures"),
    ("show me the budget chart", "pictures"),
    ("graphs of snr vs lux", "pictures"),
    ("is there a picture in the budget?", "pictures"),
    ("Are there any pictures in the meeting notes?", "pictures"),
    ("shwo me the imges", "pictures"),
    ("What does the SNR figure show?", "question"),
    ("How many chart sets are budgeted?", "question"),
    ("Which charts were used for testing?", "question"),
    ("Is the picture quality good at low light?", "question"),
    ("summarize the budget", "summary"),
]


async def main(url: str, mode: str) -> None:
    model = make_model(LLMConfig(base_url=url, model="local", output_mode=mode))
    pipe = Pipeline([], model, output_mode=mode)
    right, times = 0, []
    for request, expected in CASES:
        gated = picture_request(fix_typos(request)) or summary_request(fix_typos(request)) or _ABOUT_MANUALS.search(request)
        t = time.perf_counter()
        if gated:
            kind, topic = await Engine.classify(None, request, pipe)  # the app's own decision
            times.append(time.perf_counter() - t)
        else:
            kind, topic = "question", "(not asked)"
        ok = kind == expected
        right += ok
        print(f"{'ok ' if ok else 'BAD'} {expected:11} got {kind:11} topic={topic!r:22} {request}")
    print(f"\n{right}/{len(CASES)} right; model asked {len(times)} times, "
          f"{sum(times) / max(len(times), 1):.1f} s on average")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "native"))
