- **Sign in through your own identity provider: ADFS, Keycloak or any OpenID Connect issuer (#1823).**
  Set an issuer URL, a client id and a secret, and the Terminal's sign-in card offers a button with the
  name you choose. Claims are read from the ID token (ADFS's userinfo carries none), an internal CA
  chain can be supplied, and the allow-list and admin claim apply as for every other door.
  `VEXA_SIGNIN_METHODS` (Helm `terminal.signinMethods`) can offer only this door, or this door and the
  emailed link. See [Sign-in with your own identity provider](/sign-in-oidc).
