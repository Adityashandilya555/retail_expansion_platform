from fastapi import FastAPI

app = FastAPI(title="Z-Matrix Platform API")


@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"ok": True}
