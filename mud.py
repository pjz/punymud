#!/usr/bin/env python

# played around with porting
# http://sourcery.dyndns.org/svn/teensymud/release/tmud-2.0.0/tmud.rb

# YAML implementation:
# http://pyyaml.org/wiki/PyYAMLDocumentation

import re
import sys
import signal
import random
import traceback
from collections import namedtuple
from socketserver import ThreadingTCPServer, StreamRequestHandler

import yaml

rand = random.Random()
world = False

AUTHOR = "Jose Nazario"
VERSION = "1.1.1"

BANNER = f"""

                 This is PunyMUD version {VERSION}

             Copyright (C) 2007 by Jose Nazario
             Released under an Artistic License

 Based on TeensyMUD Ruby code Copyright (C) 2005 by Jon A. Lambert
 Original released under the terms of the TeensyMUD Public License


Login> """

HELP = """
===========================================================================
Play commands
  i[nventory] = displays player inventory
  l[ook] = displays the contents of a room
  dr[op] = drops all objects in your inventory into the room
  ex[amine] <object> = examine the named object
  g[get] = gets all objects in the room into your inventory
  k[ill] <name> = attempts to kill player (e.g. k bubba)
  s[ay] <message> = sends <message> to all players in the room
  c[hat] <message> = sends <message> to all players in the game
  h[elp]|?  = displays help
  q[uit]    = quits the game (saves player)
  <exit name> = moves player through exit named (ex. south)
===========================================================================
OLC
  O <object name> = creates a new object (ex. O rose)
  D <object number> = add description for an object
  R <room name> <exit name to> <exit name back> = creates a new room and 
    autolinks the exits using the exit names provided.
    (ex. R Kitchen north south)
===========================================================================
"""

sys.modules["mud"] = sys.modules[__name__]


class Obj(object):
    def __init__(self, name, location):
        self.name = name
        self.location = location
        self.oid = -1
        self.description = None

    def __repr__(self):
        return f"Object: {self.name} (id {self.oid})"


class Room(Obj):
    def __init__(self, name):
        self.exits = {}
        self.name = name

    def __repr__(self):
        exits = "|".join(self.exits.keys())
        return f"Room: {self.name} (id {self.oid}) - exits {exits}"


class Player(Obj):
    def __init__(self, name, io=None):
        if io:
            self.io = io
        self.name = name
        self.location = 1

    def __repr__(self):
        return f"Player: {self.name} (id {self.oid}) - at {self.location}"

    def sendto(self, s, end='\n'):
        if getattr(self, "io", False):
            self.io.wfile.write(f"{s}{end}".encode("utf8"))

    def parse(self, m):
        m = m.strip()
        pat = re.compile(r"(\w+)\W(.*)")
        try:
            args = pat.findall(m)[0]
            cmd = args[0]
            arg = args[1]
        except IndexError:
            cmd = m
            arg = False
        exits = [x.lower() for x in world.find_by_oid(self.location).exits.keys()]
        if cmd.lower() in exits:
            self.location = world.find_by_oid(self.location).exits[cmd].oid
            self.parse("look")
        elif cmd.startswith("q"):
            self.sendto("Bye bye!")
            if hasattr(self, 'io'):
                del self.io
            world.save()
        elif cmd.lower().startswith("h") or cmd.startswith("?"):
            self.sendto(HELP)
        elif cmd.startswith("i"):
            for o in world.objects_at_location(self.oid):
                self.sendto(o.name)
        elif cmd.startswith("k"):
            if not arg:
                self.parse("help")
            d = world.find_player_by_name(arg)
            if d and rand.random() < 0.3:
                world.global_message(f"{self.name} kills {d.name}")
                d.io = None
                world.delete(d)
                world.save()
            else:
                world.global_message(f"{self.name} misses")
        elif cmd.startswith("s"):
            if arg:
                self.sendto(f' You say "{arg}"')
            else:
                self.sendto(" Did you mean to say something?")
            for x in world.other_players_at_location(self.location, self.oid):
                x.sendto(' {self.name} says "{arg}"')
        elif cmd.startswith("c"):
            if arg:
                self.sendto(f' You chat, "{arg}"')
            else:
                self.sendto(" Did you mean to say something?")
            world.global_message_others(f'{self.name} chats, "{arg}"', self.oid)
        elif cmd.startswith("g"):
            for q in world.objects_at_location(self.location):
                q.location = self.oid
            self.sendto("Ok")
        elif cmd.startswith("dr"):
            for q in world.objects_at_location(self.oid):
                q.location = self.location
            self.sendto("Ok")
        elif cmd.startswith("ex"):
            if not isinstance(arg, str):
                self.parse("help")
            try:
                arg = arg.strip()
            except AttributeError:
                self.parse("help")
            found = False
            objs_at = world.objects_at_location
            for o in objs_at(self.oid) + objs_at(self.location):
                if o.name == arg:
                    if getattr(o, "description", False):
                        self.sendto(o.description)
                    else:
                        self.sendto(f"It's just a {o.name}")
                    found = True
            if not found:
                if arg:
                    self.sendto(f"No object {arg} found")
                else:
                    self.parse("help")
        elif cmd == "O":
            if not arg:
                self.parse("help")
            try:
                o = Obj(arg.strip(), self.location)
                world.add(o)
                self.sendto(f"Created object {o.oid}")
                world.save()
            except AttributeError:
                self.parse("help")
        elif cmd == "D":
            if not isinstance(arg, str):
                self.parse("help")
            oid = False
            try:
                oid, desc = arg.split(" ", 1)
            except AttributeError:
                self.parse("help")
            except ValueError:
                self.parse("help")
            try:
                oid = int(oid)
            except ValueError:
                self.parse("help")
            o = world.find_by_oid(oid)
            if o:
                o.description = desc
                world.save()
                self.sendto("Ok")
            elif oid:
                self.sendto(f"Object {oid} not found")
        elif cmd == "R":
            if not arg:
                self.parse("help")
            tmp = arg.split()
            if len(tmp) < 3:
                self.sendto(HELP)
            else:
                name = tmp[0]
                exit_name_to = tmp[1]
                exit_name_back = tmp[2]
                d = Room(name)
                world.find_by_oid(self.location).exits[exit_name_to] = d
                d.exits[exit_name_back] = world.find_by_oid(self.location)
                world.add(d)
                self.sendto("Ok")
                world.save()
        elif cmd.startswith("l"):
            self.sendto("Room: " + world.find_by_oid(self.location).name)
            if getattr(world.find_by_oid(self.location), "description", False):
                self.sendto(world.find_by_oid(self.location).description)
            self.sendto("Players:")
            for x in world.other_players_at_location(self.location, self.oid):
                if getattr(x, "io", False):
                    self.sendto(f"{x.name} is here")
            self.sendto("Objects:")
            for x in world.objects_at_location(self.location):
                self.sendto(f"A {x.name} is here")
            self.sendto(
                "Exits: " + " | ".join(world.find_by_oid(self.location).exits.keys())
            )
        elif not len(world.find_by_oid(self.location).exits.keys()):
            self.parse("look")
        else:
            self.sendto("Huh?")


MINIMAL_DB = """- !!python/object:mud.Room
  exits: {}
  name: Lobby
  oid: 1
"""


class World(object):
    def __init__(self):
        try:
            open("db/world.yaml", "r")
        except IOError:
            print("Building minimal world database ...", end="")
            f = open("db/world.yaml", "w")
            f.write(MINIMAL_DB)
            f.close()
            print("Done.")
        print("Loading world ...", end="")
        self.db = yaml.unsafe_load(open("db/world.yaml", "r"))
        if not isinstance(self.db, list):
            self.db = [self.db]
        self.dbtop = max([x.oid for x in self.db])
        print("Done.")

    def getid(self):
        self.dbtop += 1
        if self.find_by_oid(self.dbtop):
            self.getid()
        return self.dbtop

    def save(self):
        f = open("db/world.yaml", "w")
        f.write(yaml.dump(self.db))
        f.close()

    def add(self, obj):
        obj.oid = self.getid()
        self.db.insert(int(obj.oid), obj)

    def delete(self, obj):
        self.db.remove(obj)

    def find_player_by_name(self, nm):
        for o in self.db:
            if isinstance(o, Player) and o.name == nm:
                return o

    def players_at_location(self, loc):
        if not loc:
            in_loc = lambda o: True
        else:
            in_loc = lambda o: o.location == loc
        return [o for o in self.db if isinstance(o, Player) and in_loc(o)]

    def other_players_at_location(self, loc, plrid):
        if not loc:
            in_loc = lambda o: True
        else:
            in_loc = lambda o: o.location == loc
        return [o for o in self.db if isinstance(o, Player) and in_loc(o) and o.oid != plrid]


    def global_message(self, msg):
        for plr in self.players_at_location(None):
            try:
                plr.sendto(msg)
            except:
                print(f'Error sending "{msg}" to {plr.name}')

    def global_message_others(self, msg, plrid):
        for plr in self.other_players_at_location(None, plrid):
            plr.sendto(msg)

    def objects_at_location(self, loc):
        if not loc:
            in_loc = lambda o: True
        else:
            in_loc = lambda o: o.location == loc
        is_obj = lambda o: (
            isinstance(o, Obj)
            and not isinstance(o, Room)
            and not isinstance(o, Player)
            )
        return [o for o in self.db if is_obj(o) and in_loc(o)]
 
    def find_by_oid(self, i):
        for x in self.db:
            if x.oid == i:
                return x
        return None

Handles = namedtuple('Handles', 'rfile wfile')

class MudHandler(StreamRequestHandler):

    def _readline(self):
        return self.rfile.readline().decode('utf8')

    def _write(self, s):
        self.wfile.write(s.encode('utf8'))

    def handle(self):
        self._write(BANNER)
        login_name = self._readline().strip()
        if len(login_name) < 1:
            self.setup()
        io = Handles(self.rfile, self.wfile)
        d = world.find_player_by_name(login_name)
        if d:
            d.io = io
        else:
            d = Player(login_name, io)
            world.add(d)
        self._write(f"Welcome {d.name} @ {self.client_address[0]}")
        r = "look"
        while r:
            d.parse(r)
            if not getattr(d, "io", False):
                break
            self._write("> ")
            r = self._readline()


def main():
    global world
    world = World()

    z = ThreadingTCPServer(("", 4000), MudHandler)
    try:
        z.serve_forever()
    except KeyboardInterrupt:
        world.global_message("World is shutting down")
        for plr in world.players_at_location(None):
            try:
                plr.parse("quit")
            except Exception as e:
                print(f"ERROR: {plr.name} could not quit gracefully: {e!r}")
                traceback.print_exc()
        z.server_close()
    world.save()


if __name__ == "__main__":
    main()
