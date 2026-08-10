- **Admin identity reads now resolve an existing user by authoritative ID (#994).** Internal
  service consumers can bind admission and lifecycle work to the authenticated numeric user
  identity without falling back to email or creating a user as a side effect.
