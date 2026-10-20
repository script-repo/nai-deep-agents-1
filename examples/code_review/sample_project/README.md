# sample_project

A tiny billing service used by the code review agent's examples. `../sample.diff` is a change to it with a few
planted problems (SQL built by string concatenation, a hard-coded fake key, a bare `except:` and new code
without tests). `pytest` runs offline.
