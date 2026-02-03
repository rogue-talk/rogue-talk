"""WebRTC audio track implementations for aiortc."""

from __future__ import annotations

import asyncio
import fractions
import logging
import queue
import time
from typing import Any

import numpy as np
import numpy.typing as npt
from aiortc import MediaStreamTrack

from ..common.constants import CHANNELS, FRAME_SIZE, SAMPLE_RATE
from .pcm import float32_to_int16, to_float32

# Debug logging to file (doesn't interfere with terminal UI)
logger = logging.getLogger(__name__)
_debug_handler = logging.FileHandler("/tmp/rogue_talk_audio.log")
_debug_handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
logger.addHandler(_debug_handler)
logger.setLevel(logging.DEBUG)

# Import av for AudioFrame creation
try:
    import av
except ImportError:
    av = None  # type: ignore[assignment]


class AudioCaptureTrack(MediaStreamTrack):
    """Custom audio track that captures from microphone."""

    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        self._queue: asyncio.Queue[npt.NDArray[np.float32]] = asyncio.Queue(maxsize=10)
        self._start_time: float | None = None
        self._timestamp = 0
        self._sample_rate = SAMPLE_RATE
        self._frame_size = FRAME_SIZE
        # Track audio level for UI
        self.last_level: float = 0.0
        # Drop counter for debugging
        self._drop_count = 0

    def feed_audio(self, pcm_data: npt.NDArray[np.float32]) -> None:
        """Feed audio data from the capture callback (thread-safe)."""
        # Update audio level
        self.last_level = float(np.abs(pcm_data).max())
        try:
            self._queue.put_nowait(pcm_data.copy())
        except asyncio.QueueFull:
            self._drop_count += 1
            if self._drop_count % 50 == 1:
                logger.debug(f"AudioCaptureTrack: dropped {self._drop_count} frames")

    async def recv(self) -> Any:
        """Called by aiortc to get the next audio frame."""
        try:
            if av is None:
                raise RuntimeError("PyAV not available")

            if self._start_time is None:
                self._start_time = time.time()

            try:
                # Wait for audio data with timeout
                pcm_data = await asyncio.wait_for(self._queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                # Generate silence if no data
                pcm_data = np.zeros(self._frame_size, dtype=np.float32)
            except asyncio.CancelledError:
                raise

            # Convert to int16 for av.AudioFrame (mono packed format)
            pcm_int16 = float32_to_int16(pcm_data)

            # Create AudioFrame with manual plane update (most reliable method)
            frame = av.AudioFrame(format="s16", layout="mono", samples=len(pcm_int16))
            frame.sample_rate = self._sample_rate
            frame.pts = self._timestamp
            frame.time_base = fractions.Fraction(1, self._sample_rate)
            frame.planes[0].update(pcm_int16.tobytes())

            self._timestamp += len(pcm_int16)
            return frame
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"AudioCaptureTrack.recv() error: {type(e).__name__}: {e}")
            raise


class AudioPlaybackTrack:
    """Receives audio from a WebRTC track and provides it for playback."""

    def __init__(self, source_player_name: str = "") -> None:
        self.source_player_name = source_player_name
        # Use thread-safe queue since playback runs in separate thread
        # Larger buffer (50 frames = 1s) to handle bursts from WebRTC
        self._queue: queue.Queue[npt.NDArray[np.float32]] = queue.Queue(maxsize=50)
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._track: MediaStreamTrack | None = None
        self._volume: float = 1.0
        self._drop_count = 0
        self._frame_count = 0

    def set_track(self, track: MediaStreamTrack) -> None:
        """Set the WebRTC track to receive audio from."""
        self._track = track

    def set_volume(self, volume: float) -> None:
        """Set the volume scaling for this track."""
        self._volume = volume

    async def start(self) -> None:
        """Start receiving audio from the track."""
        if self._track is None:
            return
        self._running = True
        self._task = asyncio.create_task(self._receive_loop())

    async def stop(self) -> None:
        """Stop receiving audio."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _receive_loop(self) -> None:
        """Continuously receive frames from the WebRTC track."""
        if self._track is None:
            return

        frame_count = 0
        while self._running:
            try:
                frame = await self._track.recv()
                if hasattr(frame, "to_ndarray"):
                    frame_count += 1
                    # Log frame properties for debugging
                    if frame_count == 1:
                        sample_rate = getattr(frame, "sample_rate", "?")
                        samples = getattr(frame, "samples", "?")
                        fmt = getattr(frame, "format", None)
                        fmt_name = fmt.name if fmt else "?"
                        layout = getattr(frame, "layout", None)
                        layout_name = layout.name if layout else "?"
                        logger.debug(
                            f"AudioPlaybackTrack first frame: "
                            f"sample_rate={sample_rate}, "
                            f"samples={samples}, "
                            f"format={fmt_name}, "
                            f"layout={layout_name}"
                        )

                    pcm_data = frame.to_ndarray()

                    if frame_count == 1:
                        logger.debug(
                            f"AudioPlaybackTrack ndarray: "
                            f"shape={pcm_data.shape}, dtype={pcm_data.dtype}"
                        )

                    # Handle multi-channel audio - extract mono
                    if pcm_data.ndim == 2:
                        if pcm_data.shape[0] == 1:
                            # Packed interleaved stereo: shape (1, samples*channels)
                            # Deinterleave by taking every other sample (left channel)
                            pcm_data = pcm_data[0, ::2]
                        else:
                            # Planar format: shape (channels, samples)
                            pcm_data = pcm_data[0]  # Take first channel
                    else:
                        pcm_data = pcm_data.flatten()

                    # Convert to float32 and normalize
                    pcm_float = to_float32(pcm_data)

                    self._frame_count += 1
                    try:
                        self._queue.put_nowait(pcm_float * self._volume)
                    except queue.Full:
                        self._drop_count += 1
                    # Log stats periodically
                    if self._frame_count % 500 == 0:
                        logger.debug(
                            f"AudioPlaybackTrack {self.source_player_name}: "
                            f"frames={self._frame_count}, drops={self._drop_count}"
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"AudioPlaybackTrack receive error: {e}")
                break

    def get_frame(self) -> npt.NDArray[np.float32] | None:
        """Get the next audio frame if available (thread-safe)."""
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None


class ServerAudioRelay:
    """Server-side audio relay that receives from one client and can send to others."""

    def __init__(self, player_id: int) -> None:
        self.player_id = player_id
        self._incoming_queue: asyncio.Queue[npt.NDArray[np.float32]] = asyncio.Queue(
            maxsize=10
        )
        self._track: MediaStreamTrack | None = None
        self._running = False
        self._receive_task: asyncio.Task[None] | None = None
        self._drop_count = 0
        self._frame_count = 0

    def set_track(self, track: MediaStreamTrack) -> None:
        """Set the incoming audio track from this player."""
        self._track = track

    async def start_receiving(self) -> None:
        """Start receiving audio frames from the player's track."""
        if self._track is None:
            return
        self._running = True
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def stop_receiving(self) -> None:
        """Stop receiving audio."""
        self._running = False
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

    async def _receive_loop(self) -> None:
        """Receive frames from the WebRTC track."""
        if self._track is None:
            return

        while self._running:
            try:
                frame = await self._track.recv()
                if hasattr(frame, "to_ndarray"):
                    pcm_data = frame.to_ndarray()

                    # Handle multi-channel audio - extract mono
                    if pcm_data.ndim == 2:
                        if pcm_data.shape[0] == 1:
                            # Packed interleaved stereo: shape (1, samples*channels)
                            # Deinterleave by taking every other sample (left channel)
                            pcm_data = pcm_data[0, ::2]
                        else:
                            # Planar format: shape (channels, samples)
                            pcm_data = pcm_data[0]  # Take first channel
                    else:
                        pcm_data = pcm_data.flatten()

                    # Convert to float32 and normalize
                    pcm_float = to_float32(pcm_data)

                    self._frame_count += 1
                    try:
                        self._incoming_queue.put_nowait(pcm_float)
                    except asyncio.QueueFull:
                        self._drop_count += 1
                    if self._frame_count % 500 == 0:
                        logger.debug(
                            f"ServerAudioRelay player {self.player_id}: "
                            f"frames={self._frame_count}, drops={self._drop_count}"
                        )
            except Exception:
                break

    def get_audio_frame(self) -> npt.NDArray[np.float32] | None:
        """Get the latest audio frame from this player."""
        try:
            return self._incoming_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None


class ServerOutboundTrack(MediaStreamTrack):
    """Server-side track that sends audio from one source player to a client."""

    kind = "audio"

    def __init__(self, source_player_id: int) -> None:
        super().__init__()
        self.source_player_id = source_player_id
        self._queue: asyncio.Queue[npt.NDArray[np.float32]] = asyncio.Queue(maxsize=10)
        self._timestamp = 0
        self._sample_rate = SAMPLE_RATE
        self._active = False  # Only queue audio when added to peer connection
        self._drop_count = 0
        self._frame_count = 0

    def activate(self) -> None:
        """Mark track as active (added to peer connection). Audio will now be queued."""
        self._active = True

    def send_audio(self, pcm_data: npt.NDArray[np.float32]) -> None:
        """Queue audio data to send to the client.

        Note: Caller is expected to pass a buffer that won't be modified after
        this call. In practice, this is always true because the audio routing
        loop creates a new scaled_frame for each recipient.
        """
        if not self._active:
            return  # Don't queue until track is in peer connection
        self._frame_count += 1
        try:
            self._queue.put_nowait(pcm_data)
        except asyncio.QueueFull:
            self._drop_count += 1
        if self._frame_count % 500 == 0:
            logger.debug(
                f"ServerOutboundTrack player {self.source_player_id}: "
                f"frames={self._frame_count}, drops={self._drop_count}"
            )

    async def recv(self) -> Any:
        """Called by aiortc to get the next audio frame to send."""
        try:
            if av is None:
                raise RuntimeError("PyAV not available")

            try:
                pcm_data = await asyncio.wait_for(self._queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                pcm_data = np.zeros(FRAME_SIZE, dtype=np.float32)
            except asyncio.CancelledError:
                raise

            # Convert to int16 for av.AudioFrame (mono packed format)
            pcm_int16 = float32_to_int16(pcm_data)

            # Create AudioFrame with manual plane update
            frame = av.AudioFrame(format="s16", layout="mono", samples=len(pcm_int16))
            frame.sample_rate = self._sample_rate
            frame.pts = self._timestamp
            frame.time_base = fractions.Fraction(1, self._sample_rate)
            frame.planes[0].update(pcm_int16.tobytes())

            self._timestamp += len(pcm_int16)
            return frame
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"ServerOutboundTrack.recv() error: {type(e).__name__}: {e}")
            raise
