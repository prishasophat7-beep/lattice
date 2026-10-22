// UI stability: nothing jumps (layout shift), nothing spills or is cut off at any window size, and
// every screen looks the same as its approved reference screenshot (ui-tests/screenshots).
import { expect, test, type Page } from "@playwright/test";

// The app window: smallest allowed (tauri.conf.json minWidth/minHeight), a small laptop, default, full HD
const SIZES = [
  { width: 720, height: 560 },
  { width: 1024, height: 768 },
  { width: 1200, height: 820 },
  { width: 1920, height: 1080 },
];
const QUESTION = "How many days of paid time off do new employees get?";
const CLS_LIMIT = 0.1; // "good" by the Core Web Vitals definition

// Record layout shifts from the first paint (the Layout Instability API)
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const w = window as unknown as { __shifts: { t: number; value: number; nodes: string[] }[] };
    w.__shifts = [];
    new PerformanceObserver((list) => {
      for (const e of list.getEntries() as unknown as {
        startTime: number; value: number; hadRecentInput: boolean; sources?: { node?: Node | null }[];
      }[]) {
        if (e.hadRecentInput) continue; // shifts right after a click or key press are expected
        const nodes = (e.sources ?? []).map((s) => {
          const el = s.node as Element | null;
          return el ? `${el.nodeName.toLowerCase()}${el.className ? "." + String(el.className).split(" ")[0] : ""}` : "?";
        });
        w.__shifts.push({ t: e.startTime, value: e.value, nodes });
      }
    }).observe({ type: "layout-shift", buffered: true });
  });
});

async function open(page: Page) {
  await page.goto("/");
  await expect(page.locator("#question")).toBeEnabled({ timeout: 30_000 });
  await page.evaluate(() => document.fonts.ready);
}

async function ask(page: Page, question = QUESTION) {
  await page.locator("#question").fill(question);
  await page.locator(".ask-btn").click();
  await expect(page.locator(".answer:not(.answer-draft)").last()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: "Stop" })).toHaveCount(0, { timeout: 30_000 });
  await settled(page);
}

/** Wait until the conversation has finished its smooth scroll to the newest answer. */
async function settled(page: Page) {
  await page.evaluate(
    () => new Promise<void>((done) => {
      const thread = document.querySelector(".thread");
      let last = -1, still = 0;
      const tick = () => {
        const now = thread ? thread.scrollTop : 0;
        still = now === last ? still + 1 : 0;
        last = now;
        if (still >= 10) done(); else requestAnimationFrame(tick); // unchanged for 10 frames
      };
      tick();
    }),
  );
}

/** Cumulative Layout Shift: the worst burst of shifts (gaps under 1 s, at most 5 s long). */
async function cls(page: Page) {
  const shifts = await page.evaluate(() => (window as unknown as { __shifts: { t: number; value: number; nodes: string[] }[] }).__shifts);
  let worst = 0, current = 0, start = -Infinity, last = -Infinity;
  for (const s of shifts) {
    if (s.t - last > 1000 || s.t - start > 5000) { current = 0; start = s.t; }
    current += s.value; last = s.t;
    worst = Math.max(worst, current);
  }
  return { value: worst, shifts };
}

/** Elements that stick out of the window, or whose own text is cut off. */
async function overflow(page: Page) {
  return page.evaluate(() => {
    const out: string[] = [];
    const root = document.documentElement;
    if (root.scrollWidth > root.clientWidth + 1) out.push(`page scrolls sideways (${root.scrollWidth}px > ${root.clientWidth}px)`);
    const name = (el: Element) =>
      `${el.nodeName.toLowerCase()}${el.id ? "#" + el.id : ""}${typeof el.className === "string" && el.className ? "." + el.className.trim().split(/\s+/).join(".") : ""}` +
      ` "${(el.textContent ?? "").trim().slice(0, 30)}"`;
    const controls = document.querySelectorAll("button, a, input, textarea, select, h1, h2, label:not(.visually-hidden), .answer, .askbar, .sidebar");
    for (const el of controls) {
      const r = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      if (!r.width || !r.height || style.visibility === "hidden") continue;
      // only what is meant to be on screen: skip content scrolled inside its panel
      let clipped = false;
      for (let p = el.parentElement; p; p = p.parentElement) {
        const o = getComputedStyle(p).overflowY;
        if ((o === "auto" || o === "scroll") && p.scrollHeight > p.clientHeight) {
          const pr = p.getBoundingClientRect();
          if (r.bottom <= pr.top || r.top >= pr.bottom) clipped = true;
          break;
        }
      }
      if (clipped) continue;
      if (r.right > window.innerWidth + 1 || r.left < -1) out.push(`${name(el)} sticks out sideways (${Math.round(r.left)}..${Math.round(r.right)} of ${window.innerWidth})`);
      // a deliberate "..." (text-overflow: ellipsis, full text on hover) is not cut off
      if (el.matches("button, h1, h2") && el.scrollWidth > el.clientWidth + 1 && style.overflowX !== "auto" &&
          style.textOverflow !== "ellipsis")
        out.push(`${name(el)} text is cut off (${el.scrollWidth}px in ${el.clientWidth}px)`);
    }
    return out;
  });
}

/** The controls people always need are fully in the window, without scrolling anything. */
async function inReach(page: Page) {
  const out: string[] = [];
  const view = page.viewportSize()!;
  for (const [label, locator] of [
    ["question box", page.locator("#question")],
    ["Ask button", page.locator(".ask-btn")],
    ["Manage button", page.getByRole("button", { name: "Manage" })],
  ] as const) {
    const shown = locator.filter({ visible: true }); // the Ask screen stays mounted, hidden, behind the others
    if (!(await shown.count())) continue; // e.g. Ask view not shown
    const box = await shown.first().boundingBox();
    if (!box || box.y < 0 || box.x < 0 || box.y + box.height > view.height + 1 || box.x + box.width > view.width + 1)
      out.push(`${label} is not fully in the window: ${JSON.stringify(box)} in ${view.width}x${view.height}`);
  }
  return out;
}

async function noOverflow(page: Page) {
  expect(await overflow(page), "nothing sticks out or is cut off").toEqual([]);
  expect(await inReach(page), "question box, Ask and Manage in reach").toEqual([]);
}

const timesAndDates = (page: Page) => [page.locator(".answer-actions .meta"), page.locator(".history .meta, .history time")];

for (const size of SIZES) {
  test.describe(`${size.width}x${size.height}`, () => {
    test.use({ viewport: size });

    test("ask screen, empty", async ({ page }) => {
      await open(page);
      await noOverflow(page);
      await expect(page).toHaveScreenshot(`ask-empty-${size.width}.png`);
    });

    test("answer with sources", async ({ page }) => {
      await open(page);
      await ask(page);
      await expect(page.locator(".sources .source-row").first()).toBeVisible();
      await noOverflow(page);
      await expect(page).toHaveScreenshot(`answer-${size.width}.png`, { mask: timesAndDates(page) });
    });

    test("source panel", async ({ page }) => {
      await open(page);
      await ask(page);
      await page.locator(".sources .source-row").first().click();
      await page.waitForLoadState("networkidle");
      await noOverflow(page);
      await expect(page).toHaveScreenshot(`source-${size.width}.png`, { mask: timesAndDates(page) });
    });

    test("manuals", async ({ page }) => {
      await open(page);
      await page.getByRole("button", { name: "Manuals", exact: true }).first().click();
      await expect(page.locator(".doc-list li").first()).toBeVisible();
      await page.locator(".doc-list button").first().click();
      await page.waitForLoadState("networkidle");
      await noOverflow(page);
      await expect(page).toHaveScreenshot(`manuals-${size.width}.png`);
    });

    test("history", async ({ page }) => {
      await open(page);
      await page.getByRole("button", { name: "History", exact: true }).click();
      await expect(page.getByRole("heading", { name: "History" })).toBeVisible();
      await page.waitForLoadState("networkidle");
      await noOverflow(page);
      await expect(page).toHaveScreenshot(`history-${size.width}.png`, { mask: timesAndDates(page) });
    });

    test("manage panel", async ({ page }) => {
      await open(page);
      await page.getByRole("button", { name: "Manage" }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await page.waitForLoadState("networkidle");
      await noOverflow(page);
      await expect(page).toHaveScreenshot(`manage-${size.width}.png`, { mask: [page.locator(".manage .meta")] });
    });

    test("error message", async ({ page }) => {
      await open(page);
      await page.route("**/api/ask/stream", (route) => route.abort("connectionrefused"));
      await page.locator("#question").fill(QUESTION);
      await page.locator(".ask-btn").click();
      await expect(page.getByText(/not responding|went wrong|try again/i).first()).toBeVisible({ timeout: 15_000 });
      await noOverflow(page);
      await expect(page).toHaveScreenshot(`error-${size.width}.png`);
    });
  });
}

test.describe("layout shift", () => {
  test.use({ viewport: { width: 1200, height: 820 } });

  test("start-up does not jump", async ({ page }) => {
    await open(page);
    await page.waitForTimeout(2000); // status, suggestions and the knowledge base label arrive after the first paint
    const r = await cls(page);
    console.log(`CLS ${test.info().title}: ${r.value.toFixed(4)} ${JSON.stringify(r.shifts)}`);
    expect(r.value, `layout shift while starting: ${JSON.stringify(r.shifts)}`).toBeLessThan(CLS_LIMIT);
  });

  test("an answer streaming in does not jump", async ({ page }) => {
    await open(page);
    await page.waitForTimeout(1000);
    await page.evaluate(() => ((window as unknown as { __shifts: unknown[] }).__shifts = []));
    await ask(page);
    await page.waitForTimeout(1500); // pictures and sources settle
    const r = await cls(page);
    console.log(`CLS ${test.info().title}: ${r.value.toFixed(4)} ${JSON.stringify(r.shifts)}`);
    expect(r.value, `layout shift while answering: ${JSON.stringify(r.shifts)}`).toBeLessThan(CLS_LIMIT);
  });

  test("a follow-up question does not jump", async ({ page }) => {
    await open(page);
    await ask(page);
    await page.waitForTimeout(1000);
    await page.evaluate(() => ((window as unknown as { __shifts: unknown[] }).__shifts = []));
    await ask(page, "And after five years?");
    await page.waitForTimeout(1500);
    const r = await cls(page);
    console.log(`CLS ${test.info().title}: ${r.value.toFixed(4)} ${JSON.stringify(r.shifts)}`);
    expect(r.value, `layout shift on a follow-up: ${JSON.stringify(r.shifts)}`).toBeLessThan(CLS_LIMIT);
  });
});

test.describe("stress", () => {
  test("resizing with an answer on screen", async ({ page }) => {
    await page.setViewportSize({ width: 1200, height: 820 });
    await open(page);
    await ask(page);
    for (const width of [1920, 1366, 1024, 900, 800, 720, 1024, 1200]) {
      await page.setViewportSize({ width, height: width < 800 ? 560 : 820 });
      await page.waitForTimeout(150);
      expect(await overflow(page), `at ${width}px`).toEqual([]);
      expect(await inReach(page), `at ${width}px`).toEqual([]);
    }
  });

  test("large text and buttons at the smallest window", async ({ page }) => {
    await page.setViewportSize({ width: 720, height: 560 });
    await open(page);
    await page.getByRole("button", { name: "Large text and buttons" }).click();
    await noOverflow(page);
    await ask(page);
    await noOverflow(page);
    await expect(page).toHaveScreenshot("large-text-answer-720.png", { mask: timesAndDates(page) });
  });

  test("a very long question and an unbroken code", async ({ page }) => {
    await page.setViewportSize({ width: 720, height: 560 });
    await open(page);
    const long = "Where is the setting for " + "VPN-PROFILE-".repeat(30) + " and also " + "what to do when it fails, ".repeat(25);
    await page.locator("#question").fill(long);
    await noOverflow(page);
    await ask(page, long);
    await noOverflow(page);
  });

  test("ten questions in a row", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 });
    await open(page);
    for (let i = 0; i < 10; i++) await ask(page, i % 2 ? "How do I set up the VPN?" : QUESTION);
    await expect(page.locator(".answer:not(.answer-draft)")).toHaveCount(10);
    await noOverflow(page);
    // the ask bar stays in reach at the bottom of the window
    const bar = await page.locator(".askbar").boundingBox();
    expect(bar && bar.y + bar.height).toBeLessThanOrEqual(768 + 1);
  });
});

test.describe("history", () => {
  test.use({ viewport: { width: 1200, height: 820 } });

  test("search filters and delete asks first", async ({ page }) => {
    await open(page);
    await ask(page, "How do I set up the VPN?");
    await page.getByRole("button", { name: "History", exact: true }).click();
    const rows = page.locator(".history-row");
    const before = await rows.count();
    expect(before).toBeGreaterThan(1);

    // search: every word must appear in a question or answer of the conversation
    await page.getByRole("searchbox", { name: "Search history" }).fill("vpn");
    await expect(rows).not.toHaveCount(before);
    await page.getByRole("searchbox", { name: "Search history" }).fill("zzqxv nothing matches");
    await expect(page.getByText(/No conversations match/)).toBeVisible();
    await page.getByRole("searchbox", { name: "Search history" }).fill("");
    await expect(rows).toHaveCount(before);

    // delete: an inline confirmation, Cancel keeps it, Delete removes it. The real engine is called: a
    // stub here hid that the engine's CORS rules did not allow DELETE, so delete failed in the app
    const deleted: number[] = [];
    page.on("response", (r) => {
      if (r.request().method() === "DELETE") deleted.push(r.status());
    });
    await rows.first().getByRole("button", { name: /Delete conversation/ }).click();
    await rows.first().getByRole("button", { name: "Cancel" }).click();
    await expect(rows).toHaveCount(before);
    await rows.first().getByRole("button", { name: /Delete conversation/ }).click();
    await noOverflow(page);
    await rows.first().getByRole("button", { name: "Delete", exact: true }).click();
    await expect(rows).toHaveCount(before - 1);
    expect(deleted).toEqual([200]);
    await expect(page.getByRole("alert")).toHaveCount(0);
    // it stays deleted: the list after a reload comes from the engine
    await page.reload();
    await page.getByRole("button", { name: "History", exact: true }).click();
    await expect(rows).toHaveCount(before - 1);
  });
});

// Failures and their recovery: an answer is never lost and never doubled (flow audit, 20 Oct 2026)
test.describe("recovery", () => {
  test.use({ viewport: { width: 1200, height: 820 } });
  const sse = (...events: object[]) => events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
  const busy = { stage: "error", status: 503, message: "The language model is busy or still loading; try again shortly" };

  test("Try again after a failed Regenerate replaces the answer, not adds one", async ({ page }) => {
    await open(page);
    await ask(page, "How do I set up the VPN?");
    const sent: { replaces: string | null; conversation_id: string }[] = [];
    let fail = true;
    await page.route("**/api/ask/stream", async (route) => {
      sent.push(route.request().postDataJSON());
      if (fail) {
        fail = false;
        await route.fulfill({ contentType: "text/event-stream", body: sse({ stage: "selecting" }, busy) });
      } else await route.continue();
    });
    await page.getByRole("button", { name: "Regenerate" }).click();
    await expect(page.getByText(/busy or still loading/)).toBeVisible();
    await page.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByRole("button", { name: "Regenerate" })).toBeVisible({ timeout: 30_000 });
    // both asked to replace the first answer
    expect(sent.map((b) => b.replaces)).toEqual([sent[0].replaces, sent[0].replaces]);
    expect(sent[0].replaces).toBeTruthy();
    // reopened: one turn, answered
    const turns = await page.evaluate(async (cid) => {
      const r = await fetch(`http://127.0.0.1:8799/api/history?conversation_id=${cid}&limit=50`);
      return (await r.json()).map((e: { question: string; pending?: boolean }) => [e.question, !!e.pending]);
    }, sent[0].conversation_id);
    expect(turns).toEqual([["How do I set up the VPN?", false]]);
  });

  test("Ask again after a stopped answer replaces the question saved for it", async ({ page }) => {
    await open(page);
    const sent: (string | null)[] = [];
    let fail = true;
    await page.route("**/api/ask/stream", async (route) => {
      sent.push(route.request().postDataJSON().replaces);
      if (fail) {
        fail = false;
        await route.fulfill({ contentType: "text/event-stream",
                              body: sse({ stage: "held", history_id: "held01" }, { stage: "selecting" }, busy) });
      } else await route.continue();
    });
    await page.locator("#question").fill(QUESTION);
    await page.locator(".ask-btn").click();
    await page.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByRole("button", { name: "Regenerate" })).toBeVisible({ timeout: 30_000 });
    expect(sent).toEqual([null, "held01"]);
  });

  test("a stopped engine is said, and the app goes on when it is back", async ({ page }) => {
    await open(page);
    await page.route("http://127.0.0.1:8799/**", (route) => route.abort("connectionrefused"));
    await page.locator("#question").fill(QUESTION);
    await page.locator(".ask-btn").click();
    await expect(page.getByRole("alert").filter({ hasText: /engine stopped/ })).toBeVisible({ timeout: 15_000 });
    await expect(page.locator("#question")).toBeDisabled();
    await noOverflow(page);
    await page.unroute("http://127.0.0.1:8799/**");
    await expect(page.getByRole("alert").filter({ hasText: /engine stopped/ })).toHaveCount(0, { timeout: 15_000 });
    await expect(page.locator("#question")).toBeEnabled();
  });
});

test.describe("asking back and searching", () => {
  test.use({ viewport: { width: 1200, height: 820 } });
  const sse = (...events: object[]) => events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");

  test("Something else... lets the user type what they meant", async ({ page }) => {
    await open(page);
    const sent: { question: string; replaces: string | null }[] = [];
    let first = true;
    await page.route("**/api/ask/stream", async (route) => {
      const body = route.request().postDataJSON();
      sent.push(body);
      if (!first) return route.continue();
      first = false;
      await route.fulfill({ contentType: "text/event-stream", body: sse({ stage: "done", result: {
        question: body.question, found: false, answer: "Which do you mean?", mode: "manuals", citations: [],
        considered: [], seconds: { total: 1 }, model_requests: 1, unverified_numbers: [], evidence: [],
        history_id: "back01", figures: [], summary: false,
        clarify: { prompt: "Which do you mean?", options: [
          { label: "What is the End LR setting", detail: "", doc_ids: [], question: "What is the End LR setting?" },
          { label: "What is the project's end date", detail: "", doc_ids: [], question: "What is the project's end date?" }] },
      } }) });
    });
    await page.locator("#question").fill("What is the end goal?");
    await page.locator(".ask-btn").click();
    await page.getByRole("button", { name: "Something else..." }).click();
    await expect(page.getByText(/Type what you meant by “What is the end goal\?”/)).toBeVisible();
    await expect(page.locator("#question")).toBeFocused();
    await noOverflow(page);
    await page.locator("#question").fill("How do I set up the VPN?");
    await page.keyboard.press("Enter");
    await expect(page.locator(".answer:not(.answer-draft)").last()).toBeVisible({ timeout: 30_000 });
    // asked as typed, after the question back (which stays in the conversation)
    expect(sent.map((b) => [b.question, b.replaces])).toEqual([
      ["What is the end goal?", null], ["How do I set up the VPN?", null]]);
    await expect(page.getByText(/Type what you meant by/)).toHaveCount(0);
  });

  test("a slow earlier search does not replace the later one, and an error goes once search works", async ({ page }) => {
    await open(page);
    await page.getByRole("button", { name: "Manuals", exact: true }).first().click();
    let failed = false;
    await page.route("**/api/search?*", async (route) => {
      const q = new URL(route.request().url()).searchParams.get("q");
      if (q === "zzz" && !failed) {
        failed = true;
        return route.abort("connectionrefused");
      }
      if (q === "vp") {  // answered after "vpn", with a result that must never be shown
        await new Promise((r) => setTimeout(r, 1500));
        return route.fulfill({ json: [{ doc_id: "x", node_id: "0001", title: "STALE RESULT", doc_title: "x",
                                        path: ["x"], snippet: "stale", kind: "markdown", locator: "" }] });
      }
      await route.continue();
    });
    const box = page.locator("#manual-search");
    await box.fill("zzz");
    await expect(page.locator(".manuals-view .notice-bad")).toBeVisible();
    await box.fill("vp");
    await page.waitForTimeout(400); // "vp" is sent (after the 250 ms pause), then "vpn"
    await box.fill("vpn");
    await expect(page.locator(".manuals-view .notice-bad")).toHaveCount(0, { timeout: 5_000 });
    await expect(page.locator(".search-results .meta").first()).not.toHaveText("No sections match.");
    await page.waitForTimeout(2_000); // the slow "vp" answer has come by now
    await expect(page.getByText("STALE RESULT")).toHaveCount(0);
  });
});
