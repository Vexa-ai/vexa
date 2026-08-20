- **A meeting already served or stopped is never re-armed (#1211).** Calendar sync no longer
  recreates and re-dispatches an occurrence whose bot already attended the meeting or was stopped
  by the user; only genuine failures retry, and only within that occurrence's own window.
