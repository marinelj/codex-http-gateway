from html import escape
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

STATIC = Path(__file__).parent / "static"


def mount_ui(app: FastAPI) -> None:
    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")

    @app.get("/", include_in_schema=False, response_class=HTMLResponse)
    async def console(request: Request):
        base = request.scope.get("root_path", "").rstrip("/")
        html = (STATIC / "index.html").read_text().replace("{{base}}", escape(base, quote=True))
        return HTMLResponse(
            html,
            headers={
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self' data:; connect-src 'self'; base-uri 'self'; "
                    "frame-ancestors 'none'; form-action 'self'"
                ),
                "Referrer-Policy": "no-referrer",
            },
        )

    @app.get("/ui", include_in_schema=False)
    async def console_alias(request: Request):
        return RedirectResponse(request.scope.get("root_path", "").rstrip("/") + "/")
