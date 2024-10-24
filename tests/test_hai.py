from __future__ import absolute_import, division, print_function

import subprocess
import os

def test_function():
    setup = """
    #!/bin/bash
    set -x

    # Try to kill any existing gunicorn processes
    pkill gunicorn || true

    # Start gunicorn with more verbose logging and capture output
    gunicorn --workers=1 --threads=1 --log-level debug --bind 127.0.0.1:8000 "hcli_core:connector(\\"$CLI_PATH\\")" --daemon --capture-output --error-logfile - --access-logfile - &
    GUNICORN_PID=$!

    # Wait and check if server is running
    echo "Waiting for server to start..."
    sleep 5

    # Check if gunicorn is still running
    if ! ps -p $GUNICORN_PID > /dev/null; then
        echo "Gunicorn process died. Checking logs:"
        cat /tmp/gunicorn-error.log || true
        exit 1
    fi

    # Test server connection
    if ! curl -v http://127.0.0.1:8000 2>&1; then
        echo "Server failed to start. Process info:"
        ps aux | grep gunicorn
        echo "Checking for listening ports:"
        netstat -tulpn | grep LISTEN
        echo "Checking logs:"
        cat /tmp/gunicorn-error.log || true
        exit 1
    fi

    huckle cli install http://127.0.0.1:8000
    """

    p1 = subprocess.Popen(['bash', '-c', setup], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    out, err = p1.communicate()

    hello = """
    #!/bin/bash
    set -x

    eval $(huckle env)
    hai new > /dev/null 2>&1
    hai context
    kill $(ps aux | grep '[g]unicorn' | awk '{print $2}')
    """

    p2 = subprocess.Popen(['bash', '-c', hello], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    out, err = p2.communicate()
    result = out.decode('utf-8')

    assert(result == '{\n    "messages": [\n        {\n            "content": "",\n            "role": "system"\n        }\n    ],\n    "name": "",\n    "title": ""\n}\n')
