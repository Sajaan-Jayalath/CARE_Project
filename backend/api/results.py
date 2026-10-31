from urllib.parse import quote
from fastapi import APIRouter, Request, Response
from backend.dependencies import store
from backend.database.database import BackendError

router = APIRouter(prefix="/api/results", tags=["Results"])


@router.get("/{job_id}/preview")
def document_preview(job_id: str, request: Request):
    from backend.services.preview_service import preview_manifest
    return preview_manifest(store(request), job_id)


@router.get("/{job_id}/preview/pages/{number}")
def preview_page(job_id: str, number: int, request: Request):
    """A rendered page of this job's preview: the corrected copy once fixes are applied."""
    from backend.services.preview_service import job_page_image
    return Response(job_page_image(store(request), job_id, number), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/{job_id}/images/{image_id}")
def analysed_image(job_id: str, image_id: str, request: Request):
    """A display copy of an image the analysis reviewed."""
    return Response(store(request).image(job_id, image_id), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/{job_id}/document")
def corrected_document(job_id: str, request: Request):
    """A copy of the uploaded document with every applied fix written in."""
    from backend.services.export_service import corrected_document
    data, filename, media, unapplied = corrected_document(store(request), job_id)
    return Response(data, media_type=media, headers={
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe=""),
        "X-CARE-Unapplied": str(unapplied), "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.get("/{job_id}")
def results(job_id: str, request: Request):
    job = store(request).job(job_id, include_result=True)
    result = job.pop("result")
    if result is None:
        raise BackendError("result_unavailable", "No completed-window result is available; inspect the job status and error.", 409)
    return {"job": job, "is_final": job["is_final"], "result": result, "feedback": store(request).feedback(job_id),
            "edits": store(request).edits(job_id)}
