from __future__ import absolute_import, division, print_function

import subprocess
import os

def test_function():
    setup = """
    #!/bin/bash
    set -x

    gunicorn --workers=1 --threads=1 "hcli_core:connector(\\"`hcli_hai path`\\")" --error-log ./error.log --daemon
    sleep 3
    huckle cli install http://127.0.0.1:8000

    cat ./error.log
    """

    p1 = subprocess.Popen(['bash', '-c', setup], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p1.communicate()

    error = err.decode('utf-8')
    result = out.decode('utf-8')
    print(error)
    print(result)

    hello = """
    #!/bin/bash
    set -x

    eval $(huckle env)
    hai new > /dev/null 2>&1
    hai context
    kill $(ps aux | grep '[g]unicorn' | awk '{print $2}')
    """

    p2 = subprocess.Popen(['bash', '-c', hello], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p2.communicate()
    error = err.decode('utf-8')
    result = out.decode('utf-8')
    print(error)
    print(result)

    assert(result == '{\n    "messages": [\n        {\n            "content": "",\n            "role": "system"\n        }\n    ],\n    "name": "",\n    "title": ""\n}\n')
