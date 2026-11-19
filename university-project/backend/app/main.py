import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router
from app.customer import router as customer_router
from app.desk import router as desk_router
from app.simulate import router as simulate_router
from app.runtime import init_runtime, runtime, start_background, stop_background

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_runtime()
    runtime.bus.bind(asyncio.get_running_loop())
    start_background()
    yield
    stop_background()


app = FastAPI(title="PayShield", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(customer_router)
app.include_router(desk_router)
app.include_router(simulate_router)
