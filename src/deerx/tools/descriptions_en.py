"""Arac aciklamalarinin Ingilizce karsiliklari.

Aciklamalar MODELE gidiyor. Ajan yonergeleri Ingilizce secildiginde
(`prompts/en/*.md`) arac aciklamalarinin Turkce kalmasi modele iki dilli bir
baglam verirdi: yonerge bir dilde, elindeki araclarin tarifi baska dilde.

Turkce metin aracin kendi sinifinda, kodun belgesi olarak duruyor -- orasi
davranisin anlatildigi yer ve oradan sokulmesi kod okunurlugunu dusururdu.
Ingilizce karsiligi burada ve `Tool.spec()` icinde uzerine biniyor.

Bicim: `{arac_adi: {"": aciklama, parametre_adi: aciklama}}`. Bos anahtar
aracin kendi aciklamasi. `tests/test_tools.py` her aracin ve aciklamali her
parametrenin burada karsiligi oldugunu kilitler: yeni bir arac sessizce
tek dilli kalamaz.
"""

from __future__ import annotations

from ..services import DEFAULT_READY_SECONDS

ENGLISH: dict[str, dict[str, str]] = {
    # ── Is akisi danismani ────────────────────────────────────────────── #
    "read_workflow": {
        "": """
    Reads the state of the workflow being discussed: its goal, the user's
    brief, its steps (runs and their phases), the artifacts it produced and
    its plans.

    Read this BEFORE claiming anything. Project records (requirements, gaps,
    decisions, questions) belong to the PROJECT rather than this workflow,
    and appear under a separate heading in the output.
    """,
    },
    "update_workflow": {
        "": """
    Changes the title, goal or brief of the workflow being discussed. Only
    the fields you pass are changed.

    Changing the GOAL is a heavy operation: phases decide whether to re-run
    by asking "which goal was this phase completed for?", so changing the
    goal makes completed phases eligible to run again. Leave it alone unless
    the user asked for it.
    """,
        "title": "New title.",
        "goal": "New goal.",
        "brief": "New brief for the agents.",
    },
    "resolve_question": {
        "": """
    Closes an open question with the answer the user gave, or skips it with
    a stated assumption when there is no answer.

    Use it ONLY when the user gave the answer in this conversation. Do not
    write your own guess as the answer — the reason these questions exist is
    that only the user holds the answer. Ask if you are not sure.

    The answer is written to the project memory AND the knowledge base, so
    later phases can still find it after history trimming.
    """,
        "key": "Question key, e.g. Q-001.",
        "answer": "The answer the user gave.",
        "assumption": (
            "The assumption to proceed with instead of an answer. Ignored "
            "when `answer` is given."
        ),
    },
    # ── Bilgi tabani ──────────────────────────────────────────────────── #
    "search_knowledge": {
        "": """
    Runs a hybrid search (semantic + keyword) over the indexed documents and
    code base. Use this BEFORE you assume anything: confirm requirements,
    existing behaviour and terminology here.

    Tip: several narrow queries beat one broad query. Narrow the area with
    `kinds`: doc (the spec), code (existing code), web (research).
    """,
        "query": "Search query in natural language.",
        "k": "Number of chunks to return (default 8).",
        "kinds": "Source kind filter.",
    },
    "read_document": {
        "": """
    Reads an indexed document in order, whole or a range of chunks. Use it when
    the search results do not give enough context and you need to read a
    section of the spec from start to finish.
    """,
        "source": "The exact source value from `list_knowledge`, or the title.",
        "start_chunk": "Starting chunk index (0-based).",
        "count": "How many chunks to read (default 6).",
    },
    "ingest_source": {
        "": """
    Indexes a file or directory into the knowledge base. For a directory the
    include/exclude patterns from the configuration apply. Files whose content
    has not changed are skipped.
    """,
        "path": "File or directory path.",
        "force": "Re-index even if unchanged.",
    },
    "list_knowledge": {
        "": "Lists the documents in the knowledge base along with statistics.",
    },

    # ── Kayit araclari ────────────────────────────────────────────────── #
    "record_requirements": {
        "": """
    Records the requirements extracted from the document (in bulk). Every
    requirement must be tied to something in the document (`source_ref`); mark
    an inference without such a basis as `category="assumption"`.

    Key format: REQ-001, REQ-002 …  Writing the same key again updates it.
    """,
    },
    "record_questions": {
        "": """
    Records ONLY the open questions that the user can answer.

    The difference from `record_gaps` matters:
      * `record_gaps` — a shortcoming or risk the team can resolve itself.
      * `record_questions` — information that is not in the document and cannot
        be found by research either; only the user knows it. For example: "Can
        we get the ERP's API documentation?", "Which customer segment comes
        first?", "What is the budget limit?"

    An unanswered question marked `blocking=true` STOPS THE PIPELINE and the
    user is asked for an answer. Use it only when going on without the answer
    would waste a large part of the work. If you can reasonably proceed on an
    assumption, set `blocking=false` and write your assumption into
    `suggestion`.

    Look first with `read_project_state(section="questions")`: the same
    question may already have been asked and answered.

    Key format: Q-001.
    """,
    },
    "record_gaps": {
        "": """
    Records gaps, uncertainties, risks and improvement opportunities (in bulk).
    Write the basis into `evidence`: which document or code shows this? Write
    the concrete proposal into `recommendation`.

    Key format: GAP-001.
    """,
    },
    "record_decisions": {
        "": """
    Records architectural decisions (in bulk, as an ADR summary). For each
    decision write the alternatives you weighed and the trade-offs -- later
    phases use these as data.

    Key format: ADR-001.
    """,
    },
    "record_research": {
        "": """
    Records sourced findings that came out of web research (in bulk). Mark a
    finding without a source URL as `confidence="low"`.
    """,
    },
    "record_tasks": {
        "": """
    Records development tasks (in bulk). Every task must:
      * be small enough to finish in one session,
      * name the files it will touch (`files`),
      * carry a machine-verifiable acceptance criterion (`acceptance`)
        (e.g. "pytest tests/test_auth.py passes").
    Write the keys of the prerequisite tasks into `deps`; the order is derived
    from that.

    Give every task a `lane`; the implementation phase routes the task to the
    specialist agent for that lane. Prefer splitting a piece of work across
    lanes: the API endpoint a backend task, the form a frontend task, the test
    a qa task.

    Key format: T-001.
    """,
    },
    "update_task": {
        "": """
    Updates a task's status and result. Call it for every task when you finish
    it in the implementation phase. Write into `result` what was done and how
    the verification passed.
    """,
        "key": "Task key, e.g. T-003.",
        "result": "Result summary / verification evidence.",
    },
    "save_artifact": {
        "": """
    Writes a produced artifact (analysis report, architecture document, mockup,
    plan) to disk and records it in the project memory. Artifacts are collected
    under `.deerx/artifacts/`.

    For mockups write single-file HTML with no external dependencies.
    """,
        "name": "File name, e.g. `analiz-raporu.md` or `mockup-dashboard.html`.",
        "content": "The full content of the file.",
        "summary": "A one-sentence summary.",
    },
    "read_project_state": {
        "": """
    Reads the project memory: requirements, gaps, decisions, research findings,
    tasks and artifacts. Use it to build a phase on the result of the previous
    one.
    """,
        "section": "Default: all (a summary).",
    },

    # ── Dosya sistemi ─────────────────────────────────────────────────── #
    "read_file": {
        "": """
    Reads a file in the workspace. For large files use `offset` and `limit`
    (line based). The output is line-numbered to make lining up an edit easier.
    """,
        "path": "File path relative to the workspace.",
        "offset": "Starting line (1-based).",
        "limit": "How many lines to read (default 800).",
    },
    "write_file": {
        "": """
    Writes the file with the given content in full (overwrites an existing one,
    creates it otherwise). Parent directories are created automatically. Prefer
    `edit_file` for partial changes.
    """,
        "path": "File path relative to the workspace.",
        "content": "The full new content of the file.",
    },
    "edit_file": {
        "": """
    Performs an exact text replacement in a file. `old_string` must match
    EXACTLY and be UNIQUE in the file; otherwise an error is returned. Add
    enough surrounding context to make it unique. `replace_all` replaces every
    match.
    """,
        "old_string": "The exact text to replace.",
        "new_string": "The text to write in its place.",
        "replace_all": "Replace every match.",
    },
    "list_dir": {
        "": "Lists the contents of a directory. Common build/dependency "
            "directories are skipped.",
        "path": "Directory path (default: the root).",
        "depth": "How deep to descend (default 2).",
    },
    "glob_files": {
        "": "Searches for files by glob pattern (e.g. `src/**/*.py`). Returns a "
            "list of paths.",
        "pattern": "Glob pattern.",
        "path": "Search root (default: the workspace).",
    },
    "grep_files": {
        "": """
    Searches file contents with a regular expression. Narrow the file type with
    `glob`. Returns the matching lines in file:line form.
    """,
        "pattern": "Python regex pattern.",
        "path": "Search root.",
        "glob": "File filter, e.g. `*.py`.",
        "max_results": "Default 120.",
    },

    # ── Kabuk ve servisler ────────────────────────────────────────────── #
    "run_command": {
        "": """
    Runs a shell command in the workspace and returns stdout/stderr. Use it to
    run tests, install dependencies, build and for git operations. Interactive
    commands (ones that wait for input) do not work. Raise `timeout` for
    commands that will take a while.
    """,
        "command": "The command to run.",
        "cwd": "Working directory (relative to the workspace, default the root).",
        "timeout": "Timeout in seconds.",
    },
    "start_service": {
        "": """
    Starts the application in the background and keeps it up for the whole run.

    The difference from `run_command`: it does not wait for the command to
    finish. A dev server, an API, a worker -- anything that does not end is
    started here.

    If you give a `port`, the call waits until that port starts listening; that
    way "started" means "genuinely ready". If the process dies immediately, the
    end of its log comes back as the error.

    Then: open it with `preview_open`, walk it with `browser_snapshot`, check
    for errors with `browser_console`, leave evidence with
    `browser_screenshot`. Do not say it is done without seeing what you made.
    """,
        "command": "The start command, e.g. `npm run dev`.",
        "port": "The local port expected to listen. Give it for web applications.",
        "name": "Service name (default: `app`). Distinguishes several services.",
        "cwd": "Working directory (relative to the workspace).",
        "ready_seconds": f"How long to wait for the port to open (default {DEFAULT_READY_SECONDS}).",
    },
    "service_log": {
        "": """
    Reads the output of a running service.

    When the page came back empty, a request returned 500, or a build silently
    failed, the reason is here. Read what you saw in the browser together with
    what the server said.
    """,
        "name": "Service name; not needed if there is only one.",
        "lines": "How many trailing lines (default 80).",
    },
    "stop_service": {
        "": """
    Stops a background service (together with its child processes).

    Stop it when you are done: it frees the port so the next start does not run
    into "port in use". Everything is shut down at the end of the run anyway.
    """,
        "name": "Service name; not needed if there is only one.",
    },
    "list_services": {
        "": "Lists the running background services.",
    },

    # ── Web ve tarayici ───────────────────────────────────────────────── #
    "fetch_url": {
        "": """
    Downloads a web page, extracts its text and indexes it into the knowledge
    base. The difference from the server-side `web_fetch` tool: the content is
    PERSISTENT and can be found again with `search_knowledge` in later phases.

    Use it for reference documentation, API pages and technical guides.
    """,
        "url": "An http/https address.",
        "index": "Whether to add it to the knowledge base (default true).",
    },
    "browser_console": {
        "": """
    Returns the open page's OWN errors: console lines, uncaught exceptions,
    failed requests and 4xx/5xx responses.

    A snapshot tells you how the page LOOKS, not whether it works. A button can
    sit in exactly the right place and still throw an exception into the console
    when clicked -- then the application is broken. Look here after every
    interaction; always look before you say "it works".

    The record is cleared on every `preview_open`/`browse_page`, so a previous
    page's error is not mistaken for this page's.
    """,
        "all": "The whole console record instead of only the problems (default false).",
    },
    "find_images": {
        "": """
    Searches the web for images; returns titles, image addresses, size and
    SOURCE.

    Results are ordered so that sources with a known licence come first
    (Openverse, Wikimedia Commons, Unsplash, Pexels, Art Institute). If an
    image will ship in a deliverable, pick one of those and credit it in the
    slide; the licence of the others is unknown.

    Then download it into the workspace with `download_image`.
    """,
        "query": "What to look for (English gives better results).",
        "max_results": "Default 12, at most 30.",
        "free_only": "Only sources with a known licence (default yes).",
    },
    "download_image": {
        "": """
    Downloads an image into the workspace and records it as an artifact.

    Presentation and mockup HTML files can then reference it with a relative
    address from the same folder: `<img src="landscape.jpg">`.

    The source address is written into the artifact summary; you need it to
    give credit.
    """,
        "url": "Direct address of the image.",
        "name": "File name, e.g. cover.jpg",
    },
    "web_search": {
        "": """
    Searches the web with the Chrome on the server; returns titles, addresses
    and summaries.

    Summaries are not enough to decide on: call `browse_page` afterwards to
    actually read a result's content.

    Use narrow, technical queries: not "React Native" but "React Native 0.76
    new architecture breaking changes".
    """,
        "query": "Search query.",
        "max_results": "Default 8, at most 20.",
    },
    "browse_page": {
        "": """
    Opens an address in a real browser and returns its readable text.

    Pages built with JavaScript work too. After opening the page, list its
    elements with `browser_snapshot` to act on it.
    """,
        "url": "The address to open (http/https).",
        "index": "Whether to add it to the knowledge base (default yes).",
    },
    "browser_snapshot": {
        "": """
    Enumerates the clickable/typable elements on the open page.

    Every element gets a `ref` number (`e1`, `e2` …). `browser_click` and
    `browser_type` use those numbers. Do not ask for raw HTML -- this list is
    both cheaper and more reliable.
    """,
    },
    "browser_click": {
        "": """
    Clicks an element enumerated by `browser_snapshot`.

    The page may change after the click; call `browser_snapshot` again to see
    the new state.
    """,
        "ref": "Element number, e.g. e7.",
    },
    "browser_type": {
        "": """
    Types text into a field; presses Enter if asked.

    For filling a search box or trying a form. `browser_snapshot` gives you the
    field's number.
    """,
        "ref": "Field number, e.g. e3.",
        "text": "The text to type.",
        "enter": "Press Enter at the end.",
    },
    "browser_back": {
        "": "Goes back one page in the browser.",
    },
    "browser_screenshot": {
        "": """
    Takes a screenshot of the open page and saves it as an artifact.

    The user sees it in the interface. Use it while opening and checking the
    application you built yourself -- saying "it works" and showing it are not
    the same thing.
    """,
        "name": "File name, e.g. anasayfa.png",
        "full_page": "The whole page (default no).",
    },
    "preview_open": {
        "": """
    Opens the local application you started yourself in the browser.

    Only a port on 127.0.0.1 can be given, and the permission is valid only for
    this run. You must have started the application with `start_service` first
    (NOT with `run_command`: that waits for the command to finish and kills a
    server that never does).

    Do not say it is done without seeing what you made: open it, walk it with
    `browser_snapshot`, check for errors with `browser_console`, leave evidence
    with `browser_screenshot`.
    """,
        "port": "Local port, e.g. 3000.",
        "path": "Path, default /.",
    },
}
