from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from livekit.agents import metrics, MetricsCollectedEvent

_S_TO_MS = 1000.0


@dataclass
class TurnMetrics:
    turn_id: int
    started_at: datetime = field(default_factory=datetime.now)
    stt_ms: Optional[float] = None
    eou_ms: Optional[float] = None
    llm_ttft_ms: Optional[float] = None
    llm_ms: Optional[float] = None
    llm_tokens: Optional[int] = None
    tts_ttfb_ms: Optional[float] = None
    tts_ms: Optional[float] = None
    tts_characters: Optional[int] = None

    @property
    def total_ms(self) -> float:
        return sum(v for v in [self.stt_ms, self.eou_ms, self.llm_ms, self.tts_ms] if v)

    def has_data(self) -> bool:
        return any([self.stt_ms, self.llm_ms, self.tts_ms])

    def __str__(self) -> str:
        sep = "─" * 52
        rows = [f"\n📊 Turn {self.turn_id}  ({self.started_at.strftime('%H:%M:%S')})", sep]
        if self.stt_ms is not None:
            rows.append(f"  STT              {self.stt_ms:>8.0f} ms")
        if self.eou_ms is not None:
            rows.append(f"  EOU Delay        {self.eou_ms:>8.0f} ms")
        if self.llm_ttft_ms is not None:
            rows.append(f"  LLM TTFT ⭐      {self.llm_ttft_ms:>8.0f} ms")
        if self.llm_ms is not None:
            rows.append(f"  LLM Total        {self.llm_ms:>8.0f} ms")
        if self.llm_tokens is not None:
            rows.append(f"  LLM Tokens       {self.llm_tokens:>8}")
        if self.tts_ttfb_ms is not None:
            rows.append(f"  TTS TTFB         {self.tts_ttfb_ms:>8.0f} ms")
        if self.tts_ms is not None:
            rows.append(f"  TTS Total        {self.tts_ms:>8.0f} ms")
        if self.tts_characters is not None:
            rows.append(f"  TTS Characters   {self.tts_characters:>8}")
        rows.append(f"  Total Latency    {self.total_ms:>8.0f} ms")
        rows.append(sep)
        return "\n".join(rows)


@dataclass
class ThresholdAlert:
    label: str
    actual_ms: float
    limit_ms: float

    def __str__(self) -> str:
        return f"⚠️  {self.label}: {self.actual_ms:.0f}ms > {self.limit_ms:.0f}ms"


class MetricsTracker:
    _STT_LIMIT_MS = 500.0
    _LLM_TTFT_LIMIT_MS = 500.0
    _TTS_LIMIT_MS = 600.0
    _LLM_LIMIT_MS = 2000.0
    _TOTAL_LIMIT_MS = 2000.0

    def __init__(self) -> None:
        self._usage_collector = metrics.UsageCollector()
        self._turns: dict[int, TurnMetrics] = {}
        self._current_turn_id = 0
        self._alerts: list[ThresholdAlert] = []
        self._handlers: dict[type, Callable] = {
            metrics.STTMetrics: self._apply_stt,
            metrics.EOUMetrics: self._apply_eou,
            metrics.LLMMetrics: self._apply_llm,
            metrics.TTSMetrics: self._apply_tts,
            # metrics.VADMetrics: self._apply_vad,
        }

    def start_turn(self) -> int:
        self._current_turn_id += 1
        self._turns[self._current_turn_id] = TurnMetrics(turn_id=self._current_turn_id)
        return self._current_turn_id

    def on_metrics(self, ev: MetricsCollectedEvent) -> None:
        metrics.log_metrics(ev.metrics)
        self._usage_collector.collect(ev.metrics)
        if self._current_turn_id == 0:
            self.start_turn()
        turn = self._turns[self._current_turn_id]
        handler = self._handlers.get(type(ev.metrics))
        if handler:
            handler(turn, ev.metrics)

    def print_session_summary(self, call_id: Optional[str] = None, agreement_no: Optional[str] = None) -> None:
        usage = self._usage_collector.get_summary()
        print("\n\n" + "=" * 70)
        print(f"📊 SESSION METRICS SUMMARY | Agreement: {agreement_no or 'Unknown'}")
        print("=" * 70)
        for turn in self._turns.values():
            if turn.has_data():
                print(str(turn))
        self._print_usage_summary(usage)
        self._print_cost_estimate(usage)
        self._print_alerts()
        self._print_latency_statistics()
        print("=" * 70)

    def _alert_if_exceeded(self, label: str, actual_ms: float, limit_ms: float) -> None:
        if actual_ms > limit_ms:
            alert = ThresholdAlert(label, actual_ms, limit_ms)
            self._alerts.append(alert)
            print(str(alert))

    def _apply_stt(self, turn: TurnMetrics, metric: metrics.STTMetrics) -> None:
        turn.stt_ms = metric.duration * _S_TO_MS
        print(f"\n🎤 STT: {turn.stt_ms:.0f}ms  audio={metric.audio_duration * _S_TO_MS:.0f}ms")
        self._alert_if_exceeded("STT", turn.stt_ms, self._STT_LIMIT_MS)

    def _apply_eou(self, turn: TurnMetrics, metric: metrics.EOUMetrics) -> None:
        turn.eou_ms = metric.end_of_utterance_delay * _S_TO_MS
        print(f"🎤 EOU: {turn.eou_ms:.0f}ms delay")

    def _apply_llm(self, turn: TurnMetrics, metric: metrics.LLMMetrics) -> None:
        turn.llm_ttft_ms = metric.ttft * _S_TO_MS
        turn.llm_ms = metric.duration * _S_TO_MS
        turn.llm_tokens = metric.total_tokens
        print(
            f"\n🧠 LLM: TTFT={turn.llm_ttft_ms:.0f}ms  total={turn.llm_ms:.0f}ms  "
            f"tokens={metric.total_tokens} ({metric.tokens_per_second:.2f}/s)"
        )
        self._alert_if_exceeded("LLM TTFT", turn.llm_ttft_ms, self._LLM_TTFT_LIMIT_MS)
        self._alert_if_exceeded("LLM Total", turn.llm_ms, self._LLM_LIMIT_MS)
        self._alert_if_exceeded("Total Latency", turn.total_ms, self._TOTAL_LIMIT_MS)
        self.start_turn()

    def _apply_tts(self, turn: TurnMetrics, metric: metrics.TTSMetrics) -> None:
        turn.tts_ttfb_ms = metric.ttfb * _S_TO_MS
        turn.tts_ms = metric.duration * _S_TO_MS
        turn.tts_characters = metric.characters_count
        print(
            f"\n🔊 TTS: TTFB={turn.tts_ttfb_ms:.0f}ms  total={turn.tts_ms:.0f}ms  "
            f"audio={metric.audio_duration * _S_TO_MS:.0f}ms  chars={metric.characters_count}"
        )
        self._alert_if_exceeded("TTS", turn.tts_ms, self._TTS_LIMIT_MS)

    def _apply_vad(self, _turn: TurnMetrics, metric: metrics.VADMetrics) -> None:
        print(f"🎙️  VAD: {metric.inference_count} inferences  idle={metric.idle_time * _S_TO_MS:.0f}ms")

    def _print_usage_summary(self, usage) -> None:
        print("\n📈 USAGE SUMMARY")
        print("─" * 70)
        print(f"LLM Prompt Tokens:       {usage.llm_prompt_tokens:>10}")
        print(f"LLM Completion Tokens:   {usage.llm_completion_tokens:>10}")
        print(f"Total LLM Tokens:        {usage.llm_prompt_tokens + usage.llm_completion_tokens:>10}")
        print(f"STT Audio Duration:      {usage.stt_audio_duration:>10.1f} s")
        print(f"TTS Characters:          {usage.tts_characters_count:>10}")
        print(f"TTS Audio Duration:      {usage.tts_audio_duration:>10.1f} s")
        print("─" * 70)

    def _print_cost_estimate(self, usage) -> None:
        INPUT_COST_PER_TOKEN = 0.10 / 1_000_000
        OUTPUT_COST_PER_TOKEN = 0.40 / 1_000_000
        usd_to_inr = 95.0


        llm_cost = (
                usage.llm_prompt_tokens * INPUT_COST_PER_TOKEN +
                usage.llm_completion_tokens * OUTPUT_COST_PER_TOKEN
        )
        stt_cost = (usage.stt_audio_duration / 60) * 0.0058
        tts_cost = (usage.tts_characters_count / 10000) * 0.33

        total = llm_cost + stt_cost + tts_cost

        print("\n💰 COST ESTIMATE")
        print("─" * 70)
        print(f"LLM:                     ${llm_cost:>10.6f}  (Rs.{llm_cost * usd_to_inr:>10.4f})")
        print(f"STT:                     ${stt_cost:>10.6f}  (Rs.{stt_cost * usd_to_inr:>10.4f})")
        print(f"TTS:                     ${tts_cost:>10.6f}  (Rs.{tts_cost * usd_to_inr:>10.4f})")
        print(f"Total:                   ${total:>10.6f}  (Rs.{total * usd_to_inr:>10.4f})")
        print("─" * 70)

    def _print_alerts(self) -> None:
        if not self._alerts:
            return
        print("\n⚠️  THRESHOLD ALERTS")
        print("─" * 70)
        for alert in self._alerts:
            print(f"  {alert}")
        print("─" * 70)

    def _print_latency_statistics(self) -> None:
        completed_latencies = [t.total_ms for t in self._turns.values() if t.total_ms > 0]
        if not completed_latencies:
            return
        print("\n📊 LATENCY STATISTICS")
        print("─" * 70)
        print(f"Turns Completed:         {len(completed_latencies):>10}")
        print(f"Min:                     {min(completed_latencies):>10.0f} ms")
        print(f"Max:                     {max(completed_latencies):>10.0f} ms")
        print(f"Avg:                     {sum(completed_latencies) / len(completed_latencies):>10.0f} ms")
        print("─" * 70)
