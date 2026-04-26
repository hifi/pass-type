#!/usr/bin/env python3

import argparse
import asyncio
import sys
import os
import fcntl
import json
import re
from enum import IntEnum
from random import random
from jeepney import DBusAddress, new_method_call, MatchRule, Message
from jeepney.io.asyncio import open_dbus_router

class PassType:
    Action = IntEnum('Action', [('Delay', 0), ('KeyDown', 1),('KeyUp', 2),])
    DBusAddress = DBusAddress('/org/freedesktop/portal/desktop', bus_name='org.freedesktop.portal.Desktop', interface='org.freedesktop.portal.RemoteDesktop')
    _keys = {
        "backspace": 0xFF08,
        "tab": 0xFF09,
        "return": 0xFF0D,
        "escape": 0xFF1B,
        "delete": 0xFFFF,
     }

    @staticmethod
    def _make_token(t): return f"{t}_{random()}".replace(".", "_")

    async def _async_call(self, method: str, signature: str=None, body=()) -> Message:
        request_token = PassType._make_token("request")
        request_handle = f"/org/freedesktop/portal/desktop/request/{self.router.unique_name.replace('.', '_')[1:]}/{request_token}"

        for v in body:
            if isinstance(v, dict):
                v["handle_token"] = ('s', request_token)

        mr = MatchRule(interface="org.freedesktop.portal.Request", member='Response', path=request_handle, type='signal')
        with self.router.filter(mr) as queue:
            await self.router.send(new_method_call(PassType.DBusAddress, method, signature, body))
            resp = await queue.get()
            if resp.body[0] != 0:
                raise Exception(f"Async request failed with code {resp.body[0]}")
            return resp.body[1]

    async def run(self, cb):
        async with open_dbus_router() as self.router:
            self.session_handle = (await self._async_call('CreateSession', 'a{sv}', ( {"session_handle_token": ('s', PassType._make_token("session"))},)))["session_handle"][1]
            await self._async_call('SelectDevices', 'oa{sv}', (self.session_handle, {"types": ('u', 1), },))
            await self._async_call('Start', 'osa{sv}', (self.session_handle, '', {}, ))

            async def session_closed(router, session_handle):
                mr = MatchRule(interface="org.freedesktop.portal.Session", member='Closed', path=session_handle, type='signal')
                with router.filter(mr) as queue:
                    await queue.get()
                    print("Session closed")
                    exit(0)

            asyncio.create_task(session_closed(self.router, self.session_handle))

            await cb()

    async def execute(self, seq):
        for a, v in seq:
            match a:
                case PassType.Action.Delay: await asyncio.sleep(v/1000)
                case PassType.Action.KeyDown: await self.router.send_and_get_reply(new_method_call(PassType.DBusAddress, "NotifyKeyboardKeysym", 'oa{sv}iu', ( self.session_handle, {}, v, True,)))
                case PassType.Action.KeyUp: await self.router.send_and_get_reply(new_method_call(PassType.DBusAddress, "NotifyKeyboardKeysym", 'oa{sv}iu', ( self.session_handle, {}, v, False,)))

    @staticmethod
    def sequencer(start_delay, typing_delay, sequence):
        def keypress(seq, i, d):
            if i == None: return
            seq.append((PassType.Action.KeyDown, i))
            seq.append((PassType.Action.Delay, d))
            seq.append((PassType.Action.KeyUp, i))
            seq.append((PassType.Action.Delay, d))

        data = {}
        for i, l in enumerate(sys.stdin):
            l = l.rstrip('\n')
            data[str(i)] = l
            if i == 0:
                data["password"] = l
            else:
                m = l.split(":", 1)
                if len(m) == 2:
                    data[m[0].strip().lower()] = m[1].strip()

        seq = [(PassType.Action.Delay, start_delay)] if start_delay > 0 else []

        for m in re.finditer(r"({([^}]*)})|(\[([^\]]*)\])|([^{ [\]]+)", sequence):
            if m.group(2): # curly braces for input values
                for c in data[m.group(2).lower()]: keypress(seq, ord(c), typing_delay)
            elif m.group(4): # brackets for future expansion?
                pass # not implemented yet
            elif m.group(5): # raw keys
                try: i = PassType._keys[m.group(5).lower()]
                except KeyError: i = ord(m.group(5))
                finally: keypress(seq, i, typing_delay)

        return seq

    class Instance:
        def __init__(self, lock_path):
            self.lock_path = lock_path
        def __enter__(self):
            self.f = open(self.lock_path, 'w')
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def __exit__(self, exc_type, exc, tb):
            self.f.close()
            exc != BlockingIOError and os.unlink(self.lock_path)

async def main():
    parser = argparse.ArgumentParser(prog="pass-type", description="Type passwords or other text with XDG Desktop Portal", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("-s", "--sequence", default="{0}", help="Sequence to type from stdin")
    parser.add_argument("-l", "--listen", action='store_true', default=False, help="Run as a server to listen for commands")
    parser.add_argument("-c", "--connect", action='store_true', default=False, help="Connect to a server to send commands to")
    parser.add_argument("--socket-path", default="$XDG_RUNTIME_DIR/passtype.sock", help="Socket path for listen or connect")
    parser.add_argument("--lock-path", default="$XDG_RUNTIME_DIR/passtype.lock", help="Lock path for listening")
    parser.add_argument("--start-delay", type=int, default=500, help="Delay in milliseconds before sequence starts")
    parser.add_argument("--typing-delay", type=int, default=25, help="Delay in milliseconds between keypresses")
    args = parser.parse_args()

    os.umask(0o077)

    passtype = PassType()
    if args.listen:
        with PassType.Instance(os.path.expandvars(args.lock_path)):
            async def listen():
                async def client(r, w):
                    raw = json.loads((await r.read()).decode('utf-8'))
                    await passtype.execute([(PassType.Action(x[0]), int(x[1])) for x in raw] if isinstance(raw, list) else [])

                async with (await asyncio.start_unix_server(client, path=os.path.expandvars(args.socket_path))) as s:
                    await s.serve_forever()
            await passtype.run(listen)
    else:
        seq = PassType.sequencer(args.start_delay, args.typing_delay, args.sequence)
        if args.connect:
            _, w = await asyncio.open_unix_connection(os.path.expandvars(args.socket_path))
            w.write(json.dumps(seq).encode('utf-8'))
        else:
            await passtype.run(lambda: passtype.execute(seq))

if __name__ == "__main__":
    asyncio.run(main())
