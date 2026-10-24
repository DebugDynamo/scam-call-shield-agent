"""Shared Pipecat pipeline construction for browser (SmallWebRTC) and phone (Plivo websocket) transports.

Reused from the reference phone bot: Silero VAD tuning via VADParams, tool calling via FunctionSchema/ToolsSchema,
LLMContextAggregatorPair, metrics, interruptions via the aggregator turn strategies. Stripped: all interview logic,
language/prompts, hang-up gating, Smart-Turn tuning, silence watchdog (see docs/REFERENCE_CODEBASE_AUDIT.md).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from loguru import logger
from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMMessagesAppendFrame, TTSSpeakFrame
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.llm_service import FunctionCallParams
from pipecat.workers.runner import WorkerRunner

from ..agent.llm import MockLLM
from ..config import Settings
from ..providers import factory
from ..providers.mock_voice import MockLLMProcessor
from ..tools.registry import ToolRegistry

GREETING_PROMPT = "Greet the user in one short sentence and ask how you can help."
MOCK_GREETING = "Hello. This is the mock voice assistant. Speech recognition is simulated, so I cannot hear you."


def tools_schema(registry: ToolRegistry) -> ToolsSchema:
    return ToolsSchema(standard_tools=[
        FunctionSchema(
            name=t.name, description=t.description,
            properties=t.input_schema.get("properties", {}), required=t.input_schema.get("required", []),
        )
        for t in registry.all()
    ])


@dataclass
class VoiceBuild:
    worker: PipelineWorker
    processors: list[Any]
    simulated: bool


def build_voice_worker(settings: Settings, svc, rec, rt, transport, *, telephony: bool) -> VoiceBuild:
    """Construct (but do not run) the pipeline. Performs no network I/O."""
    holder: dict[str, Any] = {}
    stt, tts = factory.build_stt(settings), factory.build_tts(settings)
    mock_llm = settings.llm_provider == "mock"
    context = LLMContext([], tools_schema(svc.registry))

    async def push_ui(payload: dict[str, Any]) -> None:
        w = holder.get("worker")
        if w is not None:
            try:
                await w.rtvi.send_server_message(payload)
            except Exception as e:  # UI telemetry must never break the call
                logger.debug("rtvi server message failed: {}", type(e).__name__)

    if mock_llm:
        mllm = MockLLM()

        async def reply(user_text: str) -> str:
            turn = await rt.agent.text_turn(mllm, user_text)
            for m in turn.messages:
                if m.role == "tool":
                    await push_ui({"type": "tool", "tool": (m.tool_call or {}).get("name"), "status": (m.tool_call or {}).get("status")})
            return turn.messages[-1].text

        llm: Any = MockLLMProcessor(reply)
    else:
        llm = factory.build_llm(settings, rt.agent.system_prompt)

        def make_handler(name: str):
            async def handler(params: FunctionCallParams):
                outcome, _ = await rt.agent.call_tool(name, params.arguments or {}, add_history=False)
                await push_ui({"type": "tool", "tool": name, "status": outcome.status, "latencyMs": outcome.latency_ms,
                               "result": outcome.result if rt.agent.persist_transcripts else {}})
                await params.result_callback(outcome.for_llm())
            return handler

        for spec in svc.registry.all():
            llm.register_function(spec.name, make_handler(spec.name))

    sr_in, sr_out = (8000, 8000) if telephony else (16000, 24000)
    vad = SileroVADAnalyzer(params=VADParams(confidence=0.75, start_secs=0.25, stop_secs=0.4, min_volume=0.4))
    user_agg, assistant_agg = LLMContextAggregatorPair(context, user_params=LLMUserAggregatorParams(vad_analyzer=vad))

    @user_agg.event_handler("on_user_turn_stopped")
    async def _user_done(aggregator, strategy, message):
        text = (message.content or "").strip() if message else ""
        if text and not mock_llm:  # mock path records inside reply()
            await rt.agent.add_message("user", text)
        await svc.touch(rec.id)

    @assistant_agg.event_handler("on_assistant_turn_stopped")
    async def _assistant_done(aggregator, message):
        text = (message.content or "").strip() if message else ""
        if text and not mock_llm:
            await rt.agent.add_message("assistant", text)

    latency = UserBotLatencyObserver()

    @latency.event_handler("on_latency_measured")
    async def _latency(observer, secs):
        ms = int(secs * 1000)
        rt.diagnostics["lastLatencyMs"] = ms
        await push_ui({"type": "latency", "ms": ms})

    processors = [transport.input(), stt, user_agg, llm, tts, transport.output(), assistant_agg]
    worker = PipelineWorker(
        Pipeline(processors),
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True,
                              audio_in_sample_rate=sr_in, audio_out_sample_rate=sr_out),
        observers=[latency],
        name=f"voice-{rec.id[:8]}",
    )
    holder["worker"] = worker
    rt.worker = worker

    async def greet() -> None:
        if mock_llm:
            await worker.queue_frames([TTSSpeakFrame(MOCK_GREETING)])
        else:
            await worker.queue_frames([LLMMessagesAppendFrame([{"role": "user", "content": GREETING_PROMPT}], run_llm=True)])

    async def notify(text: str) -> None:
        if not mock_llm:
            await worker.queue_frames([LLMMessagesAppendFrame([{"role": "user", "content": f"[system note] {text}"}], run_llm=True)])
        await push_ui({"type": "approval_resolved", "text": text})

    rt.notify = notify
    rt.diagnostics["greet"] = greet
    return VoiceBuild(worker=worker, processors=processors, simulated=rec.simulated)


def spawn_runner(svc, session_id: str, rt, worker: PipelineWorker) -> asyncio.Task:
    """Run the worker in the background; always clean the session up when the pipeline finishes."""

    async def _run() -> None:
        try:
            runner = WorkerRunner(handle_sigint=False)
            await runner.add_workers(worker)
            await runner.run()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("voice pipeline for {} crashed", session_id)
            if not rt.closing:
                await svc.fail(session_id, f"pipeline error: {type(e).__name__}: {e}"[:300])
            return
        if not rt.closing:
            await svc.end(session_id, None)

    task = asyncio.create_task(_run(), name=f"voice-runner-{session_id[:8]}")
    rt.runner_task = task
    return task
