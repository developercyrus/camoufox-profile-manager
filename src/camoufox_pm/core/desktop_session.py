"""Per-profile Xvfb, x11vnc and noVNC desktop sessions."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DesktopSession:
    profile_id: str
    display: str
    vnc_port: int
    novnc_port: int
    width: int
    height: int
    novnc_url: str
    xvfb: subprocess.Popen
    x11vnc: subprocess.Popen
    novnc: subprocess.Popen

    def info(self) -> dict[str, object]:
        return {
            "display": self.display,
            "vnc_port": self.vnc_port,
            "novnc_port": self.novnc_port,
            "novnc_url": self.novnc_url,
            "width": self.width,
            "height": self.height,
        }


class DesktopSessionManager:
    """Allocate one X display + VNC + noVNC stack per running profile."""

    def __init__(
        self,
        display_base: int | None = None,
        vnc_port_base: int | None = None,
        novnc_port_base: int | None = None,
        max_sessions: int | None = None,
        public_base_url: str | None = None,
    ) -> None:
        self.display_base = display_base or int(os.getenv("CPM_DISPLAY_BASE", "100"))
        self.vnc_port_base = vnc_port_base or int(os.getenv("CPM_VNC_PORT_BASE", "5900"))
        self.novnc_port_base = novnc_port_base or int(os.getenv("CPM_NOVNC_PORT_BASE", "6080"))
        self.max_sessions = max_sessions or int(os.getenv("CPM_DESKTOP_MAX", "100"))

        self.public_base_url = (
            public_base_url
            or os.getenv("CPM_NOVNC_PUBLIC_BASE_URL", "http://127.0.0.1")
        ).rstrip("/")

        self._sessions: dict[str, DesktopSession] = {}
        self._lock = asyncio.Lock()

    def get(self, profile_id: str) -> DesktopSession | None:
        return self._sessions.get(profile_id)

    async def acquire(
        self,
        profile_id: str,
        width: int = 1920,
        height: int = 1080,
    ) -> DesktopSession:
        async with self._lock:
            existing = self._sessions.get(profile_id)
            if existing:
                return existing

            for slot in range(self.max_sessions):
                display_number = self.display_base + slot
                vnc_port = self.vnc_port_base + slot
                novnc_port = self.novnc_port_base + slot

                if any(
                    session.display == f":{display_number}"
                    or session.vnc_port == vnc_port
                    or session.novnc_port == novnc_port
                    for session in self._sessions.values()
                ):
                    continue

                display = f":{display_number}"

                session = await self._start(
                    profile_id=profile_id,
                    display=display,
                    display_number=display_number,
                    vnc_port=vnc_port,
                    novnc_port=novnc_port,
                    width=width,
                    height=height,
                )
                self._sessions[profile_id] = session
                return session

            raise RuntimeError(
                f"No desktop slot available. "
                f"Maximum simultaneous headed profiles: {self.max_sessions}"
            )

    async def _start(
        self,
        profile_id: str,
        display: str,
        display_number: int,
        vnc_port: int,
        novnc_port: int,
        width: int,
        height: int,
    ) -> DesktopSession:
        xvfb: subprocess.Popen | None = None
        x11vnc: subprocess.Popen | None = None
        novnc: subprocess.Popen | None = None

        try:
            xvfb = subprocess.Popen(
                [
                    "Xvfb",
                    display,
                    "-screen",
                    "0",
                    f"{width}x{height}x24",
                    "-nolisten",
                    "tcp",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            await self._wait_for_display(display_number, xvfb)

            x11vnc = subprocess.Popen(
                [
                    "x11vnc",
                    "-display",
                    display,
                    "-rfbport",
                    str(vnc_port),
                    "-localhost",
                    "-forever",
                    "-shared",
                    "-nopw",
                    "-noxdamage",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            await self._wait_for_port(vnc_port, x11vnc)

            novnc = subprocess.Popen(
                [
                    "/usr/share/novnc/utils/novnc_proxy",
                    "--listen",
                    str(novnc_port),
                    "--vnc",
                    f"localhost:{vnc_port}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            await self._wait_for_port(novnc_port, novnc)

            novnc_url = (
                f"{self.public_base_url}:{novnc_port}"
                f"/vnc.html?autoconnect=true&resize=scale"
            )

            return DesktopSession(
                profile_id=profile_id,
                display=display,
                vnc_port=vnc_port,
                novnc_port=novnc_port,
                width=width,
                height=height,
                novnc_url=novnc_url,
                xvfb=xvfb,
                x11vnc=x11vnc,
                novnc=novnc,
            )

        except Exception:
            self._stop_process(novnc)
            self._stop_process(x11vnc)
            self._stop_process(xvfb)
            raise

    async def _wait_for_display(
        self,
        display_number: int,
        process: subprocess.Popen,
    ) -> None:
        socket_path = Path(f"/tmp/.X11-unix/X{display_number}")

        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("Xvfb exited before the display became ready")
            if socket_path.exists():
                return
            await asyncio.sleep(0.05)

        raise RuntimeError("Timed out waiting for Xvfb display")

    async def _wait_for_port(
        self,
        port: int,
        process: subprocess.Popen,
    ) -> None:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError(f"Process for port {port} exited unexpectedly")

            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    return
            except OSError:
                await asyncio.sleep(0.05)

        raise RuntimeError(f"Timed out waiting for port {port}")

    async def release(self, profile_id: str) -> None:
        async with self._lock:
            session = self._sessions.pop(profile_id, None)

        if session is None:
            return

        self._stop_process(session.novnc)
        self._stop_process(session.x11vnc)
        self._stop_process(session.xvfb)

    async def release_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()

        for session in sessions:
            self._stop_process(session.novnc)
            self._stop_process(session.x11vnc)
            self._stop_process(session.xvfb)

    @staticmethod
    def _stop_process(process: subprocess.Popen | None) -> None:
        if process is None or process.poll() is not None:
            return

        try:
            process.terminate()
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
