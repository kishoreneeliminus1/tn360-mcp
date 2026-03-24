from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse

async def health(request):
    return JSONResponse({"status": "ok"})

app = Starlette(routes=[
    Route("/health", health),
])
