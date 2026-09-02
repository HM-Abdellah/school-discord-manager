"""Instrumented ServerBuilder used to make Discord build stalls observable and bounded."""

from __future__ import annotations

import asyncio

from services.server_builder import ServerBuilder

STEP_TIMEOUT = 45


class DiagnosticServerBuilder(ServerBuilder):
    """ServerBuilder variant that identifies and bounds individual Discord operations."""

    async def _run_step(self, label: str, operation):
        print(f"[BUILD] -> {label}", flush=True)
        try:
            result = await asyncio.wait_for(operation, timeout=STEP_TIMEOUT)
        except TimeoutError as exc:
            print(f"[BUILD] TIMEOUT after {STEP_TIMEOUT}s: {label}", flush=True)
            raise RuntimeError(f"Discord operation timed out after {STEP_TIMEOUT}s: {label}") from exc
        print(f"[BUILD] <- {label}", flush=True)
        return result

    async def _ensure_stream_role(self, level_name: str, stream_name: str):
        return await self._run_step(
            f"stream teacher role: {level_name}/{stream_name}",
            super()._ensure_stream_role(level_name, stream_name),
        )

    async def _ensure_student_stream_role(self, level_name: str, stream_name: str):
        return await self._run_step(
            f"student stream role: {level_name}/{stream_name}",
            super()._ensure_student_stream_role(level_name, stream_name),
        )

    async def _ensure_subject_role(self, level_name: str, stream_name: str, subject: str):
        return await self._run_step(
            f"subject role: {level_name}/{stream_name}/{subject}",
            super()._ensure_subject_role(level_name, stream_name, subject),
        )

    async def _get_or_create_category(self, name: str, overwrites=None):
        return await self._run_step(
            f"category: {name}",
            super()._get_or_create_category(name, overwrites),
        )

    async def _get_or_create_text(self, category, name: str, *, topic: str, overwrites):
        return await self._run_step(
            f"text channel: {category.name}/{name}",
            super()._get_or_create_text(category, name, topic=topic, overwrites=overwrites),
        )

    async def _get_or_create_voice(self, category, name: str, overwrites):
        return await self._run_step(
            f"voice channel: {category.name}/{name}",
            super()._get_or_create_voice(category, name, overwrites),
        )
