# PLANTED RED, reverted in the next commit (#2035 review): proves that one failed python leg makes
# needs.python.result a failure and the required `gates` check red, with the leg named.
def test_planted_red_leg():
    assert False, "planted: one red leg must red the gates check (#2035)"
