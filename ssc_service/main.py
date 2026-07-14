import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from routers import checkout, sessions

app = FastAPI(
    title="Smart Checkout Service",
    version="2.0.0",
    description="API cho hệ thống tự động nhận diện và tính tiền sản phẩm siêu thị",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(checkout.router)      # Legacy single-image endpoint
app.include_router(sessions.router)      # Session-based checkout (new)

@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok", "version": "2.0.0"}

@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/ui/index.html")

# Serve the UI
_ui_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ssc_ui")
if os.path.isdir(_ui_dir):
    app.mount("/ui", StaticFiles(directory=_ui_dir, html=True), name="ui")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8801, reload=True)

