"""Local text/file extraction endpoints, ready for the later React interface."""
from fastapi import APIRouter, File, UploadFile, Request, Query, Response
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from backend.ai.schemas.input_schema import StrictModel
from backend.extraction.common import MAX_BYTES
from backend.services.file_service import extract_file, extract_text
from backend.dependencies import store
from urllib.parse import quote

router = APIRouter(prefix="/api/extract", tags=["Text extraction"])
uploads = APIRouter(prefix="/api/uploads", tags=["Uploads"])


class TextInput(StrictModel):
    text: str = Field(min_length=1, max_length=1_000_000)


async def uploaded_document(file):
    try:
        data = await file.read(MAX_BYTES + 1)
        return await run_in_threadpool(extract_file, data, file.filename)
    finally:
        await file.close()


@router.post("/text")
async def text_content(body: TextInput):
    return await run_in_threadpool(extract_text, body.text)


@router.post("/file")
async def file_content(file: UploadFile = File(...)):
    return await uploaded_document(file)


@uploads.post("", status_code=201)
async def save_file(request: Request, file: UploadFile = File(...)):
    try:
        data = await file.read(MAX_BYTES + 1)
        extraction = await run_in_threadpool(extract_file, data, file.filename)
        return await run_in_threadpool(store(request).add_document, extraction, data, request.app.state.settings.max_documents)
    finally:
        await file.close()


@uploads.post("/text", status_code=201)
def save_text(request: Request, body: TextInput):
    extraction = extract_text(body.text)
    return store(request).add_document(extraction, body.text.encode(), request.app.state.settings.max_documents)


@uploads.get("")
def list_uploads(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    return {"uploads": store(request).documents(limit, offset)}


@uploads.get("/{upload_id}")
def get_upload(upload_id: str, request: Request):
    return store(request).document(upload_id)


@uploads.get("/{upload_id}/original")
def original_file(upload_id: str, request: Request):
    document = store(request).document(upload_id, original=True)
    # Pasted/typed text (format "text") downloads as text.txt, including older saved uploads.
    filename = "text.txt" if document["extraction"].get("format") == "text" else document["filename"]
    return Response(document["original"], media_type="application/octet-stream", headers={
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe=""),
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@uploads.delete("/{upload_id}", status_code=204)
def delete_upload(upload_id: str, request: Request):
    store(request).delete_document(upload_id)
    return Response(status_code=204)


@uploads.get("/{upload_id}/preview/pages/{number}")
def preview_page(upload_id: str, number: int, request: Request):
    from backend.services.preview_service import page_image
    return Response(page_image(store(request), upload_id, number), media_type="image/png", headers={"Cache-Control": "no-store"})
