- **An emailed sign-in link signs in once, on every terminal replica (#1784).** The record of used
  links moved from each terminal process to admin-api, which keeps it in the service Redis until the
  link expires. A link redeemed on one replica, or before a restart, is refused everywhere. While
  that record cannot be checked, the sign-in is refused and the link stays usable.
