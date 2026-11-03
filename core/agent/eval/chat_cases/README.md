# agent eval · chat cases

One JSON file per case for `../chat_eval.py`: a short recorded chat ending on the person's message,
the agent tools it holds (by their served names), per variant the answer the failing tool gave, and
what counts as a pass. Held offline by `core/agent/tests/test_chat_eval.py`; run live by hand.
