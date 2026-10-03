import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hcli_hai", "cli"))

from ai.policy import (  # noqa: E402
    alloc_id,
    derive_check,
    evidence_met,
    listing_files,
    observe_key,
)


def test_checks_are_derived_without_the_model():
    pwd = derive_check("print the working directory", "the directory path displayed")
    assert pwd == {"kind": "observe", "key": ["pwd"], "bash": "pwd"}

    listing = derive_check("list that directory", "a file named directory listing")
    assert listing["bash"] == "ls"

    read = derive_check("read the service.py", "contents of service.py")
    assert read["key"] == ["read", "service.py"]
    assert read["bash"] == "cat service.py"

    assert derive_check("identify the failing test", "the test name")["kind"] == "open"


def test_other_task_body_does_not_meet_evidence():
    task = {
        "check": derive_check("read the service.py", "contents of service.py"),
        "observations": [{"bash": "cat logger.py", "result": "class Logger"}],
    }
    assert evidence_met(task) is False
    task["observations"] = [{"bash": "cat service.py", "result": "class Service"}]
    assert evidence_met(task) is True
    assert observe_key("cat ./service.py") == ("read", "service.py")


def test_listing_files_skip_directories():
    blob = "\n".join([
        "total 76",
        "drwxr-xr-x 1 jeff jeff 164 Oct 3 09:16 .",
        "-rw-r--r-- 1 jeff jeff 7394 Oct 3 09:08 cli.py",
        "drwxr-xr-x 1 jeff jeff 214 Oct 3 09:09 ai",
        "-rw-r--r-- 1 jeff jeff 5149 Oct 3 09:16 service.py",
    ])
    assert listing_files(blob) == ["cli.py", "service.py"]


def test_replan_ids_do_not_recycle():
    used = {"t1", "t2", "t3"}
    assert alloc_id(used) == "t4"
    assert "t4" in used
