"""Minimal Guacamole protocol client for guacd + WebSocket tunnel helpers.

Protocol reference: https://guacamole.apache.org/doc/gug/guacamole-protocol.html
Instructions look like  "4.size,4.1024,3.768;"  where each element is
prefixed by its length in Unicode characters.
"""

from __future__ import annotations

import asyncio
import codecs
import logging

log = logging.getLogger(__name__)

INTERNAL_OPCODE = ""  # tunnel-internal instructions (ping, uuid) have an empty opcode


class GuacError(Exception):
    pass


def encode(*elements: str) -> str:
    return ",".join(f"{len(e)}.{e}" for e in map(str, elements)) + ";"


class InstructionParser:
    """Incremental parser: feed text, get complete instructions back."""

    def __init__(self) -> None:
        self.buffer = ""

    def feed(self, text: str) -> list[tuple[str, list[str], str]]:
        """Return a list of (opcode, args, raw_instruction)."""
        self.buffer += text
        out: list[tuple[str, list[str], str]] = []
        pos = 0
        buf = self.buffer
        while True:
            elements: list[str] = []
            i = pos
            complete = False
            while True:
                dot = buf.find(".", i)
                if dot == -1:
                    break
                try:
                    length = int(buf[i:dot])
                except ValueError as exc:
                    raise GuacError(f"Malformed instruction near {buf[i:i + 20]!r}") from exc
                start = dot + 1
                end = start + length
                if end >= len(buf):
                    break  # need more data (value + terminator)
                elements.append(buf[start:end])
                term = buf[end]
                i = end + 1
                if term == ";":
                    complete = True
                    break
                if term != ",":
                    raise GuacError(f"Malformed instruction terminator {term!r}")
            if not complete:
                break
            out.append((elements[0], elements[1:], buf[pos:i]))
            pos = i
        self.buffer = buf[pos:]
        return out

    def complete_prefix(self, text: str) -> str:
        """Feed text and return the concatenation of complete raw instructions."""
        return "".join(raw for _, _, raw in self.feed(text))


class GuacdConnection:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.parser = InstructionParser()
        self.pending: list[tuple[str, list[str], str]] = []

    @classmethod
    async def connect(cls, host: str, port: int, timeout: float = 10.0) -> GuacdConnection:
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        except (OSError, asyncio.TimeoutError) as exc:
            raise GuacError(f"Cannot connect to guacd at {host}:{port}: {exc}") from exc
        return cls(reader, writer)

    async def send(self, data: str) -> None:
        self.writer.write(data.encode("utf-8"))
        await self.writer.drain()

    async def read_text(self, size: int = 65536) -> str:
        chunk = await self.reader.read(size)
        if not chunk:
            return ""
        return self.decoder.decode(chunk)

    async def expect(self, opcode: str, timeout: float = 15.0) -> list[str]:
        async def _wait() -> list[str]:
            while True:
                while self.pending:
                    op, args, _ = self.pending.pop(0)
                    if op == opcode:
                        return args
                    if op == "error":
                        raise GuacError(f"guacd error: {args[0] if args else ''} ({args[1] if len(args) > 1 else ''})")
                text = await self.read_text()
                if text == "":
                    raise GuacError("guacd closed the connection during handshake")
                self.pending.extend(self.parser.feed(text))

        return await asyncio.wait_for(_wait(), timeout)

    async def handshake(self, protocol: str, params: dict[str, str], width: int, height: int, dpi: int,
                        timezone: str | None = None) -> str:
        """Perform the select/args/connect handshake; returns the connection id."""
        await self.send(encode("select", protocol))
        args = await self.expect("args")
        await self.send(encode("size", str(width), str(height), str(dpi)))
        await self.send(encode("audio"))
        await self.send(encode("video"))
        await self.send(encode("image", "image/png", "image/jpeg", "image/webp"))
        if timezone:
            await self.send(encode("timezone", timezone))
        values = []
        for name in args:
            if name.startswith("VERSION_"):
                values.append(name)  # speak the protocol version guacd offered
            else:
                values.append(params.get(name, ""))
        await self.send(encode("connect", *values))
        ready = await self.expect("ready", timeout=30.0)
        conn_id = ready[0] if ready else ""
        # Anything after "ready" that was already buffered must reach the client.
        return conn_id

    def drain_pending_raw(self) -> str:
        raw = "".join(r for _, _, r in self.pending)
        self.pending.clear()
        return raw

    async def close(self) -> None:
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
