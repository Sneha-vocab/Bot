import asyncio
import logging
import os
import sys
from pathlib import Path

from livekit.plugins.turn_detector.multilingual import MultilingualModel

_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

for _noisy in (
    "livekit", "livekit.rtc", "livekit.agents", "livekit.plugins.sarvam",
    "livekit.plugins.deepgram", "livekit.plugins.google", "livekit.plugins.silero",
    "livekit.plugins.turn_detector", "livekit.plugins.noise_cancellation",
    "httpx", "httpcore", "google_genai", "google.genai", "grpc",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

from dotenv import load_dotenv
from google.genai import types
from livekit import agents, rtc
from livekit.agents import (
    AgentSession,
    MetricsCollectedEvent,
    ConversationItemAddedEvent,
    UserInputTranscribedEvent,
    room_io,
)
from livekit.plugins import deepgram, google, noise_cancellation, silero, sarvam

from agent.metrics import MetricsTracker
from agent.survey_agent import HelloBot

load_dotenv()


# ============================================================================
# Prewarming
# ============================================================================

def prewarm(proc: agents.JobProcess):
    print("🔥 PREWARMING MODELS...")
    proc.userdata["vad"] = silero.VAD.load()
    proc.userdata["stt"] = deepgram.STT(model="nova-3", language="hi")
    proc.userdata["llm"] = google.LLM(
        model="gemini-2.0-flash",
        temperature=0.1,
        thinking_config=types.ThinkingConfig(include_thoughts=False),
    )


# ============================================================================
# Main Agent Session
# ============================================================================

async def my_agent(ctx: agents.JobContext):
    print(f"✅ Job accepted for room: {ctx.room.name}")
    tracker = MetricsTracker()
    tracker.start_turn()

    call_id = ctx.room.name or "unknown"

    logger.info(f"[{call_id}] Connecting to LiveKit room...")
    await ctx.connect()
    logger.info(f"[{call_id}] ✅ Connected to LiveKit room")

    disconnect_event = asyncio.Event()
    bridge_connected = asyncio.Event()

    @ctx.room.on("reconnecting")
    def on_reconnecting():
        logger.warning(f"[{call_id}] ⚠️ Network fluctuation — room reconnecting, staying alive...")

    @ctx.room.on("reconnected")
    def on_reconnected():
        logger.info(f"[{call_id}] ✅ Room reconnected — resuming call")

    @ctx.room.on("disconnected")
    def on_room_disconnect(reason=None):
        logger.info(f"[{call_id}] 🔴 Room disconnected (reason={reason})")
        disconnect_event.set()

    session_tts = sarvam.TTS(
        model="bulbul:v3",
        speaker="simran",
        pace=1.0,
        target_language_code="hi-IN",
    )

    session = AgentSession(
        turn_detection=MultilingualModel(),  # type: ignore[arg-type]
        min_endpointing_delay=0.1,
        max_endpointing_delay=0.4,
        stt=ctx.proc.userdata["stt"],
        llm=ctx.proc.userdata["llm"],
        tts=session_tts,
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
    )

    @session.on("user_input_transcribed")
    def on_user_input_transcribed(ev: UserInputTranscribedEvent):
        if ev.is_final:
            logger.info(f"[{call_id}] 👤 User: {ev.transcript}")

    @session.on("error")
    def on_session_error(err):
        logger.error(f"[{call_id}] ❌ Session Error: {err}")

    transcript_buffer = []

    @session.on("conversation_item_added")
    def on_conversation_item_added(event: ConversationItemAddedEvent):
        from datetime import datetime, timezone
        item = event.item
        text = (item.text_content or "").strip()
        if not text:
            return
        role = getattr(item, "role", None)
        role_str = (getattr(role, "value", None) or getattr(role, "name", None) or str(role)).lower()
        ts = datetime.now(timezone.utc)
        transcript_buffer.append((role_str, text, ts))
        logger.info(f"[{call_id}] [{role_str.upper()}] {text[:120]}")

    @session.on("metrics_collected")
    def on_metrics_collected(ev: MetricsCollectedEvent):
        tracker.on_metrics(ev)

    @ctx.room.on("participant_connected")
    def on_participant_connected(participant_details):
        logger.info(f"[{call_id}] 📞 Participant connected: {participant_details.identity}")
        bridge_connected.set()

    # Handle race: bridge may have joined before the handler was registered
    if ctx.room.remote_participants:
        bridge_connected.set()

    try:
        await session.start(
            room=ctx.room,
            agent=HelloBot(),
            room_options=room_io.RoomOptions(
                audio_input=room_io.AudioInputOptions(
                    noise_cancellation=lambda params: (
                        noise_cancellation.BVCTelephony()
                        if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                        else noise_cancellation.BVC()
                    ),
                ),
            ),
        )
        logger.info(f"[{call_id}] ✅ AgentSession started, waiting for SmartFlo bridge...")

        # Wait for the SmartFlo bridge (customer answered the phone)
        try:
            await asyncio.wait_for(bridge_connected.wait(), timeout=120.0)
            logger.info(f"[{call_id}] ✅ Bridge connected — warming up audio path")
            await asyncio.sleep(0.35)
        except asyncio.TimeoutError:
            logger.warning(f"[{call_id}] ⚠️ SmartFlo bridge never connected within 120s — aborting")
            return

        greeting_text = "Hello! Hi there, how can I help you today?"
        logger.info(f"[{call_id}] 🎙️ Playing greeting")
        try:
            await asyncio.wait_for(session.say(greeting_text, allow_interruptions=True), timeout=30.0)
        except Exception as e:
            logger.warning(f"[{call_id}] Greeting error: {e}")

        def on_session_close(ev):
            disconnect_event.set()

        session.once("close", on_session_close)

        logger.info(f"[{call_id}] Waiting for room disconnect...")
        await disconnect_event.wait()

    finally:
        tracker.print_session_summary(call_id=call_id)
        print(f"📋 Transcript summary:")
        prev_ts = None
        for idx, entry in enumerate(transcript_buffer):
            role_str, text, ts = entry[0], entry[1], entry[2]
            if prev_ts and (ts - prev_ts).total_seconds() >= 0.5:
                gap_str = f" (+{(ts - prev_ts).total_seconds():.1f}s)"
            else:
                gap_str = ""
            print(f"   {idx + 1}. [{role_str}] [{ts.strftime('%H:%M:%S')}{gap_str}] {text}")
            prev_ts = ts


server = agents.WorkerOptions(
    agent_name="HelloBot",
    entrypoint_fnc=my_agent,
    prewarm_fnc=prewarm,
    num_idle_processes=5,
)

if __name__ == "__main__":
    agents.cli.run_app(server)
