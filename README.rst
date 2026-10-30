|pypi| |build status| |pyver|

HCLI hai
========

HCLI hai is a python package wrapper that contains an HCLI sample application (hai); hai is an HCLI for interacting with Ollama (remote or local), xAI, or OpenAI models via terminal input and output streams.

----

HCLI hai wraps hai (an HCLI) and is intended to be used with an HCLI Client [1] as presented via an HCLI Connector [2].

You can find out more about HCLI on hcli.io [3]

[1] https://github.com/cometaj2/huckle

[2] https://github.com/cometaj2/hcli_core

[3] http://hcli.io

Installation
------------

HCLI hai requires a supported version of Python and pip.

You'll need an HCLI Connector to run hai. For example, you can use HCLI Core (https://github.com/cometaj2/hcli_core), a WSGI server such as Green Unicorn (https://gunicorn.org/), and an HCLI Client like Huckle (https://github.com/cometaj2/huckle).


.. code-block:: console

    pip install hcli-hai
    pip install hcli-core
    pip install huckle
    pip install gunicorn
    hcli_core cli install `hcli_hai path`
    hcli_core cli run hai | bash

Usage
-----

Open a different shell window.

Setup the huckle env eval in your .bash_profile (or other bash configuration) to avoid having to execute eval everytime you want to invoke HCLIs by name (e.g. hai).

Note that no CLI is actually installed by Huckle. Huckle reads the HCLI semantics exposed by the API via HCLI Connector and ends up behaving *like* the CLI it targets.


.. code-block:: console

    huckle cli install http://127.0.0.1:8000
    eval $(huckle env)
    hai help

Versioning
----------

This project makes use of semantic versioning (http://semver.org) and may make use of the "devx",
"prealphax", "alphax" "betax", and "rcx" extensions where x is a number (e.g. 0.3.0-prealpha1)
on github.

Supports
--------

- Chatting via input/output streams (e.g. via pipes).
- .hai folder structure in a users's home directory to help track hai configuration and contexts.
- Creating, listing, deleting, and changing conversation contexts.
- Automatic title creation based on context
- Custom context naming to help organize contexts
- Behavior setting to allow for persistent chat behavior (e.g. the Do Anything Now (DAN) prompt).
- Conversation vibing via detailed plans and HCLI integration to allow for external tool use.
  - This allows for capabilities enhancements (e.g. web search, git repo interaction, terminal use, etc.).
- hcli-problem-details use to help relay RFC 9457 problem details back to the HCLI client.
- Assistant audio summary and commentary for the last received context response.
- Supports changing of the orchestrator behavior.
- Supports HAI_HOME as environment variable and otherwise defaults to the user's home.
- Supports agents reading from the artifact input store

To Do
-----

- Support multi-process and multi-context agent runs or conversations without conflict (multi-user).
- Support individualized multiuser conversation context support.
    - Password protected multi-user support via huckle and hcli_core facilities.
- Support a memory layer for the the AI HCLI (hai).
    - Automatic context switching per NLP on received input stream.
    - Context blending to mary different contexts.
    - Automatic context compression to yield a more substantial memory footprint per context window.
    - Context reuse (e.g. reuse of a previously captured code base to avoid reprocessing context).
    - Artifact and run store search to locate previously observed contexts
- Move voice playback to the client-side (e.g. Haillo)
  - Support summarizing assistance storage alongside related context with client-side retrieval.
  - Support voice synthesis from summaries and audio retrieval client-side

- Prevent `metadata.json`, `.`, and `..` as uploaded filenames.
- Validate `context_id` against path traversal.
- Make artifact creation atomic.
- Document SHA-1 as a deduplication-only identifier.
- Validate metadata and reject symlinks if the storage directory is not fully trusted.

Bugs
----

- Occasional empty response from summarizing assistant

.. |build status| image:: https://circleci.com/gh/cometaj2/hcli_hai.svg?style=shield
   :target: https://circleci.com/gh/cometaj2/hcli_hai
.. |pypi| image:: https://img.shields.io/pypi/v/hcli-hai?label=hcli-hai
   :target: https://pypi.org/project/hcli-hai
.. |pyver| image:: https://img.shields.io/pypi/pyversions/hcli-hai.svg
   :target: https://pypi.org/project/hcli-hai
