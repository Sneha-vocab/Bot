import asyncio
import base64
import json
import os

import audioop
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from livekit import api, rtc

load_dotenv()

app = FastAPI()

LIVEKIT_URL = os.getenv('LIVEKIT_URL')
LIVEKIT_API_KEY = os.getenv('LIVEKIT_API_KEY')
LIVEKIT_API_SECRET = os.getenv('LIVEKIT_API_SECRET')
SMARTFLO_FROM_NUMBER = os.getenv('SMARTFLO_FROM_NUMBER') or os.getenv('SMARTFLO_PHONE_NUMBER', '')


def normalize_phone(number: str) -> str:
    if not number:
        return number
    clean = str(number).strip().replace(" ", "").replace("-", "").lstrip('+')
    if clean.startswith('91') and len(clean) > 10:
        clean = clean[2:]
    return clean


class SmartfloLiveKitBridge:
    def __init__(self, stream_sid: str, call_sid: str, account_sid: str,
                 from_number: str = None, to_number: str = None):
        self.stream_sid = stream_sid
        self.call_sid = call_sid
        self.account_sid = account_sid

        self.customer_phone = self._resolve_customer_phone(from_number, to_number)
        self.room_name = f"call-{self.customer_phone}"
        self.room = None
        self.audio_source = None
        self.audio_track = None
        self.ws = None
        self.chunk_counter = 1
        self._closed = asyncio.Event()

    @staticmethod
    def _resolve_customer_phone(from_number: str = None, to_number: str = None) -> str:
        smartflo_num = normalize_phone(SMARTFLO_FROM_NUMBER) if SMARTFLO_FROM_NUMBER else ''
        for num in [to_number, from_number]:
            if num:
                clean = normalize_phone(num)
                if clean and clean != smartflo_num:
                    return clean
        return normalize_phone(to_number) or normalize_phone(from_number) or "unknown"

    async def setup_livekit(self):
        token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
            .with_identity(f"smartflo-caller-{self.call_sid}") \
            .with_name("Phone Caller") \
            .with_grants(api.VideoGrants(
                room_create=True,
                room_join=True,
                room=self.room_name,
                can_publish=True,
                can_subscribe=True,
            ))

        self.room = rtc.Room()

        @self.room.on("track_subscribed")
        def on_track_subscribed(track: rtc.Track, publication, participant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                asyncio.create_task(self.forward_livekit_to_smartflo(track))

        @self.room.on("data_received")
        def on_data_received(data: rtc.DataPacket):
            try:
                payload = json.loads(data.data.decode("utf-8"))
                if payload.get("action") == "hangup":
                    self._closed.set()
            except Exception:
                pass

        await self.room.connect(LIVEKIT_URL, token.to_jwt())
        self.audio_source = rtc.AudioSource(8000, 1)
        self.audio_track = rtc.LocalAudioTrack.create_audio_track("smartflo-audio", self.audio_source)
        options = rtc.TrackPublishOptions()
        options.source = rtc.TrackSource.SOURCE_MICROPHONE
        await self.room.local_participant.publish_track(self.audio_track, options)
        print(f"[BRIDGE] ✅ Connected to LiveKit room: {self.room_name}")

    async def teardown(self):
        self._closed.set()
        if self.room:
            await self.room.disconnect()

    async def forward_livekit_to_smartflo(self, track: rtc.AudioTrack):
        audio_stream = rtc.AudioStream(track, sample_rate=8000, num_channels=1, frame_size_ms=20)
        async for event in audio_stream:
            if self._closed.is_set():
                break
            pcm = event.frame.data.tobytes()
            mulaw = audioop.lin2ulaw(pcm, 2)
            try:
                await self.ws.send_json({
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {"payload": base64.b64encode(mulaw).decode(), "chunk": self.chunk_counter}
                })
                self.chunk_counter += 1
            except Exception:
                break

    async def send_smartflo_audio_to_livekit(self, audio_payload: str):
        try:
            mulaw_data = base64.b64decode(audio_payload)
            pcm_data = audioop.ulaw2lin(mulaw_data, 2)
            frame = rtc.AudioFrame(
                data=pcm_data,
                sample_rate=8000,
                num_channels=1,
                samples_per_channel=len(pcm_data) // 2,
            )
            await self.audio_source.capture_frame(frame)
        except Exception as e:
            print(f"[BRIDGE] Audio capture error: {e}")


# ── WebSocket Endpoint ────────────────────────────────────────────────────────

@app.websocket("/smartflo/stream")
async def smartflo_websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    bridge = None
    try:
        while True:
            if bridge and bridge._closed.is_set():
                break

            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except Exception:
                break

            data = json.loads(message)
            event = data.get("event")

            if event == "start":
                start_data = data.get("start", {})
                bridge = SmartfloLiveKitBridge(
                    start_data.get("streamSid"),
                    start_data.get("callSid"),
                    start_data.get("accountSid"),
                    from_number=data.get("from") or start_data.get("from"),
                    to_number=data.get("to") or start_data.get("to"),
                )
                bridge.ws = websocket
                await bridge.setup_livekit()

            elif event == "media" and bridge:
                payload = data.get("media", {}).get("payload")
                if payload:
                    await bridge.send_smartflo_audio_to_livekit(payload)

            elif event == "stop":
                print(f"[BRIDGE] 🛑 SmartFlo stop event received")
                break

    finally:
        if bridge:
            await bridge.teardown()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("smartflow_bridge:app", host="0.0.0.0", port=8000, log_level="info")
