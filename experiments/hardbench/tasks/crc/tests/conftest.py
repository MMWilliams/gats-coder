import sys

for name in ("zlib", "binascii"):
    sys.modules[name] = None
