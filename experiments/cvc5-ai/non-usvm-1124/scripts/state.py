"""Atomically publish the live operator checkpoint."""
import argparse

from common import ROOT, now, write

p=argparse.ArgumentParser()
p.add_argument("current")
p.add_argument("--done",action="append",default=[])
p.add_argument("--next",action="append",default=[])
a=p.parse_args()
write(ROOT/"current.json",dict(current=a.current,done=a.done,next=a.next,updated=now()))
