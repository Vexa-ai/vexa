fix(gates): every gate failure now prints its diagnostic — an empty stdout Buffer is truthy, so `(e.stdout || e.stderr || e)` was discarding the real error at 22 sites (#1107).
