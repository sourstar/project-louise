"""`/api/ask/…` (07 §7.4): `ask`'s verbs over HTTP, one route each, the
body's keys the command's flags without their dashes, the answer the
command's `--json` output; the exit code is the status (0 → 200, 2 → 409,
3 → 503) and the body carries `exit` and the message. And `/api/ask/mcp`,
the MCP server of 05 §5.4 over HTTP (one JSON-RPC request per POST).
Migration step 11."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.hold.codex import CodexError

router = APIRouter(prefix="/api/ask", tags=["ask"])
STATUS = {0: 200, 1: 500, 2: 409, 3: 503}


def _answer(fn, *args, **kwargs) -> JSONResponse:
    try:
        return JSONResponse(fn(*args, **kwargs))
    except CodexError as e:
        code = int(getattr(e, "exit_code", 2))
        return JSONResponse({"exit": code, "error": str(e)}, status_code=STATUS.get(code, 500))


class SearchBody(BaseModel):
    query: str
    collections: list[str] | None = None
    space: str | None = None
    filters: dict[str, Any] | None = None
    limit: int = Field(10, ge=1, le=100)
    rerank_depth: int = Field(50, ge=1, le=300)
    as_of: str | None = None
    freshness: bool = False
    #: an EU act's past asked for: its versions the collection lacks, fetched
    #: now, bounded (05 §5.3 `history`, app/ask/history.py)
    history: bool = False
    translate: bool = False
    all_languages: bool = False
    #: the annotations collections consulted, by local name; None = every
    #: one held, [] = none (05 §5.3) — as the MCP tool takes it
    annotations: list[str] | None = None


@router.post("/search")
async def search(body: SearchBody) -> JSONResponse:
    from starlette.concurrency import run_in_threadpool
    from app.ask import results
    try:
        out = await run_in_threadpool(
            results.search, body.query, collections=body.collections, space=body.space, filters=body.filters,
            limit=body.limit, rerank_depth=body.rerank_depth, as_of=body.as_of, freshness=body.freshness,
            translate=body.translate, all_languages=body.all_languages, annotations=body.annotations,
            history=body.history)
    except CodexError as e:
        code = int(getattr(e, "exit_code", 2))
        return JSONResponse({"exit": code, "error": str(e)}, status_code=STATUS.get(code, 500))
    return JSONResponse(out)


class ChatBody(BaseModel):
    question: str
    collections: list[str] | None = None
    history: list[dict[str, Any]] | None = None
    answer_lang: str | None = None   # ISO code: answer in it; else the question's language
    # the reader's Speed: without it (Quality) older sources are read by
    # their language's reader (app/historical.py), as on /v1/chat/completions
    speed: bool = False


@router.post("/chat")
async def chat(body: ChatBody) -> JSONResponse:
    """`ask chat` over HTTP (05 §5.4; 07 §7.4): the answer with every tool
    call made and what it returned. `/v1/chat/completions` stays what it
    was — retrieval then an answer, for clients that speak that protocol."""
    import json
    from starlette.concurrency import run_in_threadpool
    from app.ask import chat as chat_mod
    try:
        out = await run_in_threadpool(chat_mod.ask, body.question, collections=body.collections,
                                      history=body.history, answer_lang=body.answer_lang,
                                      speed=body.speed)
    except CodexError as e:
        code = int(getattr(e, "exit_code", 2))
        return JSONResponse({"exit": code, "error": str(e)}, status_code=STATUS.get(code, 500))
    return JSONResponse(json.loads(json.dumps(out, default=str)))


class PdfBody(BaseModel):
    """`ask pdf` over HTTP: a question Louise answers, or — with `answer` —
    a client's own answer and the hits it used (`references`, as
    `louise_search` gives them)."""
    question: str
    collections: list[str] | None = None
    answer_lang: str | None = None
    paper: str = "a4"
    tz: str | None = None
    answer: str | None = None
    references: list[dict[str, Any]] | None = None


@router.post("/pdf")
async def pdf(body: PdfBody, request: Request):
    """`ask pdf` over HTTP (operator, 2026-11-11: the PDF "available through
    the other interfaces too"; app/ask_pdf.py): the question, the answer and
    every reference with the link to its original source, as the Ask page
    saves it; A4 or Letter. 503 while the layouts are taken or where the
    image has no PDF library."""
    from starlette.concurrency import run_in_threadpool
    from app import ask_pdf
    from app.ask import chat as chat_mod
    if body.paper not in ask_pdf.PAPER:
        return JSONResponse({"exit": 2, "error": f"paper is one of {', '.join(ask_pdf.PAPER)}"}, status_code=409)
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc)
    base = f"{proto}://{host.split(',')[0].strip()}"

    def make():
        if body.answer is not None:
            doc = ask_pdf.from_given(body.question, body.answer, body.references or [], paper=body.paper,
                                     tz=body.tz, answer_lang=body.answer_lang, collections=body.collections)
        else:
            out = chat_mod.ask(body.question, collections=body.collections, answer_lang=body.answer_lang,
                               passages=True)
            doc = ask_pdf.from_chat(body.question, out, paper=body.paper, tz=body.tz, collections=body.collections)
        return ask_pdf.make(doc, base_url=base)
    try:
        name, data = await run_in_threadpool(make)
    except CodexError as e:
        code = int(getattr(e, "exit_code", 2))
        return JSONResponse({"exit": code, "error": str(e)}, status_code=STATUS.get(code, 500))
    except (ask_pdf.Busy, ask_pdf.Unavailable) as e:
        return JSONResponse({"exit": 3, "error": str(e)}, status_code=503)
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@router.get("/pdf/{pid}")
async def pdf_kept(pid: str, request: Request, paper: str = "a4", tz: str | None = None):
    """The PDF of an answer `/v1/chat/completions` kept for its sources list
    (`louise_pdf`; app/ask_pdf.py `keep`): 404 — saying why — for one no
    longer kept."""
    from starlette.concurrency import run_in_threadpool
    from app import ask_pdf
    if paper not in ask_pdf.PAPER:
        return JSONResponse({"exit": 2, "error": f"paper is one of {', '.join(ask_pdf.PAPER)}"}, status_code=409)
    got = ask_pdf.kept(pid)
    if got is None:
        return JSONResponse({"exit": 2, "error": "this answer is no longer kept (24 hours have passed, or the "
                                                 "node restarted): ask the question again for its PDF"},
                            status_code=404)
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc)
    doc = ask_pdf.from_given(got["question"], got["answer"], got["hits"], paper=paper, tz=tz,
                             answer_lang=got["answer_lang"], collections=got["collections"])
    try:
        name, data = await run_in_threadpool(ask_pdf.make, doc, base_url=f"{proto}://{host.split(',')[0].strip()}")
    except (ask_pdf.Busy, ask_pdf.Unavailable) as e:
        return JSONResponse({"exit": 3, "error": str(e)}, status_code=503)
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


class LookupBody(BaseModel):
    id: str


@router.post("/lookup")
async def lookup(body: LookupBody) -> JSONResponse:
    from app.ask import lookup as lookup_mod
    return _answer(lookup_mod.lookup, body.id)


class TraverseBody(BaseModel):
    id: str
    relation: str | None = None
    depth: int = Field(1, ge=1, le=6)
    collection: str | None = None
    resolve_in: list[str] | None = None


@router.post("/traverse")
async def traverse(body: TraverseBody) -> JSONResponse:
    from starlette.concurrency import run_in_threadpool
    from app.ask import traverse as traverse_mod
    try:
        out = await run_in_threadpool(traverse_mod.traverse, body.id, relation=body.relation,
                                      depth=body.depth, collection=body.collection, resolve_in=body.resolve_in)
    except CodexError as e:
        code = int(getattr(e, "exit_code", 2))
        return JSONResponse({"exit": code, "error": str(e)}, status_code=STATUS.get(code, 500))
    return JSONResponse(out)


@router.get("/facets/{collection}")
async def facets(collection: str, field: str | None = None, under: str | None = None) -> JSONResponse:
    from starlette.concurrency import run_in_threadpool
    from app.ask import facets as facets_mod
    try:
        out = await run_in_threadpool(facets_mod.listing, collection, field, under)
    except CodexError as e:
        code = int(getattr(e, "exit_code", 2))
        return JSONResponse({"exit": code, "error": str(e)}, status_code=STATUS.get(code, 500))
    return JSONResponse(out)


#: a plate over this many bytes is sent in pieces (`figures.stream_figure`), never held whole
STREAMED_OVER = 32 << 20


@router.get("/figure")
async def figure(collection: str, uid: str, hash: str, download: bool = False):
    """07 §7.4's one route that is not JSON: a plate's bytes and its
    `Content-Type`, never base64 — a search result carries the hash, and a
    client fetches the picture beside the chunk that cited it.

    08 §8.7 (amended 2026-10-25, "Every plate"): every plate is carried and
    served, and only a DISPLAYABLE one — jpeg or png, which the bytes really
    are, within the byte and declared-megapixel caps — is answered inline
    under its type. Any other answers 415 with the reason and no bytes,
    unless `download=1` asks for the file: then, and for any plate asked
    that way, `application/octet-stream` with `Content-Disposition:
    attachment` — never inline, never `image/svg+xml` — so a browser saves
    it and never renders it. `nosniff` on both, so neither is re-typed.

    A PREVIEW (08 §8.7, amended 2026-11-26; operator: "we must show pictures
    in the results as it is done for gutenberg collection", "Option A"): a
    plate whose record names a type a preview is rendered for — TIFF — is
    answered inline as the PNG this node renders from it (app/ask/preview.py:
    a child process, the header's caps before any pixel), with
    `X-Louise-Preview` saying so; where none can be rendered, 415 with the
    reason, as for any plate that is not displayable. Its own bytes are
    `download=1`'s. The plate's bytes are NOT read here for it, and a plate
    over `STREAMED_OVER` is sent in pieces: EUR-Lex's largest is 565 MB and
    the web's budget is under 1 GB."""
    from fastapi.responses import Response, StreamingResponse
    from starlette.concurrency import run_in_threadpool
    from app.ask import figures as figures_mod, preview as preview_mod
    from app.hold import figures as fig
    nosniff = {"X-Content-Type-Options": "nosniff"}
    try:
        path, want, entry, _tree = await run_in_threadpool(figures_mod.figure_place, collection, uid, hash)
        media = str(entry.get("media_type") or "")
        if not download and media in preview_mod.PREVIEW_OF:
            try:
                png = await run_in_threadpool(preview_mod.png, collection, uid, hash)
            except preview_mod.NotPreviewable as e:
                return JSONResponse({"exit": 2, "error": f"not displayable: {e}; ask with download=1 for the file"},
                                    status_code=415, headers=nosniff)
            return Response(content=png, media_type="image/png", headers={
                **nosniff, "X-Louise-Preview": f"a preview rendered by this node from the plate's {media}; "
                                               f"download=1 answers the plate itself"})
        # its length by the CONTAINER's word, before any of its bytes: 404 where it is not held
        size = await run_in_threadpool(figures_mod.held_size, path, want)
        if download and size > STREAMED_OVER:
            # in pieces, hashed as it goes and held to that length: a plate that
            # is not its hash fails the transfer
            name = figures_mod.download_name(hash, media)
            return StreamingResponse(fig.stream_figure(path, want), media_type="application/octet-stream",
                                     headers={**nosniff, "Content-Length": str(size),
                                              "Content-Disposition": f'attachment; filename="{name}"',
                                              "Content-Security-Policy": "sandbox"})
        if not download:
            # refused on what the record and the container say, BEFORE its bytes
            # are read: a plate that will not be shown is not held to find that out
            why = figures_mod.entry_not_displayable({"media_type": media, "bytes": size})
            if why is not None:
                return JSONResponse({"exit": 2, "error": f"not displayable: {why}; ask with download=1 for "
                                                         f"the file"}, status_code=415, headers=nosniff)
        data = await run_in_threadpool(figures_mod.placed_bytes, path, want)
    except figures_mod.FigureNotServed as e:
        return JSONResponse({"exit": 2, "error": str(e)}, status_code=e.status)
    except CodexError as e:
        code = int(getattr(e, "exit_code", 2))
        return JSONResponse({"exit": code, "error": str(e)}, status_code=STATUS.get(code, 500))
    if download:
        name = figures_mod.download_name(hash, media)
        return Response(content=data, media_type="application/octet-stream",
                        headers={**nosniff, "Content-Disposition": f'attachment; filename="{name}"',
                                 "Content-Security-Policy": "sandbox"})
    why = figures_mod.not_displayable(data, media)
    if why is not None:
        return JSONResponse({"exit": 2, "error": f"not displayable: {why}; ask with download=1 for the file"},
                            status_code=415, headers=nosniff)
    return Response(content=data, media_type=media, headers=nosniff)


@router.post("/mcp")
async def mcp_route(request: Request) -> JSONResponse:
    """The MCP server over HTTP — the Streamable HTTP transport with JSON
    answers: one JSON-RPC message in, its response out, 202 and no body for
    a notification. A client that sends a batch gets the batch's answers."""
    from starlette.concurrency import run_in_threadpool
    from app.ask import mcp
    try:
        message = await request.json()
    except ValueError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "not JSON"}},
                            status_code=400)
    if isinstance(message, list):
        answers = [r for r in [await run_in_threadpool(mcp.handle, m) for m in message] if r is not None]
        return JSONResponse(answers) if answers else Response(status_code=202)
    response = await run_in_threadpool(mcp.handle, message)
    if response is None:
        return Response(status_code=202)
    return JSONResponse(response)


@router.get("/mcp")
async def mcp_stream() -> Response:
    """No server-initiated stream: the tools answer the request that called
    them (the transport allows 405 here)."""
    return Response(status_code=405, headers={"Allow": "POST"})
