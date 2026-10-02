"""Aplicación web del ERP."""

from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from erp.db import ErrorNegocio, conectar, inicializar
from erp.rutas import base, inicio

RAIZ = Path(__file__).resolve().parent


def crear_app(motor_voz=None, nube: bool = False) -> FastAPI:
    con = conectar()
    inicializar(con)
    con.close()

    app = FastAPI(title="ERP Ferretería", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.motor_voz = motor_voz
    app.state.nube = nube  # versión de prueba en internet: sin IA
    if nube:
        from erp import auth
        auth.SESIONES_FIRMADAS = True
    from erp.asistente.cerebro import Cerebro
    from erp.asistente.conversacion import Asistente
    llm = getattr(motor_voz, "llm", None)
    app.state.asistente = Asistente(Cerebro(llm.url, slot=1) if llm else None)
    app.mount("/static", StaticFiles(directory=RAIZ / "static"), name="static")

    @app.middleware("http")
    async def mismo_origen(request: Request, call_next):
        """Las escrituras solo se aceptan desde las páginas del propio ERP."""
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origen = request.headers.get("origin") or request.headers.get("referer")
            if origen and urlparse(origen).netloc != request.url.netloc:
                return JSONResponse({"error": "Origen no permitido."}, status_code=403)
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    @app.exception_handler(ErrorNegocio)
    async def _error_negocio(request: Request, exc: ErrorNegocio):
        return base.error_negocio(request, exc)

    @app.exception_handler(base.NoAutenticado)
    async def _no_autenticado(request: Request, exc):
        destino = f"/ingresar?siguiente={quote(request.url.path)}"
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "Tu sesión terminó. Vuelve a ingresar."}, status_code=401)
        if request.headers.get("HX-Request") == "true":
            return Response(status_code=200, headers={"HX-Redirect": destino})
        return RedirectResponse(destino, status_code=303)

    @app.exception_handler(base.SinPermiso)
    async def _sin_permiso(request: Request, exc):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "Tu usuario no tiene permiso para esto."}, status_code=403)
        if request.headers.get("HX-Request") == "true":
            return base.aviso_htmx("Tu usuario no tiene permiso para esto.", "error")
        return HTMLResponse('<p style="font-family:system-ui;padding:24px">Tu usuario no tiene permiso para ver esta página. '
                            '<a href="/">Volver</a></p>', status_code=403)

    app.include_router(inicio.router)
    from erp.rutas import (admin, asistente, caja, clientes, compras, conteos, inventario, productos, reportes,
                           traslados, ventas)
    for modulo in (asistente, productos, inventario, conteos, ventas, caja, clientes, compras, traslados, reportes, admin):
        app.include_router(modulo.router)
    return app
