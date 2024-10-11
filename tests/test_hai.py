from __future__ import absolute_import, division, print_function

import subprocess
import os

def test_function():
    setup = """
    #!/bin/bash
    set -x

    gunicorn --workers=1 --threads=1 "hcli_core:connector(\\"`hcli_hai path`\\")" --daemon
    huckle cli install http://127.0.0.1:8000
    """

    p1 = subprocess.Popen(['bash', '-c', setup], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    out, err = p1.communicate()

    hello = """
    #!/bin/bash
    set -x

    export PATH=$PATH:~/.huckle/bin
    hai new
    hai context
    kill $(ps aux | grep '[g]unicorn' | awk '{print $2}')
    """

    p2 = subprocess.Popen(['bash', '-c', hello], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    out, err = p2.communicate()
    result = out.decode('utf-8')

    assert(result == '{\n    "title": null,\n    "context": [\n        {\n            "role": "system",\n            "content": ""\n        }\n    ]\n}\n')
