"""Use the recorded profiler with portable library inputs and shared array support."""
import os
os.environ["NIGHT_PROFILE_REAL"]="1"

from profile_probe import main

if __name__=="__main__":
    main()
