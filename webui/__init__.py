"""Wave Memory WebUI — Quart + Blueprint + Hypercorn 守护线程架构 (v0.9)

对外接口保持兼容 main.py:
    from .webui import WaveMemoryWebUI
    webui = WaveMemoryWebUI(db=..., ..., port=...)
    await webui.start()
    await webui.stop()
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from astrbot.api import logger

from .container import ServiceContainer, get_container
from .server import Server


class WaveMemoryWebUI:
    """Wave Memory WebUI 入口类 — 兼容 main.py 原有调用方式。"""

    def __init__(
        self,
        db,
        query_engine,
        embedding_service,
        memory_index,
        tag_index,
        cooccurrence,
        spike_router=None,
        residual_pyramid=None,
        epa=None,
        geodesic=None,
        tag_extractor=None,
        writer=None,
        host: str = "0.0.0.0",
        port: int = 7890,
        password: str = "",
        plugin_config: dict = None,
    ):
        # 注入所有服务到全局容器
        container = get_container()
        container.initialize(
            db=db,
            query_engine=query_engine,
            embedding_service=embedding_service,
            memory_index=memory_index,
            tag_index=tag_index,
            cooccurrence=cooccurrence,
            spike_router=spike_router,
            residual_pyramid=residual_pyramid,
            epa=epa,
            geodesic=geodesic,
            tag_extractor=tag_extractor,
            writer=writer,
            password=password,
            plugin_config=plugin_config,
        )

        # 创建服务器实例
        self._server = Server(host=host, port=port)
        self._kg_warmup_task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动 WebUI 服务器，并后台预热 KG 星图缓存。"""
        await self._server.start()
        self._kg_warmup_task = asyncio.create_task(self._async_kg_cache_warmup())
        self._kg_warmup_task.add_done_callback(self._log_kg_warmup_result)

    async def _async_kg_cache_warmup(self) -> dict:
        """后台预热默认 KG 全量图缓存；不阻塞 AstrBot 启动。"""
        logger.info("[WaveMemory] KG cache warmup started")
        from .blueprints.kg import warmup_kg_cache

        result = await asyncio.to_thread(warmup_kg_cache, "facts")
        logger.info(
            f"[WaveMemory] KG cache warmup SUCCESS: layers={result.get('layers')} "
            f"edges={result.get('edges')} elapsed={result.get('elapsed_ms')}ms"
        )
        return result

    @staticmethod
    def _log_kg_warmup_result(task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            logger.warning("[WaveMemory] KG cache warmup canceled")
        except Exception as e:
            logger.warning(f"[WaveMemory] KG cache warmup failed: {e}")

    async def stop(self) -> None:
        """停止 WebUI 服务器，并取消尚未完成的后台预热任务。"""
        if self._kg_warmup_task and not self._kg_warmup_task.done():
            self._kg_warmup_task.cancel()
            try:
                await self._kg_warmup_task
            except asyncio.CancelledError:
                pass
        self._kg_warmup_task = None
        await self._server.stop()
        # 重置容器以便热重载
        ServiceContainer.reset()
