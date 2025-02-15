import subprocess
import os
import pytest

def test_hai_context(gunicorn_server, cleanup):
    hello = """
    #!/bin/bash
    set -x

    export HUCKLE_HOME=~/.huckle_test
    eval $(huckle env)

    hai new > /dev/null 2>&1
    hai context --json
    """

    p2 = subprocess.Popen(['bash', '-c', hello], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p2.communicate()
    error = err.decode('utf-8')
    result = out.decode('utf-8')

    assert('{\n    "messages": [\n        {\n            "content": "",\n            "role": "system"\n        }\n    ],\n    "name": "",\n    "title": ""\n}\n' == result)

def test_hai_name(gunicorn_server, cleanup):
    hello = """
    #!/bin/bash
    set -x

    export HUCKLE_HOME=~/.huckle_test
    eval $(huckle env)

    hai name set hello
    hai name
    """

    p3 = subprocess.Popen(['bash', '-c', hello], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p3.communicate()
    error = err.decode('utf-8')
    result = out.decode('utf-8')

    assert('hello\n' == result)
