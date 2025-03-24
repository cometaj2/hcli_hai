import subprocess
import os
import pytest

# bootstrap the test by starting an hcli server with mgmt config and fresh * admin creds
@pytest.fixture(scope="module")
def gunicorn_server():
    # Start gunicorn server
    setup = """
    #!/bin/bash
    set -x

    rm -rf ~/.huckle_test
    mkdir ~/.huckle_test
    export HUCKLE_HOME_TEST=$HUCKLE_HOME
    export HUCKLE_HOME=~/.huckle_test
    eval $(huckle env)

    echo "Cleanup preexisting huckle hcli installations..."
    huckle cli rm hco
    huckle cli rm hai

    echo "Cleanup old run data..."
    rm -f ./gunicorn-error.log
    rm -f ./test_credentials

    echo "Setup a custom credentials file for the test run"
    echo -e "[config]
core.auth = False" > ./test_credentials

    gunicorn --workers=1 --threads=100 -b 0.0.0.0:18080 "hcli_core:connector(config_path=\\\"./test_credentials\\\",plugin_path=\\\"`hcli_hai path`\\\")" --daemon --log-file=./gunicorn.log --error-logfile=./gunicorn-error.log --capture-output

    sleep 2

    curl -i http://127.0.0.1:18080
    cat ./gunicorn.log
    cat ./gunicorn-error.log
    huckle cli install http://127.0.0.1:18080

    """
    process = subprocess.Popen(['bash', '-c', setup], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = process.communicate()
    result = out.decode('utf-8')
    error = err.decode('utf-8')

    print(result)
    print(error)

    # Verify setup worked
    assert os.path.exists('./gunicorn-error.log'), "gunicorn-error.log not found"
    assert os.path.exists('./test_credentials'), "test_credentials not found"

@pytest.fixture(scope="module")
def cleanup():

    # Let the tests run
    yield

    # Enhanced cleanup with verification
    cleanup_script = """
    #!/bin/bash
    set -x  # Print commands as they execute

    rm -rf ~/.huckle_test
    export HUCKLE_HOME=$HUCKLE_HOME_TEST

    # Force kill any remaining processes
    for pid in $(ps aux | grep '[g]unicorn' | awk '{print $2}'); do
        kill -9 $pid 2>/dev/null || true
    done
    """

    # Run cleanup and capture output
    cleanup_process = subprocess.run(['bash', '-c', cleanup_script], capture_output=True, text=True)

    # One final check with Python's os module
    if os.path.exists('./gunicorn-error.log'):
        os.remove('./gunicorn-error.log')
    if os.path.exists('./gunicorn-noauth-error.log'):
        os.remove('./gunicorn-noauth-error.log')
    if os.path.exists('./test_credentials'):
        os.remove('./test_credentials')
    if os.path.exists('./test_credentials.lock'):
        os.remove('./test_credentials.lock')

    # Verify files are gone
    assert not os.path.exists('./gunicorn-error.log'), "gunicorn-error.log still exists"
    assert not os.path.exists('./gunicorn-noauth-error.log'), "gunicorn-noauth-error.log still exists"
    assert not os.path.exists('./test_credentials'), "test_credentials still exists"
    assert not os.path.exists('./test_credentials.lock'), "test_credentials.lock still exists"
