#!/usr/bin/python3
"""Forward both streams byte for byte and return as soon as cvc5 exits."""
import os
import subprocess
import sys
from threading import Thread


def send(dst, data):
    view=memoryview(data)
    while view:
        view=view[os.write(dst,view):]


def mirror(source,target,log):
    while data:=os.read(source,64*1024):
        log.write(data)
        send(target,data)


def main():
    command=[os.environ["WIRE_SOLVER"],*sys.argv[1:]]
    child=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE)
    def from_parent():
        with open(os.environ["WIRE_IN"],"wb",buffering=0) as capture:
            try:
                mirror(0,child.stdin.fileno(),capture)
            except BrokenPipeError:
                pass
            finally:
                child.stdin.close()
    parent_input=Thread(target=from_parent,daemon=True)
    parent_input.start()
    with open(os.environ["WIRE_OUT"],"wb",buffering=0) as capture:
        mirror(child.stdout.fileno(),1,capture)
    return child.wait()


if __name__=="__main__":
    sys.exit(main())
