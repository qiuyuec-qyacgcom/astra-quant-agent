"""Analysis API; all endpoints require the existing administrator session."""
from __future__ import annotations
import os
import tempfile
from pathlib import Path
from fastapi import Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from astra_backend import analysis_service as service
from astra_backend.analysis_store import Archive
from astra_backend.analysis_capture import identity
from astra_backend.analysis_metrics import breakdown
from astra_backend.analysis_export import write_bundle
from astra_backend.analysis_export_jobs import ExportJobs, ExportBusy


class ExportFileResponse(FileResponse):
    """Release the download lease even when the client disconnects mid-file."""
    async def __call__(self, scope, receive, send):
        cleanup, self.background = self.background, None
        try:
            await super().__call__(scope, receive, send)
        finally:
            if cleanup:
                await cleanup()


def install_routes(app, require_admin):
    jobs = ExportJobs()
    app.state.analysis_export_jobs = jobs

    def authenticate(x_astra_session: str | None = Header(default=None,alias="X-Astra-Session")):
        return require_admin(x_astra_session=x_astra_session)

    def selection(account: str = "", start: str | None = None, end: str | None = None,
                  inst: str = "", side: str = "", config_id: str = "", result: str = "", status: str = "closed"):
        try:
            return account or identity(), service.filters(start,end,inst,side,config_id,result,status)
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc)) from exc

    @app.get("/api/v1/ledger-statistics")
    def visible_ledger_statistics(trade_ids: str = Query("", max_length=8000)):
        from astra_backend.analysis_metrics import summarize, legacy_performance
        ids = set(trade_ids.split(",")) - {""}
        if len(ids) > 100:
            raise HTTPException(status_code=422, detail="最多统计 100 笔可见记录")
        rows = [t for t in Archive().trades(identity()) if t["id"] in ids]
        return {"statistics": summarize(rows), "performance": legacy_performance(rows)}

    prefix = "/api/v1/admin/analysis"
    dependencies = [Depends(authenticate)]

    @app.get(prefix+"/summary",dependencies=dependencies)
    def summary(selected=Depends(selection)):
        return service.load_summary(*selected)

    @app.get(prefix+"/breakdown",dependencies=dependencies)
    def grouped(by: str = "inst", selected=Depends(selection)):
        try:
            account,query=selected
            return {"by":by,"items":breakdown(Archive().trades(account,query),by)}
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc)) from exc

    @app.get(prefix+"/trades",dependencies=dependencies)
    def trades(page: int = Query(1,ge=1),page_size: int = Query(20,ge=1,le=100),selected=Depends(selection)):
        return service.trade_list(*selected,page=page,page_size=page_size)

    @app.get(prefix+"/trades/{trade_id}",dependencies=dependencies)
    def trade(trade_id: str,account: str = ""):
        value=service.trade_detail(account or identity(),trade_id)
        if value is None: raise HTTPException(status_code=404,detail="交易不存在或不属于所选账户")
        return value

    @app.get(prefix+"/events",dependencies=dependencies)
    def events(page: int = Query(1,ge=1),page_size: int = Query(30,ge=1,le=100),selected=Depends(selection)):
        return service.event_list(*selected,page=page,page_size=page_size)

    @app.get(prefix+"/events/{event_id}",dependencies=dependencies)
    def event(event_id: str,account: str = ""):
        value=service.event_detail(account or identity(),event_id)
        if value is None: raise HTTPException(status_code=404,detail="事件不存在或不属于所选账户")
        return value

    @app.get(prefix+"/configurations/{config_id}",dependencies=dependencies)
    def configuration(config_id: str,account: str = ""):
        value=service.configuration_detail(account or identity(),config_id)
        if value is None: raise HTTPException(status_code=404,detail="配置不存在或不属于所选账户")
        return value

    @app.get(prefix+"/export",dependencies=dependencies)
    def export(selected=Depends(selection)):
        # Retain the old URL for API clients, but avoid holding the ZIP in RAM.
        account,query=selected
        archive = Archive()
        directory = archive.path.parent / "analysis_exports"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, filename = tempfile.mkstemp(prefix="download-", suffix=".zip", dir=directory)
        path = Path(filename)
        try:
            with os.fdopen(fd, "wb") as target:
                write_bundle(account, query, target, archive)
            return ExportFileResponse(path, media_type="application/zip", filename="astra-analysis.zip",
                headers={"Cache-Control":"private, no-store", "Content-Encoding":"identity"},
                background=BackgroundTask(path.unlink, missing_ok=True))
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def session(x_astra_session: str | None = Header(default=None, alias="X-Astra-Session")):
        require_admin(x_astra_session=x_astra_session)
        return x_astra_session or ""

    @app.post(prefix+"/exports", status_code=202)
    def start_export(selected=Depends(selection), token=Depends(session)):
        try:
            return jobs.start(token, *selected)
        except ExportBusy:
            raise HTTPException(status_code=409, detail="已有分析包正在导出，请等待完成后重试")

    def job_or_404(action, *args):
        try:
            return action(*args)
        except KeyError:
            raise HTTPException(status_code=404, detail="导出任务不存在或已过期，请重新导出")

    @app.get(prefix+"/exports/{job_id}")
    def export_status(job_id: str, token=Depends(session)):
        return JSONResponse(job_or_404(jobs.get, token, job_id), headers={"Cache-Control":"private, no-store"})

    @app.delete(prefix+"/exports/{job_id}")
    def cancel_export(job_id: str, token=Depends(session)):
        return job_or_404(jobs.cancel, token, job_id)

    @app.post(prefix+"/exports/{job_id}/download")
    def prepare_download(job_id: str, request: Request, token=Depends(session)):
        job = job_or_404(jobs.get, token, job_id)
        if job["state"] != "ready":
            raise HTTPException(status_code=409, detail="分析包尚未生成")
        path = prefix + "/exports/" + job_id + "/file"
        response = JSONResponse({"url": path}, headers={"Cache-Control":"private, no-store"})
        # A narrowly scoped HttpOnly cookie allows the browser's native downloader
        # to authenticate, without buffering a Blob or placing credentials in URLs.
        response.set_cookie("astra_analysis_download", token, max_age=120, path=path,
                            httponly=True, secure=request.url.scheme == "https", samesite="strict")
        return response

    @app.get(prefix+"/exports/{job_id}/file")
    def export_file(job_id: str, request: Request):
        token = request.headers.get("X-Astra-Session") or request.cookies.get("astra_analysis_download") or ""
        require_admin(x_astra_session=token)
        try:
            path = job_or_404(jobs.acquire_file, token, job_id)
        except ExportBusy:
            raise HTTPException(status_code=409, detail="分析包尚未生成")
        return ExportFileResponse(path, media_type="application/zip", filename="astra-analysis.zip",
            headers={"Cache-Control":"private, no-store", "Content-Encoding":"identity"},
            background=BackgroundTask(jobs.release_file, token, job_id))
