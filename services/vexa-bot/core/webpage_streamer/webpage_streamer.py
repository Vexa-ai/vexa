import asyncio
import logging
import os
import time
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlsplit

import gi
import numpy as np
from aiohttp import web
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
from av import AudioFrame, VideoFrame
from playwright.async_api import async_playwright

gi.require_version("Gst", "1.0")
gi.require_version("GstApp", "1.0")
from gi.repository import Gst, GstApp

Gst.init(None)
os.environ.setdefault("PULSE_LATENCY_MSEC", "20")

logger = logging.getLogger(__name__)


def _call_gst_method(obj, method_name, *args):
    bound = getattr(obj, method_name, None)
    if callable(bound):
        return bound(*args)

    obj_cls = type(obj)
    unbound = getattr(obj_cls, method_name, None)
    if callable(unbound):
        return unbound(obj, *args)

    gst_unbound = getattr(Gst.Buffer, method_name, None)
    if callable(gst_unbound):
        return gst_unbound(obj, *args)

    raise AttributeError(f"Could not call {method_name} on object of type {type(obj)!r}")


def _extract_sample_buffer(sample):
    buffer = getattr(sample, "buffer", None)
    if buffer is not None:
        return buffer

    sample_cls = type(sample)
    get_buffer_unbound = getattr(sample_cls, "get_buffer", None)
    if callable(get_buffer_unbound):
        return get_buffer_unbound(sample)

    gst_get_buffer = getattr(Gst.Sample, "get_buffer", None)
    if callable(gst_get_buffer):
        return gst_get_buffer(sample)

    raise RuntimeError(f"Could not access Gst.Buffer from sample of type {type(sample)!r}")


def _normalize_clock_time(value):
    if value is None:
        return None

    try:
        value = int(value)
    except Exception:
        return None

    clock_time_none = int(getattr(Gst, "CLOCK_TIME_NONE", 2**64 - 1))
    if value < 0 or value == clock_time_none:
        return None

    return value


def _extract_buffer_pts_ns(buffer):
    try:
        pts_value = _normalize_clock_time(getattr(buffer, "pts"))
        if pts_value is not None:
            return pts_value
    except Exception:
        pass

    for method_name in ("get_pts", "get_dts"):
        try:
            pts_value = _normalize_clock_time(_call_gst_method(buffer, method_name))
            if pts_value is not None:
                return pts_value
        except Exception:
            continue

    return time.monotonic_ns()


def _extract_buffer_bytes(buffer):
    try:
        size = int(_call_gst_method(buffer, "get_size"))
        if size > 0:
            duplicated = _call_gst_method(buffer, "extract_dup", 0, size)
            if duplicated is not None:
                return bytes(duplicated)
    except Exception:
        pass

    ok, mapinfo = _call_gst_method(buffer, "map", Gst.MapFlags.READ)
    if not ok:
        raise RuntimeError("Could not map Gst.Buffer for reading")

    try:
        return bytes(mapinfo.data)
    finally:
        _call_gst_method(buffer, "unmap", mapinfo)


class GstVideoStreamTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, sink: GstApp.AppSink, width: int, height: int):
        super().__init__()
        self._sink = sink
        self._width = width
        self._height = height
        self._base_pts_ns = None

    def _pull_sample(self):
        return self._sink.emit("pull-sample")

    def _pull_frame_payload(self):
        sample = self._pull_sample()
        if sample is None:
            return None

        buffer = _extract_sample_buffer(sample)
        pts_ns = _extract_buffer_pts_ns(buffer)
        return pts_ns, _extract_buffer_bytes(buffer)

    async def recv(self) -> VideoFrame:
        loop = asyncio.get_running_loop()
        payload = await loop.run_in_executor(None, self._pull_frame_payload)
        if payload is None:
            raise asyncio.CancelledError("Video pipeline ended")

        pts_ns, raw_data = payload

        try:
            data = memoryview(raw_data)
            width, height = self._width, self._height
            y_size = width * height
            uv_size = y_size // 4

            y_plane = data[0:y_size]
            u_plane = data[y_size : y_size + uv_size]
            v_plane = data[y_size + uv_size : y_size + 2 * uv_size]

            frame = VideoFrame(format="yuv420p", width=width, height=height)
            frame.planes[0].update(y_plane)
            frame.planes[1].update(u_plane)
            frame.planes[2].update(v_plane)
        finally:
            data.release()

        if self._base_pts_ns is None:
            self._base_pts_ns = pts_ns

        rel_ns = pts_ns - self._base_pts_ns
        frame.time_base = Fraction(1, 1_000_000)
        frame.pts = rel_ns // 1_000
        return frame


class GstAudioStreamTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self, sink: GstApp.AppSink, sample_rate: int = 16000, channels: int = 1):
        super().__init__()
        self._sink = sink
        self._sample_rate = sample_rate
        self._channels = channels
        self._base_pts_ns = None

    def _pull_sample(self):
        return self._sink.emit("pull-sample")

    def _pull_frame_payload(self):
        sample = self._pull_sample()
        if sample is None:
            return None

        buffer = _extract_sample_buffer(sample)
        pts_ns = _extract_buffer_pts_ns(buffer)
        return pts_ns, _extract_buffer_bytes(buffer)

    async def recv(self) -> AudioFrame:
        loop = asyncio.get_running_loop()
        payload = await loop.run_in_executor(None, self._pull_frame_payload)
        if payload is None:
            raise asyncio.CancelledError("Audio pipeline ended")

        pts_ns, raw_data = payload

        data = raw_data
        samples = len(data) // (2 * self._channels)
        if samples <= 0:
            raise RuntimeError("Empty audio buffer")
        pcm = np.frombuffer(data, dtype=np.int16).reshape(samples, self._channels)

        layout = "stereo" if self._channels == 2 else "mono"
        frame = AudioFrame(format="s16", layout=layout, samples=samples)
        frame.planes[0].update(pcm.tobytes())
        frame.sample_rate = self._sample_rate

        if self._base_pts_ns is None:
            self._base_pts_ns = pts_ns

        rel_ns = pts_ns - self._base_pts_ns
        frame.time_base = Fraction(1, 1_000_000)
        frame.pts = rel_ns // 1_000
        return frame


class WebpageStreamer:
    def __init__(
        self,
        video_frame_size: tuple[int, int],
        port: int,
        display_name: str,
        pulse_monitor_name: str,
        keepalive_timeout_seconds: int = 900,
    ):
        self.video_frame_size = video_frame_size
        self.port = port
        self.display_name = display_name
        self.pulse_monitor_name = pulse_monitor_name
        self.keepalive_timeout_seconds = keepalive_timeout_seconds

        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.web_app = None
        self.web_runner = None
        self.web_site = None
        self.last_keepalive_time = time.time()
        self.shutdown_event = asyncio.Event()
        self.is_shutting_down = False

        self._pcs: set[RTCPeerConnection] = set()
        self._audio_relay = MediaRelay()
        self._upstream_audio_track = None

        self._gst_pipeline = None
        self._gst_video_sink = None
        self._gst_audio_sink = None
        self._video_track = None
        self._audio_track = None

    async def start_browser(self) -> None:
        if self.browser and self.page:
            return

        os.environ["DISPLAY"] = self.display_name
        browser_env = dict(os.environ)
        browser_env.setdefault("DISPLAY", self.display_name)
        browser_env.setdefault("PULSE_LATENCY_MSEC", "20")

        self.playwright = await async_playwright().start()
        args = [
            f"--window-size={self.video_frame_size[0]},{self.video_frame_size[1]}",
            "--start-fullscreen",
            "--autoplay-policy=no-user-gesture-required",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-infobars",
            "--use-fake-ui-for-media-stream",
            "--allow-running-insecure-content",
            "--ignore-certificate-errors",
            "--disable-features=BlockInsecurePrivateNetworkRequests,BlockInsecurePrivateNetworkRequestsFromPrivate,BlockInsecurePrivateNetworkRequestsFromUnknown,PrivateNetworkAccessSendPreflights,PrivateNetworkAccessRespectPreflightResults,LocalNetworkAccessChecks",
        ]
        if os.getenv("ENABLE_CHROME_SANDBOX_FOR_WEBPAGE_STREAMER", "false").lower() != "true":
            args.extend(["--no-sandbox", "--disable-setuid-sandbox"])

        self.browser = await self.playwright.chromium.launch(
            headless=False,
            args=args,
            env=browser_env,
        )
        self.context = await self.browser.new_context(
            viewport={"width": self.video_frame_size[0], "height": self.video_frame_size[1]},
            ignore_https_errors=True,
        )
        payload_path = Path(__file__).with_name("webpage_streamer_payload.js")
        payload_code = payload_path.read_text(encoding="utf-8").replace(
            "__VEXA_STREAMER_PORT__",
            str(self.port),
        )
        await self.context.add_init_script(payload_code)
        self.page = await self.context.new_page()
        self.page.on(
            "console",
            lambda msg: logger.info("Webpage streamer page console [%s] %s", msg.type, msg.text),
        )
        self.page.on(
            "pageerror",
            lambda exc: logger.error("Webpage streamer page error: %s", exc),
        )
        await self.page.set_viewport_size(
            {"width": self.video_frame_size[0], "height": self.video_frame_size[1]}
        )
        await self.enter_fullscreen()
        logger.info("Webpage streamer browser started on display %s", self.display_name)

    async def enter_fullscreen(self) -> None:
        if not self.context or not self.page:
            return

        try:
            session = await self.context.new_cdp_session(self.page)
            window = await session.send("Browser.getWindowForTarget")
            window_id = window.get("windowId")
            if window_id is None:
                return
            await session.send(
                "Browser.setWindowBounds",
                {"windowId": window_id, "bounds": {"windowState": "fullscreen"}},
            )
            logger.info("Webpage streamer browser entered fullscreen via CDP")
        except Exception as exc:
            logger.warning("Webpage streamer fullscreen failed: %s", exc)

    async def grant_microphone_permission(self, url: str) -> None:
        if not self.context:
            return
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                return
            origin = f"{parsed.scheme}://{parsed.netloc}"
            await self.context.grant_permissions(["microphone"], origin=origin)
        except Exception:
            return

    async def load_webapp(self, url: str) -> None:
        await self.start_browser()
        await self.grant_microphone_permission(url)
        logger.info("Loading webpage streamer URL: %s", url)
        await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await self.grant_microphone_permission(self.page.url)
        await self.enter_fullscreen()

    def _start_gstreamer_capture(self) -> None:
        if self._gst_pipeline is not None:
            return

        width, height = self.video_frame_size
        pipeline_desc = f"""
            ximagesrc display-name={self.display_name} use-damage=0 show-pointer=false
                ! video/x-raw,framerate=15/1,width={width},height={height}
                ! videoconvert
                ! video/x-raw,format=I420,width={width},height={height}
                ! queue max-size-buffers=2 max-size-bytes=0 max-size-time=0 leaky=downstream
                ! appsink name=video_sink emit-signals=false max-buffers=2 drop=true sync=false

            pulsesrc device={self.pulse_monitor_name} latency-time=10000 buffer-time=20000 do-timestamp=true
                ! audio/x-raw,format=S16LE,channels=1,rate=16000
                ! audioconvert
                ! audioresample
                ! queue max-size-buffers=32 max-size-bytes=0 max-size-time=200000000 leaky=downstream
                ! appsink name=audio_sink emit-signals=false max-buffers=32 drop=true sync=false
        """

        logger.info("Starting GStreamer capture on display=%s pulse=%s", self.display_name, self.pulse_monitor_name)
        self._gst_pipeline = Gst.parse_launch(pipeline_desc)
        self._gst_video_sink = self._gst_pipeline.get_by_name("video_sink")
        self._gst_audio_sink = self._gst_pipeline.get_by_name("audio_sink")

        if not self._gst_video_sink or not self._gst_audio_sink:
            raise RuntimeError("Failed to initialize webpage-streamer GStreamer sinks")

        result = self._gst_pipeline.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            self._gst_pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("Failed to start webpage-streamer capture pipeline")

        self._video_track = GstVideoStreamTrack(self._gst_video_sink, width=width, height=height)
        self._audio_track = GstAudioStreamTrack(self._gst_audio_sink, sample_rate=16000, channels=1)
        logger.info("Webpage-streamer capture pipeline is PLAYING")

    def _stop_gstreamer_capture(self) -> None:
        if self._gst_pipeline is None:
            return
        logger.info("Stopping webpage-streamer capture pipeline")
        self._gst_pipeline.set_state(Gst.State.NULL)
        self._gst_pipeline = None
        self._gst_video_sink = None
        self._gst_audio_sink = None
        self._video_track = None
        self._audio_track = None

    async def keepalive_monitor(self) -> None:
        while not self.shutdown_event.is_set():
            await asyncio.sleep(60)
            if self.shutdown_event.is_set():
                return
            if time.time() - self.last_keepalive_time > self.keepalive_timeout_seconds:
                logger.warning(
                    "No keepalive received for %.1fs, shutting down webpage streamer",
                    time.time() - self.last_keepalive_time,
                )
                await self.shutdown_process()
                return

    async def shutdown_process(self) -> None:
        if self.is_shutting_down:
            return
        self.is_shutting_down = True
        logger.info("Shutting down webpage streamer process")

        self.shutdown_event.set()

        for pc in list(self._pcs):
            try:
                await pc.close()
            except Exception:
                pass
        self._pcs.clear()

        self._stop_gstreamer_capture()

        if self.page:
            try:
                await self.page.close()
            except Exception:
                pass
            self.page = None

        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
            self.context = None

        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
            self.browser = None

        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
            self.playwright = None

        if self.web_runner:
            try:
                await self.web_runner.cleanup()
            except Exception:
                pass
            self.web_runner = None

    async def offer_meeting_audio(self, request: web.Request) -> web.Response:
        params = await request.json()
        offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

        if self._upstream_audio_track is None:
            return web.Response(status=409, text="No upstream meeting audio has been published yet.")

        pc = RTCPeerConnection()
        self._pcs.add(pc)
        pc.addTrack(self._audio_relay.subscribe(self._upstream_audio_track))

        @pc.on("connectionstatechange")
        async def on_state_change() -> None:
            if pc.connectionState in ("failed", "closed", "disconnected"):
                await pc.close()
                self._pcs.discard(pc)

        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})

    async def offer(self, request: web.Request) -> web.Response:
        params = await request.json()
        offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

        if self._gst_pipeline is None:
            self._start_gstreamer_capture()

        pc = RTCPeerConnection()
        self._pcs.add(pc)

        if self._video_track is not None:
            pc.addTrack(self._video_track)
        if self._audio_track is not None:
            pc.addTrack(self._audio_track)

        @pc.on("track")
        def on_track(track) -> None:
            if track.kind == "audio":
                self._upstream_audio_track = track
                logger.info("Stored upstream meeting-audio track for webpage microphone rebroadcast")

        @pc.on("connectionstatechange")
        async def on_state_change() -> None:
            if pc.connectionState in ("failed", "closed", "disconnected"):
                await pc.close()
                self._pcs.discard(pc)

        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})

    async def start_streaming(self, request: web.Request) -> web.Response:
        data = await request.json()
        url = (data.get("url") or "").strip()
        if not url:
            return web.json_response({"error": "url is required"}, status=400)

        await self.load_webapp(url)
        return web.json_response({"status": "success", "url": url})

    async def keepalive(self, _request: web.Request) -> web.Response:
        self.last_keepalive_time = time.time()
        return web.json_response({"status": "alive", "timestamp": self.last_keepalive_time})

    async def shutdown(self, _request: web.Request) -> web.Response:
        asyncio.create_task(self.shutdown_process())
        return web.json_response({"status": "shutting_down"})

    async def cors_preflight(self, _request: web.Request) -> web.Response:
        return web.Response(
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
                "Access-Control-Allow-Private-Network": "true",
                "Access-Control-Max-Age": "86400",
            }
        )

    @web.middleware
    async def cors_middleware(self, request: web.Request, handler):
        response = await handler(request)
        response.headers.update(
            {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
                "Access-Control-Allow-Private-Network": "true",
            }
        )
        return response

    async def run(self) -> None:
        await self.start_browser()

        app = web.Application(middlewares=[self.cors_middleware])
        self.web_app = app
        app.router.add_post("/offer", self.offer)
        app.router.add_options("/offer", self.cors_preflight)
        app.router.add_post("/offer_meeting_audio", self.offer_meeting_audio)
        app.router.add_options("/offer_meeting_audio", self.cors_preflight)
        app.router.add_post("/start_streaming", self.start_streaming)
        app.router.add_options("/start_streaming", self.cors_preflight)
        app.router.add_post("/keepalive", self.keepalive)
        app.router.add_options("/keepalive", self.cors_preflight)
        app.router.add_post("/shutdown", self.shutdown)
        app.router.add_options("/shutdown", self.cors_preflight)

        self.web_runner = web.AppRunner(app)
        await self.web_runner.setup()
        self.web_site = web.TCPSite(self.web_runner, "0.0.0.0", self.port)
        await self.web_site.start()
        logger.info("Webpage streamer listening on http://0.0.0.0:%s", self.port)

        asyncio.create_task(self.keepalive_monitor())
        await self.shutdown_event.wait()

