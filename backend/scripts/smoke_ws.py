"""Smoke test: open WS, POST canonical sentence, collect broadcast events, print, exit."""

import asyncio
import json
import sys
import urllib.request

import websockets


CANONICAL = (
    "Hey, the aircon in our room is not really cooling, and can we get two more San Migs "
    "and some extra rice? Oh, what time is breakfast?"
)


async def main() -> int:
    received: list[dict] = []
    async with websockets.connect("ws://127.0.0.1:8000/ws/dashboard") as ws:
        snapshot_raw = await ws.recv()
        snapshot = json.loads(snapshot_raw)
        print(f"snapshot: {len(snapshot.get('tickets', []))} existing tickets")

        req = urllib.request.Request(
            "http://127.0.0.1:8000/turn",
            data=json.dumps({"room": "4", "text": CANONICAL}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
        print(f"http reply: {body['reply']!r}")
        print(f"http tickets: {len(body['tickets'])}")

        try:
            while len(received) < 3:
                frame = await asyncio.wait_for(ws.recv(), timeout=2.0)
                received.append(json.loads(frame))
        except asyncio.TimeoutError:
            pass

    print(f"ws events received: {len(received)}")
    for ev in received:
        t = ev.get("ticket", {})
        print(f"  {ev['type']:18}  dept={t.get('dept'):12}  summary={t.get('summary'):24}  tags={t.get('tags')}")

    ok = (
        len(body["tickets"]) == 3
        and len(received) == 3
        and any(t["dept"] == "maintenance" for t in body["tickets"])
        and any(t["dept"] == "bar" for t in body["tickets"])
        and any(
            t["dept"] == "kitchen" and "allergy:shellfish" in t["tags"]
            for t in body["tickets"]
        )
        and "breakfast" in body["reply"].lower()
    )
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
